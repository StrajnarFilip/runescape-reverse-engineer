"""Record a pinned foreground game window locally, without sending any input.

JPEG frames and their C monotonic timestamps remain private under work/. The
native/input recorder owns game-state acquisition and low-level input hooks.
This companion captures its desktop client-area crop while it is foregrounded.
App-switch transition images can still show stale desktop composition; they
are not game-state evidence and all images remain local.
"""

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor" / "recording"))

from mss import MSS  # noqa: E402
from PIL import Image  # noqa: E402


def record(window, pid, creation_time, directory, stop_file, duration, hz):
    """Capture client pixels with exact geometry and a shared native clock."""
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    library = ctypes.WinDLL(str(Path(__file__).resolve().parents[1] / "build/bin/rs3_native.dll"))
    library.rs3_monotonic_ns.restype = ctypes.c_uint64
    library.rs3_monotonic_ns.argtypes = []
    process = kernel.OpenProcess(0x1000, False, pid)  # Query limited information only.
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    directory.mkdir(parents=True, exist_ok=False)
    frames, errors = 0, 0
    started = time.monotonic()
    last_status = 0
    try:
        stamps = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(process, *(ctypes.byref(stamp) for stamp in stamps)):
            raise ctypes.WinError(ctypes.get_last_error())
        observed_creation = stamps[0].dwLowDateTime | stamps[0].dwHighDateTime << 32
        if observed_creation != creation_time:
            raise RuntimeError("Pinned process lifetime changed")
        with MSS() as capture, (directory / "frames.jsonl").open("x", encoding="utf-8") as index:
            while time.monotonic() - started < duration and not stop_file.exists():
                frame_started = time.monotonic()
                owner = wintypes.DWORD()
                user.GetWindowThreadProcessId(window, ctypes.byref(owner))
                if owner.value != pid:
                    raise RuntimeError("Pinned window changed process")
                if user.GetForegroundWindow() == window:
                    bounds, origin = wintypes.RECT(), wintypes.POINT()
                    if not user.GetClientRect(
                        window, ctypes.byref(bounds)
                    ) or not user.ClientToScreen(window, ctypes.byref(origin)):
                        raise ctypes.WinError(ctypes.get_last_error())
                    region = dict(
                        left=origin.x, top=origin.y, width=bounds.right, height=bounds.bottom
                    )
                    native_ns = library.rs3_monotonic_ns()
                    pixels = capture.grab(region)
                    if user.GetForegroundWindow() != window:
                        continue  # Never retain an image after focus changed during capture.
                    frame = Image.frombytes("RGB", pixels.size, pixels.bgra, "raw", "BGRX")
                    filename = f"frame-{frames:07d}.jpg"
                    frame.save(directory / filename, quality=75)
                    index.write(
                        json.dumps(
                            dict(
                                sequence=frames + 1,
                                native_ns=native_ns,
                                captured_native_ns=library.rs3_monotonic_ns(),
                                filename=filename,
                                region=region,
                                pid=pid,
                                process_creation_time=creation_time,
                            )
                        )
                        + "\n"
                    )
                    index.flush()
                    frames += 1
                if time.monotonic() - last_status >= 1:
                    status = dict(
                        running=True,
                        frames=frames,
                        errors=errors,
                        pid=pid,
                        window=window,
                        sends_input=False,
                        target_hz=hz,
                    )
                    (directory / "status.json").write_text(json.dumps(status), encoding="utf-8")
                    last_status = time.monotonic()
                    if frames == 1:
                        print("SCREEN RECORDING ACTIVE; no automatic input", flush=True)
                time.sleep(max(0, 1 / hz - (time.monotonic() - frame_started)))
    except BaseException:
        errors += 1
        raise
    finally:
        kernel.CloseHandle(process)
        (directory / "status.json").write_text(
            json.dumps(
                dict(
                    running=False,
                    frames=frames,
                    errors=errors,
                    sends_input=False,
                )
            ),
            encoding="utf-8",
        )


def main():
    """Require an explicit pinned window/process lifetime and private output path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", type=int, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--creation", type=int, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--stop-file", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=1800)
    parser.add_argument("--hz", type=int, default=10)
    args = parser.parse_args()
    if not 0 < args.duration <= 1800 or not 1 <= args.hz <= 20:
        parser.error("Duration must be 1..1800 seconds and frequency 1..20 Hz")
    record(
        args.window, args.pid, args.creation, args.directory, args.stop_file, args.duration, args.hz
    )


if __name__ == "__main__":
    main()
