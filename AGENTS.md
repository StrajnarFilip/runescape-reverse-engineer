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

- Runtime source and user-facing deliverables: `outputs/rs3_reader/`.
- Our research scripts: `work/*.py`, `work/decompile.ps1`,
  `work/ghidra_scripts/ExportFunctions.java`. Generated research output is ignored.
- Current runtime uses only 64-bit Windows Python's standard library.
- Tests: `C:\Python39\python.exe -m unittest discover -s outputs/rs3_reader -v`.
- Reader access is read-only. Preserve executable-hash and signature checks.
- Never report unmapped NPC HP, hitsplats, Telos animations or phases as confirmed.
- User prefers Slovenian and autonomous progress, with questions only when their
  input is needed for controlled in-game validation.
