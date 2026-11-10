/* Read-only client discovery, attachment and fingerprinting. */
#include "process.h"

#include <bcrypt.h>
#include <stdio.h>
#include <stdlib.h>
#include <tlhelp32.h>
#include <wchar.h>

#include "inventory.h"
#include "memory.h"
#include "npcs.h"

#define CLIENT_EXECUTABLE_NAME      L"rs2client.exe"
#define SHA256_DIGEST_BYTES         32u
#define FILE_HASH_CHUNK_BYTES       65536u
#define NANOSECONDS_PER_MILLISECOND 1000000ull

uint64_t process_creation_time(HANDLE process)
{
    FILETIME creation_time;
    FILETIME exit_time;
    FILETIME kernel_time;
    FILETIME user_time;
    if (!GetProcessTimes(process, &creation_time, &exit_time, &kernel_time, &user_time))
    {
        return 0;
    }
    return ((uint64_t)creation_time.dwHighDateTime << 32) | creation_time.dwLowDateTime;
}

int process_select_profile(rs3_context *context, const char *sha256)
{
    static const client_profile *const supported_profiles[] = {&profile_primary,
                                                               &profile_compatibility};
    context->target.profile = NULL;

    const client_profile *profile = NULL;
    for (size_t index = 0; index < sizeof supported_profiles / sizeof supported_profiles[0];
         index++)
    {
        if (strcmp(sha256, supported_profiles[index]->sha256) == 0)
        {
            profile = supported_profiles[index];
            break;
        }
    }
    if (profile == NULL)
    {
        return context_fail(context, RS3_UNSUPPORTED_CLIENT,
                            "Unsupported executable SHA-256; offsets not used");
    }

    unsigned char loaded_bytes[sizeof profile->signatures[0].bytes];
    for (size_t index = 0; index < profile->signature_count; index++)
    {
        const profile_signature *signature = &profile->signatures[index];
        if (!memory_read(context, context->target.module_base + signature->rva, loaded_bytes,
                         signature->length))
        {
            return 0;
        }
        if (memcmp(loaded_bytes, signature->bytes, signature->length) != 0)
        {
            return context_fail(context, RS3_UNSUPPORTED_CLIENT,
                                "Loaded code does not match the pinned profile");
        }
    }
    context->target.profile = profile;
    return 1;
}

/** Writes the lowercase hexadecimal SHA-256 of a file into hexadecimal_digest. */
static int hash_file_sha256(const wchar_t *path, char hexadecimal_digest[72])
{
    HANDLE file =
        CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                    NULL, OPEN_EXISTING, 0, NULL);
    if (file == INVALID_HANDLE_VALUE)
    {
        return 0;
    }

    BCRYPT_ALG_HANDLE algorithm = NULL;
    BCRYPT_HASH_HANDLE hash = NULL;
    unsigned char digest[SHA256_DIGEST_BYTES];
    unsigned char chunk[FILE_HASH_CHUNK_BYTES];
    int succeeded = 0;

    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, NULL, 0) < 0 ||
        BCryptCreateHash(algorithm, &hash, NULL, 0, NULL, 0, 0) < 0)
    {
        goto cleanup;
    }
    for (;;)
    {
        DWORD bytes_read = 0;
        if (!ReadFile(file, chunk, sizeof chunk, &bytes_read, NULL))
        {
            goto cleanup;
        }
        if (bytes_read == 0)
        {
            break;
        }
        if (BCryptHashData(hash, chunk, bytes_read, 0) < 0)
        {
            goto cleanup;
        }
    }
    if (BCryptFinishHash(hash, digest, sizeof digest, 0) < 0)
    {
        goto cleanup;
    }
    for (unsigned index = 0; index < SHA256_DIGEST_BYTES; index++)
    {
        snprintf(hexadecimal_digest + index * 2, 3, "%02x", digest[index]);
    }
    succeeded = 1;

cleanup:
    if (hash != NULL)
    {
        BCryptDestroyHash(hash);
    }
    if (algorithm != NULL)
    {
        BCryptCloseAlgorithmProvider(algorithm, 0);
    }
    CloseHandle(file);
    return succeeded;
}

void process_detach(rs3_context *context)
{
    if (context->target.process_handle != NULL)
    {
        CloseHandle(context->target.process_handle);
    }
    context->target.process_handle = NULL;
    context->target.attached = 0;
    context->target.profile = NULL;
    context->player_changes.valid_mask = 0;
    context->player_changes.clock_world_state = 0;
    npc_reset_identities(context);
    inventory_reset_tracking(context->inventory);
}

void process_schedule_retry(rs3_context *context, uint64_t now_ns)
{
    context->retry.reason = context->failure_reason;
    snprintf(context->retry.message, sizeof context->retry.message, "%s", context->failure_message);
    process_detach(context);
    context->retry.retry_after_ns =
        now_ns + (uint64_t)context->retry.interval_ms * NANOSECONDS_PER_MILLISECOND;
}

/** Candidate client processes and the window-based preference among them. */
typedef struct client_window_selection
{
    const DWORD *candidate_pids;
    size_t candidate_count;
    DWORD foreground_pid;
    DWORD selected_pid;
} client_window_selection;

/** EnumWindows callback: prefers the foreground game window, then the lowest PID. */
static BOOL CALLBACK consider_client_window(HWND window, LPARAM parameter)
{
    client_window_selection *selection = (client_window_selection *)parameter;
    DWORD window_pid = 0;
    if (!IsWindowVisible(window) || GetWindow(window, GW_OWNER) != NULL)
    {
        return TRUE;
    }
    if (!GetWindowThreadProcessId(window, &window_pid))
    {
        return TRUE;
    }
    for (size_t index = 0; index < selection->candidate_count; index++)
    {
        if (selection->candidate_pids[index] != window_pid)
        {
            continue;
        }
        int has_title = GetWindowTextLengthW(window) > 0;
        int nothing_selected = selection->selected_pid == 0;
        int is_foreground = window_pid == selection->foreground_pid;
        int is_lower_background_pid = selection->selected_pid != selection->foreground_pid &&
                                      window_pid < selection->selected_pid;
        if (has_title && (nothing_selected || is_foreground || is_lower_background_pid))
        {
            selection->selected_pid = window_pid;
        }
        break;
    }
    return TRUE;
}

/** Returns the PID of the client to attach, or 0 after recording the reason. */
static DWORD discover_client(rs3_context *context)
{
    HANDLE process_list = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (process_list == INVALID_HANDLE_VALUE)
    {
        return (DWORD)context_fail(context, RS3_READ_FAILED, "Process discovery failed");
    }

    PROCESSENTRY32W entry = {0};
    entry.dwSize = sizeof entry;
    DWORD selected_pid = 0;
    DWORD *candidate_pids = NULL;
    size_t candidate_count = 0;
    for (BOOL found = Process32FirstW(process_list, &entry); found;
         found = Process32NextW(process_list, &entry))
    {
        if (_wcsicmp(entry.szExeFile, CLIENT_EXECUTABLE_NAME) != 0)
        {
            continue;
        }
        if (context->target.requested_pid != 0)
        {
            if (entry.th32ProcessID != context->target.requested_pid)
            {
                continue;
            }
            selected_pid = entry.th32ProcessID;
            break;
        }
        DWORD *grown = realloc(candidate_pids, (candidate_count + 1) * sizeof *candidate_pids);
        if (grown == NULL)
        {
            free(candidate_pids);
            CloseHandle(process_list);
            return (DWORD)context_fail(context, RS3_READ_FAILED,
                                       "Client discovery allocation failed");
        }
        candidate_pids = grown;
        candidate_pids[candidate_count++] = entry.th32ProcessID;
    }
    CloseHandle(process_list);

    if (candidate_count == 1)
    {
        selected_pid = candidate_pids[0];
    }
    else if (candidate_count > 1)
    {
        client_window_selection selection = {candidate_pids, candidate_count, 0, 0};
        HWND foreground_window = GetForegroundWindow();
        if (foreground_window != NULL)
        {
            GetWindowThreadProcessId(foreground_window, &selection.foreground_pid);
        }
        EnumWindows(consider_client_window, (LPARAM)&selection);
        selected_pid = selection.selected_pid;
    }
    free(candidate_pids);

    if (selected_pid == 0)
    {
        if (candidate_count > 1)
        {
            context_fail(context, RS3_MULTIPLE_CLIENTS,
                         "Multiple RS3 processes without a visible game window; specify a PID");
        }
        else
        {
            context_fail(context, RS3_NO_CLIENT, "No RS3 client");
        }
    }
    return selected_pid;
}

/* Fingerprint the file and the loaded entry points before following any offsets. */
int process_attach(rs3_context *context)
{
    process_target *target = &context->target;
    target->pid = 0;
    target->process_creation_time = 0;
    target->executable_sha256[0] = '\0';

    DWORD pid = discover_client(context);
    if (pid == 0)
    {
        return 0;
    }
    target->pid = pid;
    target->process_handle = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, FALSE, pid);
    if (target->process_handle == NULL)
    {
        return context_fail(context, RS3_READ_FAILED, "Cannot open RS3 with read-only access");
    }

    HANDLE module_list = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid);
    if (module_list == INVALID_HANDLE_VALUE)
    {
        return context_fail(context, RS3_READ_FAILED, "Module discovery failed");
    }
    MODULEENTRY32W main_module = {0};
    main_module.dwSize = sizeof main_module;
    BOOL found_main_module = Module32FirstW(module_list, &main_module);
    CloseHandle(module_list);
    if (!found_main_module)
    {
        return context_fail(context, RS3_READ_FAILED, "Main module unavailable");
    }

    target->process_creation_time = process_creation_time(target->process_handle);
    if (target->process_creation_time == 0)
    {
        return context_fail(context, RS3_READ_FAILED, "Process identity unavailable");
    }
    target->module_base = (uint64_t)(uintptr_t)main_module.modBaseAddr;
    target->module_size = main_module.modBaseSize;
    if (!hash_file_sha256(main_module.szExePath, target->executable_sha256))
    {
        return context_fail(context, RS3_READ_FAILED, "Cannot hash the executable");
    }
    if (!process_select_profile(context, target->executable_sha256))
    {
        return 0;
    }
    target->attached = 1;
    return 1;
}
