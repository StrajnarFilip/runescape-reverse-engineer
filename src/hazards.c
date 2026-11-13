/* Snapshot-only hazard candidates. No guide-derived areas or guessed roles. */
#include "hazards.h"

#include <string.h>

_Static_assert(sizeof(rs3_hazard) == 88, "ABI hazard entry");
_Static_assert(sizeof(rs3_hazard_info) == 80, "ABI hazard info");

#define MAX_HAZARD_PLANE    3
#define MAX_ACTOR_FOOTPRINT 64u

/** Identity evidence: original-profile manual 300% capture, 2026-11-11. */
typedef struct captured_npc_identity
{
    int32_t config_id;
    const char *name;
    uint32_t kind;
    uint32_t variant;
} captured_npc_identity;

static const captured_npc_identity captured_identities[] = {
    {22893, "Pure Anima Font", RS3_HAZARD_FONT, RS3_HAZARD_PURE},
    {22894, "Volcanic Anima Font", RS3_HAZARD_FONT, RS3_HAZARD_VOLCANIC},
    {22895, "Corrupt Anima Font", RS3_HAZARD_FONT, RS3_HAZARD_CORRUPT},
    {22896, "Pure Anima Font", RS3_HAZARD_FONT, RS3_HAZARD_PURE},
    {22905, "Pure anima-golem", RS3_HAZARD_GOLEM, RS3_HAZARD_PURE},
    {22906, "Corrupt anima-golem", RS3_HAZARD_GOLEM, RS3_HAZARD_CORRUPT},
    {22907, "Volcanic anima-golem", RS3_HAZARD_GOLEM, RS3_HAZARD_VOLCANIC},
    {22918, "Anima-golem", RS3_HAZARD_GOLEM, RS3_HAZARD_VARIANT_UNKNOWN},
};

/** Match a bounded name; nonterminated client strings cannot escape their buffer. */
static int name_equals(const rs3_npc *npc, const char *name)
{
    size_t length = strlen(name);
    return (npc->flags & RS3_NPC_NAME) && npc->name_length == length && length < sizeof npc->name &&
           memcmp(npc->name, name, length) == 0 && npc->name[length] == '\0';
}

/** Unknown config variants with a captured name remain unclassified candidates. */
static int find_candidate(const rs3_npc *npc, const captured_npc_identity **identity)
{
    int candidate = 0;
    *identity = NULL;
    for (size_t index = 0; index < sizeof captured_identities / sizeof captured_identities[0];
         index++)
    {
        const captured_npc_identity *known = &captured_identities[index];
        int same_id = (npc->flags & RS3_NPC_CONFIG) && npc->config_id == known->config_id;
        int same_name = name_equals(npc, known->name);
        candidate |= same_id || same_name;
        if (same_id && same_name)
        {
            *identity = known;
        }
    }
    return candidate;
}

/** Copy only a pose with matching lifetime and same-frame scene provenance. */
static void copy_tile(const rs3_snapshot *snapshot, uint32_t npc_index, rs3_hazard *hazard)
{
    const rs3_spatial_info *spatial = &snapshot->spatial;
    const rs3_actor_spatial *pose = &snapshot->npc_spatial[npc_index];
    const rs3_info *frame = &snapshot->info;
    const rs3_hazard_info *info = &snapshot->hazard_info;
    const uint32_t required_flags =
        RS3_SPATIAL_TILE | RS3_SPATIAL_FINE_POSITION | RS3_SPATIAL_SAME_WORLD;
    hazard->tile_reason = RS3_HAZARD_SPATIAL_UNAVAILABLE;
    if (spatial->status != RS3_AVAILABLE && spatial->status != RS3_PARTIAL)
    {
        return;
    }
    if (npc_index >= spatial->npc_count || spatial->pid != info->pid ||
        spatial->process_creation_time != info->process_creation_time ||
        spatial->sample_sequence != frame->sequence ||
        spatial->scene_epoch != snapshot->combat.scene_epoch ||
        spatial->observed_ns != frame->monotonic_ns || pose->instance_id != hazard->instance_id ||
        pose->runtime_id != hazard->runtime_id || hazard->instance_id == 0)
    {
        hazard->tile_reason = RS3_HAZARD_WRONG_SCENE;
        return;
    }
    if ((pose->valid_mask & required_flags) != required_flags || !pose->tile.valid ||
        pose->tile.x < 0 || pose->tile.x >= MAX_TILE_COORDINATE || pose->tile.y < 0 ||
        pose->tile.y >= MAX_TILE_COORDINATE || pose->tile.plane < 0 ||
        pose->tile.plane > MAX_HAZARD_PLANE || pose->footprint_size == 0 ||
        pose->footprint_size > MAX_ACTOR_FOOTPRINT)
    {
        return;
    }
    hazard->tile = pose->tile;
    hazard->footprint_size = pose->footprint_size;
    hazard->flags |= RS3_HAZARD_TILE | RS3_HAZARD_FOOTPRINT;
    hazard->tile_reason = RS3_REASON_NONE;
}

void hazards_commit(rs3_context *context, rs3_snapshot *snapshot, int npcs_decoded)
{
    rs3_hazard_info *info = &snapshot->hazard_info;
    memset(info, 0, sizeof *info);
    /* Erase storage as well as the count: failure/scene changes retain no old entries. */
    memset(snapshot->hazards, 0, sizeof snapshot->hazards);
    info->observed_ns = snapshot->info.monotonic_ns;
    info->sample_sequence = snapshot->info.sequence;
    info->scene_epoch = snapshot->combat.scene_epoch;
    info->process_creation_time = context->target.process_creation_time;
    info->pid = context->target.pid;
    info->status = RS3_UNAVAILABLE;
    info->reason = RS3_HAZARD_UNMAPPED;
    info->unmapped_channels = RS3_HAZARD_CHANNEL_ALL;
    if ((snapshot->info.status != RS3_AVAILABLE && snapshot->info.status != RS3_PARTIAL) ||
        !npcs_decoded || snapshot->info.npc_count > RS3_MAX_NPCS)
    {
        info->reason = RS3_READ_FAILED;
        return;
    }

    for (uint32_t npc_index = 0; npc_index < snapshot->info.npc_count; npc_index++)
    {
        const rs3_npc *npc = &snapshot->npcs[npc_index];
        const captured_npc_identity *identity;
        if (!find_candidate(npc, &identity))
        {
            continue;
        }
        info->candidate_count++;
        if (info->count == RS3_MAX_HAZARDS)
        {
            info->dropped_count++;
            continue;
        }
        rs3_hazard *hazard = &snapshot->hazards[info->count++];
        hazard->observed_ns = info->observed_ns;
        hazard->instance_id = npc->instance_id;
        hazard->runtime_id = npc->runtime_id;
        hazard->config_id = (npc->flags & RS3_NPC_CONFIG) ? npc->config_id : -1;
        hazard->reason = RS3_HAZARD_IDENTITY_UNMAPPED;
        hazard->timing_reason = RS3_HAZARD_UNMAPPED;
        hazard->role_reason = RS3_HAZARD_UNMAPPED;
        /* Captured identities are scoped to their executable, not extrapolated to others. */
        if (context->target.profile == &profile_primary && identity != NULL &&
            npc->instance_id != 0)
        {
            hazard->kind = identity->kind;
            hazard->variant = identity->variant;
            hazard->flags |= RS3_HAZARD_IDENTITY;
            hazard->reason = RS3_HAZARD_NEEDS_CALIBRATION;
            info->observed_channels |= identity->kind == RS3_HAZARD_FONT
                                           ? RS3_HAZARD_CHANNEL_FONTS
                                           : RS3_HAZARD_CHANNEL_GOLEMS;
        }
        copy_tile(snapshot, npc_index, hazard);
        if (!(hazard->flags & RS3_HAZARD_TILE))
        {
            info->unlocated_count++;
        }
    }
    if (info->count != 0)
    {
        info->status = RS3_PARTIAL;
        info->reason = info->dropped_count ? RS3_HAZARD_OVERFLOW : RS3_HAZARD_NEEDS_CALIBRATION;
    }
}

uint32_t rs3_hazards_sizeof(uint32_t structure)
{
    switch (structure)
    {
        case RS3_HAZARD_SIZE_INFO:
            return sizeof(rs3_hazard_info);
        case RS3_HAZARD_SIZE_ENTRY:
            return sizeof(rs3_hazard);
        default:
            return 0;
    }
}
