//! Resolve the live NPC manager's active server indices to entity pointers.

use rsmem::MemRead;
use rsoffsets::NpcIndexDef;

pub(crate) fn read_index(
    mem: &impl MemRead,
    manager: u64,
    def: &NpcIndexDef,
    vtable: u64,
    ids: &mut [u8],
    buckets: &mut [u8],
    entities: &mut Vec<(u32, u64)>,
) -> Option<(u32, u32)> {
    if mem.read_ptr(manager).ok()? != vtable {
        return None;
    }
    let cycle = mem.read::<u32>(manager + u64::from(def.cycle_offset)).ok()?;
    let active_count = mem.read::<u32>(manager + u64::from(def.active_count_offset)).ok()?;
    let ids = ids.get_mut(..(active_count as usize).checked_mul(4)?)?;
    entities.clear();
    if ids.is_empty() {
        return Some((cycle, active_count));
    }
    let [table, count] = mem.read::<[u64; 2]>(manager + u64::from(def.buckets_offset)).ok()?;
    let buckets = buckets.get_mut(..usize::try_from(count).ok()?.checked_mul(8)?)?;
    if table == 0 || buckets.is_empty() {
        return None;
    }
    mem.read_into(manager + u64::from(def.active_ids_offset), ids).ok()?;
    mem.read_into(table, buckets).ok()?;

    for key in ids.chunks_exact(4) {
        let key = u32::from_le_bytes(key.try_into().unwrap());
        let slot = (u64::from(key) % count) as usize * 8;
        let mut node = u64::from_le_bytes(buckets[slot..slot + 8].try_into().unwrap());
        // Limit the walk so a corrupt or transient chain cannot stall the sampler.
        let mut entity = 0;
        for _ in 0..64 {
            if node == 0 {
                break;
            }
            if mem.read::<u32>(node).ok()? == key {
                entity = mem.read_ptr(node.checked_add(u64::from(def.node_entity_offset))?).ok()?;
                break;
            }
            node = mem.read_ptr(node.checked_add(u64::from(def.node_next_offset))?).ok()?;
        }
        if entity == 0 {
            return None;
        }
        entities.push((key, entity));
    }
    Some((cycle, active_count))
}

#[cfg(test)]
mod tests {
    use super::*;
    use rsmem::FakeMem;

    #[test]
    fn resolves_active_keys_through_collisions_and_rejects_incomplete_polls() {
        let mut mem = FakeMem::new();
        mem.add(0x1000, vec![0; 0x200]);
        let def = NpcIndexDef {
            vtable_rva: 0,
            buckets_offset: 0x10,
            max_buckets: 2,
            active_ids_offset: 0x20,
            active_count_offset: 0x28,
            cycle_offset: 0x2c,
            node_entity_offset: 0x10,
            node_next_offset: 0x18,
        };
        mem.write(0x1000, 0x2000u64);
        mem.write(0x1010, [0x1040u64, 2]);
        mem.write(0x1020, [1u32, 5]);
        mem.write(0x1028, 2u32);
        mem.write(0x102c, 42u32);
        mem.write(0x1048, 0x1080u64);
        // Key 3 is reachable in the index but is absent from the active list.
        mem.write(0x1080, [3u64, 0, 0x3000, 0x10a0]);
        mem.write(0x10a0, [1u64, 0, 0x4000, 0x10c0]);
        mem.write(0x10c0, [5u64, 0, 0x5000, 0]);
        let mut ids = [0; 8];
        let mut buckets = [0; 16];
        let mut entities = Vec::new();
        let mut poll = |mem: &FakeMem| {
            let cycle = read_index(mem, 0x1000, &def, 0x2000, &mut ids, &mut buckets, &mut entities)?;
            Some((cycle, entities.clone()))
        };
        assert_eq!(poll(&mem), Some(((42, 2), vec![(1, 0x4000), (5, 0x5000)])));
        mem.write(0x10b8, 0x1080u64); // corrupt cyclic chain cannot resolve key 5
        assert_eq!(poll(&mem), None);
        mem.write(0x1028, 3u32); // impossible count for this layout
        assert_eq!(poll(&mem), None);
        mem.write(0x1028, 0u32);
        assert_eq!(poll(&mem), Some(((42, 0), vec![])));
        mem.write(0x1000, 0u64);
        assert_eq!(poll(&mem), None);
    }
}
