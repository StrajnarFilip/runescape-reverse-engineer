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
    "Only explicitly labelled phase/enrage/context intervals support classification; "
    "unlabelled contexts remain unknown.",
    "Cue-to-hit observation is not the server prayer-check deadline.",
    "Overhead flags are observed client state, not proof of server-side activation timing.",
    "Keyboard timestamps are sampled down edges with the recorded polling interval.",
    "Player hitmarks do not identify the attacker; pairing uses the isolated phase-1 context.",
    "No early/late protection outcome is inferred from a random damage amount alone.",
    "Complete incoming-attack coverage and safe Soul Split windows are not established.",
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


def normalize_row(row):
    """Accept legacy prayer captures, the recorder, and native schema-1 telemetry.

    Missing hit/loss channels remain missing. An omitted loss count is not
    evidence of zero loss. No raw stage is promoted to a phase here.
    """
    result = dict(row)
    native = row.get("native") or {}
    observed = row.get("observed_ns", row.get("sample_started_ns"))
    if observed is None and isinstance(row.get("monotonic_ms"), (int, float)):
        observed = round(row["monotonic_ms"] * NANOSECONDS_PER_MILLISECOND)
    result["observed_ns"] = observed
    result["combat"] = dict(row.get("combat") or {"scene_epoch": row.get("scene_epoch")})
    result["combat"].setdefault("scene_epoch", row.get("scene_epoch"))
    result["combat"].setdefault("active_prayer", None)
    actors = row.get("animations")
    if actors is None and isinstance(row.get("telos"), list):
        # This producer already selected Telos by name before emitting these actors.
        actors = [dict(actor, name=actor.get("name", "Telos")) for actor in row["telos"]]
    if not isinstance(actors, list):
        npcs = row.get("npcs") or {}
        actors = npcs.get("entities", []) if isinstance(npcs, dict) else npcs
    result["animations"] = actors
    if "npc_action_events" in row:
        result["npc_action_events"] = row["npc_action_events"] or []
    else:
        # Legacy telos_actions were filtered against the exact parent session
        # and sample by the recorder. Do not overwrite any original identity.
        result["npc_action_events"] = []
        for event in row.get("telos_actions", []) or []:
            cue = dict(event)
            for field in ("pid", "process_creation_time"):
                cue.setdefault(field, row.get(field))
            cue.setdefault("scene_epoch", result["combat"]["scene_epoch"])
            cue.setdefault("sequence", cue.get("event_sequence"))
            result["npc_action_events"].append(cue)
    if "player_hit_events" not in result and isinstance(row.get("player_hit_splats"), dict):
        section = row["player_hit_splats"]
        if "newly_observed" in section:
            result["player_hit_events"] = section["newly_observed"] or []
            result["player_hit_events_lost"] = section.get("events_lost")
    for section_name, loss_name in (
        ("hit_splats", "npc_hit_events_lost"),
        ("inventory_changes", "inventory_events_lost"),
    ):
        section = row.get(section_name)
        if loss_name not in result and isinstance(section, dict):
            result[loss_name] = section.get("events_lost")
    presses = row.get("manual_prayer_keys", row.get("manual_prayer_presses", [])) or []
    result["manual_prayer_keys"] = []
    for original in presses:
        press = dict(original)
        press.setdefault("prayer", press.get("bound_prayer"))
        label = press.get("key", "")
        if "virtual_key" not in press and label.startswith("Ctrl+F") and label[6:].isdigit():
            number = int(label[6:])
            if 1 <= number <= 12:
                press["virtual_key"] = 0x6F + number
                press["modifiers"] = 1
        result["manual_prayer_keys"].append(press)
    result.setdefault("sample_sequence", native.get("sample_sequence"))
    return result


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
    return (actor.get("name") or "").partition(",")[0].strip().casefold() == TELOS_NAME


def collect_observations(rows):
    """Return (overhead changes, key presses, Telos cues, player hit events)."""
    changes, keys, cues, hits = [], [], [], []
    previous_prayer, previous_observed_ns = {}, {}
    telos_instances = set()
    for row in rows:
        row = normalize_row(row)
        session = row_session(row)
        active_prayer = row["combat"]["active_prayer"]
        valid_observation = (
            all(type(value) is int and value > 0 for value in session)
            and type(row["observed_ns"]) is int
        )
        if valid_observation and (
            session not in previous_prayer or active_prayer != previous_prayer[session]
        ):
            changes.append(
                {
                    "session": session,
                    "prayer": active_prayer,
                    "baseline": session not in previous_prayer,
                    "observed_ns": row["observed_ns"],
                    "previous_observation_ns": previous_observed_ns.get(
                        session, row["observed_ns"]
                    ),
                }
            )
        if valid_observation:
            previous_prayer[session] = active_prayer
            previous_observed_ns[session] = row["observed_ns"]
            keys.extend(
                dict(key, session=session)
                for key in row.get("manual_prayer_keys", [])
                if type(key.get("observed_ns")) is int
            )
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
    for cue in sorted(cues, key=lambda item: item["observed_ns"]):
        if cue["action_sequence"] != MELEE_AUTO_SEQUENCE:
            continue
        session = event_session(cue)
        candidates = [
            hit
            for hit in hits
            if (event_session(hit), hit["sequence"]) not in used_hits
            and event_session(hit) == session
            and hit["hit"]["type_id"] in PAIRED_HIT_TYPES
            and -HIT_BEFORE_CUE_TOLERANCE_NS
            <= hit["observed_ns"] - cue["observed_ns"]
            <= HIT_AFTER_CUE_LIMIT_NS
        ]
        if not candidates:
            continue
        hit = min(candidates, key=lambda candidate: candidate["observed_ns"])
        used_hits.add((event_session(hit), hit["sequence"]))
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
            and not change.get("baseline", False)
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
                "virtual_key": key.get("virtual_key"),
                "modifiers": key.get("modifiers"),
                "sampled_key_to_overhead_ms": round(
                    to_milliseconds(change["observed_ns"] - key["observed_ns"]), 3
                ),
                "key_poll_interval_ms": (
                    round(to_milliseconds(key["observed_ns"] - key["previous_poll_ns"]), 3)
                    if type(key.get("previous_poll_ns")) is int
                    else key.get("poll_interval_ms")
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


def summarize(rows, *, isolated_phase_1_melee=False):
    """Return the compact timing summary of one capture."""
    rows = [normalize_row(row) for row in rows]
    changes, keys, cues, hits = collect_observations(rows)
    pairs, damage_delays_ms = (
        pair_cues_with_hits(cues, hits, changes) if isolated_phase_1_melee else ([], [])
    )
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
        "cue_hit_pairing_scope": (
            "isolated_phase_1_melee" if isolated_phase_1_melee else "disabled_attacker_unidentified"
        ),
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
        "npc_action_events_lost": known_loss_total(rows, "npc_action_events_lost"),
        "player_hit_events_lost": (
            known_loss_total(rows, "player_hit_events_lost") if hit_data_present else None
        ),
        "native_queue_losses": {
            name: known_loss_total(rows, key)
            for name, key in (
                ("npc_actions", "npc_action_events_lost"),
                ("npc_hits", "npc_hit_events_lost"),
                ("player_hits", "player_hit_events_lost"),
                ("inventory_changes", "inventory_events_lost"),
            )
        },
        "input_sent_by_calibration": input_history(rows),
    }


def input_history(rows):
    """Any explicit true is retained; omitted/invalid input provenance stays unknown."""
    values = [row.get("input_sent_by_calibration") for row in rows]
    if any(value is True for value in values):
        return True
    return False if values and all(value is False for value in values) else None


def known_loss_total(rows, key):
    """Missing loss counters remain unknown rather than silently becoming zero."""
    values = [row.get(key) for row in rows]
    if not values or any(type(value) is not int or value < 0 for value in values):
        return None
    return sum(values)


def validate_output_paths(inputs, outputs):
    """Reject aliases of captures/annotations or another output before writing."""
    protected = [Path(path).resolve() for path in inputs]
    for output in outputs:
        if output is None:
            continue
        path = Path(output).resolve()
        for existing in protected:
            if path == existing or (
                path.exists() and existing.exists() and path.samefile(existing)
            ):
                raise ValueError("Analysis outputs must differ from inputs and each other")
        protected.append(path)


def build_attack_catalogue(sources):
    """Aggregate phase-labelled native events; never derive protection deadlines.

    Each source supplies rows, its capture SHA-256 and bounded phase intervals.
    Intervals carry original session identity, explicit enrage/phase, context
    and source (user or P2_live_report). Boundary uncertainty excludes a cue
    from phase assignment, but it remains counted under unassigned_sequences.
    Labels may come from user-labelled captures or guide leads; only the former
    can validate a name. This routine does not infer phase from packed varps.
    """
    groups, provenance, unassigned = {}, [], collections.Counter()
    seen_captures = set()
    for source in sources:
        digest = source["capture_sha256"]
        if digest in seen_captures:
            raise ValueError("A capture must not be counted twice")
        seen_captures.add(digest)
        profile = source.get("client_sha256")
        for value in (digest, profile):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError("Capture and executable SHA-256 digests are required")
        rows = [normalize_row(row) for row in source["rows"]]
        observed_hashes = {row["executable_sha256"] for row in rows if row.get("executable_sha256")}
        if observed_hashes and observed_hashes != {profile}:
            raise ValueError("Executable hash differs from the capture annotation")
        intervals = source.get("intervals", [])
        for interval in intervals:
            if interval.get("source") not in ("user", "P2_live_report"):
                raise ValueError("A phase interval requires observed/user evidence")
            if (
                type(interval.get("phase")) is not int
                or not 1 <= interval["phase"] <= 5
                or type(interval.get("enrage")) is not int
                or not 0 <= interval["enrage"] <= 4000
            ):
                raise ValueError("Invalid interval phase or enrage")
            if (
                not 0 <= interval["start_ns"] < interval["end_ns"]
                or interval.get("uncertainty_ns", 0) < 0
            ):
                raise ValueError("Invalid annotation time interval")
            if len(interval.get("session", [])) != 3 or any(
                type(value) is not int or value <= 0 for value in interval["session"]
            ):
                raise ValueError("A phase interval needs the original complete session")
        _, _, cues, _ = collect_observations(rows)
        seen = set()
        for cue in cues:
            identity = (*event_session(cue), cue["instance_id"], cue["sequence"])
            if identity in seen:
                continue
            seen.add(identity)
            if type(cue.get("action_sequence")) is not int or cue["action_sequence"] < 0:
                unassigned[str(cue.get("action_sequence"))] += 1
                continue
            matches = [
                interval
                for interval in intervals
                if tuple(interval["session"]) == event_session(cue)
                and interval["start_ns"] + interval.get("uncertainty_ns", 0)
                <= cue["observed_ns"]
                < interval["end_ns"] - interval.get("uncertainty_ns", 0)
            ]
            if len(matches) != 1:
                unassigned[str(cue["action_sequence"])] += 1
                continue
            interval = matches[0]
            context = interval.get("context")
            key = (profile, interval["phase"], interval["enrage"], cue["action_sequence"], context)
            group = groups.setdefault(key, {"counts": collections.Counter(), "labels": []})
            group["counts"][digest] += 1
            label = source.get("labels", {}).get(str(cue["action_sequence"]))
            if label:
                group["labels"].append(label)
        provenance.append(
            {
                "capture_sha256": digest,
                "client_sha256": source.get("client_sha256"),
                "annotation_reference": source.get("annotation_reference"),
                "phase_sources": sorted({entry["source"] for entry in intervals}),
                "unique_telos_cues": len(seen),
                "npc_action_events_lost": known_loss_total(rows, "npc_action_events_lost"),
                "player_hit_events_lost": known_loss_total(rows, "player_hit_events_lost"),
            }
        )
    attacks = []
    for (profile, phase, enrage, sequence, context), group in sorted(
        groups.items(), key=lambda item: tuple(str(v) for v in item[0])
    ):
        labels = group["labels"]
        label = labels[0] if labels and all(entry == labels[0] for entry in labels) else {}
        labelled = bool(label) and label.get("source") == "user_labelled_native_capture"
        attacks.append(
            {
                "phase": phase,
                "enrage_range": [enrage, enrage],
                "sequence": sequence,
                "client_sha256": profile,
                "context": context,
                "name": label.get("name"),
                "damage_style": label.get("damage_style"),
                "required_protection": label.get("required_protection"),
                "hit_components": None,
                "counts_as_auto": label.get("counts_as_auto"),
                "cue_to_prayer_check_ms": None,
                "timing_evidence_kind": None,
                "sample_count": sum(group["counts"].values()),
                "sources": [
                    {"capture_sha256": digest, "count": count}
                    for digest, count in sorted(group["counts"].items())
                ],
                "evidence_kind": (
                    "user_labelled_native_capture"
                    if labelled
                    else (
                        "guide_lead"
                        if label.get("source") == "guide"
                        else "unlabelled_native_capture"
                    )
                ),
                "classification_validated": labelled,
                "timing_validated": False,
                "validated": False,
                "reason": "protection_check_unmeasured" if labelled else "sequence_unlabelled",
            }
        )
    coverage = []
    for phase in range(1, 6):
        phase_rows = [
            row for row in attacks if row["phase"] == phase and row["enrage_range"] == [300, 300]
        ]
        coverage.append(
            {
                "phase": phase,
                "enrage_range": [300, 300],
                "sample_count": sum(row["sample_count"] for row in phase_rows),
                "observed_sequences": sorted({row["sequence"] for row in phase_rows}),
                "unlabelled_sequences": sorted(
                    {row["sequence"] for row in phase_rows if not row["classification_validated"]}
                ),
                "complete": False,
                "status": "observed_partial" if phase_rows else "not_captured",
            }
        )
    return {
        "schema_version": 1,
        "scope": "measured Telos cues; no automated input authorization",
        "automatic_flick_ready": False,
        "attacks": attacks,
        "coverage": coverage,
        "sources": provenance,
        "unassigned_sequences": dict(unassigned),
        "limitations": [
            "Hitmark observation times are not protection-check deadlines.",
            "Phase/enrage/context annotations apply only to their bounded original session.",
            "Names and styles do not transfer across phases, enrages, contexts or executable hashes.",
            "Guide leads never validate a row; missing hit components/protection windows remain null.",
            "Unlabelled sequences may be transitions or idle states, not attacks.",
            "Incoming golem/projectile coverage and safe Soul Split windows remain uncalibrated.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capture", type=Path, nargs="+", required=True)
    parser.add_argument("--enrage", type=int)
    parser.add_argument(
        "--capture-enrages",
        type=int,
        nargs="+",
        help="One user enrage label per capture, in source order",
    )
    parser.add_argument("--phase", type=int, choices=range(1, 6))
    parser.add_argument(
        "--annotations",
        type=Path,
        help="Local JSON with sources keyed by capture_sha256; each has client_sha256, bounded intervals and optional labels",
    )
    parser.add_argument("--session-date", required=True, help="Client-side date, YYYY-MM-DD")
    parser.add_argument(
        "--isolated-melee",
        action="store_true",
        help="Pair 28929 cues with player hitmarks only in an explicitly isolated phase-1 melee capture",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--confirmed-devotion-opening",
        action="store_true",
        help="User confirmed Devotion active, without other defensives or stuns",
    )
    parser.add_argument(
        "--catalogue-output",
        type=Path,
        help="Also write conservative P6 metadata for the explicitly labelled isolated phase",
    )
    arguments = parser.parse_args()
    if arguments.enrage is not None and not 0 <= arguments.enrage <= 4000:
        parser.error("Enrage must be between 0 and 4000")
    if arguments.annotations is None and (arguments.phase is None or arguments.enrage is None):
        parser.error("Supply --phase and --enrage for an isolated capture, or --annotations")
    if arguments.isolated_melee and (arguments.phase != 1 or arguments.annotations):
        parser.error("--isolated-melee requires a single labelled phase-1 context")
    enrages = arguments.capture_enrages or [arguments.enrage] * len(arguments.capture)
    if len(enrages) != len(arguments.capture) or any(
        value is not None and not 0 <= value <= 4000 for value in enrages
    ):
        parser.error("Provide one enrage in 0..4000 per capture")
    if arguments.annotations and arguments.capture_enrages:
        parser.error("Use annotation intervals for captures with annotated enrage")
    try:
        validate_output_paths(
            [*arguments.capture, *([arguments.annotations] if arguments.annotations else [])],
            [arguments.output, arguments.catalogue_output],
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    sources = [
        {
            "capture_sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
            "enrage_user_label": enrage,
            "summary": summarize(
                read_rows(capture), isolated_phase_1_melee=arguments.isolated_melee
            ),
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
        "enrage_user_labels": sorted({value for value in enrages if value is not None}),
        "phase_user_label": arguments.phase,
        "input_sent_by_calibration": input_history([source["summary"] for source in sources]),
        "sources": sources,
        "historical_isolated_phase_1_labels": USER_LABELLED_CUES,
        "observed_npc_forms": OBSERVED_NPC_FORMS,
        "hitmark_symbol_leads": HITMARK_SYMBOL_LEADS,
        "automatic_flick_ready": False,
        "prayer_check_deadline_validated": False,
        "limitations": LIMITATIONS,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if arguments.catalogue_output:
        annotations = (
            json.loads(arguments.annotations.read_text(encoding="utf-8"))
            if arguments.annotations
            else {}
        )
        catalogue_sources = []
        for capture, enrage in zip(arguments.capture, enrages):
            rows = [normalize_row(row) for row in read_rows(capture)]
            digest = hashlib.sha256(capture.read_bytes()).hexdigest()
            if arguments.annotations:
                matching = [
                    source
                    for source in annotations.get("sources", [])
                    if source.get("capture_sha256") == digest
                ]
                if len(matching) != 1:
                    parser.error("Each capture needs exactly one digest-matched annotation source")
                catalogue_sources.append(dict(matching[0], rows=rows))
                continue
            sessions = {row_session(row) for row in rows if row.get("observed_ns") is not None}
            intervals = []
            for session in sessions:
                times = [
                    row["observed_ns"]
                    for row in rows
                    if row_session(row) == session and type(row.get("observed_ns")) is int
                ]
                if not all(type(value) is int and value > 0 for value in session):
                    continue
                intervals.append(
                    {
                        "session": session,
                        "start_ns": min(times),
                        "end_ns": max(times) + 1,
                        "phase": arguments.phase,
                        "enrage": enrage,
                        "source": "user",
                    }
                )
            hashes = {row["executable_sha256"] for row in rows if row.get("executable_sha256")}
            if len(hashes) != 1:
                parser.error("Catalogue sources need one exact executable hash per capture")
            catalogue_sources.append(
                {
                    "capture_sha256": digest,
                    "client_sha256": hashes.pop(),
                    "rows": rows,
                    "intervals": intervals,
                }
            )
        arguments.catalogue_output.parent.mkdir(parents=True, exist_ok=True)
        arguments.catalogue_output.write_text(
            json.dumps(build_attack_catalogue(catalogue_sources), indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps({"captures": len(sources), "automatic_flick_ready": False}))


if __name__ == "__main__":
    main()
