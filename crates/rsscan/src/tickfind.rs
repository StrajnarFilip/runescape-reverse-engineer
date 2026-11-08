//! Find the client's server-tick counter: a u32 that increments by 1 roughly every 600 ms.

use crate::scan::{for_each_chunk, read_runs};
use rsmem::{MemRead, RegionInfo};
use std::collections::HashMap;
use std::time::{Duration, Instant};

pub struct TickFindOpts {
    /// Gap between the two coarse reads.
    pub wait: Duration,
    /// How long to verify candidates at ~1 kHz.
    pub verify: Duration,
    /// Max bytes held in RAM per coarse group.
    pub group_bytes: u64,
    pub tick_ms: f64,
}

impl Default for TickFindOpts {
    fn default() -> Self {
        Self { wait: Duration::from_secs(3), verify: Duration::from_secs(6), group_bytes: 384 << 20, tick_ms: 600.0 }
    }
}

#[derive(Debug, Clone)]
pub struct TickCand {
    pub addr: u64,
    pub value: u32,
    pub increments: usize,
    pub mean_ms: f64,
    pub std_ms: f64,
}

fn snapshot<M: MemRead>(mem: &M, regions: &[RegionInfo]) -> Vec<(u64, Vec<u8>)> {
    let mut v = Vec::new();
    for_each_chunk(mem, regions, |base, bytes| v.push((base, bytes.to_vec())));
    v
}

/// Coarse pass: keep u32s whose delta over `wait` matches the expected tick count.
pub fn coarse<M: MemRead>(mem: &M, regions: &[RegionInfo], opts: &TickFindOpts, progress: &mut dyn FnMut(&str)) -> Vec<u64> {
    let expected = opts.wait.as_secs_f64() * 1000.0 / opts.tick_ms;
    let lo = (expected - 2.0).floor().max(1.0) as u32;
    let hi = (expected + 2.0).ceil() as u32;
    let mut groups: Vec<Vec<RegionInfo>> = vec![Vec::new()];
    let mut acc = 0u64;
    for r in regions {
        if acc + r.size > opts.group_bytes && !groups.last().unwrap().is_empty() {
            groups.push(Vec::new());
            acc = 0;
        }
        groups.last_mut().unwrap().push(r.clone());
        acc += r.size;
    }
    let mut out = Vec::new();
    for (gi, g) in groups.iter().enumerate() {
        progress(&format!("group {}/{}: {} MiB", gi + 1, groups.len(), g.iter().map(|r| r.size).sum::<u64>() >> 20));
        let a = snapshot(mem, g);
        std::thread::sleep(opts.wait);
        let b: HashMap<u64, Vec<u8>> = snapshot(mem, g).into_iter().collect();
        for (base, old) in &a {
            let Some(new) = b.get(base) else { continue };
            let n = old.len().min(new.len());
            let mut i = ((4 - base % 4) % 4) as usize;
            while i + 4 <= n {
                let o = u32::from_le_bytes(old[i..i + 4].try_into().unwrap());
                let v = u32::from_le_bytes(new[i..i + 4].try_into().unwrap());
                let d = v.wrapping_sub(o);
                if d >= lo && d <= hi {
                    out.push(base + i as u64);
                }
                i += 4;
            }
        }
    }
    out
}

/// Stage 1 for large sets: sampling passes may be slower than a tick, so judge by
/// average rate (monotonic, small steps, ~1 per tick) instead of exact +1 steps.
fn rate_filter<M: MemRead>(mem: &M, sorted: &[u64], opts: &TickFindOpts) -> Vec<u64> {
    let mut first: Vec<Option<(f64, u32)>> = vec![None; sorted.len()];
    let mut last: Vec<Option<(f64, u32)>> = vec![None; sorted.len()];
    let mut bad: Vec<bool> = vec![false; sorted.len()];
    let start = Instant::now();
    while start.elapsed() < opts.verify {
        let t = start.elapsed().as_secs_f64() * 1000.0;
        read_runs(mem, sorted, 4, |k, b| {
            let Some(b) = b else { return };
            let v = u32::from_le_bytes(b.try_into().unwrap());
            match last[k] {
                Some((_, o)) if v != o => {
                    let d = v.wrapping_sub(o);
                    if d > 5 {
                        bad[k] = true;
                    }
                    if first[k].is_none() {
                        first[k] = Some((t, v));
                    }
                    last[k] = Some((t, v));
                }
                None => last[k] = Some((t, v)),
                _ => {}
            }
        });
    }
    let need = (opts.verify.as_secs_f64() * 1000.0 / opts.tick_ms * 0.6).max(2.0) as u32;
    (0..sorted.len())
        .filter(|&k| !bad[k])
        .filter_map(|k| {
            let ((t0, v0), (t1, v1)) = (first[k]?, last[k]?);
            let steps = v1.wrapping_sub(v0);
            if steps < need {
                return None;
            }
            let per = (t1 - t0) / steps as f64;
            ((per - opts.tick_ms).abs() <= 0.15 * opts.tick_ms).then_some(sorted[k])
        })
        .collect()
}

/// Fine pass: keep counters with +1 steps every ~tick_ms. Large candidate sets
/// are first reduced by a rate check so the final sampling runs near 1 kHz.
pub fn verify<M: MemRead>(mem: &M, cands: &[u64], opts: &TickFindOpts) -> Vec<TickCand> {
    let mut sorted = cands.to_vec();
    sorted.sort_unstable();
    sorted.dedup();
    if sorted.len() > 2000 {
        sorted = rate_filter(mem, &sorted, opts);
    }
    let mut last: Vec<Option<u32>> = vec![None; sorted.len()];
    let mut steps: Vec<Vec<f64>> = vec![Vec::new(); sorted.len()];
    let mut bad: Vec<bool> = vec![false; sorted.len()];
    let start = Instant::now();
    while start.elapsed() < opts.verify {
        let t = start.elapsed().as_secs_f64() * 1000.0;
        read_runs(mem, &sorted, 4, |k, b| {
            let Some(b) = b else { return };
            let v = u32::from_le_bytes(b.try_into().unwrap());
            if let Some(o) = last[k] {
                if v != o {
                    if v.wrapping_sub(o) == 1 {
                        steps[k].push(t);
                    } else {
                        bad[k] = true;
                    }
                }
            }
            last[k] = Some(v);
        });
        std::thread::sleep(Duration::from_micros(800));
    }
    let need = (opts.verify.as_secs_f64() * 1000.0 / opts.tick_ms - 2.0).max(2.0) as usize;
    let mut out = Vec::new();
    for k in 0..sorted.len() {
        if bad[k] || steps[k].len() < need {
            continue;
        }
        let iv: Vec<f64> = steps[k].windows(2).map(|w| w[1] - w[0]).collect();
        if iv.is_empty() {
            continue;
        }
        let mean = iv.iter().sum::<f64>() / iv.len() as f64;
        let std = (iv.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / iv.len() as f64).sqrt();
        if (mean - opts.tick_ms).abs() <= 0.1 * opts.tick_ms && std <= 0.15 * opts.tick_ms {
            out.push(TickCand { addr: sorted[k], value: last[k].unwrap_or(0), increments: steps[k].len(), mean_ms: mean, std_ms: std });
        }
    }
    out.sort_by(|a, b| a.std_ms.partial_cmp(&b.std_ms).unwrap_or(std::cmp::Ordering::Equal));
    out
}
