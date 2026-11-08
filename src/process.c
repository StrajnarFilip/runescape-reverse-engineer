/* Read-only Windows process discovery, fingerprinting and bounded memory access. */
#include "internal.h"

#include <bcrypt.h>
#include <stdio.h>
#include <stdlib.h>
#include <tlhelp32.h>
#include <wchar.h>

#include "profile_generated.h"

uint64_t monotonic_ns(void) {
    LARGE_INTEGER c,f; QueryPerformanceCounter(&c); QueryPerformanceFrequency(&f);
    return (uint64_t)(c.QuadPart/f.QuadPart)*1000000000ull+
           (uint64_t)(c.QuadPart%f.QuadPart)*1000000000ull/(uint64_t)f.QuadPart;
}
uint64_t unix_ns(void) {
    FILETIME t; ULARGE_INTEGER x; GetSystemTimePreciseAsFileTime(&t);
    x.LowPart=t.dwLowDateTime; x.HighPart=t.dwHighDateTime;
    return (x.QuadPart-116444736000000000ull)*100ull;
}
int fail(rs3_context *c,uint32_t reason,const char *message) {
    c->reason=reason; snprintf(c->error,sizeof c->error,"%s",message); return 0;
}
int windows_read(void *user,uint64_t address,void *out,size_t size) {
    rs3_context *c=user; SIZE_T got=0;
    return ReadProcessMemory(c->process,(const void*)(uintptr_t)address,out,size,&got) && got==size;
}
int rd(rs3_context *c,uint64_t a,void *out,size_t n) {
    c->calls++; c->bytes+=n;
    if (!pointer_valid(a) || !n || n>16*1024*1024 || n>0x7fffffffffffULL-a ||
        !c->read(c->read_user,a,out,n)) return fail(c,RS3_READ_FAILED,"Process-memory read failed");
    return 1;
}
int ptr(rs3_context *c,uint64_t a,uint64_t *out) {
    if (!rd(c,a,out,8)) return 0;
    return !*out || pointer_valid(*out) || fail(c,RS3_READ_FAILED,"Invalid remote pointer");
}
int signatures(rs3_context *c,const char *hash) {
    c->profile = NULL;
    const client_profile *profile = strcmp(hash, p_profile.sha256) == 0 ? &p_profile
                                  : strcmp(hash, pos_profile.sha256) == 0 ? &pos_profile : NULL;
    if (!profile) {
        return fail(c, RS3_UNSUPPORTED_CLIENT, "Unsupported executable SHA-256; offsets not used");
    }
    unsigned char b[64];
    for (size_t i=0;i<profile->signature_count;i++) {
        const profile_signature *s=profile->signatures+i;
        if (!rd(c,c->module_base+s->rva,b,s->length)) return 0;
        if (memcmp(b,s->bytes,s->length)) return fail(c,RS3_UNSUPPORTED_CLIENT,"Loaded code does not match the pinned profile");
    }
    c->profile = profile;
    return 1;
}
static int hash_file(const wchar_t *path,char result[72]) {
    BCRYPT_ALG_HANDLE algorithm=NULL; BCRYPT_HASH_HANDLE hash=NULL;
    unsigned char digest[32],buffer[65536]; DWORD n=0; int ok=0;
    HANDLE file=CreateFileW(path,GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,NULL,OPEN_EXISTING,0,NULL);
    if (file==INVALID_HANDLE_VALUE) return 0;
    if (BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,NULL,0)<0 ||
        BCryptCreateHash(algorithm,&hash,NULL,0,NULL,0,0)<0) goto done;
    for (;;) {
        if (!ReadFile(file,buffer,sizeof buffer,&n,NULL)) goto done;
        if (!n) break;
        if (BCryptHashData(hash,buffer,n,0)<0) goto done;
    }
    if (BCryptFinishHash(hash,digest,sizeof digest,0)<0) goto done;
    for (unsigned i=0;i<32;i++) snprintf(result+i*2,3,"%02x",digest[i]);
    ok=1;
done:
    if (hash) BCryptDestroyHash(hash);
    if (algorithm) BCryptCloseAlgorithmProvider(algorithm,0);
    CloseHandle(file); return ok;
}
void detach(rs3_context *c) {
    if (c->process) CloseHandle(c->process);
    c->process=NULL; c->attached=0; c->previous_valid=0; c->clock_context=0;
    c->profile = NULL;
    reset_identities(c);
    inventory_reset(c->inventory);
}
void schedule_retry(rs3_context *c,uint64_t now) {
    c->retry_reason=c->reason;
    snprintf(c->retry_error,sizeof c->retry_error,"%s",c->error);
    detach(c); c->retry_after=now+(uint64_t)c->retry_ms*1000000ull;
}
typedef struct client_windows {
    const DWORD *pids;
    size_t count;
    DWORD foreground_pid, selected_pid;
} client_windows;

static BOOL CALLBACK consider_client_window(HWND window, LPARAM param) {
    client_windows *selection = (client_windows *)param;
    DWORD pid = 0;
    if (!IsWindowVisible(window) || GetWindow(window, GW_OWNER)) return TRUE;
    if (!GetWindowThreadProcessId(window, &pid)) return TRUE;
    for (size_t i = 0; i < selection->count; i++) {
        if (selection->pids[i] != pid) continue;
        if (GetWindowTextLengthW(window) > 0 &&
            (!selection->selected_pid || pid == selection->foreground_pid ||
             (selection->selected_pid != selection->foreground_pid && pid < selection->selected_pid))) {
            selection->selected_pid = pid;
        }
        break;
    }
    return TRUE;
}

static DWORD discover_client(rs3_context *c) {
    HANDLE list = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (list == INVALID_HANDLE_VALUE) return fail(c, RS3_READ_FAILED, "Process discovery failed");
    PROCESSENTRY32W entry = {0};
    entry.dwSize = sizeof entry;
    DWORD pid = 0, *pids = NULL;
    size_t count = 0;
    for (BOOL found = Process32FirstW(list, &entry); found; found = Process32NextW(list, &entry)) {
        if (_wcsicmp(entry.szExeFile, L"rs2client.exe")) continue;
        if (c->requested_pid) {
            if (entry.th32ProcessID != c->requested_pid) continue;
            pid = entry.th32ProcessID;
            break;
        }
        DWORD *next = realloc(pids, (count + 1) * sizeof *pids);
        if (!next) {
            free(pids);
            CloseHandle(list);
            return fail(c, RS3_READ_FAILED, "Client discovery allocation failed");
        }
        pids = next;
        pids[count++] = entry.th32ProcessID;
    }
    CloseHandle(list);
    if (count == 1) {
        pid = pids[0];
    } else if (count > 1) {
        client_windows selection = {pids, count, 0, 0};
        HWND foreground = GetForegroundWindow();
        if (foreground) GetWindowThreadProcessId(foreground, &selection.foreground_pid);
        EnumWindows(consider_client_window, (LPARAM)&selection);
        pid = selection.selected_pid;
    }
    free(pids);
    if (!pid) fail(c, count > 1 ? RS3_MULTIPLE_CLIENTS : RS3_NO_CLIENT,
                   count > 1 ? "Multiple RS3 processes without a visible game window; specify a PID" : "No RS3 client");
    return pid;
}

/* Fingerprint the file and loaded entrypoints before following any offsets. */
int attach(rs3_context *c) {
    c->pid=0; c->creation_time=0; c->actual_sha256[0]=0;
    DWORD pid = discover_client(c);
    if (!pid) return 0;
    c->pid=pid;
    c->process=OpenProcess(PROCESS_QUERY_INFORMATION|PROCESS_VM_READ,FALSE,pid);
    if (!c->process) return fail(c,RS3_READ_FAILED,"Cannot open RS3 with read-only access");
    HANDLE list=CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32,pid);
    MODULEENTRY32W module={0}; module.dwSize=sizeof module;
    if (list==INVALID_HANDLE_VALUE) return fail(c,RS3_READ_FAILED,"Module discovery failed");
    int found=Module32FirstW(list,&module); CloseHandle(list);
    if (!found) return fail(c,RS3_READ_FAILED,"Main module unavailable");
    FILETIME born,ended,kernel,user; ULARGE_INTEGER time;
    if (!GetProcessTimes(c->process,&born,&ended,&kernel,&user)) return fail(c,RS3_READ_FAILED,"Process identity unavailable");
    time.LowPart=born.dwLowDateTime; time.HighPart=born.dwHighDateTime;
    c->creation_time=time.QuadPart; c->module_base=(uint64_t)(uintptr_t)module.modBaseAddr;
    c->module_size=module.modBaseSize;
    if (!hash_file(module.szExePath,c->actual_sha256)) return fail(c,RS3_READ_FAILED,"Cannot hash the executable");
    if (!signatures(c,c->actual_sha256)) return 0;
    c->attached=1; return 1;
}
