RS3 LIVE TRACKER - direct memory telemetry

START
  Build the C library first; see ../rs3_native/README.rst.
  Double-click Start Tracker.cmd on this machine (C:\Python39\pythonw.exe),
  or run: python tracker.py
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

INVENTORY, EQUIPMENT AND ABILITIES
  New tabs show all 28 backpack slots, equipped gear and individual cooldowns.
  Slots are 1..28 in the UI and zero-based in JSON/C; the four-column tracker
  grid is a logical view and does not measure screen coordinates.
  Item names/IDs/quantities, explicit potion families/doses, moves, replacements,
  equipment transfers and dose changes come from the C collector. A token follows
  unambiguous changes; identical duplicates can be ambiguous.
  Saradomin brew flask (6) ID 23351 -> (5) ID 23353 retains its healing family.
  Super restore ID 3028 -> 3030 was verified live with the same tracking token.
  The 169 ability definitions include 140 client timer mappings. Hurricane,
  Flurry and Resonance were checked live with two-handed, dual-wield and shield
  setups. Unknown cooldowns remain unknown; gear match is not full castability.
  Bone Shield substitution, runes, unlocks and other cast conditions are separate
  checks. See inventory-and-abilities.rst and inventory-validation-summary.json.
  The build generates rs3_catalog.bin beside the DLL from the installed cache.
  Missing metadata leaves raw IDs/positions available, with explicit unknowns.

AUDITABILITY
  Samples go to logs/capture-DATE-TIME-PID.jsonl at 20 Hz. Logs rotate at about
  25 MB with three backup parts. Each record contains acquisition time, sequence,
  PID, executable hash, raw/decoded values, read duration and native diagnostics.
  Buttons open the log folder and copy current JSON. Data is read directly from
  memory. Only QUERY_INFORMATION | VM_READ access is requested. No game functions
  are called, no game memory is written, and no keys/clicks are sent.
  All source, comments, UI and documentation use English.

LIMITATIONS
  Supported executable SHA-256:
    fd8afd32f9829b1fb0a953b238c504e75526b7af5e2920120aa027777a358d45
  Different hashes or loaded signatures are refused before offsets are used.
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
