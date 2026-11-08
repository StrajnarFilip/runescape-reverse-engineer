RS3 LIVE TRACKER - direct memory telemetry

START
  Build the C library first; see ../rs3_native/README.rst.
  Double-click Start Tracker.cmd, or run python tracker.py. The launcher
  uses pythonw.exe from C:\Python39 or PATH and forwards command-line options.
  Close the window to stop its Python consumer and native worker.
  No pip packages, injected game agent, or network server are required.

COLLECTION
  Default: C worker at 100 Hz; Python UI/log output at 20 Hz.
  python tracker.py --native-hz 500
  Requested rates depend on system load. A faster reader does not increase the
  game's server update rate. Raw/source tab shows engine timing and event gaps.
  Use --backend python to select the original Python decoder explicitly.
  RS3_BACKEND=python selects it through the environment. RS3_NATIVE_LIBRARY can
  select an alternative DLL. Missing/mismatched DLLs fail explicitly.

LIVE PLAYER TILE IN CLIENT 6ada3f59-1a86000
  Run python tracker.py --backend python to see Player Tile at 20 Hz.
  This profile covers position. Combat telemetry uses the 951-3 profile.
  The JSONL position field gives x (east), y (north) and plane (0–3).
  A null position indicates the tile is unavailable.

VERIFIED FIELDS IN CLIENT 951-3
  HP/max HP: direct VARP 13537/13538.
  Prayer: VARP 3274 bits 0..14 / 10; UI displays the integer part.
  Max prayer: base prayer skill level * 10 (derived, not a separate max variable).
  Adrenaline: VARP 679 / 10; previously observed changing from 0 to 100% in combat.
  GCD: VARC 2091/2092 against the client logic clock; five live starts previously
    confirmed 90 ticks * 20 ms = 1.8 s.
  NPCs: name, config ID/type, runtime ID/instance, current and maximum HP from
    numeric statistics. Goblin 550/550, Giant spider 1000/1000, Goblin 550 -> 326
    alongside the user's independently reported 224 hit. Also captured 550/1000.
  Hit splats: raw type ID, amount, recipient, start/end client ticks, duration.
  Filters and history are available. Wounded NPCs appear before healthy ones.
  Telos phases and animations remain unconfirmed.

AUDITABILITY
  Samples go to logs/capture-DATE-TIME-PID.jsonl at 20 Hz. Logs rotate at about
  25 MB with three backup parts. Each record contains acquisition time, sequence,
  PID, executable hash, raw/decoded values, read duration and native diagnostics.
  Buttons open the log folder and copy current JSON. Data is read directly from
  memory. Only QUERY_INFORMATION | VM_READ access is requested. No game functions
  are called, no game memory is written, and no keys/clicks are sent.
  All source, comments, UI and documentation use English.

LIMITATIONS
  Supported hashes and loaded-code signatures are listed in profile_*.json.
  Both are checked before offsets are used.
  Failed reads/process loss produce unavailable fields. Stale samples are hidden
  in the UI. Logout and world changes still need controlled live validation;
  process existence and an advancing client clock do not prove logged-in state.
  last_change_age_ms is time since the last observed value change, not the age
  of server data. The latest server update time is unknown.
  A snapshot is not atomic. NPCs cover the loaded scene, without a distance check.
  Untransmitted NPC HP is null/not_transmitted, not zero HP.
  Hitmark amounts may be healing, zero or text marks; the attacker is unknown.
  newly_observed means first observed (including active marks at attachment).
  The native queue holds 4096 observations and reports events_lost on overwrite.
  Polling can miss records overwritten/removed between native samples.

CLI AND TESTS
  python reader.py --once --raw
  python reader.py --backend python --once
  python reader.py --native-hz 500 --hz 20 --duration 60 --raw --output capture.jsonl
  python tracker.py --history PATH_TO_EARLIER_CAPTURE.jsonl
  python -m unittest discover -s . -v

  Deterministic C tests: run ../rs3_native/build.py --test from a compiler-enabled
  shell. Python tests include the original decoder and native ABI/binding checks.
  See validation-summary.json, npc-validation-summary.json and the native
  validation report for actual live results and their limits. The C port was
  checked against the Python reader; full Telos combat remains untested.
  Visual inspection through Computer Use was previously denied. Window creation
  and real capture logs were verified; no visual inspection is claimed.
