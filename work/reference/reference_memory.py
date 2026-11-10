"""Small read-only Windows x64 process-memory reader for research and parity checks.

The process is opened with PROCESS_QUERY_INFORMATION | PROCESS_VM_READ only;
this module never writes to another process.
"""

import ctypes
import hashlib
import struct
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

if ctypes.sizeof(ctypes.c_void_p) != 8:
    raise RuntimeError("Use 64-bit Python")

CLIENT_EXECUTABLE_NAME = "rs2client.exe"
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE_ALL = 0x00000018  # TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
STILL_ACTIVE = 259
GW_OWNER = 4
MEM_COMMIT = 0x1000
PAGE_NOACCESS_OR_GUARD = 0x101
PAGE_READABLE = 0xEE
USER_ADDRESS_MINIMUM = 0x10000
USER_ADDRESS_LIMIT = 0x7FFFFFFFFFFF
MAX_READ_BYTES = 16 * 1024 * 1024
HASH_CHUNK_BYTES = 1024 * 1024
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32")
WindowEnumerationCallback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * 260),
    ]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


def _bind(library, name, argument_types, result_type):
    function = getattr(library, name)
    function.argtypes = argument_types
    function.restype = result_type
    return function


_create_toolhelp_snapshot = _bind(
    kernel32, "CreateToolhelp32Snapshot", [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE
)
_process_first = _bind(
    kernel32, "Process32FirstW", [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)], wintypes.BOOL
)
_process_next = _bind(
    kernel32, "Process32NextW", [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)], wintypes.BOOL
)
_module_first = _bind(
    kernel32, "Module32FirstW", [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)], wintypes.BOOL
)
_open_process = _bind(
    kernel32, "OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE
)
_close_handle = _bind(kernel32, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL)
_read_process_memory = _bind(
    kernel32,
    "ReadProcessMemory",
    [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ],
    wintypes.BOOL,
)
_virtual_query = _bind(
    kernel32,
    "VirtualQueryEx",
    [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t],
    ctypes.c_size_t,
)
_get_exit_code = _bind(
    kernel32, "GetExitCodeProcess", [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL
)
_get_process_times = _bind(
    kernel32,
    "GetProcessTimes",
    [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4,
    wintypes.BOOL,
)
_enumerate_windows = _bind(
    user32, "EnumWindows", [WindowEnumerationCallback, wintypes.LPARAM], wintypes.BOOL
)
_get_foreground_window = _bind(user32, "GetForegroundWindow", [], wintypes.HWND)
_get_window_thread_process_id = _bind(
    user32,
    "GetWindowThreadProcessId",
    [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)],
    wintypes.DWORD,
)
_is_window_visible = _bind(user32, "IsWindowVisible", [wintypes.HWND], wintypes.BOOL)
_get_window = _bind(user32, "GetWindow", [wintypes.HWND, wintypes.UINT], wintypes.HWND)
_get_window_text_length = _bind(user32, "GetWindowTextLengthW", [wintypes.HWND], ctypes.c_int)


class MemoryReadError(OSError):
    """A remote read failed or returned data that cannot be trusted."""


def processes(name=CLIENT_EXECUTABLE_NAME, include_children=True):
    """Yield the PIDs of processes with this executable name.

    Compatibility Mode can launch several helper children with the same
    executable; include_children=False yields only the root processes.
    """
    snapshot = _create_toolhelp_snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    parent_by_pid = {}
    try:
        found = _process_first(snapshot, ctypes.byref(entry))
        while found:
            if entry.szExeFile.lower() == name.lower():
                parent_by_pid[entry.th32ProcessID] = entry.th32ParentProcessID
            found = _process_next(snapshot, ctypes.byref(entry))
    finally:
        _close_handle(snapshot)
    for pid, parent_pid in parent_by_pid.items():
        if include_children or parent_pid not in parent_by_pid:
            yield pid


def _window_process_id(window):
    pid = wintypes.DWORD()
    if window:
        _get_window_thread_process_id(window, ctypes.byref(pid))
    return pid.value


def visible_client_pid(pids):
    """Prefer the foreground game window, then the lowest PID with a game window."""
    foreground_pid = _window_process_id(_get_foreground_window())
    visible_pids = set()

    @WindowEnumerationCallback
    def consider_window(window, _parameter):
        if _is_window_visible(window) and not _get_window(window, GW_OWNER):
            pid = _window_process_id(window)
            if pid in pids and _get_window_text_length(window) > 0:
                visible_pids.add(pid)
        return True

    _enumerate_windows(consider_window, 0)
    return min(visible_pids, key=lambda pid: (pid != foreground_pid, pid), default=None)


@dataclass(frozen=True)
class Module:
    base: int
    size: int
    path: str


def main_module(pid):
    """Return the main executable module of a process."""
    snapshot = _create_toolhelp_snapshot(TH32CS_SNAPMODULE_ALL, pid)
    if snapshot == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    entry = MODULEENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        if not _module_first(snapshot, ctypes.byref(entry)):
            raise ctypes.WinError(ctypes.get_last_error())
        return Module(entry.modBaseAddr, entry.modBaseSize, entry.szExePath)
    finally:
        _close_handle(snapshot)


def is_user_address(address):
    return USER_ADDRESS_MINIMUM <= address < USER_ADDRESS_LIMIT


class ProcessMemory:
    """Read-only handle to another process with bounded, typed reads."""

    ACCESS = PROCESS_QUERY_INFORMATION | PROCESS_VM_READ

    def __init__(self, pid):
        self.pid = pid
        self.handle = _open_process(self.ACCESS, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            creation_time = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            if not _get_process_times(
                self.handle,
                ctypes.byref(creation_time),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            self.creation_time = (creation_time.dwHighDateTime << 32) | creation_time.dwLowDateTime
            self.module = main_module(pid)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            _close_handle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exception_info):
        self.close()

    def alive(self):
        exit_code = wintypes.DWORD()
        return bool(
            self.handle
            and _get_exit_code(self.handle, ctypes.byref(exit_code))
            and exit_code.value == STILL_ACTIVE
        )

    def read(self, address, size):
        """Return exactly size bytes at address, or raise MemoryReadError."""
        if not is_user_address(address) or not 0 < size <= MAX_READ_BYTES:
            raise MemoryReadError("Invalid read range")
        buffer = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t()
        succeeded = _read_process_memory(
            self.handle, address, buffer, size, ctypes.byref(bytes_read)
        )
        if not succeeded or bytes_read.value != size:
            raise MemoryReadError(ctypes.get_last_error(), "Incomplete process-memory read")
        return buffer.raw

    def unpack(self, address, layout):
        return struct.unpack(layout, self.read(address, struct.calcsize(layout)))

    def pointer(self, address):
        """Read a pointer; null is allowed, any other value must be a user address."""
        (value,) = self.unpack(address, "<Q")
        if value and not is_user_address(value):
            raise MemoryReadError("Invalid pointer")
        return value

    def i32(self, address):
        return self.unpack(address, "<i")[0]

    def u32(self, address):
        return self.unpack(address, "<I")[0]

    def regions(self, start=USER_ADDRESS_MINIMUM, end=USER_ADDRESS_LIMIT):
        """Yield (base, size, type, protection) of committed, readable regions."""
        address = start
        information = MEMORY_BASIC_INFORMATION()
        while address < end:
            queried = _virtual_query(
                self.handle, address, ctypes.byref(information), ctypes.sizeof(information)
            )
            if not queried:
                break
            next_address = information.BaseAddress + information.RegionSize
            if next_address <= address:
                break
            # Keep committed readable regions; exclude guard and no-access pages.
            readable = (
                information.State == MEM_COMMIT
                and not information.Protect & PAGE_NOACCESS_OR_GUARD
                and bool(information.Protect & PAGE_READABLE)
            )
            if readable:
                yield (
                    information.BaseAddress,
                    information.RegionSize,
                    information.Type,
                    information.Protect,
                )
            address = next_address

    def identity(self):
        """Return the process identity including the SHA-256 of its executable."""
        digest = hashlib.sha256()
        with Path(self.module.path).open("rb") as executable:
            for chunk in iter(lambda: executable.read(HASH_CHUNK_BYTES), b""):
                digest.update(chunk)
        return {
            "pid": self.pid,
            "creation_time": self.creation_time,
            "path": self.module.path,
            "sha256": digest.hexdigest(),
            "module_base": hex(self.module.base),
            "module_size": self.module.size,
        }
