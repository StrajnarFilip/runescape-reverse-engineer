/* Catalog format: 88-byte header, sorted 40-byte items, 48-byte abilities,
 * and UTF-8 strings. All words are little endian.
 */
#include "inventory.h"

#include <bcrypt.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

#include "profile_generated.h"

inventory_state *inventory_create(void) {
    return calloc(1, sizeof(inventory_state));
}

void inventory_reset(inventory_state *v) { if(v) { v->previous_valid=0; v->world=v->manager=0; } }
void inventory_free(inventory_state *v) { if(v) { free(v->catalog); free(v); } }
static int catalog_error(inventory_state *v,const char *message) {
    snprintf(v->catalog_error,sizeof v->catalog_error,"%s",message); return RS3_E_ABI;
}
int digest_bytes(const unsigned char *data,uint32_t size,unsigned char digest[32]) {
    BCRYPT_ALG_HANDLE a=NULL; BCRYPT_HASH_HANDLE h=NULL; int ok=0;
    if(BCryptOpenAlgorithmProvider(&a,BCRYPT_SHA256_ALGORITHM,NULL,0)<0 || BCryptCreateHash(a,&h,NULL,0,NULL,0,0)<0) goto done;
    if(BCryptHashData(h,(PUCHAR)data,size,0)>=0 && BCryptFinishHash(h,digest,32,0)>=0)ok=1;
done:
    if (h) BCryptDestroyHash(h);
    if (a) BCryptCloseAlgorithmProvider(a, 0);
    return ok;
}
int catalog_load_wide(inventory_state *v,const wchar_t *path) {
    HANDLE f=CreateFileW(path,GENERIC_READ,FILE_SHARE_READ,NULL,OPEN_EXISTING,0,NULL);
    if(f==INVALID_HANDLE_VALUE)return catalog_error(v,"Metadata file unavailable; generate rs3_catalog.bin");
    LARGE_INTEGER size; unsigned char *data=NULL; int result=RS3_E_ABI; DWORD got=0;
    if(!GetFileSizeEx(f,&size) || size.QuadPart<89 || size.QuadPart>16*1024*1024)goto done;
    data=malloc((size_t)size.QuadPart); if(!data){result=RS3_E_MEMORY;goto done;}
    if(!ReadFile(f,data,(DWORD)size.QuadPart,&got,NULL) || got!=(DWORD)size.QuadPart)goto done;
    uint32_t ni=u32(data+12),na=u32(data+16),ns=u32(data+20);
    if(memcmp(data,"RS3CAT1\0",8) || u32(data+8)!=1 || ni>1000000 || na>RS3_MAX_ABILITIES || !ns ||
       88ull+ni*40ull+na*48ull+ns!=(uint64_t)size.QuadPart)goto done;
    unsigned char hash[32];
    if(!digest_bytes(data+88,(uint32_t)size.QuadPart-88,hash) || memcmp(hash,data+56,32))goto done;
    for(unsigned i=0;i<32;i++) {
        unsigned byte; if(sscanf(&P_SHA256[i*2],"%2x",&byte)!=1 || data[24+i]!=byte)goto done;
    }
    const char *strings=(const char*)data+88+ni*40+na*48;
    if(strings[0] || strings[ns-1])goto done;
    for(unsigned kind=0;kind<2;kind++) {
        const unsigned char *records=data+88+(kind?ni*40:0); uint32_t count=kind?na:ni,stride=kind?48:40,last=0;
        for(uint32_t i=0;i<count;i++) {
            const unsigned char *r=records+i*stride; uint32_t id=u32(r),offset=u32(r+4);
            if((i && id<=last) || id>1000000 || offset>=ns)goto done;
            const char *end=memchr(strings+offset,0,ns-offset);
            if(!end || end-strings-offset>511 || !valid_utf8(strings+offset,(uint32_t)(end-strings-offset)))goto done;
            if(kind && (i32(r+8)<-1 || i32(r+12)<-1 || i32(r+8)>65535 || i32(r+12)>65535 ||
                        u32(r+16)>86400000 || u32(r+20)>15 || u32(r+28)>1 ||
                        (u32(r+28)==1 && (i32(r+8)<0 || i32(r+12)<0))))goto done;
            last=id;
        }
    }
    free(v->catalog);v->catalog=data;data=NULL;v->catalog_size=(size_t)size.QuadPart;
    v->item_count=ni;v->ability_count=na;v->strings_size=ns;
    v->item_defs=v->catalog+88;v->ability_defs=v->item_defs+ni*40;v->strings=(const char*)v->ability_defs+na*48;
    v->catalog_status=RS3_AVAILABLE;v->catalog_error[0]=0;result=RS3_OK;
done:
    free(data);CloseHandle(f);
    if(result && !v->catalog_error[0])catalog_error(v,"Invalid metadata format, checksum or client fingerprint");
    return result;
}
void inventory_default_catalog(rs3_context *c) {
    wchar_t path[32768]; HMODULE module=NULL;
    if(!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
        (LPCWSTR)(uintptr_t)&rs3_create,&module) || !GetModuleFileNameW(module,path,32768))return;
    wchar_t *slash=wcsrchr(path,L'\\');if(!slash || (size_t)(slash-path)+17>=32768)return;
    wcscpy(slash+1,L"rs3_catalog.bin");catalog_load_wide(c->inventory,path);
}
static void catalog_name(char out[128],const inventory_state *v,uint32_t offset) {
    const char *name=v->strings+offset;size_t n=strlen(name);if(n>127)n=127;
    while(n && !valid_utf8(name,(uint32_t)n))n--;
    memcpy(out,name,n);out[n]=0;
}
static const unsigned char *item_definition(const inventory_state *v,int32_t id) {
    uint32_t lo=0,hi=v->item_count;
    while(lo<hi){uint32_t mid=lo+(hi-lo)/2;uint32_t key=u32(v->item_defs+mid*40);if(key<(uint32_t)id)lo=mid+1;else hi=mid;}
    if(lo<v->item_count && u32(v->item_defs+lo*40)==(uint32_t)id)return v->item_defs+lo*40;
    return NULL;
}
static void describe_item(inventory_state *v,rs3_item *item) {
    item->doses=item->next_dose_item_id=item->equip_slot=-1;
    if(item->item_id<0)return;
    const unsigned char *r=item_definition(v,item->item_id);if(!r)return;
    item->flags|=RS3_ITEM_METADATA;item->family_id=u32(r+8);item->doses=i32(r+12);item->next_dose_item_id=i32(r+16);
    item->equip_slot=i32(r+20);item->combat_style=u32(r+24);item->equipment_flags=u32(r+28);
    item->effect_flags=u32(r+32);item->base_heal=u32(r+36);catalog_name(item->name,v,u32(r+4));
}

static int inventory_slots(rs3_context *c,rs3_snapshot *s,uint64_t state) {
    inventory_state *v=c->inventory;uint64_t manager,begin,end,cap;unsigned char header[24],after[24];
    if(!ptr(c,state+P_INV_MANAGER_OFFSET,&manager) || !manager)return fail(c,RS3_READ_FAILED,"Inventory manager unavailable");
    if(!rd(c,manager+8,header,24))return 0;
    begin=u64(header);end=u64(header+8);cap=u64(header+16);
    if(end<begin || cap<end || (end-begin)%72 || end-begin>sizeof v->entries || (begin && !pointer_valid(begin)))
        return fail(c,RS3_READ_FAILED,"Invalid inventory registry vector");
    size_t size=(size_t)(end-begin);uint32_t found=0,item_count=0,last_key=0;
    if(size && !rd(c,begin,v->entries,size))return 0;
    for(size_t offset=0;offset<size;offset+=72) {
        const unsigned char *r=v->entries+offset;uint32_t key=u32(r);
        if (offset && key <= last_key) return fail(c, RS3_READ_FAILED, "Unsorted inventory registry");
        last_key = key;
        if(key!=186 && key!=188)continue;
        uint32_t id=key/2,expected=id==93?28:19;
        uint64_t data=u64(r+24),stop=u64(r+32),capacity=u64(r+40);
        if(u32(r+16)!=id || stop<data || capacity<stop || stop-data!=expected*8)
            return fail(c,RS3_READ_FAILED,"Invalid inventory slot vector");
        unsigned char slots[28*8],check[28*8];
        if(!rd(c,data,slots,expected*8) || !rd(c,data,check,expected*8) || memcmp(slots,check,expected*8))
            return fail(c,RS3_READ_FAILED,"Inventory slots changed during read");
        for(uint32_t j=0;j<expected;j++) {
            rs3_item item={0};item.container_id=id;item.slot=j;item.item_id=i32(slots+j*8);item.quantity=u32(slots+j*8+4);
            if(item.item_id<-1 || item.item_id>1000000 || (item.item_id==-1 && item.quantity))
                return fail(c,RS3_READ_FAILED,"Invalid inventory item record");
            describe_item(v,&item);s->items[item_count++]=item;
        }
        found|=id==93?1u:2u;
    }
    if(!rd(c,manager+8,after,24) || memcmp(header,after,24) ||
       (size && (!rd(c,begin,v->checked_entries,size) || memcmp(v->entries,v->checked_entries,size))))
        return fail(c,RS3_READ_FAILED,"Inventory registry changed during read");
    uint64_t after_manager;
    if(!ptr(c,state+P_INV_MANAGER_OFFSET,&after_manager) || after_manager!=manager)
        return fail(c,RS3_READ_FAILED,"Inventory manager changed during read");
    if(found!=3)return fail(c,RS3_READ_FAILED,"Backpack or equipment container not transmitted");
    s->inventory_info.flags|=found;s->inventory_info.backpack_count=28;s->inventory_info.equipment_count=19;
    v->staged_manager=manager;return 1;
}
static int variable_node_page(rs3_context *c,uint64_t address,unsigned char out[48]) {
    inventory_state *v=c->inventory;uint64_t page=address&~4095ull;size_t offset=(size_t)(address-page);
    if(offset<=4096-48) {
        unsigned slot=(unsigned)(((page>>12)^(page>>20))&127);
        if(v->page_bases[slot]!=page) {
            v->page_bases[slot]=0;
            if(rd(c,page,v->pages[slot],4096))v->page_bases[slot]=page;
        }
        if(v->page_bases[slot]==page){memcpy(out,v->pages[slot]+offset,48);return 1;}
    }
    return rd(c,address,out,48); /* Guard-page boundary or sparse test fixture. */
}
/* Batch the bucket array and pages containing variable nodes. Re-read relevant
 * node values in a second page pass: torn/reused entries remain unknown. */
static int inventory_timers(rs3_context *c,uint64_t client) {
    inventory_state *v=c->inventory;unsigned char header[16],check[16];
    memset(v->wanted,0,sizeof v->wanted);memset(v->present,0,sizeof v->present);
    memset(v->page_bases,0,sizeof v->page_bases);unsigned selected=0;
    for(uint32_t i=0;i<v->ability_count;i++) {
        const unsigned char *r=v->ability_defs+i*48;
        if(u32(r+28)==1){v->wanted[u32(r+8)]=1;v->wanted[u32(r+12)]=1;}
    }
    if(!rd(c,client+P_CLIENT_TABLE_OFFSET,header,16))return 0;
    uint64_t table=u64(header),count=u64(header+8),sentinel;
    if(!table || !count || count>1000000)return fail(c,RS3_READ_FAILED,"Ability variable table unavailable");
    if(c->bucket_capacity<count){void *p=realloc(c->buckets,(size_t)count*8);if(!p)return 0;c->buckets=p;c->bucket_capacity=(size_t)count;}
    if(!rd(c,table,c->buckets,(size_t)count*8) || !rd(c,table+count*8,&sentinel,8))return 0;
    for(uint64_t bucket=0;bucket<count;bucket++) {
        uint64_t node=c->buckets[bucket],seen[128];unsigned n=0;
        while(node && node!=sentinel) {
            if(n==128)return fail(c,RS3_READ_FAILED,"Ability variable chain exceeds bound");
            for(unsigned j=0;j<n;j++)if(seen[j]==node)return fail(c,RS3_READ_FAILED,"Ability variable chain cycle");
            seen[n++]=node;unsigned char data[48];
            if(!variable_node_page(c,node,data))return 0;
            uint32_t id=u32(data);
            if(id<65536 && v->wanted[id]) {
                int64_t value;
                if(data[32]==0)value=i32(data+8);else if(data[32]==1)memcpy(&value,data+8,8);else return 0;
                if(selected==4096 || v->present[id])return fail(c,RS3_READ_FAILED,"Invalid ability variable selection");
                v->values[id]=value;v->present[id]=1;
                v->selected_addresses[selected]=node;v->selected_ids[selected]=id;v->selected_types[selected++]=data[32];
            }
            node=u64(data+40);
        }
    }
    memset(v->page_bases,0,sizeof v->page_bases);
    for(unsigned i=0;i<selected;i++) {
        unsigned char data[48];uint32_t id=v->selected_ids[i];int64_t value=0;
        if(!variable_node_page(c,v->selected_addresses[i],data))return 0;
        if(data[32]==0)value=i32(data+8);else if(data[32]==1)memcpy(&value,data+8,8);
        if(u32(data)!=id || data[32]!=v->selected_types[i] || value!=v->values[id])v->present[id]=0;
    }
    if(!rd(c,client+P_CLIENT_TABLE_OFFSET,check,16) || memcmp(header,check,16))
        return fail(c,RS3_READ_FAILED,"Ability variable table changed during read");
    return 1;
}
static uint32_t equipment_match(const rs3_snapshot *s,const rs3_ability *a) {
    if((s->inventory_info.flags&3)!=3)return RS3_EQUIPMENT_UNKNOWN;
    const rs3_item *main=NULL,*off=NULL;
    for(uint32_t i=0;i<RS3_MAX_ITEMS;i++)if(s->items[i].container_id==94) {
        if (s->items[i].slot == 3) main = s->items + i;
        if (s->items[i].slot == 5) off = s->items + i;
    }
    if(!main || !off)return RS3_EQUIPMENT_UNKNOWN;
    uint32_t req=a->requirements;
    if((main->item_id>=0 && !(main->flags&RS3_ITEM_METADATA)) || (off->item_id>=0 && !(off->flags&RS3_ITEM_METADATA)))return RS3_EQUIPMENT_UNKNOWN;
    int weapon=(main->equipment_flags&RS3_WEAPON)!=0,off_weapon=(off->equipment_flags&RS3_WEAPON)!=0;
    if((req&RS3_REQ_DUAL_WIELD) && (!weapon || !off_weapon || main->combat_style!=off->combat_style))return RS3_EQUIPMENT_BLOCKED;
    if((req&RS3_REQ_TWO_HANDED) && !(main->equipment_flags&RS3_TWO_HANDED))return RS3_EQUIPMENT_BLOCKED;
    if((req&RS3_REQ_WEAPON) && !weapon)return RS3_EQUIPMENT_BLOCKED;
    if(a->combat_style && !weapon && !(req&(RS3_REQ_DUAL_WIELD|RS3_REQ_TWO_HANDED|RS3_REQ_WEAPON)))return RS3_EQUIPMENT_UNKNOWN;
    if(a->combat_style && weapon && main->combat_style!=a->combat_style)return RS3_EQUIPMENT_BLOCKED;
    if((req&(RS3_REQ_DUAL_WIELD|RS3_REQ_TWO_HANDED|RS3_REQ_WEAPON)) && a->combat_style && main->combat_style!=a->combat_style)return RS3_EQUIPMENT_BLOCKED;
    if((req&RS3_REQ_SHIELD) && !((main->equipment_flags|off->equipment_flags)&RS3_SHIELD)) {
        /* Bone Shield can substitute for some defensives, but not all. Do not
         * claim blocked/castable until the ability exception/rune check is mapped. */
        if(!(s->inventory_info.flags&16) || s->inventory_info.bone_shield_tier)return RS3_EQUIPMENT_UNKNOWN;
        return RS3_EQUIPMENT_BLOCKED;
    }
    return RS3_EQUIPMENT_ALLOWED;
}
void inventory_stage(rs3_context *c,rs3_snapshot *s,uint64_t client,uint64_t state) {
    inventory_state *v=c->inventory;rs3_inventory_info *info=&s->inventory_info;
    info->catalog_status=v->catalog_status;info->status=RS3_AVAILABLE;
    if(v->catalog_status!=RS3_AVAILABLE){info->reason=RS3_CATALOG_UNAVAILABLE;snprintf(info->message,sizeof info->message,"%s",v->catalog_error);}
    else info->flags|=8;
    if(!inventory_slots(c,s,state)) {
        info->status=RS3_UNAVAILABLE;info->reason=RS3_READ_FAILED;info->backpack_count=info->equipment_count=0;
        info->flags&=~3u;snprintf(info->message,sizeof info->message,"%s",c->error);
    }
    uint64_t player,table,count,sentinel;int64_t bone;int present;
    if(capture(c,0,&player) && ptr(c,player+P_PLAYER_TABLE_OFFSET,&table) && rd(c,player+P_PLAYER_COUNT_OFFSET,&count,8) &&
       table && count && count<=1000000 && rd(c,table+count*8,&sentinel,8) &&
       variable(c,table,count,sentinel,P_INV_BONE_SHIELD_VARP,&bone,&present) && present) {
        info->bone_shield_tier=((uint32_t)bone>>30)&3u;info->flags|=16;
    }
    int timers_ok=v->ability_count?inventory_timers(c,client):0;if(timers_ok)info->flags|=4;
    for(uint32_t i=0;i<v->ability_count;i++) {
        const unsigned char *r=v->ability_defs+i*48;rs3_ability a={0};
        a.struct_id=u32(r);a.start_var=i32(r+8);a.end_var=i32(r+12);a.base_cooldown_ms=u32(r+16);
        a.requirements=u32(r+20);a.combat_style=u32(r+24);a.timer_source=u32(r+28);a.level_requirement=u32(r+32);
        a.adrenaline_requirement_raw=u32(r+36);a.flags=(u32(r+44)&1?2u:0u)|(u32(r+44)&2?4u:0u);catalog_name(a.name,v,u32(r+4));
        a.reason=a.timer_source?RS3_NOT_SET:RS3_UNMAPPED_TIMER;
        if(timers_ok && a.timer_source && v->present[a.start_var] && v->present[a.end_var]) {
            a.start_tick=(uint32_t)v->values[a.start_var];a.end_tick=(uint32_t)v->values[a.end_var];
            int32_t duration=tick_delta(a.end_tick,a.start_tick),elapsed=tick_delta(s->info.logic_tick,a.start_tick);
            a.reason=RS3_INCONSISTENT_CLOCK;
            if(duration>=0 && duration<=4320000 && elapsed>=0) {
                a.flags|=1;a.reason=RS3_REASON_NONE;a.duration_ms=duration*20;
                a.remaining_ms=elapsed<duration?(duration-elapsed)*20:0;
            }
        } else if(a.timer_source && !timers_ok)a.reason=RS3_READ_FAILED;
        a.equipment_match=equipment_match(s,&a);s->abilities[info->ability_count++]=a;
    }
}
int same_item(const rs3_item *a,const rs3_item *b,int dose_change) {
    return a->item_id>=0 && b->item_id>=0 && a->quantity==b->quantity &&
        (a->item_id==b->item_id || (dose_change && a->family_id==b->family_id &&
                                   a->doses>0 && b->doses==a->doses-1 && a->next_dose_item_id==b->item_id));
}
void inventory_event(rs3_context *c,rs3_snapshot *s,uint32_t kind,const rs3_item *before,const rs3_item *after) {
    inventory_state *v=c->inventory;rs3_item_event *e=v->events+v->sequence%ITEM_QUEUE_CAPACITY;
    memset(e,0,sizeof *e);e->before.item_id=e->after.item_id=-1;e->before.doses=e->after.doses=-1;
    e->sequence=++v->sequence;e->observed_at_ns=s->info.timestamp_ns;e->process_creation_time=c->creation_time;
    e->pid=c->pid;e->kind=kind;if(before)e->before=*before;if(after)e->after=*after;
    e->flags=(before?before->flags:0)|(after?after->flags:0);
}
void inventory_commit(rs3_context *c,rs3_snapshot *s,uint64_t world) {
    inventory_state *v=c->inventory;
    if((s->inventory_info.flags&3)!=3)return;
    if(v->world!=world || v->manager!=v->staged_manager){inventory_reset(v);v->world=world;v->manager=v->staged_manager;}
    int matched[RS3_MAX_ITEMS],used[RS3_MAX_ITEMS]={0};
    for(unsigned i=0;i<RS3_MAX_ITEMS;i++)matched[i]=-1;
    if(v->previous_valid) {
        /* Prefer a stable exact slot, then uniquely matching IDs across both
         * containers, then an explicitly defined next-dose variant. */
        for(unsigned i=0;i<RS3_MAX_ITEMS;i++)if(same_item(s->items+i,v->previous+i,0)) {matched[i]=(int)i;used[i]=1;}
        for(unsigned pass=0;pass<2;pass++)for(unsigned i=0;i<RS3_MAX_ITEMS;i++)if(matched[i]<0 && s->items[i].item_id>=0) {
            unsigned n=0,index=0,targets=0;
            for(unsigned j=0;j<RS3_MAX_ITEMS;j++)if(!used[j] && same_item(v->previous+j,s->items+i,(int)pass)){n++;index=j;}
            if(n==1)for(unsigned k=0;k<RS3_MAX_ITEMS;k++)if(matched[k]<0 && same_item(v->previous+index,s->items+k,(int)pass))targets++;
            if(n==1 && targets==1){matched[i]=(int)index;used[index]=1;}
            else if(n || targets)s->items[i].flags|=RS3_ITEM_AMBIGUOUS_IDENTITY;
        }
        for(unsigned i=0;i<RS3_MAX_ITEMS;i++)if(matched[i]<0 && !used[i] && s->items[i].item_id>=0 &&
            s->items[i].item_id==v->previous[i].item_id){matched[i]=(int)i;used[i]=1;}
    }
    AcquireSRWLockExclusive(&c->events_lock);
    for(unsigned i=0;i<RS3_MAX_ITEMS;i++) {
        rs3_item *now=s->items+i;rs3_item *old=matched[i]>=0?v->previous+matched[i]:NULL;
        if(now->item_id<0)continue;
        now->token=old?old->token:++v->token;
        if(!v->previous_valid)continue; /* Baseline is not a bag full of "new" items. */
        if(!old && !used[i] && v->previous[i].item_id>=0) {
            inventory_event(c,s,RS3_ITEM_CHANGED,v->previous+i,now);used[i]=1;
        } else if(!old)inventory_event(c,s,RS3_ITEM_ADDED,NULL,now);
        else if(old->item_id!=now->item_id)inventory_event(c,s,RS3_ITEM_DOSES_CHANGED,old,now);
        else if(old->quantity!=now->quantity)inventory_event(c,s,RS3_ITEM_QUANTITY_CHANGED,old,now);
        else if(old->container_id!=now->container_id || old->slot!=now->slot)inventory_event(c,s,RS3_ITEM_MOVED,old,now);
    }
    if(v->previous_valid)for(unsigned i=0;i<RS3_MAX_ITEMS;i++)if(!used[i] && v->previous[i].item_id>=0)
        inventory_event(c,s,RS3_ITEM_REMOVED,v->previous+i,NULL);
    memcpy(v->previous,s->items,sizeof v->previous);v->previous_valid=1;
    s->inventory_info.change_sequence=v->sequence;ReleaseSRWLockExclusive(&c->events_lock);
}

int32_t rs3_load_catalog(rs3_context *c,const char *path) {
    if (!c || !path) return RS3_E_ARGUMENT;
    int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, NULL, 0);
    if (n < 1 || n > 32768) return RS3_E_ARGUMENT;
    wchar_t *wide = malloc((size_t)n * sizeof *wide);
    if (!wide) return RS3_E_MEMORY;
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, wide, n);
    AcquireSRWLockExclusive(&c->control);
    int result = c->worker ? RS3_E_STATE : catalog_load_wide(c->inventory, wide);
    ReleaseSRWLockExclusive(&c->control);
    free(wide);
    return result;
}
int32_t rs3_snapshot_inventory_info(const rs3_snapshot *s,rs3_inventory_info *out,uint32_t size) {
    if (!s || !out) return RS3_E_ARGUMENT;
    if (size < sizeof *out) return RS3_E_ABI;
    *out = s->inventory_info;
    return RS3_OK;
}
int32_t rs3_snapshot_items(const rs3_snapshot *s,uint32_t container,rs3_item *out,uint32_t capacity,uint32_t *required) {
    if(!s || !required || (container && container!=93 && container!=94))return RS3_E_ARGUMENT;
    uint32_t count=s->inventory_info.backpack_count+s->inventory_info.equipment_count;*required=0;
    for(uint32_t i=0;i<count;i++)if(!container || s->items[i].container_id==container)(*required)++;
    if (capacity < *required) return RS3_E_BUFFER;
    if (*required && !out) return RS3_E_ARGUMENT;
    uint32_t at=0;for(uint32_t i=0;i<count;i++)if(!container || s->items[i].container_id==container)out[at++]=s->items[i];return RS3_OK;
}
int32_t rs3_snapshot_abilities(const rs3_snapshot *s,rs3_ability *out,uint32_t capacity,uint32_t *required) {
    if (!s || !required) return RS3_E_ARGUMENT;
    *required = s->inventory_info.ability_count;
    if (capacity < *required) return RS3_E_BUFFER;
    if (*required && !out) return RS3_E_ARGUMENT;
    if (*required) memcpy(out, s->abilities, *required * sizeof *out);
    return RS3_OK;
}
int32_t rs3_item_events_since(rs3_context *c,uint64_t after,rs3_item_event *out,uint32_t capacity,uint32_t *written,uint64_t *next,uint64_t *lost) {
    if(!c || !written || !next || !lost || (capacity && !out))return RS3_E_ARGUMENT;
    AcquireSRWLockShared(&c->events_lock);inventory_state *v=c->inventory;
    uint64_t latest=v->sequence,oldest=latest>=ITEM_QUEUE_CAPACITY?latest-ITEM_QUEUE_CAPACITY+1:1;
    *written=0;*lost=0;*next=after;if(after>latest){ReleaseSRWLockShared(&c->events_lock);return RS3_E_ARGUMENT;}
    uint64_t first=after+1;if(first<oldest){*lost=oldest-first;first=oldest;*next=oldest-1;}
    while(first<=latest && *written<capacity){out[(*written)++]=v->events[(first-1)%ITEM_QUEUE_CAPACITY];*next=first++;}
    ReleaseSRWLockShared(&c->events_lock);return RS3_OK;
}
