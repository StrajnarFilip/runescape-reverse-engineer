"""Summarize local Telos captures without publishing raw gameplay records.

The report keeps only timing statistics, counts and capture digests. Cue-to-hit
delays are client observations, not server prayer-check deadlines.

Usage::

    python work/analyze_telos_calibration.py --capture telos.jsonl --enrage 300
        --phase 1 --session-date 2026-11-10 --output report.json
"""

import argparse
import collections
import hashlib
import json
import statistics
from pathlib import Path

NANOSECONDS_PER_MILLISECOND = 1_000_000
MELEE_AUTO_SEQUENCE = 28929
MELEE_DAMAGE_HIT_TYPE = 133
MISS_HIT_TYPE = 482
PAIRED_HIT_TYPES = (MELEE_DAMAGE_HIT_TYPE, MISS_HIT_TYPE)
# A hit may be observed in the cue's own sample, slightly before the cue timestamp.
HIT_BEFORE_CUE_TOLERANCE_NS = 100 * NANOSECONDS_PER_MILLISECOND
HIT_AFTER_CUE_LIMIT_NS = 1_200 * NANOSECONDS_PER_MILLISECOND
KEY_BEFORE_CHANGE_TOLERANCE_NS = 20 * NANOSECONDS_PER_MILLISECOND
KEY_TO_CHANGE_LIMIT_NS = 1_500 * NANOSECONDS_PER_MILLISECOND
CURSES_VARIABLE_ID = "3275"
TELOS_NAME = "telos"
DEVOTION_STRUCT_ID = 25028
OPENING_AUTO_COUNT = 3
OPENING_OBSERVATION_LIMIT_NS = 8_000 * NANOSECONDS_PER_MILLISECOND
CLIENT_LOGIC_TICK_MS = 20
MAX_DEVOTION_CONTROL_AGE_MS = 8000

USER_LABELLED_CUES = {
    "28929": "ordinary melee auto in the isolated phase-1 melee context",
    "28931": "tendrils",
    "28938": "uppercut",
    "28935": "Hold Still",
}
OBSERVED_NPC_FORMS = {"22891": "Telos active", "22892": "Telos dormant"}
HITMARK_SYMBOL_LEADS = {
    "133": "melee_damage_parent",
    "143": "heal_splat_parent",
    "144": "damage_parent",
    "482": "miss_parent",
}
LIMITATIONS = [
    "Only isolated phase-1 melee autos have been checked; other phases/styles "
    "and complete attack coverage remain unvalidated. Per-capture enrage labels are retained.",
    "Cue-to-hit observation is not the server prayer-check deadline.",
    "Overhead flags are observed client state, not proof of server-side activation timing.",
    "Manual key times are sampled down edges; scripted times record Windows input acceptance.",
    "Player hitmarks do not identify the attacker; pairing uses the isolated phase-1 context.",
    "No early/late protection outcome is inferred from a random damage amount alone.",
    "Complete incoming-attack coverage and safe Soul Split windows are not established.",
    "Observed success/failure delays are not a universal threshold over network and tick jitter.",
]


def to_milliseconds(nanoseconds):
    return nanoseconds / NANOSECONDS_PER_MILLISECOND


def delay_summary(delays_ms):
    """Return count, minimum, median and maximum of delays in milliseconds."""
    if not delays_ms:
        return {"count": 0}
    return {
        "count": len(delays_ms),
        "min_ms": round(min(delays_ms), 3),
        "median_ms": round(statistics.median(delays_ms), 3),
        "max_ms": round(max(delays_ms), 3),
    }


def read_rows(path):
    """Return the complete JSONL rows of a capture."""
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.endswith("\n"):
                break  # A running writer may not have completed its last row yet.
            if line.strip():
                rows.append(json.loads(line))
    return rows


def row_session(row):
    return row["pid"], row["process_creation_time"], row["combat"]["scene_epoch"]


def event_session(event):
    return event["pid"], event["process_creation_time"], event["scene_epoch"]


def is_telos(actor):
    return actor["name"].partition(",")[0].strip().casefold() == TELOS_NAME


def collect_observations(rows):
    """Return (overhead changes, key presses, Telos cues, player hit events)."""
    changes, keys, cues, hits = [], [], [], []
    previous_prayer, previous_observed_ns = {}, {}
    telos_instances = set()
    for row in rows:
        session = row_session(row)
        active_prayer = row["combat"]["active_prayer"]
        if session not in previous_prayer or active_prayer != previous_prayer[session]:
            changes.append(
                {
                    "session": session,
                    "prayer": active_prayer,
                    "observed_ns": row["observed_ns"],
                    "previous_observation_ns": row.get("previous_sample_ns")
                    or previous_observed_ns.get(session, row["observed_ns"]),
                }
            )
        previous_prayer[session] = active_prayer
        previous_observed_ns[session] = row["observed_ns"]
        keys.extend(dict(key, session=session) for key in row.get("manual_prayer_keys", []))
        telos_instances.update(
            (session, actor["instance_id"]) for actor in row["animations"] if is_telos(actor)
        )
        cues.extend(row.get("npc_action_events", []))
        hits.extend(row.get("player_hit_events", []))
    telos_cues = [
        cue for cue in cues if (event_session(cue), cue["instance_id"]) in telos_instances
    ]
    return changes, keys, telos_cues, hits


def pair_cues_with_hits(cues, hits, changes):
    """Pair each melee auto cue with the first unused melee damage or miss hit after it."""
    pairs, damage_delays_ms, used_hits = [], [], set()
    for cue in cues:
        if cue["action_sequence"] != MELEE_AUTO_SEQUENCE:
            continue
        session = event_session(cue)
        candidates = [
            hit
            for hit in hits
            if hit["sequence"] not in used_hits
            and event_session(hit) == session
            and hit["hit"]["type_id"] in PAIRED_HIT_TYPES
            and -HIT_BEFORE_CUE_TOLERANCE_NS
            <= hit["observed_ns"] - cue["observed_ns"]
            <= HIT_AFTER_CUE_LIMIT_NS
        ]
        if not candidates:
            continue
        hit = min(candidates, key=lambda candidate: candidate["observed_ns"])
        used_hits.add(hit["sequence"])
        delay_ms = to_milliseconds(hit["observed_ns"] - cue["observed_ns"])
        changes_before_hit = [
            change
            for change in changes
            if change["session"] == session and change["observed_ns"] <= hit["observed_ns"]
        ]
        pairs.append(
            {
                "cue_event": cue["sequence"],
                "hit_event": hit["sequence"],
                "cue_to_observed_hit_ms": round(delay_ms, 3),
                "hit_type": hit["hit"]["type_id"],
                "amount": hit["hit"]["amount"],
                "prayer_near_hit_observation": (
                    changes_before_hit[-1]["prayer"] if changes_before_hit else None
                ),
            }
        )
        if hit["hit"]["type_id"] == MELEE_DAMAGE_HIT_TYPE:
            damage_delays_ms.append(delay_ms)
    return pairs, damage_delays_ms


def pair_keys_with_changes(keys, changes):
    """Pair each key press with the nearest unused change to its bound prayer."""
    key_pairs, acknowledged_changes = [], set()
    for key in keys:
        candidates = [
            change
            for change in changes
            if (change["session"], change["observed_ns"]) not in acknowledged_changes
            and change["session"] == key["session"]
            and change["prayer"] == key["prayer"]
            and key["observed_ns"] - KEY_BEFORE_CHANGE_TOLERANCE_NS
            <= change["observed_ns"]
            <= key["observed_ns"] + KEY_TO_CHANGE_LIMIT_NS
        ]
        if not candidates:
            continue
        change = min(candidates, key=lambda item: abs(item["observed_ns"] - key["observed_ns"]))
        acknowledged_changes.add((change["session"], change["observed_ns"]))
        key_pairs.append(
            {
                "prayer": key["prayer"],
                "virtual_key": key["virtual_key"],
                "modifiers": key["modifiers"],
                "sampled_key_to_overhead_ms": round(
                    to_milliseconds(change["observed_ns"] - key["observed_ns"]), 3
                ),
                "key_poll_interval_ms": round(
                    to_milliseconds(key["observed_ns"] - key["previous_poll_ns"]), 3
                ),
            }
        )
    return key_pairs


def devotion_opening_summary(rows, *, clean_opening_confirmed=False):
    """Describe the first manual Devotion opening without deriving a server deadline.

    Cooldown evidence is paired with a manual cast, not treated as an active
    buff. The clean-opening label must come from controlled player validation.
    """
    changes, keys, cues, hits = collect_observations(rows)
    casts = [
        dict(press, session=row_session(row))
        for row in rows
        for press in row.get("manual_ability_keys", [])
        if press.get("ability") == "devotion"
    ]
    timer_changes, previous_timers = [], {}
    for row in rows:
        timer = row.get("devotion_cooldown", {})
        session = row_session(row)
        identity = (timer.get("start_tick"), timer.get("end_tick"))
        if (
            timer.get("struct_id") == DEVOTION_STRUCT_ID
            and timer.get("timer_valid")
            and identity != previous_timers.get(session)
        ):
            timer_changes.append(dict(row, session=session))
        if timer.get("timer_valid"):
            previous_timers[session] = identity
    matched_cast = next(
        (
            (cast, row)
            for cast in casts
            for row in timer_changes
            if row["session"] == cast["session"]
            and -KEY_BEFORE_CHANGE_TOLERANCE_NS
            <= row["observed_ns"] - cast["observed_ns"]
            <= KEY_TO_CHANGE_LIMIT_NS
        ),
        None,
    )
    if matched_cast is None:
        return None
    cast, timer_row = matched_cast
    session = cast["session"]
    opening = [
        cue
        for cue in cues
        if event_session(cue) == session
        and cue["action_sequence"] == MELEE_AUTO_SEQUENCE
        and 0 <= cue["observed_ns"] - timer_row["observed_ns"] <= OPENING_OBSERVATION_LIMIT_NS
    ][:OPENING_AUTO_COUNT]
    pairs, _ = pair_cues_with_hits(opening, hits, changes)
    pairs_by_cue = {pair["cue_event"]: pair for pair in pairs}
    trials = []
    previous_hit_ns = timer_row["observed_ns"]
    for index, cue in enumerate(opening):
        pair = pairs_by_cue.get(cue["sequence"])
        if pair is None:
            continue
        hit = next(
            hit
            for hit in hits
            if hit["sequence"] == pair["hit_event"] and event_session(hit) == session
        )
        presses = [
            key
            for key in keys
            if key["session"] == session
            and key["prayer"] == "melee"
            and previous_hit_ns <= key["observed_ns"] <= hit["observed_ns"]
        ]
        press = presses[-1] if presses else None
        acknowledgements = [
            change
            for change in changes
            if press is not None
            and change["session"] == session
            and change["prayer"] == "melee"
            and press["observed_ns"]
            <= change["observed_ns"]
            <= press["observed_ns"] + KEY_TO_CHANGE_LIMIT_NS
        ]
        acknowledgement = acknowledgements[0] if acknowledgements else None

        def relative_ms(timestamp, cue_observed_ns=cue["observed_ns"]):
            return round(to_milliseconds(timestamp - cue_observed_ns), 3)

        trials.append(
            dict(
                pair,
                auto_number=index + 1,
                sampled_melee_key_after_cue_ms=(
                    relative_ms(press["observed_ns"]) if press else None
                ),
                key_previous_poll_after_cue_ms=(
                    relative_ms(press["previous_poll_ns"]) if press else None
                ),
                melee_first_observed_after_cue_ms=(
                    relative_ms(acknowledgement["observed_ns"]) if acknowledgement else None
                ),
                melee_previous_sample_after_cue_ms=(
                    relative_ms(acknowledgement["previous_observation_ns"])
                    if acknowledgement
                    else None
                ),
                outcome=(
                    "one_damage_during_user_confirmed_devotion"
                    if clean_opening_confirmed
                    and pair["hit_type"] == MELEE_DAMAGE_HIT_TYPE
                    and pair["amount"] == 1
                    else "not_classified"
                ),
            )
        )
        previous_hit_ns = hit["observed_ns"]
    return {
        "scope": "first manual Devotion cast and up to three following phase-1 melee autos",
        "clean_opening_user_confirmed": clean_opening_confirmed,
        "devotion_struct_id": DEVOTION_STRUCT_ID,
        "sampled_cast_key_to_native_cooldown_ms": round(
            to_milliseconds(timer_row["observed_ns"] - cast["observed_ns"]), 3
        ),
        "devotion_buff_active_from_memory": None,
        "trials": trials,
        "prayer_check_deadline_validated": False,
        "interpretation": (
            "Successful switch observations do not establish the latest reliable switch time. "
            "Client overhead flags and hitmarks are sampled separately."
        ),
    }


def controlled_trial_summary(rows, *, clean_opening_confirmed=False):
    """Summarize accepted experiment inputs separately from manual key observations."""
    if not any(row.get("input_mode") == "controlled_calibration" for row in rows):
        return None
    updates = [update for row in rows for update in row.get("calibration_updates", [])]
    accepted = [update for update in updates if update["event"] == "input_accepted"]
    test_commands = [
        update for update in accepted if update.get("requested_after_cue_ms") is not None
    ]
    paired_hits = [update for update in updates if update["event"] == "paired_hit"]

    def hit_for_stage(stage):
        candidate = next((hit for hit in paired_hits if hit["stage"] == stage), None)
        return (
            {
                "amount": candidate["hit"]["hit"]["amount"],
                "type_id": candidate["hit"]["hit"]["type_id"],
            }
            if candidate
            else None
        )

    test = test_commands[-1] if test_commands else None
    acknowledgement = (
        next(
            (
                update
                for update in updates
                if update["event"] == "acknowledged"
                and update["prayer"] == "melee"
                and update["observed_ns"] > test["accepted_ns"]
            ),
            None,
        )
        if test
        else None
    )
    acknowledged = acknowledgement is not None
    result = hit_for_stage("result")
    pre_dispatch_hit = hit_for_stage("delay")
    control = hit_for_stage("control")
    result_age_ms = None
    for row in rows:
        if any(
            update["event"] == "paired_hit" and update["stage"] == "result"
            for update in row.get("calibration_updates", [])
        ):
            timer = row.get("devotion_cooldown", {})
            if timer.get("timer_valid"):
                result_age_ms = (
                    (row["client_logic_tick"] - timer["start_tick"]) & 0xFFFFFFFF
                ) * CLIENT_LOGIC_TICK_MS
    outcome = "unlabelled"
    if (
        clean_opening_confirmed
        and control == {"amount": 1, "type_id": MELEE_DAMAGE_HIT_TYPE}
        and result_age_ms is not None
        and 0 <= result_age_ms <= MAX_DEVOTION_CONTROL_AGE_MS
        and result
    ):
        if result["type_id"] == MISS_HIT_TYPE:
            outcome = "miss_inconclusive"
        elif result["type_id"] == MELEE_DAMAGE_HIT_TYPE:
            if result["amount"] == 1:
                outcome = "protected"
            elif result["amount"] > 1:
                outcome = "late_protection"
            else:
                outcome = "zero_damage_inconclusive"
    if pre_dispatch_hit and pre_dispatch_hit["type_id"] == MISS_HIT_TYPE and not test:
        outcome = "miss_before_dispatch_inconclusive"
    return {
        "final_state": rows[-1]["trial_state"],
        "input_sent_by_calibration": bool(accepted),
        "accepted_command_counts": dict(collections.Counter(key["prayer"] for key in accepted)),
        "planned_test_delay_ms": rows[-1].get("requested_delay_ms"),
        "requested_test_delay_ms": test["requested_after_cue_ms"] if test else None,
        "accepted_test_delay_ms": round(test["accepted_after_cue_ms"], 3) if test else None,
        "control_hit": control,
        "test_hit": result,
        "pre_dispatch_hit": pre_dispatch_hit,
        "clean_opening_user_confirmed": clean_opening_confirmed if control else None,
        "observed_control_age_at_test_hit_ms": result_age_ms,
        "outcome": outcome,
        "melee_acknowledged_after_test_command": acknowledged,
        "accepted_test_key_to_melee_ack_ms": (
            round(to_milliseconds(acknowledgement["observed_ns"] - test["accepted_ns"]), 3)
            if acknowledgement
            else None
        ),
        "devotion_buff_active_from_memory": None,
        "abort_reasons": [update["reason"] for update in updates if update["event"] == "aborted"],
        "interpretation": (
            "One-damage control and result support a protected case; damage above one "
            "needs active-Devotion context. Neither establishes an exact server deadline."
        ),
        "prayer_check_deadline_validated": False,
    }


def anticipatory_trial_summary(rows, *, clean_opening_confirmed=False):
    """Keep forecasts, actual early commands and observed curses distinct."""
    if not any(row.get("anticipatory_mode") for row in rows):
        return None
    updates = [update for row in rows for update in row.get("calibration_updates", [])]
    schedules = [update for update in updates if update["event"] == "prediction_scheduled"]
    cues = [update for update in updates if update["event"] == "auto_cue"]
    hits = [update for update in updates if update["event"] == "paired_hit"]
    accepted = [update for update in updates if update["event"] == "input_accepted"]
    acknowledgements = [update for update in updates if update["event"] == "acknowledged"]
    cycles = []
    for schedule in schedules:
        matching_cues = [
            cue
            for cue in cues
            if cue["stage"] == "result"
            and abs(cue["cue_ns"] - schedule["next_cue_ns"]) <= 200 * NANOSECONDS_PER_MILLISECOND
        ]
        if not matching_cues:
            continue
        cue = matching_cues[0]
        hit = next((entry for entry in hits if entry["cue_sequence"] == cue["cue_sequence"]), None)
        protection = next(
            (
                key
                for key in accepted
                if key["prayer"] == "melee"
                and key.get("predicted_cue_ns") == schedule["next_cue_ns"]
            ),
            None,
        )
        protection_ack = next(
            (
                ack
                for ack in acknowledgements
                if protection
                and ack["prayer"] == "melee"
                and ack["observed_ns"] > protection["accepted_ns"]
            ),
            None,
        )
        anchor = next(
            (entry for entry in hits if entry["cue_sequence"] == schedule["anchor_cue_sequence"]),
            None,
        )
        soul_split = next(
            (
                key
                for key in accepted
                if anchor
                and key["prayer"] == "soul_split"
                and anchor["hit"]["observed_ns"]
                <= key["accepted_ns"]
                < schedule["protection_send_ns"]
            ),
            None,
        )
        soul_split_ack = next(
            (
                ack
                for ack in acknowledgements
                if soul_split
                and ack["prayer"] == "soul_split"
                and soul_split["accepted_ns"] < ack["observed_ns"] < cue["cue_ns"]
            ),
            None,
        )
        observed_soul_split_ms = (
            to_milliseconds(protection_ack["observed_ns"] - soul_split_ack["observed_ns"])
            if protection_ack and soul_split_ack
            else None
        )
        next_soul_split_ack = next(
            (
                ack
                for ack in acknowledgements
                if protection_ack
                and ack["prayer"] == "soul_split"
                and ack["observed_ns"] > protection_ack["observed_ns"]
            ),
            None,
        )
        cycles.append(
            {
                "anchor_cue": schedule["anchor_cue_sequence"],
                "actual_cue": cue["cue_sequence"],
                "period_ms": schedule["period_ms"],
                "lead_before_predicted_cue_ms": schedule["lead_before_cue_ms"],
                "prediction_error_ms": round(
                    to_milliseconds(cue["cue_ns"] - schedule["next_cue_ns"]), 3
                ),
                "command_before_actual_cue_ms": (
                    round(to_milliseconds(cue["cue_ns"] - protection["accepted_ns"]), 3)
                    if protection
                    else None
                ),
                "melee_observed_before_actual_cue_ms": (
                    round(to_milliseconds(cue["cue_ns"] - protection_ack["observed_ns"]), 3)
                    if protection_ack
                    else None
                ),
                "melee_observed_before_hit_ms": (
                    round(
                        to_milliseconds(hit["hit"]["observed_ns"] - protection_ack["observed_ns"]),
                        3,
                    )
                    if hit and protection_ack
                    else None
                ),
                "soul_split_observed_ms": (
                    round(observed_soul_split_ms, 3) if observed_soul_split_ms is not None else None
                ),
                "melee_observed_ms_until_next_soul_split": (
                    round(
                        to_milliseconds(
                            next_soul_split_ack["observed_ns"] - protection_ack["observed_ns"]
                        ),
                        3,
                    )
                    if next_soul_split_ack
                    else None
                ),
                "hit": (
                    {"amount": hit["hit"]["hit"]["amount"], "type_id": hit["hit"]["hit"]["type_id"]}
                    if hit
                    else None
                ),
            }
        )
    return {
        "final_state": rows[-1]["trial_state"],
        "clean_opening_user_confirmed": clean_opening_confirmed,
        "input_sent_by_calibration": bool(accepted),
        "cycles": cycles,
        "control_hit": next(
            (
                {"amount": entry["hit"]["hit"]["amount"], "type_id": entry["hit"]["hit"]["type_id"]}
                for entry in hits
                if entry["stage"] == "control"
            ),
            None,
        ),
        "devotion_buff_active_from_memory": None,
        "abort_reasons": [update["reason"] for update in updates if update["event"] == "aborted"],
        "completed_cycles": sum(update["event"] == "cycle_completed" for update in updates),
        "prayer_check_deadline_validated": False,
        "scope": "bounded isolated phase-1 melee opening, not other phases or Magic cue classification",
    }


def summarize(rows):
    """Return the compact timing summary of one capture."""
    changes, keys, cues, hits = collect_observations(rows)
    pairs, damage_delays_ms = pair_cues_with_hits(cues, hits, changes)
    key_pairs = pair_keys_with_changes(keys, changes)
    hit_data_present = any("player_hit_events" in row for row in rows)
    miss_delays_ms = [
        pair["cue_to_observed_hit_ms"] for pair in pairs if pair["hit_type"] == MISS_HIT_TYPE
    ]
    curse_raw_values = {
        row["combat"]["raw_flags"][CURSES_VARIABLE_ID]
        for row in rows
        if row["combat"].get("raw_flags", {}).get(CURSES_VARIABLE_ID) is not None
    }
    return {
        "retained_rows": len(rows),
        "player_hit_data_present": hit_data_present,
        "executable_sha256_values": sorted(
            {row["executable_sha256"] for row in rows if row.get("executable_sha256")}
        ),
        "observed_curse_raw_values": sorted(curse_raw_values),
        "action_sequence_counts": dict(
            collections.Counter(str(cue["action_sequence"]) for cue in cues)
        ),
        "player_hit_events": len(hits),
        "hit_type_counts": dict(collections.Counter(str(hit["hit"]["type_id"]) for hit in hits)),
        "cue_hit_pairs": pairs,
        "cue_to_observed_damage_delay": delay_summary(damage_delays_ms),
        "cue_to_observed_miss_delay": delay_summary(miss_delays_ms),
        "manual_key_events": len(keys),
        "matched_keyboard_changes": key_pairs,
        "sampled_key_to_overhead_delay": delay_summary(
            [pair["sampled_key_to_overhead_ms"] for pair in key_pairs]
        ),
        "npc_action_events_lost": sum(row.get("npc_action_events_lost", 0) for row in rows),
        "player_hit_events_lost": (
            sum(row.get("player_hit_events_lost", 0) for row in rows) if hit_data_present else None
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capture", type=Path, nargs="+", required=True)
    parser.add_argument("--enrage", type=int, required=True)
    parser.add_argument(
        "--capture-enrages",
        type=int,
        nargs="+",
        help="Per-capture user labels, in the same order as --capture",
    )
    parser.add_argument("--phase", type=int, choices=[1], required=True)
    parser.add_argument("--session-date", required=True, help="Client-side date, YYYY-MM-DD")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--confirmed-devotion-opening",
        action="store_true",
        help="Player confirmed Devotion active and no other defensives/stuns for the opening",
    )
    arguments = parser.parse_args()
    enrages = arguments.capture_enrages or [arguments.enrage] * len(arguments.capture)
    if len(enrages) != len(arguments.capture) or any(not 0 <= value <= 4000 for value in enrages):
        parser.error("Provide one enrage in 0..4000 per capture")
    sources = [
        {
            "capture_sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
            "enrage_user_label": enrage,
            "summary": summarize(read_rows(capture)),
            "devotion_opening": devotion_opening_summary(
                read_rows(capture), clean_opening_confirmed=arguments.confirmed_devotion_opening
            ),
            "controlled_trial": controlled_trial_summary(
                read_rows(capture), clean_opening_confirmed=arguments.confirmed_devotion_opening
            ),
            "anticipatory_trial": anticipatory_trial_summary(
                read_rows(capture), clean_opening_confirmed=arguments.confirmed_devotion_opening
            ),
        }
        for capture, enrage in zip(arguments.capture, enrages)
    ]
    report = {
        "client_session_date": arguments.session_date,
        "enrage_user_label": enrages[0] if len(set(enrages)) == 1 else None,
        "enrage_user_labels": sorted(set(enrages)),
        "phase_user_label": arguments.phase,
        "input_sent_by_calibration": any(
            any(
                source[kind] and source[kind]["input_sent_by_calibration"]
                for kind in ("controlled_trial", "anticipatory_trial")
            )
            for source in sources
        ),
        "sources": sources,
        "user_labelled_cues": USER_LABELLED_CUES,
        "observed_npc_forms": OBSERVED_NPC_FORMS,
        "hitmark_symbol_leads": HITMARK_SYMBOL_LEADS,
        "automatic_flick_ready": False,
        "prayer_check_deadline_validated": False,
        "limitations": LIMITATIONS,
    }
    controlled = [source["controlled_trial"] for source in sources if source["controlled_trial"]]
    successful_delays = sorted(
        trial["accepted_test_delay_ms"] for trial in controlled if trial["outcome"] == "protected"
    )
    failed_delays = sorted(
        trial["accepted_test_delay_ms"]
        for trial in controlled
        if trial["outcome"] == "late_protection"
    )
    report["controlled_input_observations"] = {
        "successful_accepted_delays_ms": successful_delays,
        "late_accepted_delays_ms": failed_delays,
        "latest_observed_success_ms": max(successful_delays) if successful_delays else None,
        "earliest_observed_late_ms": min(failed_delays) if failed_delays else None,
        "universal_cutoff_established": False,
        "prayer_check_deadline_validated": False,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"captures": len(sources), "automatic_flick_ready": False}))


if __name__ == "__main__":
    main()
