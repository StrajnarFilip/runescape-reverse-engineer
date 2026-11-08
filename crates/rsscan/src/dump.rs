//! Hex / typed dumps and struct field diffing over time.

use rsmem::{MemRead, RegionInfo};
use std::collections::BTreeSet;
use std::time::{Duration, Instant};

fn in_regions(regions: &[RegionInfo], v: u64) -> bool {
    let i = regions.partition_point(|r| r.end() <= v);
    i < regions.len() && regions[i].base <= v
}

/// One line per 8 bytes: offset, hex, i32 pair, f32 pair, and a pointer marker.
pub fn dump(bytes: &[u8], base: u64, regions_sorted: &[RegionInfo]) -> Vec<String> {
    let mut out = Vec::new();
    for (k, c) in bytes.chunks(8).enumerate() {
        let off = k * 8;
        let hex: Vec<String> = c.iter().map(|b| format!("{b:02x}")).collect();
        let mut line = format!("{:#06x} {:#x}  {:<23}", off, base + off as u64, hex.join(" "));
        if c.len() == 8 {
            let q = u64::from_le_bytes(c.try_into().unwrap());
            let a = i32::from_le_bytes(c[0..4].try_into().unwrap());
            let b = i32::from_le_bytes(c[4..8].try_into().unwrap());
            let fa = f32::from_le_bytes(c[0..4].try_into().unwrap());
            let fb = f32::from_le_bytes(c[4..8].try_into().unwrap());
            line += &format!("  i32 {a:>11} {b:>11}  f32 {fa:>10.3} {fb:>10.3}");
            if q != 0 && in_regions(regions_sorted, q) {
                line += &format!("  -> ptr {q:#x}");
            }
        }
        out.push(line);
    }
    out
}

#[derive(Debug, Clone)]
pub struct FieldStats {
    pub offset: usize,
    pub changes: usize,
    pub first: i32,
    pub last: i32,
    pub min: i32,
    pub max: i32,
    pub distinct: BTreeSet<i32>,
    pub change_times: Vec<f64>,
}

/// Sample `len` bytes at `addr` at `hz` for `duration`; track every 4-byte field.
/// `mark(t_ms)` is polled each pass and returns a label when the user marks a moment.
pub fn structdiff<M: MemRead>(
    mem: &M,
    addr: u64,
    len: usize,
    hz: f64,
    duration: Duration,
    mut mark: impl FnMut() -> Option<String>,
) -> (Vec<FieldStats>, Vec<(f64, String)>) {
    let n = len / 4;
    let mut stats: Vec<Option<FieldStats>> = vec![None; n];
    let mut marks = Vec::new();
    let mut buf = vec![0u8; n * 4];
    let period = Duration::from_secs_f64(1.0 / hz.max(1.0));
    let start = Instant::now();
    while start.elapsed() < duration {
        let t = start.elapsed().as_secs_f64() * 1000.0;
        if let Some(l) = mark() {
            marks.push((t, l));
        }
        if mem.read_into(addr, &mut buf).is_ok() {
            for (i, st) in stats.iter_mut().enumerate() {
                let v = i32::from_le_bytes(buf[i * 4..i * 4 + 4].try_into().unwrap());
                match st {
                    None => {
                        *st = Some(FieldStats {
                            offset: i * 4,
                            changes: 0,
                            first: v,
                            last: v,
                            min: v,
                            max: v,
                            distinct: [v].into(),
                            change_times: vec![],
                        })
                    }
                    Some(s) => {
                        if s.last != v {
                            s.changes += 1;
                            s.change_times.push(t);
                            s.last = v;
                            s.min = s.min.min(v);
                            s.max = s.max.max(v);
                            if s.distinct.len() < 32 {
                                s.distinct.insert(v);
                            }
                        }
                    }
                }
            }
        }
        std::thread::sleep(period);
    }
    (stats.into_iter().flatten().collect(), marks)
}
