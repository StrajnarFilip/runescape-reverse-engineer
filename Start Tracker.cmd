@echo off
rem Starts the desktop tracker without a console window. Arguments are forwarded,
rem for example: "Start Tracker.cmd" --pid 12345 --native-hz 500
cd /d "%~dp0"
set "PYTHONPATH=%~dp0python;%PYTHONPATH%"
if exist "C:\Python39\pythonw.exe" (
    start "" "C:\Python39\pythonw.exe" -m rs3_reader.tracker %*
) else (
    start "" pythonw.exe -m rs3_reader.tracker %*
)
