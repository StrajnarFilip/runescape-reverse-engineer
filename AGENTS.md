# Project workflow

This workspace contains the user's RS3 native-memory reader and live tracker.

## GitHub synchronization requested by the user

- The project repository is https://github.com/StrajnarFilip/runescape-reverse-engineer.
- The user explicitly authorized publishing the existing project and future
  changes to this repository. Keep this preference for later tasks in this project.
- After completing project changes, run the relevant checks, inspect the staged
  diff for unrelated files or secrets, commit, and push the project changes to
  `origin` on the current branch. Report the resulting commit or link.
- Do not force-push, discard user changes, rewrite published history, or publish
  unrelated files. Resolve concurrent remote changes before a normal push.
- Do not publish credentials, process-memory dumps, raw gameplay captures,
  Jagex game binaries/cache files, installed third-party tools or Ghidra databases.
  Keep source scripts, version profiles, findings and compact validation reports.
- No scheduled/background uploader is required; synchronize as part of completing
  authorized project work.

## Project layout and checks

- Native library: `src/` and `include/rs3/`. Python tracker and bindings:
  the `rs3_reader` package in `python/rs3_reader/` (run with
  `python -m rs3_reader.<module>` and `PYTHONPATH=python`). Shared client
  profiles: `profiles/`. `docs/development.rst` maps every module.
- Our research scripts: `work/*.py`, `work/decompile.ps1`,
  `work/ghidra_scripts/ExportFunctions.java`. Generated research output is ignored.
- The sole runtime acquisition engine is a Windows x64 C11 DLL. The Python UI
  uses the standard library. The independent decoder under `work/reference/`
  supports research and parity benchmarks.
- Configure and build: `cmake -S . -B build -A x64` and
  `cmake --build build --config Release`.
- C and Python tests: `ctest --test-dir build -C Release --output-on-failure`.
- Optional live parity/performance report: `python work/benchmark_native.py`.
- Build products and downloaded compilers stay local. Preserve the public C ABI,
  caller ownership and explicit event overflow reporting when extending the DLL.
- Generated `rs3_catalog.bin` metadata stays local. Commit its independent decoder,
  compact ability bindings and validation reports, not extracted cache catalogs.
- Inventory/equipment/individual ability sampling runs in C. Use
  `docs/inventory-and-abilities.rst` for layouts and known limits.
- Reader access is read-only. Preserve executable-hash and signature checks.
- Never report unmapped NPC HP, hitsplats, Telos animations or phases as confirmed.
- Use English throughout source code, comments, UI labels, documentation and
  examples. The user explicitly requested this project-wide language change.
- Code style requested by the user: Allman braces and explicit variable names.
  Format C and Java with the repository `.clang-format` (`clang-format -i`);
  Python follows PEP 8 with 100 columns (`pyproject.toml`). Name offsets and
  limits instead of using magic numbers, and document non-static functions.
- Work autonomously; ask only when input is needed for controlled in-game validation.
- The requested Telos combat style is melee. Research notes and source references
  are in `docs/telos-melee-research.rst`. Keep guide-derived rules
  separate from live-validated fields. Some PVME pages retain pre-modernisation
  abilities or conflicting timings; do not import those as verified constants.
- EoF stored-special identity, Telos action/phase recognition and complete
  castability are not established merely by inventory IDs or published guides.
- Prayer timing code is in `src/prayer.c`, with additive APIs in
  `include/rs3/prayer.h` and `include/rs3/combat.h`; see `docs/prayer-flick.rst`.
  The intended account now has Soul Split; all four configured curses and six
  Ctrl+F3/Ctrl+F4 switches were observed on 2026-11-10. Preserve Ctrl+F1 Magic, Ctrl+F2 Ranged,
  Ctrl+F3 Soul Split, Ctrl+F4 Melee bindings in `profiles/prayer_flick.json`.
- Manual Devotion calibration uses the user's Alt+G binding. Capture it with
  `work/capture_prayer.py --record-devotion-alt-g`; native cooldown evidence is
  separate from the still-unmapped active buff. The 2026-11-11 clean opening
  produced three 1-damage autos, including Melee keys 228ms and 93ms after cues.
  See `docs/validation/telos-prayer-check-2026-11-11.json`. Successful cases alone
  do not establish the latest reliable switch time or validate the automatic trial.
- Raw NPC animation IDs, overhead flags and original-profile player hitmarks
  are available. Two 300% phase-1 captures associate 28929 with melee autos,
  28931 with tendrils, 28938 with uppercut and 28935 with Hold Still. See
  `docs/validation/telos-prayer-calibration-2026-11-10.json`. Other phases/styles,
  prayer-check deadlines and complete incoming-attack coverage remain
  uncalibrated. The observer must not present itself as a live flicker.
  Do not send production prayer toggles on unvalidated or stale timing evidence.
  On 2026-11-11 the user explicitly authorized bounded automatic calibration
  instead of manual subsecond switches. `work/calibrate_prayer_check.py` may
  perform one test return from Soul Split after a one-damage Devotion control,
  using fresh observations and the existing foreground C input checks. The
  player starts the encounter and casts Devotion. Keep the regular observer
  and trial validation flags unchanged; calibration is not a validated flicker.
  Completed clean experiments observed a protected return at 359ms after the
  auto cue (300%) and late returns at 408ms/452ms (2000%) and 516ms (300%).
  These are client-input observations, not a universal server deadline. Enrage
  labels are metadata and do not drive the isolated ordinary-auto delay.
- The user also authorized a bounded predictive opening test: `--anticipate`
  uses the C `rs3_prayer_predict_cycle` calculator (2400ms cadence, protection
  requested 100ms before the next estimated cue). An initial 900ms-lead test
  protected both autos but left only about one tick of Soul Split, so the user
  requested a shorter protection interval. `--cycles` is capped at two
  predicted autos after the Devotion control. The user stays in melee range
  without stuns. Stage signatures only invalidate the forecast; they are not
  a validated phase decoder. Magic styles are supported by the C calculator,
  but unmapped live styles/actions terminate this melee-only opening test.
  The retuned test observed about 1800/1810ms Soul Split and 610ms Melee, with
  both predicted autos dealing 1 under the Devotion control. Evidence is in
  `docs/validation/telos-prayer-anticipatory-2026-11-11.json`. The final handoff
  leaves Melee active; do not interpret it as continuous multi-phase coverage.
- Target Telos range: 300%-4000% enrage. The intended Soul Split account is ready;
  phase-1 opening autos have been captured at 300% and 2000%. Key-to-client-overhead timing and
  cue-to-hitmark timing do not establish server protection deadlines.
