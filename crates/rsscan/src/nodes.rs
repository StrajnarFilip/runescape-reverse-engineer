//! RS3 var hash-table nodes.
//!
//! Observed layout (build 6ada3f59): 0x50-byte nodes, often contiguous in pools.
//! ```text
//! +0x00 u64  tag (7 for int vars)
//! +0x20 u64  hash
//! +0x28 ptr  link
//! +0x30 ptr  -> node+0x28 (self link)   <- signature used here
//! +0x38 i32  key (var id)
//! +0x40 i32  value
//! +0x48 ptr
//! ```

use crate::scan::for_each_chunk;
use rsmem::{MemRead, RegionInfo};

pub const KEY_OFF: u64 = 0x38;
pub const VALUE_OFF: u64 = 0x40;
const LINK_OFF: usize = 0x28;
const SELF_OFF: usize = 0x30;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VarNode {
    pub node: u64,
    pub tag: u64,
    pub key: i32,
}

impl VarNode {
    pub fn value_addr(&self) -> u64 {
        self.node + VALUE_OFF
    }
}

/// Scan for nodes whose `+0x30` points at their own `+0x28` and whose key is in range.
pub fn find_var_nodes<M: MemRead>(mem: &M, regions: &[RegionInfo], max_key: i32, max_tag: u64) -> Vec<VarNode> {
    let mut out = Vec::new();
    for_each_chunk(mem, regions, |base, bytes| {
        let mut i = ((8 - base % 8) % 8) as usize;
        while i + 0x48 <= bytes.len() {
            let n = base + i as u64;
            let link = u64::from_le_bytes(bytes[i + SELF_OFF..i + SELF_OFF + 8].try_into().unwrap());
            if link == n + LINK_OFF as u64 {
                let key = i32::from_le_bytes(bytes[i + 0x38..i + 0x3C].try_into().unwrap());
                let tag = u64::from_le_bytes(bytes[i..i + 8].try_into().unwrap());
                if (0..=max_key).contains(&key) && tag <= max_tag {
                    out.push(VarNode { node: n, tag, key });
                }
            }
            i += 8;
        }
    });
    out.sort_by_key(|n| (n.key, n.node));
    out
}

fn ptr_like(v: u64) -> bool {
    (0x10000..0x7FFF_FFFF_FFFF).contains(&v)
}

/// Looser match that also catches nodes chained to others in a bucket:
/// tag == `tag`, key in range with zero upper half, and pointer-like links at +0x28/+0x30.
pub fn find_var_nodes_loose<M: MemRead>(mem: &M, regions: &[RegionInfo], max_key: i32, tag: u64) -> Vec<VarNode> {
    let mut out = Vec::new();
    for_each_chunk(mem, regions, |base, bytes| {
        let mut i = ((8 - base % 8) % 8) as usize;
        while i + 0x48 <= bytes.len() {
            let t = u64::from_le_bytes(bytes[i..i + 8].try_into().unwrap());
            if t == tag {
                let link = u64::from_le_bytes(bytes[i + LINK_OFF..i + LINK_OFF + 8].try_into().unwrap());
                let back = u64::from_le_bytes(bytes[i + SELF_OFF..i + SELF_OFF + 8].try_into().unwrap());
                let key = i32::from_le_bytes(bytes[i + 0x38..i + 0x3C].try_into().unwrap());
                let hi = u32::from_le_bytes(bytes[i + 0x3C..i + 0x40].try_into().unwrap());
                if hi == 0 && (0..=max_key).contains(&key) && ptr_like(link) && ptr_like(back) {
                    out.push(VarNode { node: base + i as u64, tag: t, key });
                }
            }
            i += 8;
        }
    });
    out.sort_by_key(|n| (n.key, n.node));
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use rsmem::FakeMem;

    #[test]
    fn finds_self_linked_nodes() {
        let mut m = FakeMem::new();
        m.add(0x10000, vec![0; 0x200]);
        for (k, node) in [(13537i32, 0x10000u64), (13747, 0x10050)] {
            m.write(node, 7u64);
            m.write(node + 0x30, node + 0x28);
            m.write(node + 0x38, k);
            m.write(node + 0x40, 9900i32);
        }
        let nodes = find_var_nodes(&m, &m.region_infos(), 200_000, 64);
        assert_eq!(nodes.iter().map(|n| n.key).collect::<Vec<_>>(), vec![13537, 13747]);
        assert_eq!(nodes[0].value_addr(), 0x10040);
    }
}
