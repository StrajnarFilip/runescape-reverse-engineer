"""Capture the foreground RuneScape client and send explicit actions through Leonardo."""

from __future__ import annotations

import argparse
import ctypes
import json
import time
from ctypes import wintypes

from mss import MSS
from PIL import Image, PngImagePlugin

from rs3_reader.rotation_keys import ArduinoBackend, bind, parse_chord, windows_library

WINDOW_TITLES = ("RuneScape (Compatibility Mode)", "RuneScape")
FRAME_TAG = "rs3_game_frame"
PER_MONITOR_AWARE_V2 = -4
ROOT_ANCESTOR = 2
MAX_MOVE = 127
MOVE_TOLERANCE = 2
MAX_MOVE_REPORTS = 64
POINTER_SETTLE_SECONDS = 0.015
KEY_HOLD_SECONDS = 0.05


class GameWindow:
    """Read the visible client rectangle and verify where USB HID input will land."""

    def __init__(self):
        user = windows_library("user32")
        set_dpi = bind(user, "SetThreadDpiAwarenessContext", wintypes.HANDLE, [wintypes.HANDLE])
        if not set_dpi(PER_MONITOR_AWARE_V2):
            raise ctypes.WinError(ctypes.get_last_error())
        self.foreground = bind(user, "GetForegroundWindow", wintypes.HWND, [])
        self.get_rect = bind(
            user, "GetClientRect", wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        )
        point_ptr = ctypes.POINTER(wintypes.POINT)
        self.client_to_screen = bind(
            user, "ClientToScreen", wintypes.BOOL, [wintypes.HWND, point_ptr]
        )
        self.get_cursor = bind(user, "GetCursorPos", wintypes.BOOL, [point_ptr])
        self.window_at = bind(user, "WindowFromPoint", wintypes.HWND, [wintypes.POINT])
        self.ancestor = bind(user, "GetAncestor", wintypes.HWND, [wintypes.HWND, wintypes.UINT])
        get_title = bind(
            user, "GetWindowTextW", ctypes.c_int, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        )
        self.handle = self.foreground()
        title = ctypes.create_unicode_buffer(128)
        get_title(self.handle, title, len(title))
        if title.value not in WINDOW_TITLES:
            raise RuntimeError("Bring RuneScape to the foreground for capture and input")

    def check_focus(self):
        """Stop if another window owns the foreground."""
        if self.foreground() != self.handle:
            raise RuntimeError("RuneScape lost foreground focus")

    def bounds(self):
        """Return the client rectangle in physical screen pixels."""
        self.check_focus()
        rect = wintypes.RECT()
        origin = wintypes.POINT()
        if not self.get_rect(self.handle, ctypes.byref(rect)) or not self.client_to_screen(
            self.handle, ctypes.byref(origin)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom

    def cursor(self):
        """Read the current pointer position in physical screen pixels."""
        point = wintypes.POINT()
        if not self.get_cursor(ctypes.byref(point)):
            raise ctypes.WinError(ctypes.get_last_error())
        return point.x, point.y

    def check_point(self, x, y):
        """Ensure a mouse action will land on the game or one of its child windows."""
        self.check_focus()
        window = self.window_at(wintypes.POINT(x, y))
        if not window or self.ancestor(window, ROOT_ANCESTOR) != self.handle:
            raise RuntimeError("The pointer target is covered or outside the game window")


def capture(window, path, height):
    """Capture a desktop crop and embed the geometry used to map image pixels back to it."""
    bounds = window.bounds()
    left, top, right, bottom = bounds
    region = {"left": left, "top": top, "width": right - left, "height": bottom - top}
    started = time.perf_counter()
    with MSS() as backend:
        pixels = backend.grab(region)
        frame = Image.frombytes("RGB", pixels.size, pixels.bgra, "raw", "BGRX")
    capture_ms = (time.perf_counter() - started) * 1000
    if window.bounds() != bounds:
        raise RuntimeError("RuneScape moved during capture. Take a new screenshot.")
    if height < frame.height:
        frame.thumbnail((frame.width, height))
    tags = PngImagePlugin.PngInfo()
    tags.add_text(FRAME_TAG, json.dumps([window.handle, *bounds]))
    frame.save(path, format="PNG", pnginfo=tags, compress_level=1)
    print(f"{frame.width}x{frame.height}, MSS capture {capture_ms:.0f} ms, saved to {path}")


def frame_target(window, path, x, y):
    """Translate a screenshot pixel and reject moved or replaced client windows."""
    bounds = window.bounds()
    with Image.open(path) as frame:
        if frame.info.get(FRAME_TAG) != json.dumps([window.handle, *bounds]):
            raise RuntimeError("Screenshot geometry no longer matches RuneScape. Capture again.")
        if not (0 <= x < frame.width and 0 <= y < frame.height):
            raise ValueError("Point is outside the screenshot")
        left, top, right, bottom = bounds
        target = (
            left + int((x + 0.5) * (right - left) / frame.width),
            top + int((y + 0.5) * (bottom - top) / frame.height),
        )
    window.check_point(*target)
    return bounds, target


def move_pointer(board, window, bounds, target):
    """Use cursor feedback to compensate for Windows mouse scaling and acceleration."""

    def check_geometry():
        if window.bounds() != bounds:
            raise RuntimeError("RuneScape moved during pointer positioning. Capture again.")

    for _ in range(MAX_MOVE_REPORTS):
        check_geometry()
        cursor_x, cursor_y = window.cursor()
        dx, dy = target[0] - cursor_x, target[1] - cursor_y
        if max(abs(dx), abs(dy)) <= MOVE_TOLERANCE:
            window.check_point(cursor_x, cursor_y)
            return
        horizontal = max(-MAX_MOVE, min(MAX_MOVE, dx))
        vertical = max(-MAX_MOVE, min(MAX_MOVE, dy))
        board.command(f"MOVE {horizontal} {vertical}", window.check_focus)
        time.sleep(POINTER_SETTLE_SECONDS)
    raise RuntimeError("Leonardo could not position the pointer at the requested pixel")


def perform_action(window, args):
    """Open a serial session for one requested mouse or keyboard action."""
    if args.action == "key":
        chord = parse_chord(args.chord)
    else:
        bounds, target = frame_target(window, args.frame, args.x, args.y)
        if args.action == "drag":
            end_bounds, end = frame_target(window, args.frame, args.end_x, args.end_y)
            if end_bounds != bounds:
                raise RuntimeError("RuneScape moved while mapping the drag endpoints")
    board = ArduinoBackend(args.port)
    try:
        if args.action == "key":
            board.tap(chord, KEY_HOLD_SECONDS, window.check_focus, time.sleep)
        else:
            move_pointer(board, window, bounds, target)
            if args.action == "click":
                board.command(f"CLICK {args.button}", window.check_focus)
            elif args.action == "wheel":
                board.command(f"WHEEL {args.steps}", window.check_focus)
            elif args.action == "drag":
                board.command("DOWN L", window.check_focus)
                move_pointer(board, window, bounds, end)
                board.command("UP L", window.check_focus)
    finally:
        board.close()


def main():
    """Parse explicit capture and HID commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Leonardo serial port (e.g. COM7) for HID input")
    actions = parser.add_subparsers(dest="action", required=True)
    shot = actions.add_parser("shot", help="save a PNG of the foreground game client")
    shot.add_argument("frame")
    shot.add_argument("--height", type=int, default=1000)
    for action in ("move", "click", "wheel", "drag"):
        pointer = actions.add_parser(action)
        pointer.add_argument("frame")
        pointer.add_argument("x", type=int)
        pointer.add_argument("y", type=int)
        if action == "click":
            pointer.add_argument("--button", choices=("L", "R", "M"), default="L")
        elif action == "wheel":
            pointer.add_argument("steps", type=int)
        elif action == "drag":
            pointer.add_argument("end_x", type=int)
            pointer.add_argument("end_y", type=int)
    actions.add_parser("key").add_argument("chord")
    args = parser.parse_args()
    try:
        if args.action == "shot":
            if args.height <= 0:
                raise ValueError("Capture height must be positive")
            capture(GameWindow(), args.frame, args.height)
        else:
            if not args.port:
                raise ValueError("Specify the Leonardo port before the action, e.g. --port COM7")
            if args.action == "wheel" and not -MAX_MOVE <= args.steps <= MAX_MOVE:
                raise ValueError(f"Wheel steps must be between {-MAX_MOVE} and {MAX_MOVE}")
            perform_action(GameWindow(), args)
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
