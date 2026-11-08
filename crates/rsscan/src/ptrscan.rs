//! Reverse pointer scan: find static paths `module+off -> [chain]` that lead to a target address.
//!
//! Level by level, stream all writable memory looking for qwords `v` with
//! `t - max_offset <= v <= t` for a current target `t`. A hit at a static
//! (module image) address ends a path; other hits become next-level targets.

use crate::scan::for_each_chunk;
use rsmem::{MemRead, ModuleInfo, RegionInfo};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone)]
pub struct PtrScanOpts {
    pub max_depth: usize,
    pub max_offset: u64,
    /// Max targets carried to the next level.
    pub max_frontier: usize,
    pub max_results: usize,
}

impl Default for PtrScanOpts {
    fn default() -> Self {
        Self { max_depth: 4, max_offset: 0x1000, max_frontier: 20_000, max_results: 500 }
    }
}

/// `addr = module.base + module_offset; for off in chain { addr = read_ptr(addr) + off }`
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PtrPath {
    pub module: String,
    pub module_offset: u64,
    pub chain: Vec<i64>,
}

impl PtrPath {
    pub fn resolve<M: MemRead>(&self, mem: &M, modules: &[ModuleInfo]) -> Option<u64> {
        let m = modules.iter().find(|m| m.name.eq_ignore_ascii_case(&self.module))?;
        mem.follow_chain(m.base + self.module_offset, &self.chain).ok()
    }

    pub fn display(&self) -> String {
        let chain: Vec<String> = self.chain.iter().map(|o| format!("{o:#x}")).collect();
        format!("{}+{:#x} -> [{}]", self.module, self.module_offset, chain.join(", "))
    }

    pub fn toml_snippet(&self, name: &str) -> String {
        let chain: Vec<String> = self.chain.iter().map(|o| format!("{o:#x}")).collect();
        format!(
            "[bases.{name}]\nmodule = \"{}\"\nmodule_offset = {:#x}\nchain = [{}]\n",
            self.module,
            self.module_offset,
            chain.join(", ")
        )
    }
}

/// Every 8-aligned qword in `regions` whose value lies in `[target - max_offset, target]`.
/// Returns `(address holding the pointer, pointer value)`.
pub fn refs<M: MemRead>(mem: &M, regions: &[RegionInfo], target: u64, max_offset: u64, limit: usize) -> Vec<(u64, u64)> {
    let lo = target.saturating_sub(max_offset);
    let mut out = Vec::new();
    for_each_chunk(mem, regions, |base, bytes| {
        if out.len() >= limit {
            return;
        }
        let mut i = ((8 - base % 8) % 8) as usize;
        while i + 8 <= bytes.len() {
            let v = u64::from_le_bytes(bytes[i..i + 8].try_into().unwrap());
            if v >= lo && v <= target {
                out.push((base + i as u64, v));
                if out.len() >= limit {
                    return;
                }
            }
            i += 8;
        }
    });
    out
}

struct Node {
    parent: usize,
    off: u64,
}

const ROOT: usize = usize::MAX;

pub fn ptrscan<M: MemRead>(
    mem: &M,
    regions: &[RegionInfo],
    modules: &[ModuleInfo],
    target: u64,
    opts: &PtrScanOpts,
    progress: &mut dyn FnMut(&str),
) -> Vec<PtrPath> {
    let static_of = |a: u64| modules.iter().find(|m| m.contains(a));
    let mut results: Vec<PtrPath> = Vec::new();
    if let Some(m) = static_of(target) {
        results.push(PtrPath { module: m.name.clone(), module_offset: target - m.base, chain: vec![] });
    }
    let mut nodes: Vec<Node> = Vec::new();
    // frontier: (target address, node index or ROOT)
    let mut frontier: Vec<(u64, usize)> = vec![(target, ROOT)];
    let chain_of = |nodes: &Vec<Node>, mut idx: usize, first_off: u64| -> Vec<i64> {
        let mut chain = vec![first_off as i64];
        while idx != ROOT {
            chain.push(nodes[idx].off as i64);
            idx = nodes[idx].parent;
        }
        chain
    };
    for depth in 1..=opts.max_depth {
        if frontier.is_empty() || results.len() >= opts.max_results {
            break;
        }
        frontier.sort_unstable_by_key(|f| f.0);
        let lo = frontier[0].0.saturating_sub(opts.max_offset);
        let hi = frontier[frontier.len() - 1].0;
        let mut next: Vec<(u64, usize, u64)> = Vec::new(); // (addr, parent frontier idx, off)
        let mut found_static: Vec<(String, u64, usize, u64)> = Vec::new();
        for_each_chunk(mem, regions, |base, bytes| {
            let mut i = ((8 - base % 8) % 8) as usize;
            while i + 8 <= bytes.len() {
                let v = u64::from_le_bytes(bytes[i..i + 8].try_into().unwrap());
                if v >= lo && v <= hi {
                    let a = base + i as u64;
                    let mut j = frontier.partition_point(|f| f.0 < v);
                    while j < frontier.len() && frontier[j].0 - v <= opts.max_offset {
                        let off = frontier[j].0 - v;
                        if a != frontier[j].0 {
                            match static_of(a) {
                                Some(m) => found_static.push((m.name.clone(), a - m.base, j, off)),
                                None => next.push((a, j, off)),
                            }
                        }
                        j += 1;
                    }
                }
                i += 8;
            }
        });
        for (module, moff, j, off) in found_static {
            let parent = frontier[j].1;
            results.push(PtrPath { module, module_offset: moff, chain: chain_of(&nodes, parent, off) });
        }
        // Next level: dedupe by address keeping the smallest offset, prefer small offsets.
        next.sort_unstable_by_key(|n| (n.0, n.2));
        next.dedup_by_key(|n| n.0);
        next.sort_unstable_by_key(|n| n.2);
        next.truncate(opts.max_frontier);
        progress(&format!("depth {depth}: {} static paths so far, {} targets for next level", results.len(), next.len()));
        let mut new_frontier = Vec::with_capacity(next.len());
        for (a, j, off) in next {
            nodes.push(Node { parent: frontier[j].1, off });
            new_frontier.push((a, nodes.len() - 1));
        }
        frontier = new_frontier;
    }
    results.sort_by_key(|p| (p.chain.len(), p.chain.iter().map(|o| o.unsigned_abs()).sum::<u64>()));
    results.dedup();
    results.truncate(opts.max_results);
    results
}

#[cfg(test)]
mod tests {
    use super::*;
    use rsmem::FakeMem;

    #[test]
    fn finds_two_level_path() {
        let mut m = FakeMem::new();
        m.add(0x1000, vec![0; 0x1000]); // "module"
        m.add(0x10_0000, vec![0; 0x1000]); // heap object
        m.add(0x20_0000, vec![0; 0x1000]); // heap array
        m.write(0x1100u64, 0x10_0000u64); // static -> object
        m.write(0x10_0040u64, 0x20_0000u64); // object+0x40 -> array
        let target = 0x20_0000u64 + 0x10;
        let modules = vec![ModuleInfo { name: "game.exe".into(), base: 0x1000, size: 0x1000, path: String::new() }];
        let regions = m.region_infos();
        let paths = ptrscan(&m, &regions, &modules, target, &PtrScanOpts::default(), &mut |_| {});
        let want = PtrPath { module: "game.exe".into(), module_offset: 0x100, chain: vec![0x40, 0x10] };
        assert!(paths.contains(&want), "{paths:?}");
        assert_eq!(want.resolve(&m, &modules), Some(target));
    }
}
