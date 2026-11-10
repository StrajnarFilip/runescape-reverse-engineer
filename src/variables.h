/*
 * Domain registry and sparse variable tables.
 *
 * The client registers its player and client contexts in a binary tree keyed
 * by domain ID. Each context owns a hash table of variables (VarPlayer or
 * VarClient) whose chained nodes hold a tagged 32-bit or 64-bit value.
 */
#ifndef RS3_VARIABLES_H
#define RS3_VARIABLES_H

#include <stdint.h>

#include "context.h"

/** Registered context domains. */
typedef enum context_domain
{
    DOMAIN_PLAYER,
    DOMAIN_CLIENT
} context_domain;

/** A validated variable hash table. */
typedef struct variable_table
{
    uint64_t bucket_array; /**< Address of the bucket pointer array. */
    uint64_t bucket_count;
    uint64_t sentinel; /**< End-of-chain marker stored after the last bucket. */
} variable_table;

/** Byte size of a variable node as read by the decoder. */
#define VARIABLE_NODE_BYTES 48u
/** Values of the node type tag. */
#define VARIABLE_TYPE_INT32 0u
#define VARIABLE_TYPE_INT64 1u

/**
 * Resolves a domain through the registry and verifies its callback handler
 * before trusting the captured context pointer.
 */
int domain_capture(rs3_context *context, context_domain domain, uint64_t *context_address);

/**
 * Reads the table header stored at header_address (bucket pointer, bucket
 * count) and the sentinel that follows the buckets.
 */
int variable_table_open(rs3_context *context, uint64_t header_address, variable_table *table);

/** Re-reads the table header and returns nonzero when it still describes table. */
int variable_table_unchanged(rs3_context *context, uint64_t header_address,
                             const variable_table *table);

/**
 * Looks up one variable. An absent sparse entry clears *present and still
 * returns success; it never reads as a numeric zero.
 */
int variable_table_find(rs3_context *context, const variable_table *table, uint32_t variable_id,
                        int64_t *value, int *present);

/**
 * Decodes the tagged value of a node copied into node_bytes.
 * Returns 0 for a non-numeric tag.
 */
int variable_node_value(const unsigned char *node_bytes, int64_t *value);

#endif /* RS3_VARIABLES_H */
