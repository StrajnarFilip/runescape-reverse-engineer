//! High-rate sampling of a set of addresses with user "marks" (hotkeys / Enter)
//! and ranking of which addresses change right after the marks.

use crate::scan::{f64_of, read_runs};
use rsmem::MemRead;
use rsoffsets::ValType;
use std::io::{BufRead, Write};
use std::sync::mpsc;
use std::time::{Duration, Instant};

pub struct WatchOpts {
    pub hz: f64,
    pub duration: Duration,
    /// (virtual key, label) polled globally each pass.
    pub mark_keys: Vec<(i32, String)>,
    /// Read marks from stdin lines too (each line = one mark, text = label).
    pub stdin_marks: bool,
    /// Print each change live when watching at most this many addresses.
    pub live_limit: usize,
    pub log: Option<std::path::PathBuf>,
}

#[derive(Debug, Clone)]
pub struct Change {
    pub t_ms: f64,
    pub idx: usize,
    pub old: f64,
    pub new: f64,
}

#[derive(Debug, Default)]
pub struct WatchResult {
    pub changes: Vec<Change>,
    pub marks: Vec<(f64, String)>,
    pub passes: u64,
    pub elapsed: Duration,
    pub last: Vec<Option<f64>>,
}

#[derive(Debug, Clone)]
pub struct Ranked {
    pub idx: usize,
    pub changes: usize,
    /// Marks followed by a change within the window.
    pub hits: usize,
    /// Changes not following any mark.
    pub extra: usize,
    pub score: f64,
}

fn spawn_stdin_marks() -> mpsc::Receiver<String> {
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let stdin = std::io::stdin();
        for line in stdin.lock().lines() {
            match line {
                Ok(l) => {
                    if tx.send(l).is_err() {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
    });
    rx
}

/// `key_state(vk)` reports whether a key is down (injected for testability).
pub fn watch<M: MemRead>(
    mem: &M,
    ty: ValType,
    addrs: &[u64],
    opts: &WatchOpts,
    key_state: &dyn Fn(i32) -> bool,
    mut out: impl FnMut(&str),
) -> WatchResult {
    let mut order: Vec<usize> = (0..addrs.len()).collect();
    order.sort_by_key(|&i| addrs[i]);
    let sorted: Vec<u64> = order.iter().map(|&i| addrs[i]).collect();
    let mut last: Vec<Option<f64>> = vec![None; addrs.len()];
    let mut res = WatchResult::default();
    let mut log = opts.log.as_ref().and_then(|p| std::fs::File::create(p).ok()).map(std::io::BufWriter::new);
    let stdin_rx = opts.stdin_marks.then(spawn_stdin_marks);
    let mut key_was: Vec<bool> = vec![false; opts.mark_keys.len()];
    let period = Duration::from_secs_f64(1.0 / opts.hz.max(1.0));
    let live = addrs.len() <= opts.live_limit;
    let start = Instant::now();
    let mut next = start;
    let mut last_summary = start;
    let mut changes_since_summary = 0usize;
    while start.elapsed() < opts.duration {
        let now = Instant::now();
        let t_ms = now.duration_since(start).as_secs_f64() * 1000.0;
        for (k, (vk, label)) in opts.mark_keys.iter().enumerate() {
            let down = key_state(*vk);
            if down && !key_was[k] {
                res.marks.push((t_ms, label.clone()));
                out(&format!("[{t_ms:9.1} ms] MARK {label}"));
                if let Some(l) = log.as_mut() {
                    let _ = writeln!(l, r#"{{"t_ms":{t_ms:.3},"mark":{label:?}}}"#);
                }
            }
            key_was[k] = down;
        }
        if let Some(rx) = &stdin_rx {
            while let Ok(line) = rx.try_recv() {
                let label = if line.trim().is_empty() { "enter".to_string() } else { line.trim().to_string() };
                res.marks.push((t_ms, label.clone()));
                out(&format!("[{t_ms:9.1} ms] MARK {label}"));
            }
        }
        read_runs(mem, &sorted, ty.size(), |k, b| {
            let Some(b) = b else { return };
            let idx = order[k];
            let v = f64_of(ty, ty.raw(b));
            if let Some(o) = last[idx] {
                if o != v && !(o.is_nan() && v.is_nan()) {
                    res.changes.push(Change { t_ms, idx, old: o, new: v });
                    changes_since_summary += 1;
                    if live {
                        out(&format!("[{t_ms:9.1} ms] {:#x}: {} -> {}", addrs[idx], o, v));
                    }
                    if let Some(l) = log.as_mut() {
                        let _ = writeln!(l, r#"{{"t_ms":{t_ms:.3},"addr":"{:#x}","old":{o},"new":{v}}}"#, addrs[idx]);
                    }
                }
            }
            last[idx] = Some(v);
        });
        res.passes += 1;
        if !live && now.duration_since(last_summary) >= Duration::from_secs(1) {
            out(&format!("[{t_ms:9.1} ms] {changes_since_summary} changes in the last second"));
            changes_since_summary = 0;
            last_summary = now;
        }
        next += period;
        let now = Instant::now();
        if next > now {
            std::thread::sleep(next - now);
        } else {
            next = now;
        }
    }
    res.elapsed = start.elapsed();
    res.last = last;
    if let Some(l) = log.as_mut() {
        let _ = l.flush();
    }
    res
}

/// Rank addresses by how consistently they change within `window_ms` after a mark.
pub fn rank(res: &WatchResult, n: usize, window_ms: f64) -> Vec<Ranked> {
    let mut per: Vec<Vec<f64>> = vec![Vec::new(); n];
    for c in &res.changes {
        per[c.idx].push(c.t_ms);
    }
    let mut out: Vec<Ranked> = per
        .iter()
        .enumerate()
        .filter(|(_, ts)| !ts.is_empty())
        .map(|(idx, ts)| {
            let hits = res.marks.iter().filter(|(m, _)| ts.iter().any(|t| *t >= *m && *t <= m + window_ms)).count();
            let extra = ts.iter().filter(|t| !res.marks.iter().any(|(m, _)| **t >= *m && **t <= m + window_ms)).count();
            let score = if res.marks.is_empty() {
                0.0
            } else {
                hits as f64 / res.marks.len() as f64 - 0.5 * extra as f64 / ts.len() as f64
            };
            Ranked { idx, changes: ts.len(), hits, extra, score }
        })
        .collect();
    out.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal).then(b.hits.cmp(&a.hits)));
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ranking_prefers_mark_followers() {
        let res = WatchResult {
            changes: vec![
                Change { t_ms: 105.0, idx: 0, old: 0.0, new: 1.0 },
                Change { t_ms: 1110.0, idx: 0, old: 1.0, new: 2.0 },
                Change { t_ms: 50.0, idx: 1, old: 0.0, new: 1.0 },
                Change { t_ms: 500.0, idx: 1, old: 1.0, new: 2.0 },
                Change { t_ms: 900.0, idx: 1, old: 2.0, new: 3.0 },
            ],
            marks: vec![(100.0, "a".into()), (1100.0, "a".into())],
            ..Default::default()
        };
        let r = rank(&res, 3, 300.0);
        assert_eq!(r[0].idx, 0);
        assert_eq!(r[0].hits, 2);
        assert_eq!(r[0].extra, 0);
        assert!(r[0].score > r[1].score);
    }
}
