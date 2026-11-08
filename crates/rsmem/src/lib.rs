//! Read-only process memory primitives.
//!
//! * [`mem`]: the [`MemRead`] trait (implemented by the live [`Process`] and by
//!   [`FakeMem`] for tests), plus module/region descriptions.
//! * [`pattern`]: IDA-style byte patterns (`"48 8B 05 ?? ?? ?? ??"`).
//! * [`pe`]: minimal PE header parsing (build id, sections).
//! * `win` (Windows only): process discovery, `ReadProcessMemory`, region walking,
//!   a high-resolution ticker and key-state polling.
//!
//! Nothing in this crate writes to another process. Handles are opened with
//! `PROCESS_VM_READ | PROCESS_QUERY_LIMITED_INFORMATION` only.

pub mod mem;
pub mod pattern;
pub mod pe;
#[cfg(windows)]
pub mod win;

pub use mem::{FakeMem, MemRead, ModuleInfo, ReadError, RegionInfo, RegionKind};
pub use pattern::{rip_target, Pattern};
#[cfg(windows)]
pub use win::{
    find_clients, key_down, list_processes, visible_windows, vk_from_name, ClientInfo, ProcInfo,
    Process, Ticker, WindowInfo,
};

/// Format an address as `module+0xoff` when it falls inside a module, else `0x...`.
pub fn fmt_addr(addr: u64, modules: &[ModuleInfo]) -> String {
    match modules.iter().find(|m| m.contains(addr)) {
        Some(m) => format!("{}+{:#x}", m.name, addr - m.base),
        None => format!("{addr:#x}"),
    }
}
