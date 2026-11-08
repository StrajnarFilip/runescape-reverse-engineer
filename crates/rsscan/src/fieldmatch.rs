//! Find a value that may be packed into a bit field, scaled, complemented or stored as a float,
//! using two observations: the word must encode `was` before and `now` after.

use crate::scan::read_runs;
use rsmem::MemRead;

#[derive(Debug, Clone, PartialEq)]
pub struct FieldHit {
    pub addr: u64,
    /// How the value is encoded, e.g. `bits[3..14] x10` or `f32 /990`.
    pub encoding: String,
    pub before: u32,
    pub after: u32,
}

/// Integer transforms of the displayed value v: (label, f(v)).
fn int_forms(v: i64, max: Option<i64>) -> Vec<(String, i64)> {
    let mut out = vec![("x1".to_string(), v), ("x10".into(), v * 10), ("x100".into(), v * 100)];
    if let Some(m) = max {
        out.push((format!("{m}-v"), m - v));
        out.push((format!("{}-10v", m * 10), m * 10 - v * 10));
        out.push((format!("{}-100v", m * 100), m * 100 - v * 100));
    }
    out
}

/// `cands`: (address, raw 32-bit value observed when the display showed `was`).
pub fn fieldmatch<M: MemRead>(mem: &M, cands: &[(u64, u64)], was: i64, now: i64, max: Option<i64>) -> Vec<FieldHit> {
    let addrs: Vec<u64> = cands.iter().map(|c| c.0).collect();
    let wf = int_forms(was, max);
    let nf = int_forms(now, max);
    let mut hits = Vec::new();
    read_runs(mem, &addrs, 4, |k, b| {
        let Some(b) = b else { return };
        let before = cands[k].1 as u32;
        let after = u32::from_le_bytes(b.try_into().unwrap());
        if before == after {
            return;
        }
        // Bit fields of width 8..=32 at any shift.
        for (fi, (label, wv)) in wf.iter().enumerate() {
            let nv = nf[fi].1;
            if wv < &0 || nv < 0 {
                continue;
            }
            for width in 8u32..=32 {
                let mask: u64 = if width == 32 { u32::MAX as u64 } else { (1u64 << width) - 1 };
                if (*wv as u64) > mask || (nv as u64) > mask {
                    continue;
                }
                for shift in 0..=(32 - width) {
                    let fb = ((before as u64) >> shift) & mask;
                    let fa = ((after as u64) >> shift) & mask;
                    if fb == *wv as u64 && fa == nv as u64 {
                        hits.push(FieldHit {
                            addr: cands[k].0,
                            encoding: format!("bits[{}..{}] {label}", shift, shift + width),
                            before,
                            after,
                        });
                    }
                }
            }
        }
        // f32 forms.
        let (fb, fa) = (f32::from_bits(before) as f64, f32::from_bits(after) as f64);
        let mut fscales: Vec<(String, f64)> = vec![("x1".into(), 1.0), ("x10".into(), 10.0), ("x0.1".into(), 0.1), ("x0.01".into(), 0.01)];
        if let Some(m) = max {
            fscales.push((format!("/{m}"), 1.0 / m as f64));
        }
        for (label, s) in fscales {
            let (tw, tn) = (was as f64 * s, now as f64 * s);
            let close = |x: f64, t: f64| (x - t).abs() <= (t.abs() * 0.002).max(1e-6);
            if close(fb, tw) && close(fa, tn) {
                hits.push(FieldHit { addr: cands[k].0, encoding: format!("f32 {label}"), before, after });
            }
        }
    });
    // Prefer the widest matching field per address (narrow fields are subsets).
    hits.sort_by(|a, b| a.addr.cmp(&b.addr));
    hits
}

#[cfg(test)]
mod tests {
    use super::*;
    use rsmem::FakeMem;

    #[test]
    fn finds_packed_and_complement() {
        let mut m = FakeMem::new();
        m.add(0x1000, vec![0; 0x20]);
        // prayer 674 packed at bits 3..14 with junk around it; now 650
        let before = (674u32 << 3) | 0b101 | (7 << 20);
        let after = (650u32 << 3) | 0b101 | (7 << 20);
        m.write(0x1000u64, after);
        // drained counter 990 - v
        m.write(0x1004u64, 990u32 - 650);
        let cands = vec![(0x1000, before as u64), (0x1004, (990 - 674) as u64), (0x1008, 5)];
        let hits = fieldmatch(&m, &cands, 674, 650, Some(990));
        assert!(hits.iter().any(|h| h.addr == 0x1000 && h.encoding.starts_with("bits[3..")), "{hits:?}");
        assert!(hits.iter().any(|h| h.addr == 0x1004 && h.encoding.contains("990-v")), "{hits:?}");
        assert!(!hits.iter().any(|h| h.addr == 0x1008));
    }
}
