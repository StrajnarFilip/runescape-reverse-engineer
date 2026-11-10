/* Minimap projection, foreground input guards and prayer key chords. */
#include "fixture.h"

#include <math.h>

#include "input.h"
#include "process.h"

static void projects_minimap_tiles_and_guards_actions(void)
{
    rs3_position player_tile = {1, 3200, 3200, 0};
    rs3_minimap minimap = {.anchor = player_tile,
                           .screen_x = -250,
                           .screen_y = 400,
                           .east_dx = 3,
                           .east_dy = 4,
                           .north_dx = 4,
                           .north_dy = -3,
                           .radius_pixels = 25};
    POINT pixel;
    assert(rs3_sizeof(RS3_SIZE_MINIMAP) == sizeof minimap);
    assert(input_project_minimap_tile(&player_tile, &minimap, 3203, 3204, 0, &pixel) == RS3_OK);
    assert(pixel.x == -225 && pixel.y == 400);
    assert(input_project_minimap_tile(&player_tile, &minimap, 3199, 3198, 0, &pixel) == RS3_OK);
    assert(pixel.x == -261 && pixel.y == 402);
    assert(input_project_minimap_tile(&player_tile, &minimap, 3203, 3205, 0, &pixel) ==
           RS3_E_TARGET);
    assert(input_project_minimap_tile(&player_tile, &minimap, 3203, 3204, 1, &pixel) ==
           RS3_E_TARGET);
    assert(input_project_minimap_tile(&player_tile, &minimap, -1, 3200, 0, &pixel) ==
           RS3_E_ARGUMENT);

    /* The calibration must describe the sampled tile. */
    player_tile.x++;
    assert(input_project_minimap_tile(&player_tile, &minimap, 3203, 3204, 0, &pixel) ==
           RS3_E_STATE);
    player_tile.x--;

    /* Non-finite or degenerate axes are rejected. */
    minimap.north_dx = NAN;
    assert(input_project_minimap_tile(&player_tile, &minimap, 3203, 3204, 0, &pixel) ==
           RS3_E_ARGUMENT);
    minimap.north_dx = -2.25;
    assert(input_project_minimap_tile(&player_tile, &minimap, 3203, 3204, 0, &pixel) ==
           RS3_E_ARGUMENT);
    minimap.north_dx = 4;

    /* A fresh fixture without a verified frame fails before dispatch. */
    fixture *test_fixture = fixture_create_empty();
    rs3_snapshot *snapshot = test_fixture->snapshot;
    assert(rs3_move_to_tile(snapshot, 3200, 3200, 0, &minimap) == RS3_E_STATE);
    assert(rs3_interact(snapshot, 1, 0, 0, 0) == RS3_E_ARGUMENT);
    assert(rs3_activate_ability(snapshot, 1, VK_LWIN) == RS3_E_ARGUMENT);

    /* With a verified frame, missing abilities, NPCs and off-map tiles are targets. */
    snapshot->profile = &profile_compatibility;
    snapshot->position = player_tile;
    snapshot->info.status = RS3_AVAILABLE;
    snapshot->info.pid = GetCurrentProcessId();
    snapshot->info.process_creation_time = process_creation_time(GetCurrentProcess());
    snapshot->info.monotonic_ns = rs3_monotonic_ns();
    assert(rs3_activate_ability(snapshot, 1, 'Q') == RS3_E_TARGET);
    assert(rs3_interact(snapshot, 1, RS3_ENTITY_OPEN_MENU, 0, 0) == RS3_E_TARGET);
    assert(rs3_move_to_tile(snapshot, 3210, 3200, 0, &minimap) == RS3_E_TARGET);

    /* A frame from the future, as after a clock mismatch, is stale. */
    snapshot->info.monotonic_ns += 5000000000ull;
    assert(rs3_interact(snapshot, 0, RS3_ENTITY_SELECT_OPTION, 0, 0) == RS3_E_STATE);
    fixture_destroy(test_fixture);
}

static void builds_chords_and_releases_partial_insertions(void)
{
    INPUT chord[MAX_CHORD_INPUTS];
    INPUT releases[MAX_CHORD_INPUTS];
    uint32_t chord_length;
    assert(input_build_key_chord(VK_F1, RS3_KEY_CTRL, chord, &chord_length) == RS3_OK);
    assert(chord_length == 4);
    assert(chord[0].ki.wVk == VK_CONTROL && chord[0].ki.dwFlags == 0);
    assert(chord[1].ki.wVk == VK_F1 && chord[1].ki.dwFlags == 0);
    assert(chord[2].ki.wVk == VK_F1 && chord[2].ki.dwFlags == KEYEVENTF_KEYUP);
    assert(chord[3].ki.wVk == VK_CONTROL && chord[3].ki.dwFlags == KEYEVENTF_KEYUP);

    /* Only the keys pressed by the inserted prefix are released, newest first. */
    for (uint32_t inserted = 1; inserted < 4; inserted++)
    {
        uint32_t release_count = input_release_inserted(chord, inserted, releases);
        assert(release_count == (inserted == 1 ? 1u : 2u));
        for (uint32_t index = 0; index < release_count; index++)
        {
            assert(releases[index].ki.dwFlags & KEYEVENTF_KEYUP);
        }
        assert(releases[release_count - 1].ki.wVk == VK_CONTROL);
    }

    uint32_t all_modifiers = RS3_KEY_CTRL | RS3_KEY_SHIFT | RS3_KEY_ALT;
    assert(input_build_key_chord('A', all_modifiers, chord, &chord_length) == RS3_OK);
    assert(chord_length == MAX_CHORD_INPUTS);
    assert(input_build_key_chord(VK_CONTROL, 0, chord, &chord_length) == RS3_E_ARGUMENT);

    fixture *test_fixture = fixture_create_empty();
    assert(rs3_set_prayer(test_fixture->snapshot, RS3_PRAYER_MAGIC, VK_F1, RS3_KEY_CTRL) ==
           RS3_E_STATE);
    assert(rs3_set_prayer(test_fixture->snapshot, RS3_PRAYER_MAGIC, VK_F1, 8) == RS3_E_ARGUMENT);
    fixture_destroy(test_fixture);
}

int main(void)
{
    RUN_TEST(projects_minimap_tiles_and_guards_actions);
    RUN_TEST(builds_chords_and_releases_partial_insertions);
    puts("PASS: input tests");
    return 0;
}
