/* Minimal C consumer. No import library or language-specific bridge required.
 * Usage: read.exe [absolute/path/rs3_native.dll] [seconds]
 * The DLL is loaded from an explicit path or the executable directory only.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define RS3_STATIC
#include "rs3/rs3.h"

int main(int argc,char **argv) {
    char path[MAX_PATH];
    if (argc>1) snprintf(path,sizeof path,"%s",argv[1]);
    else {
        if (!GetModuleFileNameA(NULL,path,MAX_PATH)) return 1;
        char *slash=strrchr(path,'\\'); if(!slash)return 1;
        snprintf(slash+1,(size_t)(path+MAX_PATH-slash-1),"rs3_native.dll");
    }
    HMODULE dll=LoadLibraryExA(path,NULL,LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!dll) { fprintf(stderr,"Cannot load DLL: %lu\n",GetLastError()); return 1; }
    /* Derive function pointer types from declarations using explicit C types. */
#define FN(name,ret,args) ret (*name) args; do { FARPROC symbol=GetProcAddress(dll,#name); \
    if (!symbol) { fprintf(stderr,"Missing export: %s\n",#name); return 1; } \
    _Static_assert(sizeof name==sizeof symbol,"Function pointer size"); memcpy(&name,&symbol,sizeof name); } while(0)
    FN(rs3_abi_version,uint32_t,(void));
    FN(rs3_create,int32_t,(const rs3_options*,rs3_context**));
    FN(rs3_destroy,void,(rs3_context*));
    FN(rs3_snapshot_create,int32_t,(rs3_snapshot**));
    FN(rs3_snapshot_destroy,void,(rs3_snapshot*));
    FN(rs3_start,int32_t,(rs3_context*,uint32_t));
    FN(rs3_copy_latest,int32_t,(rs3_context*,rs3_snapshot*));
    FN(rs3_snapshot_info,int32_t,(const rs3_snapshot*,rs3_info*,uint32_t));
    FN(rs3_snapshot_player,int32_t,(const rs3_snapshot*,rs3_player*,uint32_t));
    FN(rs3_snapshot_npcs,int32_t,(const rs3_snapshot*,rs3_npc*,uint32_t,uint32_t*));
    rs3_context *context=NULL; rs3_snapshot *snapshot=NULL;
    int result=1;
    if (rs3_abi_version()!=RS3_ABI_VERSION || rs3_create(NULL,&context) || rs3_snapshot_create(&snapshot)) goto done;
    if (rs3_start(context,100)) goto done;
    unsigned seconds=argc>2?(unsigned)atoi(argv[2]):5;
    for (unsigned i=0;i<seconds*10;i++) {
        rs3_info info; rs3_player player; rs3_npc npcs[RS3_MAX_NPCS]; uint32_t count=0;
        Sleep(100);
        if (rs3_copy_latest(context,snapshot) || rs3_snapshot_info(snapshot,&info,sizeof info) ||
            rs3_snapshot_player(snapshot,&player,sizeof player) ||
            rs3_snapshot_npcs(snapshot,npcs,RS3_MAX_NPCS,&count)) goto done;
        printf("sample=%llu status=%u PID=%u NPCs=%u ", (unsigned long long)info.sequence,info.status,info.pid,count);
        if (player.valid_variables&(1ull<<RS3_HEALTH)) printf("HP=%lld ",(long long)player.raw_variables[RS3_HEALTH]);
        else printf("HP=unknown ");
        printf("read=%.3fms %s\n",info.sample_duration_ns/1e6,info.message);
    }
    result=0;
done:
    rs3_destroy(context); rs3_snapshot_destroy(snapshot); FreeLibrary(dll); return result;
}
