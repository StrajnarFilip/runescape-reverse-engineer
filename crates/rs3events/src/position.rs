use rsmem::MemRead;
use rsmodel::TilePosition;
use rsoffsets::PlayerPositionLayout;

const FINE_PER_TILE: f32 = 512.0;
const WORLD_TILES: f32 = 12_800.0;

/// Follow the active local-player slot each time. Scene rebuilds can replace the entity.
fn local_player(mem: &impl MemRead, root: u64, layout: &PlayerPositionLayout) -> Option<u64> {
    let client = mem.read_ptr(root).ok()?;
    if client == 0 || mem.read::<i32>(client + layout.state).ok()? != layout.in_game_state {
        return None;
    }
    let context = mem.read_ptr(client + layout.player_context).ok()?;
    if context == 0 || mem.read_ptr(context).ok()? != client {
        return None;
    }
    let active = mem.read_ptr(context + layout.player_override).ok()?;
    if active != 0 {
        return Some(active);
    }

    let index = u64::try_from(mem.read::<i32>(context + layout.player_index).ok()?).ok()?;
    let table = mem.read_ptr(client + layout.player_table).ok()?;
    if table == 0 {
        return None;
    }
    let [start, end] = mem.read::<[u64; 2]>(table + layout.table_range).ok()?;
    if start == 0 || index >= end.checked_sub(start)? / 8 {
        return None;
    }
    let slot = mem.read_ptr(start + index * 8).ok()?;
    if slot == 0 {
        return None;
    }
    let player = mem.read_ptr(slot + layout.slot_entity).ok()?;
    (player != 0).then_some(player)
}

fn sample(mem: &impl MemRead, root: u64, layout: &PlayerPositionLayout) -> Option<(u64, TilePosition)> {
    let player = local_player(mem, root, layout)?;
    let plane = mem.read::<i32>(player + layout.plane).ok()?;
    let size = mem.read::<i32>(player + layout.size).ok()?;
    let [fine_x, _, fine_z] = mem.read::<[f32; 3]>(player + layout.fine_position).ok()?;
    if !(0..=3).contains(&plane) || size < 1 {
        return None;
    }
    // Match CoordGrid's footprint origin conversion for the current entity.
    let x = fine_x / FINE_PER_TILE - (size / 2) as f32;
    let y = fine_z / FINE_PER_TILE - (size / 2) as f32;
    if !(0.0..WORLD_TILES).contains(&x) || !(0.0..WORLD_TILES).contains(&y) {
        return None;
    }
    Some((player, TilePosition { x: x as i32, y: y as i32, plane: plane as u8 }))
}

pub(crate) fn read(
    mem: &impl MemRead,
    module_base: u64,
    layout: &PlayerPositionLayout,
    retries: u32,
) -> Option<TilePosition> {
    let root = module_base + layout.client_ptr_rva;
    let mut previous = sample(mem, root, layout);
    for _ in 0..=retries {
        let current = sample(mem, root, layout);
        if current == previous {
            return current.map(|(_, tile)| tile);
        }
        previous = current;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use rsmem::FakeMem;

    #[test]
    fn reads_occupied_tile_and_clears_it_on_scene_exit() {
        let layout = PlayerPositionLayout {
            client_ptr_rva: 0x10,
            state: 0x8,
            in_game_state: 30,
            player_context: 0x10,
            player_index: 0x8,
            player_override: 0x10,
            player_table: 0x18,
            table_range: 0x10,
            slot_entity: 0x38,
            plane: 0x50,
            size: 0x18c,
            fine_position: 0x280,
        };
        let mut mem = FakeMem::new();
        mem.add(0x1000, vec![0; 0x1000]);
        mem.write(0x1010, 0x1100u64);
        mem.write(0x1108, 30i32);
        mem.write(0x1110, 0x1200u64);
        mem.write(0x1118, 0x1300u64);
        mem.write(0x1200, 0x1100u64);
        mem.write(0x1208, 1i32);
        mem.write(0x1310, [0x1400u64, 0x1410]);
        mem.write(0x1408, 0x1500u64);
        mem.write(0x1538, 0x1600u64);
        mem.write(0x1650, 2i32);
        mem.write(0x178c, 1i32);
        mem.write(0x1880, [3165.5 * FINE_PER_TILE, 1285.0, 3482.5 * FINE_PER_TILE]);
        assert_eq!(read(&mem, 0x1000, &layout, 1), Some(TilePosition { x: 3165, y: 3482, plane: 2 }));

        // An axis boundary changes exactly one tile. Height is independent of plane.
        mem.write(0x1880, [3166.0 * FINE_PER_TILE, 0.0, 3482.5 * FINE_PER_TILE]);
        assert_eq!(read(&mem, 0x1000, &layout, 1), Some(TilePosition { x: 3166, y: 3482, plane: 2 }));
        mem.write(0x1880, [3166.0 * FINE_PER_TILE, 0.0, f32::NAN]);
        assert_eq!(read(&mem, 0x1000, &layout, 1), None);

        mem.write(0x1880, [3166.0 * FINE_PER_TILE, 0.0, 3482.5 * FINE_PER_TILE]);
        mem.write(0x1208, 2i32);
        assert_eq!(read(&mem, 0x1000, &layout, 1), None);
        mem.write(0x1208, 1i32);
        mem.write(0x1108, 20i32);
        assert_eq!(read(&mem, 0x1000, &layout, 1), None);
    }
}
