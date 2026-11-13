/* Explicit, opt-in foreground input. Sampling never calls this path. */
#include "input.h"

#include <math.h>
#include <string.h>

#include "clock.h"
#include "context.h"
#include "player.h"
#include "process.h"

/** Maximum snapshot age for movement, abilities and interactions. */
#define ACTION_MAX_AGE_NS 1000000000ull
/** Maximum snapshot age and clock stall for prayer toggles. */
#define PRAYER_MAX_AGE_NS 150000000ull
#define MAX_PLANE         3
/** Absolute mouse coordinates span 0..65535 across the virtual desktop. */
#define ABSOLUTE_MOUSE_RANGE 65536
#define MODIFIER_COUNT       3u
#define VALID_MODIFIER_MASK  ((uint32_t)(RS3_KEY_CTRL | RS3_KEY_SHIFT | RS3_KEY_ALT))
/** Reject numerically singular fitted map axes, irrespective of their pixel scale. */
#define MIN_MINIMAP_RELATIVE_AREA 1e-6

/** Windows virtual keys pressed for each modifier bit, in bit order. */
static const WORD modifier_virtual_keys[MODIFIER_COUNT] = {VK_CONTROL, VK_SHIFT, VK_MENU};

/** Returns nonzero for a verified frame that is at most one second old. */
static int is_fresh_game_frame(const rs3_snapshot *snapshot)
{
    if (snapshot->profile == NULL || snapshot->info.pid == 0 ||
        snapshot->info.process_creation_time == 0 ||
        (snapshot->info.status != RS3_AVAILABLE && snapshot->info.status != RS3_PARTIAL))
    {
        return 0;
    }
    uint64_t now_ns = clock_monotonic_ns();
    return now_ns >= snapshot->info.monotonic_ns &&
           now_ns - snapshot->info.monotonic_ns <= ACTION_MAX_AGE_NS;
}

static int is_valid_tile(int32_t tile_x, int32_t tile_y, int32_t plane)
{
    return tile_x >= 0 && tile_x < MAX_TILE_COORDINATE && tile_y >= 0 &&
           tile_y < MAX_TILE_COORDINATE && plane >= 0 && plane <= MAX_PLANE;
}

/** Accepts an unmodified Windows A-Z, 0-9 or F1-F12 virtual key. */
static int is_bindable_key(uint32_t virtual_key)
{
    return (virtual_key >= 'A' && virtual_key <= 'Z') ||
           (virtual_key >= '0' && virtual_key <= '9') ||
           (virtual_key >= VK_F1 && virtual_key <= VK_F12);
}

int32_t input_project_minimap_tile(const rs3_position *player_tile, const rs3_minimap *minimap,
                                   int32_t tile_x, int32_t tile_y, int32_t plane, POINT *pixel)
{
    if (player_tile == NULL || minimap == NULL || pixel == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    const rs3_position *anchor = &minimap->anchor;
    if (!is_valid_tile(tile_x, tile_y, plane) || minimap->radius_pixels == 0 ||
        minimap->reserved != 0 || anchor->valid != 1 ||
        !is_valid_tile(anchor->x, anchor->y, anchor->plane) || !isfinite(minimap->east_dx) ||
        !isfinite(minimap->east_dy) || !isfinite(minimap->north_dx) || !isfinite(minimap->north_dy))
    {
        return RS3_E_ARGUMENT;
    }

    /* The two tile axes must span an area on screen. */
    double determinant =
        minimap->east_dx * minimap->north_dy - minimap->east_dy * minimap->north_dx;
    double axis_norm =
        hypot(minimap->east_dx, minimap->east_dy) * hypot(minimap->north_dx, minimap->north_dy);
    if (!isfinite(determinant) || !isfinite(axis_norm) || axis_norm == 0.0 ||
        fabs(determinant) / axis_norm <= MIN_MINIMAP_RELATIVE_AREA)
    {
        return RS3_E_ARGUMENT;
    }
    if (player_tile->valid != 1 || player_tile->x != anchor->x || player_tile->y != anchor->y ||
        player_tile->plane != anchor->plane)
    {
        return RS3_E_STATE;
    }
    if (plane != player_tile->plane)
    {
        return RS3_E_TARGET;
    }

    /* Apply the calibrated screen displacement for each world-axis step. */
    int32_t tiles_east = tile_x - player_tile->x;
    int32_t tiles_north = tile_y - player_tile->y;
    double pixel_x =
        round(minimap->screen_x + tiles_east * minimap->east_dx + tiles_north * minimap->north_dx);
    double pixel_y =
        round(minimap->screen_y + tiles_east * minimap->east_dy + tiles_north * minimap->north_dy);
    if (!(pixel_x >= INT32_MIN && pixel_x <= INT32_MAX && pixel_y >= INT32_MIN &&
          pixel_y <= INT32_MAX))
    {
        return RS3_E_TARGET;
    }

    double offset_x = pixel_x - minimap->screen_x;
    double offset_y = pixel_y - minimap->screen_y;
    double radius = minimap->radius_pixels;
    if (offset_x * offset_x + offset_y * offset_y > radius * radius)
    {
        return RS3_E_TARGET;
    }
    *pixel = (POINT){(LONG)pixel_x, (LONG)pixel_y};
    return RS3_OK;
}

uint32_t input_release_inserted(const INPUT *events, uint32_t inserted_count,
                                INPUT releases[MAX_CHORD_INPUTS])
{
    uint32_t release_count = 0;
    for (uint32_t index = inserted_count; index > 0 && release_count < MAX_CHORD_INPUTS; index--)
    {
        const INPUT *event = &events[index - 1];
        if (event->type == INPUT_KEYBOARD && !(event->ki.dwFlags & KEYEVENTF_KEYUP))
        {
            releases[release_count] = *event;
            releases[release_count].ki.dwFlags |= KEYEVENTF_KEYUP;
            release_count++;
        }
        else if (event->type == INPUT_MOUSE &&
                 (event->mi.dwFlags & (MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_RIGHTDOWN)))
        {
            releases[release_count] = *event;
            releases[release_count].mi.dwFlags = (event->mi.dwFlags & MOUSEEVENTF_LEFTDOWN)
                                                     ? MOUSEEVENTF_LEFTUP
                                                     : MOUSEEVENTF_RIGHTUP;
            release_count++;
        }
    }
    return release_count;
}

int32_t input_build_key_chord(uint32_t virtual_key, uint32_t modifiers,
                              INPUT chord[MAX_CHORD_INPUTS], uint32_t *input_count)
{
    if (chord == NULL || input_count == NULL || (modifiers & ~VALID_MODIFIER_MASK) != 0 ||
        !is_bindable_key(virtual_key))
    {
        return RS3_E_ARGUMENT;
    }
    uint32_t count = 0;
    memset(chord, 0, MAX_CHORD_INPUTS * sizeof *chord);
    for (uint32_t modifier = 0; modifier < MODIFIER_COUNT; modifier++)
    {
        if (modifiers & (1u << modifier))
        {
            chord[count].type = INPUT_KEYBOARD;
            chord[count].ki.wVk = modifier_virtual_keys[modifier];
            count++;
        }
    }
    chord[count].type = INPUT_KEYBOARD;
    chord[count].ki.wVk = (WORD)virtual_key;
    count++;
    chord[count] = chord[count - 1];
    chord[count].ki.dwFlags = KEYEVENTF_KEYUP;
    count++;
    for (uint32_t modifier = MODIFIER_COUNT; modifier > 0; modifier--)
    {
        if (modifiers & (1u << (modifier - 1)))
        {
            chord[count].type = INPUT_KEYBOARD;
            chord[count].ki.wVk = modifier_virtual_keys[modifier - 1];
            chord[count].ki.dwFlags = KEYEVENTF_KEYUP;
            count++;
        }
    }
    *input_count = count;
    return RS3_OK;
}

static int window_belongs_to(HWND window, DWORD expected_pid)
{
    DWORD window_pid = 0;
    return window != NULL && GetWindowThreadProcessId(window, &window_pid) &&
           window_pid == expected_pid;
}

/**
 * Sends events to the game window after re-verifying the process identity
 * and foreground ownership immediately before dispatch.
 */
static int32_t send_to_game(const rs3_snapshot *snapshot, HWND window, INPUT *events,
                            UINT event_count)
{
    if (!window_belongs_to(window, snapshot->info.pid) || !IsWindowVisible(window) ||
        IsIconic(window) || !IsWindowEnabled(window))
    {
        return RS3_E_TARGET;
    }
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, snapshot->info.pid);
    if (process == NULL)
    {
        return RS3_E_TARGET;
    }

    int32_t result = RS3_E_TARGET;
    DWORD exit_code;
    /* The creation time distinguishes a restarted client whose PID was reused. */
    int same_live_process =
        process_creation_time(process) == snapshot->info.process_creation_time &&
        GetExitCodeProcess(process, &exit_code) && exit_code == STILL_ACTIVE;
    if (same_live_process && GetForegroundWindow() == window &&
        window_belongs_to(window, snapshot->info.pid))
    {
        if (is_fresh_game_frame(snapshot))
        {
            UINT sent_count = SendInput(event_count, events, sizeof *events);
            /*
             * A partially inserted chord may leave several keys down. Release
             * only the keys and buttons this call inserted.
             */
            if (sent_count != 0 && sent_count < event_count)
            {
                INPUT releases[MAX_CHORD_INPUTS];
                uint32_t release_count = input_release_inserted(events, sent_count, releases);
                if (release_count != 0)
                {
                    SendInput(release_count, releases, sizeof *releases);
                }
            }
            result = sent_count == event_count ? RS3_OK : RS3_E_OS;
        }
        else
        {
            result = RS3_E_STATE;
        }
    }
    CloseHandle(process);
    return result;
}

static int user_holds_modifier(void)
{
    return GetAsyncKeyState(VK_SHIFT) < 0 || GetAsyncKeyState(VK_CONTROL) < 0 ||
           GetAsyncKeyState(VK_MENU) < 0 || GetAsyncKeyState(VK_LWIN) < 0 ||
           GetAsyncKeyState(VK_RWIN) < 0;
}

static int user_holds_mouse_button(void)
{
    return GetAsyncKeyState(VK_LBUTTON) < 0 || GetAsyncKeyState(VK_RBUTTON) < 0 ||
           GetAsyncKeyState(VK_MBUTTON) < 0;
}

int32_t rs3_set_prayer(const rs3_snapshot *snapshot, uint32_t prayer, uint32_t virtual_key,
                       uint32_t modifiers)
{
    INPUT chord[MAX_CHORD_INPUTS];
    uint32_t chord_length;
    int32_t chord_result = input_build_key_chord(virtual_key, modifiers, chord, &chord_length);
    if (snapshot == NULL || prayer < RS3_PRAYER_MELEE || prayer > RS3_PRAYER_NECROMANCY ||
        chord_result != RS3_OK)
    {
        return RS3_E_ARGUMENT;
    }
    if (!is_fresh_game_frame(snapshot) ||
        clock_monotonic_ns() - snapshot->info.monotonic_ns > PRAYER_MAX_AGE_NS ||
        snapshot->player.clock_stalled_ns > PRAYER_MAX_AGE_NS)
    {
        return RS3_E_STATE;
    }
    uint32_t active_prayer = rs3_combat_active_prayer(&snapshot->combat);
    int prayer_points_known = (snapshot->player.valid_variables & (1ull << RS3_PRAYER_PACKED)) != 0;
    int64_t prayer_points =
        snapshot->player.raw_variables[RS3_PRAYER_PACKED] & PLAYER_PRAYER_POINTS_MASK;
    if (snapshot->combat.scene_epoch == 0 || active_prayer == RS3_PRAYER_UNKNOWN ||
        active_prayer == prayer || !prayer_points_known || prayer_points <= 0 ||
        user_holds_modifier() || GetAsyncKeyState((int)virtual_key) < 0)
    {
        return RS3_E_STATE;
    }
    return send_to_game(snapshot, GetForegroundWindow(), chord, chord_length);
}

/** Returns nonzero when point lies in the unobscured client area of window. */
static int point_in_client_area(HWND window, DWORD pid, POINT point)
{
    WINDOWINFO window_info = {0};
    window_info.cbSize = sizeof window_info;
    RECT cursor_clip;
    if (!GetWindowInfo(window, &window_info) || !PtInRect(&window_info.rcClient, point) ||
        !GetClipCursor(&cursor_clip) || !PtInRect(&cursor_clip, point))
    {
        return 0;
    }
    /* A point inside the client rectangle can still be covered by another window. */
    HWND window_under_point = WindowFromPhysicalPoint(point);
    return window_belongs_to(window_under_point, pid) &&
           GetAncestor(window_under_point, GA_ROOT) == window;
}

/** Converts a virtual-desktop pixel into the middle of its absolute mouse interval. */
static LONG absolute_mouse_coordinate(int64_t pixel, int extent)
{
    int64_t first = (pixel * ABSOLUTE_MOUSE_RANGE + extent - 1) / extent;
    int64_t last = ((pixel + 1) * ABSOLUTE_MOUSE_RANGE + extent - 1) / extent - 1;
    return (LONG)((first + last) / 2);
}

static int32_t click_game(const rs3_snapshot *snapshot, POINT point, int right_button)
{
    HWND window = GetForegroundWindow();
    if (!window_belongs_to(window, snapshot->info.pid))
    {
        return RS3_E_TARGET;
    }
    if (user_holds_modifier() || user_holds_mouse_button())
    {
        return RS3_E_STATE;
    }

    /* Use physical pixels for hit-testing and for the virtual desktop bounds. */
    DPI_AWARENESS_CONTEXT previous_awareness =
        SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE);
    if (previous_awareness == NULL)
    {
        return RS3_E_OS;
    }
    int inside_client = point_in_client_area(window, snapshot->info.pid, point);
    int desktop_width = GetSystemMetrics(SM_CXVIRTUALSCREEN);
    int desktop_height = GetSystemMetrics(SM_CYVIRTUALSCREEN);
    int64_t desktop_x = (int64_t)point.x - GetSystemMetrics(SM_XVIRTUALSCREEN);
    int64_t desktop_y = (int64_t)point.y - GetSystemMetrics(SM_YVIRTUALSCREEN);
    SetThreadDpiAwarenessContext(previous_awareness);
    if (!inside_client || desktop_x < 0 || desktop_x >= desktop_width || desktop_y < 0 ||
        desktop_y >= desktop_height || desktop_width > ABSOLUTE_MOUSE_RANGE ||
        desktop_height > ABSOLUTE_MOUSE_RANGE)
    {
        return RS3_E_TARGET;
    }

    INPUT events[3] = {0};
    events[0].type = INPUT_MOUSE;
    events[1].type = INPUT_MOUSE;
    events[2].type = INPUT_MOUSE;
    events[0].mi.dx = absolute_mouse_coordinate(desktop_x, desktop_width);
    events[0].mi.dy = absolute_mouse_coordinate(desktop_y, desktop_height);
    events[0].mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK |
                           MOUSEEVENTF_MOVE_NOCOALESCE;
    events[1].mi.dwFlags = right_button ? MOUSEEVENTF_RIGHTDOWN : MOUSEEVENTF_LEFTDOWN;
    events[2].mi.dwFlags = right_button ? MOUSEEVENTF_RIGHTUP : MOUSEEVENTF_LEFTUP;
    return send_to_game(snapshot, window, events, 3);
}

int32_t rs3_move_to_tile(const rs3_snapshot *snapshot, int32_t x, int32_t y, int32_t plane,
                         const rs3_minimap *minimap)
{
    if (snapshot == NULL || minimap == NULL)
    {
        return RS3_E_ARGUMENT;
    }
    if (!is_fresh_game_frame(snapshot))
    {
        return RS3_E_STATE;
    }
    POINT pixel;
    int32_t result = input_project_minimap_tile(&snapshot->position, minimap, x, y, plane, &pixel);
    if (result != RS3_OK)
    {
        return result;
    }
    return click_game(snapshot, pixel, 0);
}

static int snapshot_has_ability(const rs3_snapshot *snapshot, uint32_t struct_id)
{
    for (uint32_t index = 0; index < snapshot->inventory_info.ability_count; index++)
    {
        if (snapshot->abilities[index].struct_id == struct_id)
        {
            return 1;
        }
    }
    return 0;
}

int32_t rs3_activate_ability(const rs3_snapshot *snapshot, uint32_t struct_id, uint32_t virtual_key)
{
    if (snapshot == NULL || !is_bindable_key(virtual_key))
    {
        return RS3_E_ARGUMENT;
    }
    if (!is_fresh_game_frame(snapshot))
    {
        return RS3_E_STATE;
    }
    if (!snapshot_has_ability(snapshot, struct_id))
    {
        return RS3_E_TARGET;
    }
    if (user_holds_modifier() || GetAsyncKeyState((int)virtual_key) < 0)
    {
        return RS3_E_STATE;
    }

    INPUT events[2] = {0};
    events[0].type = INPUT_KEYBOARD;
    events[1].type = INPUT_KEYBOARD;
    events[0].ki.wVk = (WORD)virtual_key;
    events[1].ki.wVk = (WORD)virtual_key;
    events[1].ki.dwFlags = KEYEVENTF_KEYUP;
    return send_to_game(snapshot, GetForegroundWindow(), events, 2);
}

static int snapshot_has_npc(const rs3_snapshot *snapshot, uint64_t instance_id)
{
    for (uint32_t index = 0; index < snapshot->info.npc_count; index++)
    {
        if (snapshot->npcs[index].instance_id == instance_id)
        {
            return 1;
        }
    }
    return 0;
}

int32_t rs3_interact(const rs3_snapshot *snapshot, uint64_t instance_id, uint32_t action,
                     int32_t screen_x, int32_t screen_y)
{
    if (snapshot == NULL || action < RS3_ENTITY_DEFAULT || action > RS3_ENTITY_SELECT_OPTION)
    {
        return RS3_E_ARGUMENT;
    }
    if (!is_fresh_game_frame(snapshot))
    {
        return RS3_E_STATE;
    }
    if (instance_id != 0 && !snapshot_has_npc(snapshot, instance_id))
    {
        return RS3_E_TARGET;
    }
    return click_game(snapshot, (POINT){screen_x, screen_y}, action == RS3_ENTITY_OPEN_MENU);
}
