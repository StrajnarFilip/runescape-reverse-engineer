/*
 * Deterministic fake-memory fixtures for the native decoder tests.
 *
 * A fixture replaces the context's memory reader with a set of zero-filled
 * blocks at fixed fake addresses. Tests link the private library objects
 * directly; the DLL exports no testing or injection interface.
 */
#ifndef RS3_TEST_FIXTURE_H
#define RS3_TEST_FIXTURE_H

/* Assertions must run even when the toolchain defines NDEBUG for optimized builds. */
#undef NDEBUG
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#include "context.h"

/* Fake addresses of the client structures. */
#define FIXTURE_MODULE_BASE       0x140000000ull
#define FIXTURE_PLAYER            0x200000ull
#define FIXTURE_CLIENT            0x300000ull
#define FIXTURE_WORLD             0x400000ull
#define FIXTURE_NPC_MANAGER       0x500000ull
#define FIXTURE_ACTOR             0x600000ull
#define FIXTURE_PLAYER_TABLE      0x700000ull
#define FIXTURE_CLIENT_TABLE      0x710000ull
#define FIXTURE_VARIABLE_NODES    0x720000ull
#define FIXTURE_NPC_TABLE         0x730000ull
#define FIXTURE_NPC_NODE          0x740000ull
#define FIXTURE_NPC_CONFIG        0x750000ull
#define FIXTURE_HIT_RING          0x760000ull
#define FIXTURE_HIT_RECORD        0x770000ull
#define FIXTURE_SKILLS_MANAGER    0x780000ull
#define FIXTURE_SKILL_ENTRIES     0x790000ull
#define FIXTURE_DOMAIN_NODES      0x800000ull
#define FIXTURE_FUNCTION_OBJECTS  0x810000ull
#define FIXTURE_FUNCTION_VTABLES  0x820000ull
#define FIXTURE_INVENTORY_MANAGER 0x830000ull
#define FIXTURE_INVENTORY_ENTRIES 0x840000ull
#define FIXTURE_BACKPACK_SLOTS    0x850000ull
#define FIXTURE_EQUIPMENT_SLOTS   0x860000ull
#define FIXTURE_EXTRA_VARIABLES   0x870000ull

/** Every variable table in the fixture has this many buckets plus a sentinel. */
#define FIXTURE_BUCKET_COUNT          97u
#define FIXTURE_SENTINEL              UINT64_MAX
#define FIXTURE_VARIABLE_NODE_BYTES   48u
#define FIXTURE_PID                   123u
#define FIXTURE_PROCESS_CREATION_TIME 456u
#define FIXTURE_TIMESTAMP_NS          1000000000ull
#define FIXTURE_NPC_RUNTIME_ID        42u
#define FIXTURE_LOGIC_TICK            1005u

/* Variable IDs of the ability timers and their extra nodes in the inventory fixture. */
#define FIXTURE_FLURRY_START_VAR       2107u
#define FIXTURE_FLURRY_END_VAR         2108u
#define FIXTURE_RESONANCE_START_VAR    2147u
#define FIXTURE_RESONANCE_END_VAR      2148u
#define FIXTURE_BONE_SHIELD_NODE_INDEX 4u

/* Item IDs of the synthetic catalog. */
#define FIXTURE_ITEM_SWORD            100
#define FIXTURE_ITEM_OFF_HAND_SWORD   101
#define FIXTURE_ITEM_TWO_HANDED_SWORD 102
#define FIXTURE_ITEM_SHIELD           103
#define FIXTURE_ITEM_DEFENDER         104
#define FIXTURE_ITEM_BREW_6           200
#define FIXTURE_ITEM_BREW_5           201
#define FIXTURE_ITEM_OTHER            300

typedef struct memory_block
{
    uint64_t base_address;
    size_t size;
    unsigned char *data;
} memory_block;

typedef struct fixture
{
    memory_block blocks[64];
    unsigned block_count;
    /*
     * Optional mutation that reproduces a concurrent client update: on the
     * mutate_on_read-th read of trigger_address, write mutation_value to
     * mutation_address just before the read is served.
     */
    uint64_t trigger_address;
    unsigned trigger_read_count;
    unsigned mutate_on_read;
    uint64_t mutation_address;
    uint32_t mutation_value;
    rs3_context *context;
    rs3_snapshot *snapshot;
} fixture;

/** Creates a context whose reads are served by the fixture and that has no blocks. */
fixture *fixture_create_empty(void);

/** Creates a fixture with player, client, variables and one NPC for a profile. */
fixture *fixture_create_for_profile(const client_profile *profile);

/** Creates fixture_create_for_profile(&profile_primary). */
fixture *fixture_create(void);

/** Adds the inventory registry, timer variables and a synthetic catalog. */
fixture *fixture_create_inventory(void);

void fixture_destroy(fixture *test_fixture);

/** Maps a zero-filled block of size bytes at base_address. */
void fixture_add_block(fixture *test_fixture, uint64_t base_address, size_t size);

/** Returns the storage of size bytes at address, or NULL when it is not mapped. */
unsigned char *fixture_bytes(fixture *test_fixture, uint64_t address, size_t size);

void fixture_put_u32(fixture *test_fixture, uint64_t address, uint32_t value);
void fixture_put_u64(fixture *test_fixture, uint64_t address, uint64_t value);
uint64_t fixture_get_u64(fixture *test_fixture, uint64_t address);

/** Links a variable node into the bucket chain of a fixture table and sets its value. */
void fixture_put_variable(fixture *test_fixture, uint64_t table, uint64_t node,
                          uint32_t variable_id, uint64_t value);

/** Writes one backpack or equipment slot record. */
void fixture_set_slot(fixture *test_fixture, uint64_t slots, unsigned slot, int32_t item_id,
                      uint32_t quantity);

/** Decodes one frame into the fixture snapshot, as the worker would. */
int fixture_decode(fixture *test_fixture);

/** Arranges a mutation on the read_number-th read of trigger_address. */
void fixture_mutate_on_read(fixture *test_fixture, uint64_t trigger_address, unsigned read_number,
                            uint64_t mutation_address, uint32_t mutation_value);

/** Removes any arranged mutation. */
void fixture_clear_mutation(fixture *test_fixture);

/** Prints the test name and runs it. */
#define RUN_TEST(test_function)                                                                    \
    do                                                                                             \
    {                                                                                              \
        printf("RUN  %s\n", #test_function);                                                       \
        test_function();                                                                           \
    } while (0)

#endif /* RS3_TEST_FIXTURE_H */
