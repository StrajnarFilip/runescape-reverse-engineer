Telos automation roadmap
========================

Target: unattended melee Telos kills, 300%-4000% enrage, on the intended Soul
Split account. Written 2026-11-11.

The repository observes player statistics, GCD, NPC life points and hitmarks,
NPC animation events, overhead prayers, inventory, ability cooldowns and
local-player hitmarks, and has a small opt-in input layer. Nothing decides or
plays yet. This roadmap splits the remaining work into **19 tracks, one agent
each**, with fixed contracts between them.

Scope: the work makes the bot function. It contains no work aimed at hiding
automation from Jagex. Memory access stays read-only. The account risk is the
user's.

How an agent knows its part
---------------------------

One track = one agent = one branch = one brief file under ``tracks/``.

Start an agent with exactly this prompt, changing only the track ID::

    Do track P1 of the Telos roadmap. Read AGENTS.md,
    docs/telos-automation/README.rst and docs/telos-automation/tracks/P1.rst.
    Work on branch telos/P1 cut from telos/integration. Touch only the files
    your brief says you own.

Every brief has the same fields:

* **Status**: ``open``, ``in progress``, ``waiting for live session`` or
  ``done``. Only the track's own agent edits it. Reading the headers of
  ``tracks/*.rst`` shows overall progress.
* **Goal**: the one outcome the track exists for.
* **Read first**: the files that hold the context.
* **Owns**: the only files this agent may create or edit. Everything else is
  read-only for it, apart from the shared wiring exception below.
* **Depends on**: tracks that must be ``done`` (or the fallback to use until
  they are).
* **Delivers**: the contract fields, files or behaviour other tracks consume.
* **Done when**: the acceptance test.
* **Needs the user**: the exact in-game session required, if any.
* **Requests**: where the agent writes down anything it needs outside its Owns
  list. It does not edit other tracks' files; it records the request and
  continues with what it can do.

Working procedure for every agent:

1. Set ``Status: in progress`` in your brief and commit that first.
2. Do the work on ``telos/<ID>``, cut from ``telos/integration``.
3. When only live validation is left, set ``Status: waiting for live session``
   and list the exact steps the user must perform under **Needs the user**.
4. To finish: rebase on ``telos/integration``, run the checks below, set
   ``Status: done``, commit and push your branch. Track L merges branches into
   ``telos/integration`` one at a time.

Track index
-----------

==== =============================== ==== ====== ============== ==============
ID   Track                           Wave Kind   Starts after   Live session
==== =============================== ==== ====== ============== ==============
S0a  Contracts and replay            0    Python --             no
S0b  Single runtime profile          0    C      --             yes
P1   Spatial                         1    C      S0a            yes
P2   Encounter variables             1    C      S0a            yes
P3   Player effects                  1    C      S0a            yes
P5   Interface state                 1    C      S0a            yes
P6   Attack catalogue                1    Data   S0a            yes
A1   Actuator backends               1    Python S0a            no
D1   Encounter tracker               1    Python S0a            no
D3   Survival manager                1    Python S0a            no
D4   Melee rotation engine           1    Python S0a            no
D6   Arbiter                         1    Python S0a            no
D8   Supervisor                      1    Python S0a            no
P4   Scene hazards                   2    C      P1             yes
A2   Movement primitives             2    Mixed  P1, A1         yes
D2   Prayer executor                 2    Python D1, P6, A1     no
D5   Mechanics and movement          2    Python P4, A2         no
D7   Out-of-fight loop               2    Python P5, A2         no
L    Integration ladder              3    Mixed  all            yes
==== =============================== ==== ====== ============== ==============

Wave 0 runs first, both tracks at once. All of wave 1 can run at the same time
as soon as S0a is done. Each wave 2 track starts when its own dependencies are
done. Track L is serial.

Architecture
------------

::

    C DLL (perception) -> WorldState -> trackers -> intent producers -> arbiter -> actuator
                              ^                                                      |
                              +---------------- ack by observation -------------------+

* Perception stays in the C DLL (``src/``, ``include/rs3/``), read-only.
* Everything from ``WorldState`` onwards is Python in
  ``python/rs3_reader/bot/``, standard library only, Python 3.9 compatible.
* The encounter decision stack uses Python prayer timing rules. The native
  planner remains available through its public C ABI.

The three contracts
-------------------

Track S0a defines these; every other track codes against them.

``WorldState``
    One immutable view per sample. Every field is optional and carries its age
    and source. Unknown stays unknown: a missing field never defaults to a safe
    or ready value.

``Intent``
    One proposed action: set prayer, activate ability, eat or drink a slot,
    move to a tile, or interact with an entity or object. It carries a
    priority, a deadline and the evidence that justified it.

``Actuator``
    ``send(intent)`` returns accepted or rejected. Calls are serialized and
    limited by a per-tick budget. Accepted input is not success: only a later
    observation confirms it, as ``rs3_prayer_note_sent`` already requires.

Intent producers (D2, D3, D4, D5, D7) never call an actuator. Only the arbiter
(D6) does. The supervisor (D8) can stop everything.

Shared limits
-------------

**Live game time.** Only one agent works with the user in game at a time.
Agents do static work in parallel, then queue with
``Status: waiting for live session``.

**Shared DLL wiring.** ``src/context.c``, ``src/frame.c``, ``src/snapshot.c``,
``CMakeLists.txt``, ``python/rs3_reader/native.py`` and
``python/rs3_reader/telemetry.py`` are touched by several C tracks. A C track
may add only its own registration lines there (module call, snapshot accessor,
test target, binding, telemetry field) and must not restructure them. These
branches merge serially.

Rules for every track
---------------------

These restate ``AGENTS.md``; that file wins if they ever differ.

* Memory access is read-only. Preserve executable-hash and signature checks.
  Call no game function and write nothing to the game.
* Unknown, stale or unmapped data is never reported as confirmed. Live
  decisions check PID, process creation time, scene epoch and observation age.
* The public C ABI is additive. Assert new layouts and expose their sizes.
* Guide-derived rules stay separate from live-validated ones. PVME and Wiki
  values are leads, not constants.
* Do not enable automatic input on unvalidated or stale timing evidence.
* C: Allman braces, explicit names, named offsets, ``clang-format -i``.
  Python: PEP 8, 100 columns. English everywhere.
* Raw captures, memory dumps, cache extracts and ``rs3_catalog.bin`` stay
  local. Commit compact validation reports only.
* Run the checks, inspect the staged diff, commit, and push your branch.

Checks::

    cmake -S . -B build -A x64
    cmake --build build --config Release
    ctest --test-dir build -C Release --output-on-failure

Verification of the whole
-------------------------

* Every branch keeps the build and tests green.
* Every decision track has golden intent logs from real captures in the S0a
  replay harness.
* Shadow mode (track L, rung 1) reports per kill: threats predicted against
  hits taken, intents against the user's own actions, and watchdog trips.
* A ladder rung passes only after repeated clean runs with zero watchdog trips
  and zero event-queue losses.
