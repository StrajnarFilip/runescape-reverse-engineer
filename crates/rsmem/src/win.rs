//! Windows implementation: process discovery and read-only memory access.

use crate::mem::{MemRead, ModuleInfo, ReadError, RegionInfo, RegionKind};
use anyhow::{bail, Result};
use std::ffi::c_void;
use std::mem::{size_of, zeroed};
use std::time::{Duration, Instant};
use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, BOOL, HANDLE, HWND, INVALID_HANDLE_VALUE, LPARAM};
use windows_sys::Win32::System::Diagnostics::Debug::ReadProcessMemory;
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Module32FirstW, Module32NextW, Process32FirstW, Process32NextW, MODULEENTRY32W,
    PROCESSENTRY32W, TH32CS_SNAPMODULE, TH32CS_SNAPMODULE32, TH32CS_SNAPPROCESS,
};
use windows_sys::Win32::System::Memory::{
    VirtualQueryEx, MEMORY_BASIC_INFORMATION, MEM_COMMIT, MEM_IMAGE, MEM_MAPPED, PAGE_EXECUTE, PAGE_EXECUTE_READ,
    PAGE_EXECUTE_READWRITE, PAGE_EXECUTE_WRITECOPY, PAGE_GUARD, PAGE_NOACCESS, PAGE_READWRITE, PAGE_WRITECOPY,
};
use windows_sys::Win32::System::Threading::{
    CreateWaitableTimerExW, GetCurrentThread, GetExitCodeProcess, OpenProcess, QueryFullProcessImageNameW,
    SetThreadPriority, SetWaitableTimer, WaitForSingleObject, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_VM_READ,
};
use windows_sys::Win32::UI::Input::KeyboardAndMouse::GetAsyncKeyState;
use windows_sys::Win32::UI::WindowsAndMessaging::{EnumWindows, GetWindowTextW, GetWindowThreadProcessId, IsWindowVisible};

#[link(name = "ntdll")]
extern "system" {
    fn NtQueryInformationProcess(h: HANDLE, class: i32, info: *mut c_void, len: u32, ret: *mut u32) -> i32;
}

fn wide_to_string(w: &[u16]) -> String {
    let end = w.iter().position(|&c| c == 0).unwrap_or(w.len());
    String::from_utf16_lossy(&w[..end])
}

fn last_error() -> u32 {
    unsafe { GetLastError() }
}

/// A read-only handle to another process.
pub struct Process {
    h: HANDLE,
    pid: u32,
}

// The handle is only used for read-only, thread-safe Win32 calls.
unsafe impl Send for Process {}
unsafe impl Sync for Process {}

impl Drop for Process {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.h);
        }
    }
}

impl MemRead for Process {
    fn read_into(&self, addr: u64, buf: &mut [u8]) -> Result<(), ReadError> {
        if buf.is_empty() {
            return Ok(());
        }
        let mut n: usize = 0;
        let ok = unsafe {
            ReadProcessMemory(self.h, addr as *const c_void, buf.as_mut_ptr() as *mut c_void, buf.len(), &mut n)
        };
        if ok == 0 || n != buf.len() {
            return Err(ReadError { addr, len: buf.len(), code: last_error() });
        }
        Ok(())
    }
}

impl Process {
    /// Open with `PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION` (never write access).
    pub fn open(pid: u32) -> Result<Self> {
        let h = unsafe { OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
        if h.is_null() {
            bail!("OpenProcess({pid}) failed: os error {}", last_error());
        }
        Ok(Self { h, pid })
    }

    pub fn pid(&self) -> u32 {
        self.pid
    }

    pub fn is_alive(&self) -> bool {
        let mut code: u32 = 0;
        let ok = unsafe { GetExitCodeProcess(self.h, &mut code) };
        ok != 0 && code == 259 // STILL_ACTIVE
    }

    pub fn exe_path(&self) -> Option<String> {
        let mut buf = vec![0u16; 1024];
        let mut len = buf.len() as u32;
        let ok = unsafe { QueryFullProcessImageNameW(self.h, 0, buf.as_mut_ptr(), &mut len) };
        (ok != 0).then(|| String::from_utf16_lossy(&buf[..len as usize]))
    }

    /// Full command line (ProcessCommandLineInformation), if the OS allows it.
    pub fn command_line(&self) -> Option<String> {
        #[repr(C)]
        struct UnicodeString {
            len: u16,
            max: u16,
            buf: *const u16,
        }
        let mut cap = 4096usize;
        for _ in 0..3 {
            let mut buf = vec![0u64; cap / 8];
            let mut ret: u32 = 0;
            let st = unsafe {
                NtQueryInformationProcess(self.h, 60, buf.as_mut_ptr() as *mut c_void, (buf.len() * 8) as u32, &mut ret)
            };
            if st >= 0 {
                let us = unsafe { std::ptr::read(buf.as_ptr() as *const UnicodeString) };
                if us.buf.is_null() || us.len == 0 {
                    return None;
                }
                let s = unsafe { std::slice::from_raw_parts(us.buf, us.len as usize / 2) };
                return Some(String::from_utf16_lossy(s));
            }
            if (ret as usize) > cap {
                cap = ret as usize + 64;
            } else {
                return None;
            }
        }
        None
    }

    pub fn modules(&self) -> Result<Vec<ModuleInfo>> {
        let mut snap = INVALID_HANDLE_VALUE;
        for _ in 0..5 {
            snap = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, self.pid) };
            if snap != INVALID_HANDLE_VALUE {
                break;
            }
            if last_error() != 24 {
                break; // ERROR_BAD_LENGTH means retry
            }
        }
        if snap == INVALID_HANDLE_VALUE {
            bail!("CreateToolhelp32Snapshot(modules, {}) failed: os error {}", self.pid, last_error());
        }
        let mut out = Vec::new();
        unsafe {
            let mut me: MODULEENTRY32W = zeroed();
            me.dwSize = size_of::<MODULEENTRY32W>() as u32;
            if Module32FirstW(snap, &mut me) != 0 {
                loop {
                    out.push(ModuleInfo {
                        name: wide_to_string(&me.szModule),
                        base: me.modBaseAddr as u64,
                        size: me.modBaseSize as u64,
                        path: wide_to_string(&me.szExePath),
                    });
                    if Module32NextW(snap, &mut me) == 0 {
                        break;
                    }
                }
            }
            CloseHandle(snap);
        }
        Ok(out)
    }

    /// The process's main executable module.
    pub fn main_module(&self) -> Result<ModuleInfo> {
        let mods = self.modules()?;
        let exe = self
            .exe_path()
            .and_then(|p| p.rsplit(['\\', '/']).next().map(|s| s.to_ascii_lowercase()));
        if let Some(exe) = exe {
            if let Some(m) = mods.iter().find(|m| m.name.to_ascii_lowercase() == exe) {
                return Ok(m.clone());
            }
        }
        mods.into_iter().next().ok_or_else(|| anyhow::anyhow!("process {} has no modules", self.pid))
    }

    /// All committed regions.
    pub fn regions(&self) -> Vec<RegionInfo> {
        let mut out = Vec::new();
        let mut addr: u64 = 0;
        loop {
            let mut mbi: MEMORY_BASIC_INFORMATION = unsafe { zeroed() };
            let r = unsafe {
                VirtualQueryEx(self.h, addr as *const c_void, &mut mbi, size_of::<MEMORY_BASIC_INFORMATION>())
            };
            if r == 0 {
                break;
            }
            let base = mbi.BaseAddress as u64;
            let size = mbi.RegionSize as u64;
            if mbi.State == MEM_COMMIT {
                let p = mbi.Protect;
                let readable = p != 0 && p & (PAGE_NOACCESS | PAGE_GUARD) == 0;
                let writable = p & (PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY) != 0;
                let executable = p & (PAGE_EXECUTE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY) != 0;
                let kind = if mbi.Type == MEM_IMAGE {
                    RegionKind::Image
                } else if mbi.Type == MEM_MAPPED {
                    RegionKind::Mapped
                } else {
                    RegionKind::Private
                };
                out.push(RegionInfo { base, size, protect: p, kind, readable, writable, executable });
            }
            let next = base.saturating_add(size);
            if next <= addr {
                break;
            }
            addr = next;
        }
        out
    }
}

/// A process from a Toolhelp snapshot.
#[derive(Debug, Clone)]
pub struct ProcInfo {
    pub pid: u32,
    pub ppid: u32,
    pub name: String,
}

pub fn list_processes() -> Vec<ProcInfo> {
    let mut out = Vec::new();
    unsafe {
        let snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snap == INVALID_HANDLE_VALUE {
            return out;
        }
        let mut pe: PROCESSENTRY32W = zeroed();
        pe.dwSize = size_of::<PROCESSENTRY32W>() as u32;
        if Process32FirstW(snap, &mut pe) != 0 {
            loop {
                out.push(ProcInfo {
                    pid: pe.th32ProcessID,
                    ppid: pe.th32ParentProcessID,
                    name: wide_to_string(&pe.szExeFile),
                });
                if Process32NextW(snap, &mut pe) == 0 {
                    break;
                }
            }
        }
        CloseHandle(snap);
    }
    out
}

/// A visible top-level window.
#[derive(Debug, Clone)]
pub struct WindowInfo {
    pub pid: u32,
    pub title: String,
}

pub fn visible_windows() -> Vec<WindowInfo> {
    unsafe extern "system" fn cb(hwnd: HWND, lp: LPARAM) -> BOOL {
        let v = &mut *(lp as *mut Vec<WindowInfo>);
        if IsWindowVisible(hwnd) == 0 {
            return 1;
        }
        let mut pid: u32 = 0;
        GetWindowThreadProcessId(hwnd, &mut pid);
        let mut buf = [0u16; 512];
        let n = GetWindowTextW(hwnd, buf.as_mut_ptr(), buf.len() as i32);
        if n > 0 {
            v.push(WindowInfo { pid, title: String::from_utf16_lossy(&buf[..n as usize]) });
        }
        1
    }
    let mut v: Vec<WindowInfo> = Vec::new();
    unsafe {
        EnumWindows(Some(cb), &mut v as *mut Vec<WindowInfo> as LPARAM);
    }
    v
}

/// A running game client (main process, not a `--compileshader` helper).
#[derive(Debug, Clone)]
pub struct ClientInfo {
    pub pid: u32,
    pub title: String,
    /// World from the launch command line, e.g. `world54` (the world it started on).
    pub launch_world: Option<String>,
}

/// Processes named `exe` that own a visible window whose title starts with `title_prefix`.
pub fn find_clients(exe: &str, title_prefix: &str) -> Vec<ClientInfo> {
    let procs = list_processes();
    let wins = visible_windows();
    let mut out: Vec<ClientInfo> = Vec::new();
    for p in procs.iter().filter(|p| p.name.eq_ignore_ascii_case(exe)) {
        let Some(w) = wins.iter().find(|w| w.pid == p.pid && w.title.starts_with(title_prefix)) else {
            continue;
        };
        let launch_world = Process::open(p.pid).ok().and_then(|h| h.command_line()).and_then(|c| parse_world(&c));
        out.push(ClientInfo { pid: p.pid, title: w.title.clone(), launch_world });
    }
    out.sort_by_key(|c| c.pid);
    out
}

fn parse_world(cmdline: &str) -> Option<String> {
    let i = cmdline.find("://world")?;
    let rest = &cmdline[i + 3..];
    let end = rest.find('.')?;
    Some(rest[..end].to_string())
}

/// True while the virtual key is held down (global, focus-independent).
pub fn key_down(vk: i32) -> bool {
    (unsafe { GetAsyncKeyState(vk) } as u16) & 0x8000 != 0
}

/// Parse a key name (`F8`, `1`, `Q`, `SPACE`, `SHIFT`, `CTRL`, `ALT`, `TAB`) into a virtual-key code.
pub fn vk_from_name(name: &str) -> Option<i32> {
    let n = name.trim().to_ascii_uppercase();
    match n.as_str() {
        "SPACE" => return Some(0x20),
        "SHIFT" => return Some(0x10),
        "CTRL" | "CONTROL" => return Some(0x11),
        "ALT" => return Some(0x12),
        "TAB" => return Some(0x09),
        "ENTER" => return Some(0x0D),
        "`" | "BACKTICK" => return Some(0xC0),
        "-" | "MINUS" => return Some(0xBD),
        "=" | "EQUALS" => return Some(0xBB),
        _ => {}
    }
    if let Some(num) = n.strip_prefix('F') {
        if let Ok(k) = num.parse::<i32>() {
            if (1..=24).contains(&k) {
                return Some(0x70 + k - 1);
            }
        }
    }
    let mut chars = n.chars();
    match (chars.next(), chars.next()) {
        (Some(c), None) if c.is_ascii_digit() || c.is_ascii_uppercase() => Some(c as i32),
        _ => None,
    }
}

/// Fixed-rate ticker using a high-resolution waitable timer (falls back to sleep).
pub struct Ticker {
    h: HANDLE,
    period: Duration,
    next: Instant,
}

unsafe impl Send for Ticker {}

impl Ticker {
    pub fn new(period: Duration) -> Self {
        // CREATE_WAITABLE_TIMER_HIGH_RESOLUTION = 2, TIMER_ALL_ACCESS = 0x1F0003
        let h = unsafe { CreateWaitableTimerExW(std::ptr::null(), std::ptr::null(), 2, 0x1F0003) };
        Self { h, period, next: Instant::now() }
    }

    /// Raise the calling thread's priority (THREAD_PRIORITY_HIGHEST).
    pub fn boost_current_thread() {
        unsafe {
            SetThreadPriority(GetCurrentThread(), 2);
        }
    }

    /// Sleep until the next period boundary. Returns false if we were already late.
    pub fn wait(&mut self) -> bool {
        self.next += self.period;
        let now = Instant::now();
        if self.next <= now {
            if now - self.next > self.period * 20 {
                self.next = now; // fell far behind (e.g. machine suspended); resync
            }
            return false;
        }
        let dur = self.next - now;
        if !self.h.is_null() {
            let due: i64 = -((dur.as_nanos() / 100).max(1) as i64);
            unsafe {
                if SetWaitableTimer(self.h, &due, 0, None, std::ptr::null(), 0) != 0 {
                    WaitForSingleObject(self.h, 0xFFFF_FFFF);
                    return true;
                }
            }
        }
        std::thread::sleep(dur);
        true
    }
}

impl Drop for Ticker {
    fn drop(&mut self) {
        if !self.h.is_null() {
            unsafe {
                CloseHandle(self.h);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn world_from_cmdline() {
        let c = r#""C:\x\rs2client.exe" "35" "https://world54.runescape.com/k=5" "36""#;
        assert_eq!(parse_world(c).as_deref(), Some("world54"));
        assert_eq!(parse_world("nothing"), None);
    }

    #[test]
    fn key_names() {
        assert_eq!(vk_from_name("F8"), Some(0x77));
        assert_eq!(vk_from_name("1"), Some(0x31));
        assert_eq!(vk_from_name("q"), Some(0x51));
        assert_eq!(vk_from_name("space"), Some(0x20));
        assert_eq!(vk_from_name("F99"), None);
    }

    #[test]
    fn reads_own_process() {
        let me = Process::open(std::process::id()).unwrap();
        let v: u64 = 0xDEAD_BEEF_1234_5678;
        let got: u64 = me.read(&v as *const u64 as u64).unwrap();
        assert_eq!(got, v);
        assert!(me.is_alive());
        let main = me.main_module().unwrap();
        assert!(main.size > 0);
        let hdr = crate::pe::read_headers(&me, main.base).unwrap();
        assert!(hdr.sections.iter().any(|s| s.executable()));
        assert!(!me.regions().is_empty());
    }

    #[test]
    fn ticker_runs() {
        let mut t = Ticker::new(Duration::from_millis(1));
        let start = Instant::now();
        for _ in 0..50 {
            t.wait();
        }
        let el = start.elapsed();
        assert!(el >= Duration::from_millis(45), "{el:?}");
        assert!(el < Duration::from_millis(500), "{el:?}");
    }
}
