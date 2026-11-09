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
  `python/rs3_reader/`. Shared client profiles: `profiles/`.
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
- Work autonomously; ask only when input is needed for controlled in-game validation.
- The requested Telos combat style is melee. Research notes and source references
  are in `docs/telos-melee-research.rst`. Keep guide-derived rules
  separate from live-validated fields. Some PVME pages retain pre-modernisation
  abilities or conflicting timings; do not import those as verified constants.
- EoF stored-special identity, Telos action/phase recognition and complete
  castability are not established merely by inventory IDs or published guides.
