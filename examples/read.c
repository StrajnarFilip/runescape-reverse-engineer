/*
 * Minimal C consumer that loads the DLL at run time. No import library or
 * language-specific bridge is required.
 *
 * Usage: read_example.exe [absolute\path\rs3_native.dll] [seconds]
 * The DLL is loaded from the explicit path or from the executable's directory.
 */
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define RS3_STATIC
#include "rs3/rs3.h"

#define DEFAULT_DURATION_SECONDS 5u
#define SAMPLES_PER_SECOND       10u
#define WORKER_HZ                100u

/** Function pointers resolved from the DLL exports. */
typedef struct rs3_functions
{
    uint32_t (*abi_version)(void);
    int32_t (*create)(const rs3_options *, rs3_context **);
    void (*destroy)(rs3_context *);
    int32_t (*snapshot_create)(rs3_snapshot **);
    void (*snapshot_destroy)(rs3_snapshot *);
    int32_t (*start)(rs3_context *, uint32_t);
    int32_t (*copy_latest)(rs3_context *, rs3_snapshot *);
    int32_t (*snapshot_info)(const rs3_snapshot *, rs3_info *, uint32_t);
    int32_t (*snapshot_player)(const rs3_snapshot *, rs3_player *, uint32_t);
    int32_t (*snapshot_npcs)(const rs3_snapshot *, rs3_npc *, uint32_t, uint32_t *);
    int32_t (*snapshot_inventory_info)(const rs3_snapshot *, rs3_inventory_info *, uint32_t);
    int32_t (*snapshot_items)(const rs3_snapshot *, uint32_t, rs3_item *, uint32_t, uint32_t *);
} rs3_functions;

/** Copies an export into a function pointer of the same size. */
static int resolve_export(HMODULE library, const char *name, void *function_pointer,
                          size_t pointer_size)
{
    FARPROC symbol = GetProcAddress(library, name);
    if (symbol == NULL)
    {
        fprintf(stderr, "Missing export: %s\n", name);
        return 0;
    }
    if (pointer_size != sizeof symbol)
    {
        return 0;
    }
    memcpy(function_pointer, &symbol, sizeof symbol);
    return 1;
}

#define RESOLVE(library, functions, member)                                                        \
    resolve_export(library, "rs3_" #member, &(functions)->member, sizeof((functions)->member))

static int resolve_functions(HMODULE library, rs3_functions *functions)
{
    return RESOLVE(library, functions, abi_version) && RESOLVE(library, functions, create) &&
           RESOLVE(library, functions, destroy) && RESOLVE(library, functions, snapshot_create) &&
           RESOLVE(library, functions, snapshot_destroy) && RESOLVE(library, functions, start) &&
           RESOLVE(library, functions, copy_latest) && RESOLVE(library, functions, snapshot_info) &&
           RESOLVE(library, functions, snapshot_player) &&
           RESOLVE(library, functions, snapshot_npcs) &&
           RESOLVE(library, functions, snapshot_inventory_info) &&
           RESOLVE(library, functions, snapshot_items);
}

/** Writes the path of rs3_native.dll beside this executable into path. */
static int library_path_beside_executable(char path[MAX_PATH])
{
    if (!GetModuleFileNameA(NULL, path, MAX_PATH))
    {
        return 0;
    }
    char *last_separator = strrchr(path, '\\');
    if (last_separator == NULL)
    {
        return 0;
    }
    snprintf(last_separator + 1, (size_t)(path + MAX_PATH - last_separator - 1), "rs3_native.dll");
    return 1;
}

/** Prints one line per sample. Returns nonzero when every accessor succeeded. */
static int print_sample(const rs3_functions *functions, rs3_context *context,
                        rs3_snapshot *snapshot)
{
    static rs3_npc npcs[RS3_MAX_NPCS];
    rs3_info info;
    rs3_player player;
    rs3_inventory_info inventory;
    rs3_item backpack[28];
    uint32_t npc_count = 0;
    uint32_t backpack_slots = 0;
    if (functions->copy_latest(context, snapshot) != RS3_OK ||
        functions->snapshot_info(snapshot, &info, sizeof info) != RS3_OK ||
        functions->snapshot_player(snapshot, &player, sizeof player) != RS3_OK ||
        functions->snapshot_npcs(snapshot, npcs, RS3_MAX_NPCS, &npc_count) != RS3_OK ||
        functions->snapshot_inventory_info(snapshot, &inventory, sizeof inventory) != RS3_OK ||
        functions->snapshot_items(snapshot, RS3_CONTAINER_BACKPACK, backpack, 28,
                                  &backpack_slots) != RS3_OK)
    {
        return 0;
    }

    printf("sample=%llu status=%u PID=%u NPCs=%u ", (unsigned long long)info.sequence, info.status,
           info.pid, npc_count);
    if (player.valid_variables & (1ull << RS3_HEALTH))
    {
        printf("HP=%lld ", (long long)player.raw_variables[RS3_HEALTH]);
    }
    else
    {
        printf("HP=unknown ");
    }
    printf("backpack_slots=%u abilities=%u read=%.3fms %s\n", backpack_slots,
           inventory.ability_count, info.sample_duration_ns / 1e6, info.message);
    return 1;
}

int main(int argument_count, char **arguments)
{
    char library_path[MAX_PATH];
    if (argument_count > 1)
    {
        snprintf(library_path, sizeof library_path, "%s", arguments[1]);
    }
    else if (!library_path_beside_executable(library_path))
    {
        return 1;
    }

    HMODULE library = LoadLibraryExA(library_path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (library == NULL)
    {
        fprintf(stderr, "Cannot load DLL: %lu\n", GetLastError());
        return 1;
    }

    rs3_functions functions;
    rs3_context *context = NULL;
    rs3_snapshot *snapshot = NULL;
    int exit_code = 1;
    if (!resolve_functions(library, &functions))
    {
        goto cleanup;
    }
    if (functions.abi_version() != RS3_ABI_VERSION || functions.create(NULL, &context) != RS3_OK ||
        functions.snapshot_create(&snapshot) != RS3_OK ||
        functions.start(context, WORKER_HZ) != RS3_OK)
    {
        goto cleanup;
    }

    unsigned duration_seconds =
        argument_count > 2 ? (unsigned)atoi(arguments[2]) : DEFAULT_DURATION_SECONDS;
    for (unsigned sample = 0; sample < duration_seconds * SAMPLES_PER_SECOND; sample++)
    {
        Sleep(1000 / SAMPLES_PER_SECOND);
        if (!print_sample(&functions, context, snapshot))
        {
            goto cleanup;
        }
    }
    exit_code = 0;

cleanup:
    if (context != NULL)
    {
        functions.destroy(context);
    }
    if (snapshot != NULL)
    {
        functions.snapshot_destroy(snapshot);
    }
    FreeLibrary(library);
    return exit_code;
}
