/*
 * Version-pinned client profiles.
 *
 * tools/generate_profile.py produces the layout macros (profile_generated.h)
 * and the two profile tables (profile_generated.c) from profiles/*.json. The
 * reader only follows offsets after the executable hash and every code
 * signature of a profile match.
 */
#ifndef RS3_PROFILE_H
#define RS3_PROFILE_H

#include <stddef.h>
#include <stdint.h>

#include "rs3/rs3.h"
#include "profile_generated.h"

/** Expected bytes of the loaded code at one relative virtual address. */
typedef struct profile_signature
{
    uint32_t rva;
    uint32_t length;
    unsigned char bytes[64];
} profile_signature;

/** Executable-specific addresses of one supported client build. */
typedef struct client_profile
{
    const char *sha256;        /**< Lowercase hexadecimal SHA-256 of rs2client.exe. */
    const char *version;       /**< Full version label. */
    const char *short_version; /**< Label copied into rs3_info.client_version. */
    const profile_signature *signatures;
    size_t signature_count;
    uint32_t registry_rva;             /**< Domain registry of the player/client contexts. */
    uint32_t player_handler_rva;       /**< Callback that owns the player domain. */
    uint32_t client_handler_rva;       /**< Callback that owns the client domain. */
    uint32_t client_vtable_rva;        /**< Virtual table of the client context. */
    uint32_t npc_manager_vtable_rva;   /**< Virtual table of the NPC manager. */
    uint32_t npc_entity_vtable_rva;    /**< Virtual table of an NPC actor. */
    uint32_t npc_animation_vtable_rva; /**< Animation component; 0 when unverified. */
    uint32_t npc_action_stamp_offset;  /**< Actor action application stamp; 0 when absent. */
    uint32_t player_actor_vtable_rva;  /**< Local player actor; 0 when unverified. */
    int has_position;                  /**< Nonzero when the player tile layout is verified. */
} client_profile;

/** Original 951-3 executable. */
extern const client_profile profile_primary;
/** Compatibility Mode executable with the same combat layout and a player tile. */
extern const client_profile profile_compatibility;
/** Variable IDs in enum rs3_variable order. */
extern const uint32_t profile_variable_ids[RS3_VARIABLE_COUNT];

#endif /* RS3_PROFILE_H */
