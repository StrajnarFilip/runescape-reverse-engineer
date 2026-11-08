//! Minimal PE (Portable Executable) header parsing for an in-memory image.

use crate::mem::MemRead;

#[derive(Debug, Clone)]
pub struct Section {
    pub name: String,
    /// RVA of the section.
    pub va: u32,
    pub vsize: u32,
    pub characteristics: u32,
}

impl Section {
    pub fn executable(&self) -> bool {
        self.characteristics & 0x2000_0000 != 0
    }
    pub fn writable(&self) -> bool {
        self.characteristics & 0x8000_0000 != 0
    }
}

#[derive(Debug, Clone)]
pub struct PeHeaders {
    pub timestamp: u32,
    pub size_of_image: u32,
    pub sections: Vec<Section>,
}

impl PeHeaders {
    /// Stable identifier for a client build: link timestamp + image size.
    pub fn build_id(&self) -> String {
        format!("{:08x}-{:x}", self.timestamp, self.size_of_image)
    }
}

fn u16_at(b: &[u8], o: usize) -> Result<u16, String> {
    b.get(o..o + 2).map(|s| u16::from_le_bytes([s[0], s[1]])).ok_or_else(|| "PE header truncated".to_string())
}
fn u32_at(b: &[u8], o: usize) -> Result<u32, String> {
    b.get(o..o + 4)
        .map(|s| u32::from_le_bytes([s[0], s[1], s[2], s[3]]))
        .ok_or_else(|| "PE header truncated".to_string())
}

pub fn parse(buf: &[u8]) -> Result<PeHeaders, String> {
    if buf.get(0..2) != Some(b"MZ") {
        return Err("missing MZ signature".into());
    }
    let nt = u32_at(buf, 0x3C)? as usize;
    if buf.get(nt..nt + 4) != Some(b"PE\0\0") {
        return Err("missing PE signature".into());
    }
    let fh = nt + 4;
    let nsec = u16_at(buf, fh + 2)? as usize;
    let timestamp = u32_at(buf, fh + 4)?;
    let opt_size = u16_at(buf, fh + 16)? as usize;
    let opt = fh + 20;
    let size_of_image = u32_at(buf, opt + 56)?;
    let sec_table = opt + opt_size;
    let mut sections = Vec::with_capacity(nsec);
    for i in 0..nsec {
        let s = sec_table + i * 40;
        let raw = buf.get(s..s + 8).ok_or("section table truncated")?;
        let name = String::from_utf8_lossy(raw).trim_end_matches('\0').to_string();
        sections.push(Section {
            name,
            vsize: u32_at(buf, s + 8)?,
            va: u32_at(buf, s + 12)?,
            characteristics: u32_at(buf, s + 36)?,
        });
    }
    Ok(PeHeaders { timestamp, size_of_image, sections })
}

pub fn read_headers<M: MemRead>(mem: &M, base: u64) -> Result<PeHeaders, String> {
    let buf = mem.read_vec(base, 0x1000).map_err(|e| e.to_string())?;
    parse(&buf)
}

/// Read every executable section of the image as `(absolute address, bytes)`.
/// Unreadable pages are zero-filled so offsets stay correct.
pub fn read_code<M: MemRead>(mem: &M, base: u64, hdr: &PeHeaders) -> Vec<(u64, Vec<u8>)> {
    hdr.sections
        .iter()
        .filter(|s| s.executable() && s.vsize > 0)
        .map(|s| {
            let start = base + s.va as u64;
            (start, read_lossy(mem, start, s.vsize as usize))
        })
        .collect()
}

/// Read `len` bytes in 1 MiB chunks, falling back to 4 KiB pages; failed pages are zeros.
pub fn read_lossy<M: MemRead>(mem: &M, start: u64, len: usize) -> Vec<u8> {
    const CHUNK: usize = 1 << 20;
    const PAGE: usize = 0x1000;
    let mut out = vec![0u8; len];
    let mut off = 0;
    while off < len {
        let n = CHUNK.min(len - off);
        if mem.read_into(start + off as u64, &mut out[off..off + n]).is_err() {
            let mut p = 0;
            while p < n {
                let m = PAGE.min(n - p);
                let dst = &mut out[off + p..off + p + m];
                if mem.read_into(start + (off + p) as u64, dst).is_err() {
                    dst.fill(0);
                }
                p += m;
            }
        }
        off += n;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fake_pe() -> Vec<u8> {
        let mut b = vec![0u8; 0x400];
        b[0..2].copy_from_slice(b"MZ");
        b[0x3C..0x40].copy_from_slice(&0x80u32.to_le_bytes());
        b[0x80..0x84].copy_from_slice(b"PE\0\0");
        let fh = 0x84;
        b[fh + 2..fh + 4].copy_from_slice(&2u16.to_le_bytes());
        b[fh + 4..fh + 8].copy_from_slice(&0x6543_2100u32.to_le_bytes());
        b[fh + 16..fh + 18].copy_from_slice(&0xF0u16.to_le_bytes());
        let opt = fh + 20;
        b[opt..opt + 2].copy_from_slice(&0x20Bu16.to_le_bytes());
        b[opt + 56..opt + 60].copy_from_slice(&0x5000u32.to_le_bytes());
        let st = opt + 0xF0;
        b[st..st + 5].copy_from_slice(b".text");
        b[st + 8..st + 12].copy_from_slice(&0x1000u32.to_le_bytes());
        b[st + 12..st + 16].copy_from_slice(&0x1000u32.to_le_bytes());
        b[st + 36..st + 40].copy_from_slice(&0x6000_0020u32.to_le_bytes());
        let s2 = st + 40;
        b[s2..s2 + 5].copy_from_slice(b".data");
        b[s2 + 8..s2 + 12].copy_from_slice(&0x800u32.to_le_bytes());
        b[s2 + 12..s2 + 16].copy_from_slice(&0x2000u32.to_le_bytes());
        b[s2 + 36..s2 + 40].copy_from_slice(&0xC000_0040u32.to_le_bytes());
        b
    }

    #[test]
    fn parses_headers() {
        let h = parse(&fake_pe()).unwrap();
        assert_eq!(h.timestamp, 0x6543_2100);
        assert_eq!(h.size_of_image, 0x5000);
        assert_eq!(h.build_id(), "65432100-5000");
        assert_eq!(h.sections.len(), 2);
        assert!(h.sections[0].executable() && !h.sections[0].writable());
        assert!(h.sections[1].writable() && !h.sections[1].executable());
        assert_eq!(h.sections[1].va, 0x2000);
    }
}
