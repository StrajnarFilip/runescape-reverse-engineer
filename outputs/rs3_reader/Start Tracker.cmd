@echo off
cd /d "%~dp0"
if exist "C:\Python39\pythonw.exe" (
    start "" "C:\Python39\pythonw.exe" "%~dp0tracker.py" %*
) else (
    start "" pythonw.exe "%~dp0tracker.py" %*
)
