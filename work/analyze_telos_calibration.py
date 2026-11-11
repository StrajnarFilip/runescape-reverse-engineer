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
    "Only 300% phase 1 has been captured; range, other phases and higher enrages "
    "remain unvalidated.",
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
                    "previous_observation_ns": previous_observed_ns.get(
                        session, row["observed_ns"]
                    ),
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
            sum(row.get("player_hit_events_lost", 0) for row in rows)
            if hit_data_present
            else None
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capture", type=Path, nargs="+", required=True)
    parser.add_argument("--enrage", type=int, required=True)
    parser.add_argument("--phase", type=int, choices=[1], required=True)
    parser.add_argument("--session-date", required=True, help="Client-side date, YYYY-MM-DD")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    sources = [
        {
            "capture_sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
            "summary": summarize(read_rows(capture)),
        }
        for capture in arguments.capture
    ]
    report = {
        "client_session_date": arguments.session_date,
        "enrage_user_label": arguments.enrage,
        "phase_user_label": arguments.phase,
        "input_sent_by_calibration": False,
        "sources": sources,
        "user_labelled_cues": USER_LABELLED_CUES,
        "observed_npc_forms": OBSERVED_NPC_FORMS,
        "hitmark_symbol_leads": HITMARK_SYMBOL_LEADS,
        "automatic_flick_ready": False,
        "prayer_check_deadline_validated": False,
        "limitations": LIMITATIONS,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"captures": len(sources), "automatic_flick_ready": False}))


if __name__ == "__main__":
    main()
