"""Small Windows x64 process-memory reader. No process-write operations."""
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
import hashlib
from pathlib import Path
import struct

if C.sizeof(C.c_void_p) != 8:
    raise RuntimeError("Use 64-bit Python")

k32 = C.WinDLL("kernel32", use_last_error=True)

class PROCESSENTRY32W(C.Structure):
    _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD),
                ("th32ProcessID", W.DWORD), ("th32DefaultHeapID", C.c_size_t),
                ("th32ModuleID", W.DWORD), ("cntThreads", W.DWORD),
                ("th32ParentProcessID", W.DWORD), ("pcPriClassBase", W.LONG),
                ("dwFlags", W.DWORD), ("szExeFile", W.WCHAR * 260)]

class MODULEENTRY32W(C.Structure):
    _fields_ = [("dwSize", W.DWORD), ("th32ModuleID", W.DWORD),
                ("th32ProcessID", W.DWORD), ("GlblcntUsage", W.DWORD),
                ("ProccntUsage", W.DWORD), ("modBaseAddr", C.c_void_p),
                ("modBaseSize", W.DWORD), ("hModule", W.HMODULE),
                ("szModule", W.WCHAR * 256), ("szExePath", W.WCHAR * 260)]

class MEMORY_BASIC_INFORMATION(C.Structure):
    _fields_ = [("BaseAddress", C.c_void_p), ("AllocationBase", C.c_void_p),
                ("AllocationProtect", W.DWORD), ("PartitionId", W.WORD),
                ("RegionSize", C.c_size_t), ("State", W.DWORD),
                ("Protect", W.DWORD), ("Type", W.DWORD)]

def _bind(name, args, result):
    fn = getattr(k32, name)
    fn.argtypes, fn.restype = args, result
    return fn

_snapshot = _bind("CreateToolhelp32Snapshot", [W.DWORD, W.DWORD], W.HANDLE)
_process_first = _bind("Process32FirstW", [W.HANDLE, C.POINTER(PROCESSENTRY32W)], W.BOOL)
_process_next = _bind("Process32NextW", [W.HANDLE, C.POINTER(PROCESSENTRY32W)], W.BOOL)
_module_first = _bind("Module32FirstW", [W.HANDLE, C.POINTER(MODULEENTRY32W)], W.BOOL)
_module_next = _bind("Module32NextW", [W.HANDLE, C.POINTER(MODULEENTRY32W)], W.BOOL)
_open = _bind("OpenProcess", [W.DWORD, W.BOOL, W.DWORD], W.HANDLE)
_close = _bind("CloseHandle", [W.HANDLE], W.BOOL)
_read = _bind("ReadProcessMemory", [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)], W.BOOL)
_query = _bind("VirtualQueryEx", [W.HANDLE, C.c_void_p, C.POINTER(MEMORY_BASIC_INFORMATION), C.c_size_t], C.c_size_t)
_exitcode = _bind("GetExitCodeProcess", [W.HANDLE, C.POINTER(W.DWORD)], W.BOOL)
_times = _bind("GetProcessTimes", [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4, W.BOOL)

INVALID_HANDLE = C.c_void_p(-1).value

class MemoryReadError(OSError):
    pass

def processes(name="rs2client.exe", include_children=True):
    handle = _snapshot(2, 0)
    if handle == INVALID_HANDLE:
        raise C.WinError(C.get_last_error())
    entry = PROCESSENTRY32W()
    entry.dwSize = C.sizeof(entry)
    matches = {}
    try:
        result = _process_first(handle, C.byref(entry))
        while result:
            if entry.szExeFile.lower() == name.lower():
                matches[entry.th32ProcessID] = entry.th32ParentProcessID
            result = _process_next(handle, C.byref(entry))
    finally:
        _close(handle)
    # Compatibility Mode can launch several same-executable helper children.
    for pid, parent in matches.items():
        if include_children or parent not in matches:
            yield pid

@dataclass(frozen=True)
class Module:
    base: int
    size: int
    path: str

def main_module(pid):
    handle = _snapshot(0x18, pid)
    if handle == INVALID_HANDLE:
        raise C.WinError(C.get_last_error())
    entry = MODULEENTRY32W()
    entry.dwSize = C.sizeof(entry)
    try:
        if not _module_first(handle, C.byref(entry)):
            raise C.WinError(C.get_last_error())
        return Module(entry.modBaseAddr, entry.modBaseSize, entry.szExePath)
    finally:
        _close(handle)

class ProcessMemory:
    ACCESS = 0x0400 | 0x0010  # QUERY_INFORMATION | VM_READ

    def __init__(self, pid):
        self.pid = pid
        self.handle = _open(self.ACCESS, False, pid)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        try:
            creation, exit_time, kernel, user = (W.FILETIME() for _ in range(4))
            if not _times(self.handle, C.byref(creation), C.byref(exit_time), C.byref(kernel), C.byref(user)):
                raise C.WinError(C.get_last_error())
            self.creation_time = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            self.module = main_module(pid)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            _close(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def alive(self):
        code = W.DWORD()
        return bool(self.handle and _exitcode(self.handle, C.byref(code)) and code.value == 259)

    def read(self, address, size):
        if not 0x10000 <= address < 0x7FFFFFFFFFFF or not 0 < size <= 16 * 1024 * 1024:
            raise MemoryReadError("Invalid read range")
        buffer = C.create_string_buffer(size)
        count = C.c_size_t()
        ok = _read(self.handle, address, buffer, size, C.byref(count))
        if not ok or count.value != size:
            raise MemoryReadError(C.get_last_error(), "Incomplete process-memory read")
        return buffer.raw

    def unpack(self, address, fmt):
        return struct.unpack(fmt, self.read(address, struct.calcsize(fmt)))

    def pointer(self, address):
        value, = self.unpack(address, "<Q")
        if value and not 0x10000 <= value < 0x7FFFFFFFFFFF:
            raise MemoryReadError("Invalid pointer")
        return value

    def i32(self, address):
        return self.unpack(address, "<i")[0]

    def u32(self, address):
        return self.unpack(address, "<I")[0]

    def regions(self, start=0x10000, end=0x7FFFFFFFFFFF):
        address = start
        info = MEMORY_BASIC_INFORMATION()
        while address < end:
            if not _query(self.handle, address, C.byref(info), C.sizeof(info)):
                break
            next_address = info.BaseAddress + info.RegionSize
            if next_address <= address:
                break
            readable = info.State == 0x1000 and not (info.Protect & 0x101) and bool(info.Protect & 0xEE)
            if readable:
                yield (info.BaseAddress, info.RegionSize, info.Type, info.Protect)
            address = next_address

    def identity(self):
        digest = hashlib.sha256()
        with Path(self.module.path).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return {"pid": self.pid, "creation_time": self.creation_time,
                "path": self.module.path, "sha256": digest.hexdigest(),
                "module_base": hex(self.module.base), "module_size": self.module.size}
