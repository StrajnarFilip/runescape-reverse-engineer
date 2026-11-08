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

- Runtime source and user-facing deliverables: `outputs/rs3_reader/` and
  `outputs/rs3_native/`.
- Our research scripts: `work/*.py`, `work/decompile.ps1`,
  `work/ghidra_scripts/ExportFunctions.java`. Generated research output is ignored.
- The default acquisition engine is a Windows x64 C11 DLL. The Python UI uses
  only the standard library; the original Python decoder is an explicit backend.
- Build and C tests: `C:\Python39\python.exe outputs/rs3_native/build.py --cc
  work/tools/zig-x86_64-windows-0.15.2/zig.exe --test` (or an installed C compiler).
- Tests: `C:\Python39\python.exe -m unittest discover -s outputs/rs3_reader -v`.
- Optional live parity/performance report: `C:\Python39\python.exe work/benchmark_native.py`.
- Build products and downloaded compilers stay local. Preserve the public C ABI,
  caller ownership and explicit event overflow reporting when extending the DLL.
- Generated `rs3_catalog.bin` metadata stays local. Commit its independent decoder,
  compact ability bindings and validation reports, not extracted cache catalogs.
- Inventory/equipment/individual ability sampling runs in C. Use
  `outputs/rs3_reader/inventory-and-abilities.rst` for layouts and known limits.
- Reader access is read-only. Preserve executable-hash and signature checks.
- Never report unmapped NPC HP, hitsplats, Telos animations or phases as confirmed.
- Use English throughout source code, comments, UI labels, documentation and
  examples. The user explicitly requested this project-wide language change.
- Work autonomously; ask only when input is needed for controlled in-game validation.
