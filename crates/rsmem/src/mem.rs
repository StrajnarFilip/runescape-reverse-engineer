use std::fmt;

/// A failed read of another process's memory.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ReadError {
    pub addr: u64,
    pub len: usize,
    /// OS error code (0 when the failure is a null pointer in a chain).
    pub code: u32,
}

impl fmt::Display for ReadError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.code == 0 {
            write!(f, "null pointer while reading {} bytes at {:#x}", self.len, self.addr)
        } else {
            write!(f, "read of {} bytes at {:#x} failed (os error {})", self.len, self.addr, self.code)
        }
    }
}

impl std::error::Error for ReadError {}

/// Anything we can read bytes from: a live process, or a fake for tests.
pub trait MemRead {
    fn read_into(&self, addr: u64, buf: &mut [u8]) -> Result<(), ReadError>;

    fn read<T: bytemuck::Pod>(&self, addr: u64) -> Result<T, ReadError> {
        let mut v = <T as bytemuck::Zeroable>::zeroed();
        self.read_into(addr, bytemuck::bytes_of_mut(&mut v))?;
        Ok(v)
    }

    fn read_ptr(&self, addr: u64) -> Result<u64, ReadError> {
        self.read::<u64>(addr)
    }

    fn read_vec(&self, addr: u64, len: usize) -> Result<Vec<u8>, ReadError> {
        let mut v = vec![0u8; len];
        self.read_into(addr, &mut v)?;
        Ok(v)
    }

    /// Cheat-Engine-style pointer chain: for each offset, `addr = read_ptr(addr) + off`.
    /// An empty chain returns `start` unchanged.
    fn follow_chain(&self, start: u64, chain: &[i64]) -> Result<u64, ReadError> {
        let mut a = start;
        for &off in chain {
            let p = self.read_ptr(a)?;
            if p == 0 {
                return Err(ReadError { addr: a, len: 8, code: 0 });
            }
            a = p.wrapping_add(off as u64);
        }
        Ok(a)
    }
}

impl<T: MemRead + ?Sized> MemRead for &T {
    fn read_into(&self, addr: u64, buf: &mut [u8]) -> Result<(), ReadError> {
        (**self).read_into(addr, buf)
    }
}

/// A loaded module (exe or dll) in the target process.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ModuleInfo {
    pub name: String,
    pub base: u64,
    pub size: u64,
    pub path: String,
}

impl ModuleInfo {
    pub fn contains(&self, addr: u64) -> bool {
        addr >= self.base && addr < self.base + self.size
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RegionKind {
    Private,
    Image,
    Mapped,
}

/// A committed virtual memory region.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RegionInfo {
    pub base: u64,
    pub size: u64,
    pub protect: u32,
    pub kind: RegionKind,
    pub readable: bool,
    pub writable: bool,
    pub executable: bool,
}

impl RegionInfo {
    pub fn end(&self) -> u64 {
        self.base + self.size
    }
}

/// In-memory fake address space for unit tests.
#[derive(Debug, Default, Clone)]
pub struct FakeMem {
    pub regions: Vec<(u64, Vec<u8>)>,
}

impl FakeMem {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn add(&mut self, base: u64, bytes: Vec<u8>) {
        self.regions.push((base, bytes));
    }

    pub fn write<T: bytemuck::Pod>(&mut self, addr: u64, v: T) {
        let bytes = bytemuck::bytes_of(&v);
        for (base, data) in &mut self.regions {
            if addr >= *base && addr + bytes.len() as u64 <= *base + data.len() as u64 {
                let off = (addr - *base) as usize;
                data[off..off + bytes.len()].copy_from_slice(bytes);
                return;
            }
        }
        panic!("FakeMem::write outside any region at {addr:#x}");
    }

    /// Describe the fake regions as writable private memory (for scanners).
    pub fn region_infos(&self) -> Vec<RegionInfo> {
        self.regions
            .iter()
            .map(|(b, d)| RegionInfo {
                base: *b,
                size: d.len() as u64,
                protect: 0x04,
                kind: RegionKind::Private,
                readable: true,
                writable: true,
                executable: false,
            })
            .collect()
    }
}

impl MemRead for FakeMem {
    fn read_into(&self, addr: u64, buf: &mut [u8]) -> Result<(), ReadError> {
        for (base, data) in &self.regions {
            if addr >= *base && addr + buf.len() as u64 <= *base + data.len() as u64 {
                let off = (addr - *base) as usize;
                buf.copy_from_slice(&data[off..off + buf.len()]);
                return Ok(());
            }
        }
        Err(ReadError { addr, len: buf.len(), code: 299 })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chain_follows_pointers() {
        let mut m = FakeMem::new();
        m.add(0x1000, vec![0; 0x100]);
        m.add(0x5000, vec![0; 0x100]);
        m.write(0x1010u64, 0x5000u64); // static at 0x1010 -> object at 0x5000
        m.write(0x5040u64, 0x5080u64); // object+0x40 -> array at 0x5080
        m.write(0x5080u64 + 8, 1234i32);
        let a = m.follow_chain(0x1010, &[0x40, 8]).unwrap();
        assert_eq!(a, 0x5088);
        assert_eq!(m.read::<i32>(a).unwrap(), 1234);
        assert_eq!(m.follow_chain(0x1010, &[]).unwrap(), 0x1010);
    }

    #[test]
    fn chain_null_is_error() {
        let mut m = FakeMem::new();
        m.add(0x1000, vec![0; 0x10]);
        let e = m.follow_chain(0x1000, &[0]).unwrap_err();
        assert_eq!(e.code, 0);
    }
}
