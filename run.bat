@echo off
rem Start the RS3 event server: WebSocket at ws://127.0.0.1:7878/events and the
rem live viewer at http://127.0.0.1:7878/ (same port). Double-click or run from a terminal.
rem Extra arguments are passed to rs3-eventd, e.g.  run.bat --record session.jsonl
rem Set RS3_NO_OPEN=1 to skip opening the browser.
setlocal
cd /d "%~dp0"

if "%~1"=="" (
  powershell.exe -NoProfile -NonInteractive -Command "$listener = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 7878 -State Listen -ErrorAction SilentlyContinue; if ($listener -and (Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue).ProcessName -eq 'rs3-eventd') { exit 0 }; exit 1"
  if not errorlevel 1 (
    echo rs3-eventd is already serving port 7878. Stop it before rerunning to load updates.
    if not "%RS3_NO_OPEN%"=="1" start "" "http://127.0.0.1:7878/"
    exit /b 0
  )
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
