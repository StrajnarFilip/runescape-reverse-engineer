//! Discovery toolkit for finding game state in client memory.
//!
//! Everything here is read-only. The library is generic over [`rsmem::MemRead`]
//! so each algorithm is testable against a fake address space or the mock target.

pub mod addr;
pub mod dump;
pub mod fieldmatch;
pub mod live;
pub mod nodes;
pub mod ptrscan;
pub mod scan;
pub mod session;
pub mod siggen;
pub mod tickfind;
pub mod watch;

pub use addr::parse_addr;
pub use ptrscan::{ptrscan, PtrPath, PtrScanOpts};
pub use scan::{Cond, RegionFilter};
pub use siggen::{siggen, Sig};
