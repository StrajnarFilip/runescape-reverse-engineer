@echo off
rem Start the RS3 event server: WebSocket at ws://127.0.0.1:7878/events and the
rem live viewer at http://127.0.0.1:7878/ (same port). Double-click or run from a terminal.
rem Extra arguments are passed to rs3-eventd, e.g.  run.bat --record session.jsonl
rem Set RS3_NO_OPEN=1 to skip opening the browser.
setlocal
cd /d "%~dp0"

tasklist /FI "IMAGENAME eq rs3-eventd.exe" 2>nul | find /I "rs3-eventd.exe" >nul
if %errorlevel%==0 (
  echo rs3-eventd is already running. Opening the viewer...
  if not "%RS3_NO_OPEN%"=="1" start "" "http://127.0.0.1:7878/"
  exit /b 0
)

where cargo >nul 2>nul
if %errorlevel%==0 (set "CARGO=cargo") else (set "CARGO=%USERPROFILE%\.cargo\bin\cargo.exe")
if not "%CARGO%"=="cargo" if not exist "%CARGO%" (
  echo Could not find cargo. Install Rust from https://rustup.rs and try again.
  pause
  exit /b 1
)

echo Building rs3-eventd (only slow the first time)...
"%CARGO%" build --release -q -p rs3-eventd
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)

set "OPEN=--open"
if "%RS3_NO_OPEN%"=="1" set "OPEN="
"target\release\rs3-eventd.exe" %OPEN% %*

echo.
echo Server stopped.
pause
