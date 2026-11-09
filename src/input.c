/* Explicit foreground actions. Sampling never calls this input path. */
#include "internal.h"

#include <math.h>

#define ACTION_MAX_AGE_NS 1000000000ull

static int fresh_game_frame(const rs3_snapshot *s) {
    if (!s->profile || !s->info.pid || !s->info.process_creation_time ||
        (s->info.status != RS3_AVAILABLE && s->info.status != RS3_PARTIAL)) return 0;
    uint64_t now = monotonic_ns();
    return now >= s->info.monotonic_ns && now - s->info.monotonic_ns <= ACTION_MAX_AGE_NS;
}

static int valid_tile(int32_t x, int32_t y, int32_t plane) {
    return x >= 0 && x < RS3_TILE_LIMIT && y >= 0 && y < RS3_TILE_LIMIT &&
           plane >= 0 && plane <= 3;
}

int32_t project_minimap_tile(const rs3_position *position, const rs3_minimap *map,
    int32_t x, int32_t y, int32_t plane, POINT *out) {
    if (!valid_tile(x, y, plane) || !map->radius_pixels || map->reserved ||
        map->anchor.valid != 1 || !valid_tile(map->anchor.x, map->anchor.y, map->anchor.plane) ||
        !isfinite(map->east_dx) || !isfinite(map->east_dy) ||
        !isfinite(map->north_dx) || !isfinite(map->north_dy)) return RS3_E_ARGUMENT;

    // The two tile axes must span an area on screen.
    double determinant = map->east_dx * map->north_dy - map->east_dy * map->north_dx;
    if (!isfinite(determinant) || determinant == 0.0) return RS3_E_ARGUMENT;

    if (!position->valid || position->x != map->anchor.x || position->y != map->anchor.y ||
        position->plane != map->anchor.plane) return RS3_E_STATE;
    if (plane != position->plane) return RS3_E_TARGET;

    // Apply the calibrated screen displacement for each world-axis step.
    int32_t east = x - position->x, north = y - position->y;
    double px = round(map->screen_x + east * map->east_dx + north * map->north_dx);
    double py = round(map->screen_y + east * map->east_dy + north * map->north_dy);
    if (!(px >= INT32_MIN && px <= INT32_MAX && py >= INT32_MIN && py <= INT32_MAX))
        return RS3_E_TARGET;

    double dx = px - map->screen_x, dy = py - map->screen_y;
    double radius = map->radius_pixels;
    if (dx * dx + dy * dy > radius * radius) return RS3_E_TARGET;
    *out = (POINT){(LONG)px, (LONG)py};
    return RS3_OK;
}

static int window_pid_matches(HWND window, DWORD expected) {
    DWORD pid = 0;
    return window && GetWindowThreadProcessId(window, &pid) && pid == expected;
}

static int32_t send_to_game(const rs3_snapshot *s, HWND window, INPUT *events, UINT count) {
    if (!window_pid_matches(window, s->info.pid) || !IsWindowVisible(window) ||
        IsIconic(window) || !IsWindowEnabled(window)) return RS3_E_TARGET;

    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, s->info.pid);
    if (!process) return RS3_E_TARGET;
    DWORD exit_code;
    int32_t result = RS3_E_TARGET;
    // Creation time distinguishes a restarted client whose PID was reused.
    // Recheck foreground ownership immediately before sending input.
    if (process_creation_time(process) == s->info.process_creation_time &&
        GetExitCodeProcess(process, &exit_code) && exit_code == STILL_ACTIVE &&
        GetForegroundWindow() == window && window_pid_matches(window, s->info.pid)) {
        if (fresh_game_frame(s)) {
            UINT sent = SendInput(count, events, sizeof *events);
            // If a down event was inserted without its matching up, try to release it.
            if (sent == count - 1) SendInput(1, events + sent, sizeof *events);
            result = sent == count ? RS3_OK : RS3_E_OS;
        } else {
            result = RS3_E_STATE;
        }
    }
    CloseHandle(process);
    return result;
}

static int modifier_held(void) {
    return GetAsyncKeyState(VK_SHIFT) < 0 || GetAsyncKeyState(VK_CONTROL) < 0 ||
           GetAsyncKeyState(VK_MENU) < 0 || GetAsyncKeyState(VK_LWIN) < 0 ||
           GetAsyncKeyState(VK_RWIN) < 0;
}

static int point_in_client(HWND window, DWORD pid, POINT point) {
    WINDOWINFO info = {0};
    info.cbSize = sizeof info;
    RECT clip;
    if (!GetWindowInfo(window, &info) || !PtInRect(&info.rcClient, point) ||
        !GetClipCursor(&clip) || !PtInRect(&clip, point)) return 0;
    // A point within the client rectangle can still be covered by another window.
    HWND hit = WindowFromPhysicalPoint(point);
    return window_pid_matches(hit, pid) && GetAncestor(hit, GA_ROOT) == window;
}

static LONG absolute_mouse_coordinate(int64_t pixel, int extent) {
    // Use the middle representable value for the pixel's 16-bit interval.
    int64_t first = (pixel * 65536 + extent - 1) / extent;
    int64_t last = ((pixel + 1) * 65536 + extent - 1) / extent - 1;
    return (LONG)((first + last) / 2);
}

static int32_t click_game(const rs3_snapshot *s, POINT point, int secondary) {
    HWND window = GetForegroundWindow();
    if (!window_pid_matches(window, s->info.pid)) return RS3_E_TARGET;
    if (modifier_held() || GetAsyncKeyState(VK_LBUTTON) < 0 ||
        GetAsyncKeyState(VK_RBUTTON) < 0 || GetAsyncKeyState(VK_MBUTTON) < 0)
        return RS3_E_STATE;

    // Use physical pixels for hit-testing and the virtual desktop bounds.
    DPI_AWARENESS_CONTEXT prior = SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE);
    if (!prior) return RS3_E_OS;
    int inside = point_in_client(window, s->info.pid, point);
    int width = GetSystemMetrics(SM_CXVIRTUALSCREEN), height = GetSystemMetrics(SM_CYVIRTUALSCREEN);
    int64_t x = (int64_t)point.x - GetSystemMetrics(SM_XVIRTUALSCREEN);
    int64_t y = (int64_t)point.y - GetSystemMetrics(SM_YVIRTUALSCREEN);
    SetThreadDpiAwarenessContext(prior);
    if (!inside || x < 0 || x >= width || y < 0 || y >= height ||
        width > 65536 || height > 65536) return RS3_E_TARGET;

    INPUT events[3] = {0};
    events[0].type = events[1].type = events[2].type = INPUT_MOUSE;
    events[0].mi.dx = absolute_mouse_coordinate(x, width);
    events[0].mi.dy = absolute_mouse_coordinate(y, height);
    events[0].mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE |
                           MOUSEEVENTF_VIRTUALDESK | MOUSEEVENTF_MOVE_NOCOALESCE;
    events[1].mi.dwFlags = secondary ? MOUSEEVENTF_RIGHTDOWN : MOUSEEVENTF_LEFTDOWN;
    events[2].mi.dwFlags = secondary ? MOUSEEVENTF_RIGHTUP : MOUSEEVENTF_LEFTUP;
    return send_to_game(s, window, events, 3);
}

int32_t rs3_move_to_tile(const rs3_snapshot *s, int32_t x, int32_t y, int32_t plane,
    const rs3_minimap *map) {
    if (!s || !map) return RS3_E_ARGUMENT;
    if (!fresh_game_frame(s)) return RS3_E_STATE;
    POINT point;
    int32_t result = project_minimap_tile(&s->position, map, x, y, plane, &point);
    if (result != RS3_OK) return result;
    return click_game(s, point, 0);
}

static int known_ability(const rs3_snapshot *s, uint32_t struct_id) {
    for (uint32_t i = 0; i < s->inventory_info.ability_count; i++)
        if (s->abilities[i].struct_id == struct_id) return 1;
    return 0;
}

int32_t rs3_activate_ability(const rs3_snapshot *s, uint32_t struct_id, uint32_t key) {
    if (!s || !((key >= 'A' && key <= 'Z') || (key >= '0' && key <= '9') ||
                (key >= VK_F1 && key <= VK_F12))) return RS3_E_ARGUMENT;
    if (!fresh_game_frame(s)) return RS3_E_STATE;
    if (!known_ability(s, struct_id)) return RS3_E_TARGET;
    if (modifier_held() || GetAsyncKeyState((int)key) < 0) return RS3_E_STATE;

    INPUT events[2] = {0};
    events[0].type = events[1].type = INPUT_KEYBOARD;
    events[0].ki.wVk = events[1].ki.wVk = (WORD)key;
    events[1].ki.dwFlags = KEYEVENTF_KEYUP;
    return send_to_game(s, GetForegroundWindow(), events, 2);
}

static int known_npc(const rs3_snapshot *s, uint64_t instance_id) {
    for (uint32_t i = 0; i < s->info.npc_count; i++)
        if (s->npcs[i].instance_id == instance_id) return 1;
    return 0;
}

int32_t rs3_interact(const rs3_snapshot *s, uint64_t instance_id, uint32_t action,
    int32_t screen_x, int32_t screen_y) {
    if (!s || action < RS3_ENTITY_DEFAULT || action > RS3_ENTITY_SELECT_OPTION)
        return RS3_E_ARGUMENT;
    if (!fresh_game_frame(s)) return RS3_E_STATE;
    if (instance_id && !known_npc(s, instance_id)) return RS3_E_TARGET;
    return click_game(s, (POINT){screen_x, screen_y}, action == RS3_ENTITY_OPEN_MENU);
}
