//! Value scanning (first / next), region selection and chunked reading.

use anyhow::{anyhow, bail, Result};
use rsmem::{MemRead, ModuleInfo, RegionInfo, RegionKind};
use rsoffsets::ValType;
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::Path;

pub const CHUNK: usize = 1 << 20;
pub const PAGE: usize = 0x1000;
/// Refuse to keep more candidates than this (narrow the first scan instead).
pub const MAX_CANDIDATES: usize = 60_000_000;

#[derive(Clone, Debug, Default)]
pub struct RegionFilter {
    pub include_readonly: bool,
    pub include_mapped: bool,
    /// Only regions inside the main module image (static data).
    pub module_only: bool,
}

pub fn select_regions(all: &[RegionInfo], f: &RegionFilter, main: &ModuleInfo) -> Vec<RegionInfo> {
    all.iter()
        .filter(|r| r.readable)
        .filter(|r| f.include_readonly || r.writable)
        .filter(|r| f.include_mapped || r.kind != RegionKind::Mapped)
        .filter(|r| !f.module_only || (r.base >= main.base && r.end() <= main.base + main.size))
        .cloned()
        .collect()
}

pub fn total_bytes(regions: &[RegionInfo]) -> u64 {
    regions.iter().map(|r| r.size).sum()
}

/// Visit readable memory in up-to-1 MiB pieces (unreadable pages are skipped).
pub fn for_each_chunk<M: MemRead>(mem: &M, regions: &[RegionInfo], mut f: impl FnMut(u64, &[u8])) {
    let mut buf = vec![0u8; CHUNK];
    for r in regions {
        let mut off = 0u64;
        while off < r.size {
            let n = (CHUNK as u64).min(r.size - off) as usize;
            let addr = r.base + off;
            if mem.read_into(addr, &mut buf[..n]).is_ok() {
                f(addr, &buf[..n]);
            } else {
                // Page-wise fallback; emit contiguous readable runs.
                let mut run_start: Option<usize> = None;
                let mut p = 0usize;
                while p < n {
                    let m = PAGE.min(n - p);
                    let ok = mem.read_into(addr + p as u64, &mut buf[p..p + m]).is_ok();
                    match (ok, run_start) {
                        (true, None) => run_start = Some(p),
                        (false, Some(s)) => {
                            f(addr + s as u64, &buf[s..p]);
                            run_start = None;
                        }
                        _ => {}
                    }
                    p += m;
                }
                if let Some(s) = run_start {
                    f(addr + s as u64, &buf[s..n]);
                }
            }
            off += n as u64;
        }
    }
}

/// Scan condition. `old` is the previous value (None in a first scan).
#[derive(Clone, Debug, PartialEq)]
pub enum Cond {
    Eq(Vec<f64>),
    Ne(f64),
    Gt(f64),
    Lt(f64),
    Between(f64, f64),
    Changed,
    Unchanged,
    Inc,
    Dec,
    IncBy(f64),
    DecBy(f64),
    Unknown,
}

impl Cond {
    /// Parse CLI words: `9900`, `eq 52,520`, `between 0 1000`, `changed`, `incby 5`, `unknown`.
    pub fn parse(words: &[String]) -> Result<Self> {
        let w: Vec<&str> = words.iter().map(String::as_str).collect();
        let num = |s: &str| -> Result<f64> { s.trim().parse::<f64>().map_err(|_| anyhow!("bad number {s:?}")) };
        let list = |s: &str| -> Result<Vec<f64>> { s.split(',').map(num).collect() };
        Ok(match w.as_slice() {
            ["unknown"] => Cond::Unknown,
            ["changed"] => Cond::Changed,
            ["unchanged"] => Cond::Unchanged,
            ["inc"] | ["increased"] => Cond::Inc,
            ["dec"] | ["decreased"] => Cond::Dec,
            ["eq", v] => Cond::Eq(list(v)?),
            ["ne", v] => Cond::Ne(num(v)?),
            ["gt", v] => Cond::Gt(num(v)?),
            ["lt", v] => Cond::Lt(num(v)?),
            ["incby", v] => Cond::IncBy(num(v)?),
            ["decby", v] => Cond::DecBy(num(v)?),
            ["between", a, b] => Cond::Between(num(a)?, num(b)?),
            [v] => Cond::Eq(list(v)?),
            _ => bail!("unrecognized condition {words:?}; try: <value>, eq a,b, ne, gt, lt, between a b, changed, unchanged, inc, dec, incby n, decby n, unknown"),
        })
    }

    pub fn needs_old(&self) -> bool {
        matches!(self, Cond::Changed | Cond::Unchanged | Cond::Inc | Cond::Dec | Cond::IncBy(_) | Cond::DecBy(_))
    }

    pub fn test(&self, old: Option<f64>, new: f64, float: bool, tol: f64) -> bool {
        let eq = |a: f64, b: f64| if float { (a - b).abs() <= tol } else { a == b };
        match self {
            Cond::Eq(vs) => vs.iter().any(|v| eq(new, *v)),
            Cond::Ne(v) => !eq(new, *v),
            Cond::Gt(v) => new > *v,
            Cond::Lt(v) => new < *v,
            Cond::Between(a, b) => new >= *a && new <= *b,
            Cond::Unknown => true,
            Cond::Changed => old.is_some_and(|o| !eq(o, new)),
            Cond::Unchanged => old.is_some_and(|o| eq(o, new)),
            Cond::Inc => old.is_some_and(|o| new > o && !eq(o, new)),
            Cond::Dec => old.is_some_and(|o| new < o && !eq(o, new)),
            Cond::IncBy(d) => old.is_some_and(|o| eq(new - o, *d)),
            Cond::DecBy(d) => old.is_some_and(|o| eq(o - new, *d)),
        }
    }
}

pub fn f64_of(ty: ValType, raw: u64) -> f64 {
    ty.from_raw(raw).as_f64()
}

/// First scan over `regions`, testing every `align`-aligned element.
pub fn first_scan<M: MemRead>(mem: &M, regions: &[RegionInfo], ty: ValType, align: usize, cond: &Cond, tol: f64) -> Result<Vec<(u64, u64)>> {
    if cond.needs_old() {
        bail!("{cond:?} needs a previous value; use it with `next`");
    }
    if matches!(cond, Cond::Unknown) {
        bail!("use `first <type> unknown` via the snapshot path");
    }
    let size = ty.size();
    let float = ty.is_float();
    let mask = if size == 8 { u64::MAX } else { (1u64 << (size * 8)) - 1 };
    let raw_targets: Option<Vec<u64>> = match cond {
        Cond::Eq(vs) if !float => Some(vs.iter().map(|v| ty.to_raw(*v) & mask).collect()),
        _ => None,
    };
    let mut out: Vec<(u64, u64)> = Vec::new();
    let mut overflow = false;
    for_each_chunk(mem, regions, |base, bytes| {
        if overflow {
            return;
        }
        let mut i = ((align as u64 - base % align as u64) % align as u64) as usize;
        while i + size <= bytes.len() {
            let raw = ty.raw(&bytes[i..]);
            let hit = match &raw_targets {
                Some(t) => t.contains(&raw),
                None => cond.test(None, f64_of(ty, raw), float, tol),
            };
            if hit {
                out.push((base + i as u64, raw));
                if out.len() > MAX_CANDIDATES {
                    overflow = true;
                    return;
                }
            }
            i += align;
        }
    });
    if overflow {
        bail!("more than {MAX_CANDIDATES} matches; use a more specific value or --module-only");
    }
    Ok(out)
}

/// Read sorted addresses in coalesced runs (up to 64 KiB each).
/// `f(index, bytes)` gets `None` when the address was unreadable.
pub fn read_runs<M: MemRead>(mem: &M, addrs: &[u64], size: usize, mut f: impl FnMut(usize, Option<&[u8]>)) {
    const RUN: u64 = 64 * 1024;
    let mut buf = vec![0u8; RUN as usize + 16];
    let mut i = 0;
    while i < addrs.len() {
        let start = addrs[i];
        let mut j = i;
        while j + 1 < addrs.len() && addrs[j + 1] >= start && addrs[j + 1] + size as u64 - start <= RUN {
            j += 1;
        }
        let len = (addrs[j] + size as u64 - start) as usize;
        if mem.read_into(start, &mut buf[..len]).is_ok() {
            for k in i..=j {
                let o = (addrs[k] - start) as usize;
                f(k, Some(&buf[o..o + size]));
            }
        } else {
            let mut one = vec![0u8; size];
            for k in i..=j {
                f(k, mem.read_into(addrs[k], &mut one).ok().map(|_| &one[..]));
            }
        }
        i = j + 1;
    }
}

/// Re-test candidates against current memory. Candidates must be sorted by address.
pub fn next_scan<M: MemRead>(mem: &M, ty: ValType, cands: &[(u64, u64)], cond: &Cond, tol: f64) -> Vec<(u64, u64)> {
    let addrs: Vec<u64> = cands.iter().map(|c| c.0).collect();
    let float = ty.is_float();
    let mut out = Vec::new();
    read_runs(mem, &addrs, ty.size(), |k, bytes| {
        if let Some(b) = bytes {
            let raw = ty.raw(b);
            let old = f64_of(ty, cands[k].1);
            if cond.test(Some(old), f64_of(ty, raw), float, tol) {
                out.push((cands[k].0, raw));
            }
        }
    });
    out
}

/// Save raw bytes of all regions for a later `next` against an unknown initial value.
pub fn snapshot_to_file<M: MemRead>(mem: &M, regions: &[RegionInfo], path: &Path) -> Result<u64> {
    let mut w = BufWriter::new(std::fs::File::create(path)?);
    let mut total = 0u64;
    let mut err: Option<std::io::Error> = None;
    for_each_chunk(mem, regions, |base, bytes| {
        if err.is_some() {
            return;
        }
        let r = w
            .write_all(&base.to_le_bytes())
            .and_then(|_| w.write_all(&(bytes.len() as u64).to_le_bytes()))
            .and_then(|_| w.write_all(bytes));
        if let Err(e) = r {
            err = Some(e);
        }
        total += bytes.len() as u64;
    });
    if let Some(e) = err {
        return Err(e.into());
    }
    w.flush()?;
    Ok(total)
}

/// Compare a snapshot file against current memory, producing explicit candidates.
pub fn next_from_snapshot<M: MemRead>(mem: &M, ty: ValType, align: usize, path: &Path, cond: &Cond, tol: f64) -> Result<Vec<(u64, u64)>> {
    let mut r = BufReader::new(std::fs::File::open(path)?);
    let size = ty.size();
    let float = ty.is_float();
    let mut out = Vec::new();
    let mut hdr = [0u8; 16];
    let mut old = Vec::new();
    let mut cur = Vec::new();
    loop {
        match r.read_exact(&mut hdr) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => break,
            Err(e) => return Err(e.into()),
        }
        let base = u64::from_le_bytes(hdr[0..8].try_into().unwrap());
        let len = u64::from_le_bytes(hdr[8..16].try_into().unwrap()) as usize;
        old.resize(len, 0);
        r.read_exact(&mut old)?;
        cur.resize(len, 0);
        if mem.read_into(base, &mut cur).is_err() {
            continue;
        }
        let mut i = ((align as u64 - base % align as u64) % align as u64) as usize;
        while i + size <= len {
            let (o, n) = (ty.raw(&old[i..]), ty.raw(&cur[i..]));
            if cond.test(Some(f64_of(ty, o)), f64_of(ty, n), float, tol) {
                out.push((base + i as u64, n));
                if out.len() > MAX_CANDIDATES {
                    bail!("more than {MAX_CANDIDATES} matches; repeat with a stricter condition");
                }
            }
            i += align;
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use rsmem::FakeMem;

    fn mem() -> FakeMem {
        let mut m = FakeMem::new();
        m.add(0x10000, vec![0; 0x3000]);
        m.write(0x10010u64, 9900i32);
        m.write(0x10020u64, 9900i32);
        m.write(0x11000u64, 520i32);
        m
    }

    #[test]
    fn first_then_next() {
        let mut m = mem();
        let regions = m.region_infos();
        let c = first_scan(&m, &regions, ValType::I32, 4, &Cond::Eq(vec![9900.0]), 0.0).unwrap();
        assert_eq!(c.iter().map(|x| x.0).collect::<Vec<_>>(), vec![0x10010, 0x10020]);
        m.write(0x10010u64, 9800i32);
        let n = next_scan(&m, ValType::I32, &c, &Cond::Eq(vec![9800.0]), 0.0);
        assert_eq!(n, vec![(0x10010, 9800)]);
        let multi = first_scan(&m, &regions, ValType::I32, 4, &Cond::parse(&["52,520".into()]).unwrap(), 0.0).unwrap();
        assert_eq!(multi.len(), 1);
        let dec = next_scan(&m, ValType::I32, &c, &Cond::Dec, 0.0);
        assert_eq!(dec.len(), 1);
    }

    #[test]
    fn snapshot_roundtrip() {
        let mut m = mem();
        let regions = m.region_infos();
        let dir = std::env::temp_dir().join(format!("rsscan-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("snap.bin");
        snapshot_to_file(&m, &regions, &p).unwrap();
        m.write(0x10020u64, 9950i32);
        let inc = next_from_snapshot(&m, ValType::I32, 4, &p, &Cond::Inc, 0.0).unwrap();
        assert_eq!(inc, vec![(0x10020, 9950)]);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn runs_coalesce_and_handle_gaps() {
        let m = mem();
        let addrs = vec![0x10010, 0x10020, 0x11000, 0x90000];
        let mut seen = vec![];
        read_runs(&m, &addrs, 4, |k, b| seen.push((k, b.map(|b| i32::from_le_bytes(b.try_into().unwrap())))));
        assert_eq!(seen, vec![(0, Some(9900)), (1, Some(9900)), (2, Some(520)), (3, None)]);
    }

    #[test]
    fn cond_parse() {
        assert_eq!(Cond::parse(&["between".into(), "0".into(), "10".into()]).unwrap(), Cond::Between(0.0, 10.0));
        assert!(Cond::parse(&["what".into(), "x".into()]).is_err());
        assert!(Cond::Inc.test(Some(1.0), 2.0, false, 0.0));
        assert!(!Cond::Inc.test(None, 2.0, false, 0.0));
    }
}
