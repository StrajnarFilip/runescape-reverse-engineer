/* Read-only Windows process discovery, fingerprinting and bounded memory access. */
#include "internal.h"

#include <bcrypt.h>
#include <stdio.h>
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
    unsigned char b[64];
    if (strcmp(hash,P_SHA256)) return fail(c,RS3_UNSUPPORTED_CLIENT,"Unsupported executable SHA-256; offsets not used");
    for (size_t i=0;i<sizeof p_signatures/sizeof p_signatures[0];i++) {
        const profile_signature *s=p_signatures+i;
        if (!rd(c,c->module_base+s->rva,b,s->length)) return 0;
        if (memcmp(b,s->bytes,s->length)) return fail(c,RS3_UNSUPPORTED_CLIENT,"Loaded code does not match the pinned profile");
    }
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
    reset_identities(c);
    inventory_reset(c->inventory);
}
void schedule_retry(rs3_context *c,uint64_t now) {
    c->retry_reason=c->reason;
    snprintf(c->retry_error,sizeof c->retry_error,"%s",c->error);
    detach(c); c->retry_after=now+(uint64_t)c->retry_ms*1000000ull;
}
/* Fingerprint the file and loaded entrypoints before following any offsets. */
int attach(rs3_context *c) {
    c->pid=0; c->creation_time=0; c->actual_sha256[0]=0;
    HANDLE list=CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS,0);
    PROCESSENTRY32W pe={0}; pe.dwSize=sizeof pe; uint32_t pid=0,count=0;
    if (list==INVALID_HANDLE_VALUE) return fail(c,RS3_READ_FAILED,"Process discovery failed");
    if (Process32FirstW(list,&pe)) do {
        if (!_wcsicmp(pe.szExeFile,L"rs2client.exe") && (!c->requested_pid || pe.th32ProcessID==c->requested_pid)) {
            pid=pe.th32ProcessID; count++;
        }
    } while (Process32NextW(list,&pe));
    CloseHandle(list);
    if (count!=1) { c->pid=0; return fail(c,count?RS3_MULTIPLE_CLIENTS:RS3_NO_CLIENT,
        count?"Multiple RS3 clients; select a PID":"No RS3 client"); }
    c->pid=pid;
    c->process=OpenProcess(PROCESS_QUERY_INFORMATION|PROCESS_VM_READ,FALSE,pid);
    if (!c->process) return fail(c,RS3_READ_FAILED,"Cannot open RS3 with read-only access");
    list=CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|TH32CS_SNAPMODULE32,pid);
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
