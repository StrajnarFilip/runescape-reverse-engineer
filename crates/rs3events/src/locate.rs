//! Locating keyed containers (sorted var tables, hash-table var nodes) by scanning memory.
//! Scans run on a background thread with their own process handle; results arrive by channel.

use rsmem::{MemRead, Process, RegionInfo, RegionKind};
use rsoffsets::LocatorDef;
use std::collections::HashMap;

const CHUNK: usize = 1 << 20;
const MAX_KEY: i64 = 1 << 24;

#[derive(Debug, Clone, PartialEq)]
pub enum Located {
    /// Sorted table at `start` with `count` entries; `entries` maps requested keys to entry addresses.
    Table { start: u64, count: u64, entries: HashMap<i64, u64> },
    /// Node addresses for the requested keys.
    Nodes { entries: HashMap<i64, u64> },
    /// Objects carrying `vtable` at +0 (key 0 = first object found).
    Objects { vtable: u64, entries: HashMap<i64, u64>, count: usize },
}

pub fn locate_vtable_objects<M: MemRead>(mem: &M, regions: &[RegionInfo], vtable: u64, pick_by_mode_at: Option<u32>) -> Result<Located, String> {
    let mut objs: Vec<u64> = Vec::new();
    for_each_chunk(mem, regions, |base, bytes| {
        let mut i = ((8 - base % 8) % 8) as usize;
        while i + 8 <= bytes.len() {
            if u64::from_le_bytes(bytes[i..i + 8].try_into().unwrap()) == vtable {
                objs.push(base + i as u64);
                if objs.len() >= 8192 {
                    return false;
                }
            }
            i += 8;
        }
        true
    });
    if objs.is_empty() {
        return Err(format!("no object with vtable {vtable:#x}"));
    }
    let chosen = match pick_by_mode_at {
        None => objs[0],
        Some(off) => {
            let vals: Vec<(u64, u32)> = objs.iter().filter_map(|o| mem.read::<u32>(o + off as u64).ok().map(|v| (*o, v))).collect();
            let mut counts: HashMap<u32, usize> = HashMap::new();
            for (_, v) in &vals {
                if *v != 0 {
                    *counts.entry(*v).or_default() += 1;
                }
            }
            let (mode, n) = counts.into_iter().max_by_key(|(v, n)| (*n, *v)).ok_or("all candidate fields are zero")?;
            if n < 2 {
                return Err(format!("{} objects but no shared non-zero value at +{off:#x}", objs.len()));
            }
            vals.into_iter().find(|(_, v)| *v == mode).map(|(o, _)| o).unwrap()
        }
    };
    Ok(Located::Objects { vtable, entries: HashMap::from([(0, chosen)]), count: objs.len() })
}

#[derive(Debug)]
pub struct LocateResult {
    pub locator: String,
    pub result: Result<Located, String>,
    pub secs: f64,
}

fn scan_regions(proc: &Process) -> Vec<RegionInfo> {
    proc.regions().into_iter().filter(|r| r.readable && r.writable && r.kind != RegionKind::Mapped).collect()
}

fn for_each_chunk<M: MemRead>(mem: &M, regions: &[RegionInfo], mut f: impl FnMut(u64, &[u8]) -> bool) {
    let mut buf = vec![0u8; CHUNK];
    for r in regions {
        let mut off = 0u64;
        while off < r.size {
            let n = (CHUNK as u64).min(r.size - off) as usize;
            if mem.read_into(r.base + off, &mut buf[..n]).is_ok() && !f(r.base + off, &buf[..n]) {
                return;
            }
            off += n as u64;
        }
    }
}

fn key_at<M: MemRead>(mem: &M, entry: u64, key_off: u32) -> Option<i64> {
    mem.read::<i32>(entry + key_off as u64).ok().map(|k| k as i64)
}

/// Binary search a sorted table for `key`.
pub fn table_lookup<M: MemRead>(mem: &M, start: u64, count: u64, stride: u32, key_off: u32, key: i64) -> Option<u64> {
    let (mut lo, mut hi) = (0u64, count);
    while lo < hi {
        let mid = (lo + hi) / 2;
        let e = start + mid * stride as u64;
        let k = key_at(mem, e, key_off)?;
        match k.cmp(&key) {
            std::cmp::Ordering::Equal => return Some(e),
            std::cmp::Ordering::Less => lo = mid + 1,
            std::cmp::Ordering::Greater => hi = mid,
        }
    }
    None
}

/// Walk a strictly increasing run of keys around `entry`. Returns (start, count).
fn walk_run<M: MemRead>(mem: &M, entry: u64, stride: u32, key_off: u32) -> (u64, u64) {
    let s = stride as u64;
    let k0 = key_at(mem, entry, key_off).unwrap_or(-1);
    let mut start = entry;
    let mut prev = k0;
    for _ in 0..200_000 {
        let e = start.wrapping_sub(s);
        match key_at(mem, e, key_off) {
            Some(k) if k >= 0 && k < prev => {
                start = e;
                prev = k;
            }
            _ => break,
        }
    }
    let mut end = entry + s;
    let mut prev = k0;
    for _ in 0..200_000 {
        match key_at(mem, end, key_off) {
            Some(k) if k > prev && k < MAX_KEY => {
                prev = k;
                end += s;
            }
            _ => break,
        }
    }
    (start, (end - start) / s)
}

pub fn locate_sorted_table<M: MemRead>(mem: &M, regions: &[RegionInfo], def: &LocatorDef, keys: &[i64]) -> Result<Located, String> {
    let LocatorDef::SortedTable { stride, key_off, value_off, probe_keys, min_entries, max_entries } = def else {
        return Err("not a sorted table".into());
    };
    // Definition/index tables store a function of the key (key or key+1); skip those.
    let looks_like_index = |start: u64, count: u64| -> bool {
        let n = count.min(64);
        let same = (0..n)
            .filter(|i| {
                let e = start + i * *stride as u64;
                match (key_at(mem, e, *key_off), mem.read::<i32>(e + *value_off as u64).ok()) {
                    (Some(k), Some(v)) => v as i64 == k || v as i64 == k + 1,
                    _ => false,
                }
            })
            .count() as u64;
        n > 0 && same * 10 >= n * 8
    };
    let probe = *probe_keys.first().ok_or("probe_keys is empty")? as i32;
    let ko = *key_off as usize;
    let mut cands: Vec<u64> = Vec::new();
    for_each_chunk(mem, regions, |base, bytes| {
        let mut i = ((8 - base % 8) % 8) as usize;
        while i + ko + 4 <= bytes.len() {
            if i32::from_le_bytes(bytes[i + ko..i + ko + 4].try_into().unwrap()) == probe {
                cands.push(base + i as u64);
                if cands.len() > 200_000 {
                    return false;
                }
            }
            i += 8;
        }
        true
    });
    let mut best: Option<(u64, u64)> = None;
    for c in cands {
        // Cheap pre-check: neighbours must be ordered around the probe.
        let (Some(p), Some(n)) = (key_at(mem, c.wrapping_sub(*stride as u64), *key_off), key_at(mem, c + *stride as u64, *key_off)) else {
            continue;
        };
        if !(p < probe as i64 && n > probe as i64 && p >= 0) {
            continue;
        }
        let (start, count) = walk_run(mem, c, *stride, *key_off);
        if count < *min_entries as u64 || max_entries.is_some_and(|m| count > m as u64) || looks_like_index(start, count) {
            continue;
        }
        if !probe_keys.iter().all(|k| table_lookup(mem, start, count, *stride, *key_off, *k).is_some()) {
            continue;
        }
        if best.is_none_or(|b| count > b.1) {
            best = Some((start, count));
        }
    }
    let (start, count) = best.ok_or("no sorted table containing all probe keys found")?;
    let entries = keys.iter().filter_map(|k| table_lookup(mem, start, count, *stride, *key_off, *k).map(|e| (*k, e))).collect();
    Ok(Located::Table { start, count, entries })
}

pub fn locate_hash_nodes<M: MemRead>(mem: &M, regions: &[RegionInfo], def: &LocatorDef, keys: &[i64]) -> Result<Located, String> {
    let LocatorDef::HashNodes { tag, key_off, link_off, self_off, .. } = def else {
        return Err("not hash nodes".into());
    };
    let (ko, lo, so) = (*key_off as usize, *link_off as u64, *self_off as usize);
    let need = ko.max(so + 8).max(8);
    let mut entries: HashMap<i64, u64> = HashMap::new();
    for_each_chunk(mem, regions, |base, bytes| {
        let mut i = ((8 - base % 8) % 8) as usize;
        while i + need + 4 <= bytes.len() {
            let n = base + i as u64;
            if u64::from_le_bytes(bytes[i + so..i + so + 8].try_into().unwrap()) == n + lo
                && u64::from_le_bytes(bytes[i..i + 8].try_into().unwrap()) == *tag
            {
                let k = i32::from_le_bytes(bytes[i + ko..i + ko + 4].try_into().unwrap()) as i64;
                if keys.contains(&k) {
                    entries.entry(k).or_insert(n);
                    if entries.len() == keys.len() {
                        return false;
                    }
                }
            }
            i += 8;
        }
        true
    });
    if entries.is_empty() {
        return Err("no nodes with the requested keys found".into());
    }
    Ok(Located::Nodes { entries })
}

/// Run a locate on a fresh handle (called from a background thread).
pub fn run_locate(pid: u32, name: String, def: LocatorDef, keys: Vec<i64>) -> LocateResult {
    let t = std::time::Instant::now();
    let result = match Process::open(pid) {
        Err(e) => Err(e.to_string()),
        Ok(p) => {
            let regions = scan_regions(&p);
            match &def {
                LocatorDef::SortedTable { .. } => locate_sorted_table(&p, &regions, &def, &keys),
                LocatorDef::HashNodes { .. } => locate_hash_nodes(&p, &regions, &def, &keys),
                LocatorDef::VtableObject { vtable_rva, module, pick_by_mode_at } => {
                    let base = match module {
                        None => p.main_module().map(|m| m.base).map_err(|e| e.to_string()),
                        Some(n) => p
                            .modules()
                            .map_err(|e| e.to_string())
                            .and_then(|ms| ms.into_iter().find(|m| m.name.eq_ignore_ascii_case(n)).map(|m| m.base).ok_or(format!("module {n} not loaded"))),
                    };
                    base.and_then(|b| locate_vtable_objects(&p, &regions, b + vtable_rva, *pick_by_mode_at))
                }
            }
        }
    };
    LocateResult { locator: name, result, secs: t.elapsed().as_secs_f64() }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rsmem::FakeMem;

    #[test]
    fn sorted_table_and_nodes() {
        let mut m = FakeMem::new();
        m.add(0x10000, vec![0xAA; 0x4000]);
        // table of 300 entries, keys 0,3,6,... stride 0x30, value = key*2 at +8
        let start = 0x10100u64;
        for i in 0..300u64 {
            m.write(start + i * 0x30, (i * 3) as i32);
            m.write(start + i * 0x30 + 4, 0u32);
            m.write(start + i * 0x30 + 8, (i * 6) as i32);
        }
        // terminate the run with a smaller key
        m.write(start + 300 * 0x30, -5i32);
        m.write(start - 0x30, 100_000_000i32);
        let def = LocatorDef::SortedTable { stride: 0x30, key_off: 0, value_off: 8, probe_keys: vec![450, 3], min_entries: 100, max_entries: Some(4096) };
        let regions = m.region_infos();
        match locate_sorted_table(&m, &regions, &def, &[450, 897, 7]).unwrap() {
            Located::Table { start: s, count, entries } => {
                assert_eq!(s, start);
                assert_eq!(count, 300);
                assert_eq!(entries[&450], start + 150 * 0x30);
                assert_eq!(entries[&897], start + 299 * 0x30);
                assert!(!entries.contains_key(&7));
            }
            other => panic!("{other:?}"),
        }
        // hash node
        let n = 0x13000u64;
        m.write(n, 7u64);
        m.write(n + 0x30, n + 0x28);
        m.write(n + 0x38, 13537i32);
        m.write(n + 0x40, 9900i32);
        let hd = LocatorDef::HashNodes { tag: 7, key_off: 0x38, value_off: 0x40, link_off: 0x28, self_off: 0x30 };
        match locate_hash_nodes(&m, &regions, &hd, &[13537]).unwrap() {
            Located::Nodes { entries } => assert_eq!(entries[&13537], n),
            other => panic!("{other:?}"),
        }
    }
}
