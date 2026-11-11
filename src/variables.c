/* Domain registry and sparse variable tables. */
#include "variables.h"

#include "decode_utils.h"
#include "memory.h"

/** Maximum depth walked in the domain registry tree. */
#define MAX_REGISTRY_DEPTH 64u
/** Bytes read from each registry node; covers the key and the function pointer. */
#define REGISTRY_NODE_BYTES 104u
/** Offset of the handler entry in the registered function object's virtual table. */
#define REGISTRY_HANDLER_VTABLE_OFFSET 16u
/** Maximum chain length walked in one variable bucket. */
#define MAX_VARIABLE_CHAIN_LENGTH 1024u
/** Largest accepted bucket count of a variable table. */
#define MAX_VARIABLE_BUCKETS 1000000u

static int node_was_visited(const uint64_t *visited, unsigned visited_count, uint64_t node)
{
    for (unsigned index = 0; index < visited_count; index++)
    {
        if (visited[index] == node)
        {
            return 1;
        }
    }
    return 0;
}

int domain_capture(rs3_context *context, context_domain domain, uint64_t *context_address)
{
    const client_profile *profile = context->target.profile;
    uint32_t domain_id =
        domain == DOMAIN_CLIENT ? LAYOUT_CLIENT_DOMAIN_ID : LAYOUT_PLAYER_DOMAIN_ID;
    uint32_t handler_rva =
        domain == DOMAIN_CLIENT ? profile->client_handler_rva : profile->player_handler_rva;

    uint64_t node;
    uint64_t registry_root =
        context->target.module_base + profile->registry_rva + LAYOUT_REGISTRY_ROOT_OFFSET;
    if (!memory_read_pointer(context, registry_root, &node))
    {
        return 0;
    }

    uint64_t visited[MAX_REGISTRY_DEPTH];
    unsigned visited_count = 0;
    while (node != 0 && visited_count < MAX_REGISTRY_DEPTH)
    {
        if (node_was_visited(visited, visited_count, node))
        {
            return context_fail(context, RS3_READ_FAILED, "Domain registry cycle");
        }
        visited[visited_count++] = node;

        unsigned char node_bytes[REGISTRY_NODE_BYTES];
        if (!memory_read(context, node, node_bytes, sizeof node_bytes))
        {
            return 0;
        }
        int32_t key = decode_i32(node_bytes + LAYOUT_TREE_KEY_OFFSET);
        if (key == (int32_t)domain_id)
        {
            uint64_t function_object = decode_u64(node_bytes + LAYOUT_FUNCTION_POINTER_OFFSET);
            uint64_t function_vtable;
            uint64_t handler;
            if (!memory_read_pointer(context, function_object, &function_vtable) ||
                !memory_read_pointer(context, function_vtable + REGISTRY_HANDLER_VTABLE_OFFSET,
                                     &handler))
            {
                return 0;
            }
            if (handler != context->target.module_base + handler_rva)
            {
                return context_fail(context, RS3_UNSUPPORTED_CLIENT,
                                    "Domain callback profile mismatch");
            }
            if (!memory_read_pointer(context, function_object + LAYOUT_FUNCTION_CAPTURE_OFFSET,
                                     context_address))
            {
                return 0;
            }
            if (*context_address == 0)
            {
                return context_fail(context, RS3_READ_FAILED, "Domain context unavailable");
            }
            return 1;
        }
        uint32_t child_offset =
            key < (int32_t)domain_id ? LAYOUT_TREE_RIGHT_OFFSET : LAYOUT_TREE_LEFT_OFFSET;
        node = decode_u64(node_bytes + child_offset);
    }
    return context_fail(context, RS3_READ_FAILED, "Domain registry unavailable");
}

int variable_table_open(rs3_context *context, uint64_t header_address, variable_table *table)
{
    unsigned char header[16];
    if (!memory_read(context, header_address, header, sizeof header))
    {
        return 0;
    }
    table->bucket_array = decode_u64(header);
    table->bucket_count = decode_u64(header + 8);
    if (!is_valid_remote_pointer(table->bucket_array) || table->bucket_count < 1 ||
        table->bucket_count > MAX_VARIABLE_BUCKETS)
    {
        return context_fail(context, RS3_READ_FAILED, "Variable table unavailable");
    }
    uint64_t sentinel_address = table->bucket_array + table->bucket_count * sizeof(uint64_t);
    return memory_read(context, sentinel_address, &table->sentinel, sizeof table->sentinel);
}

int variable_table_unchanged(rs3_context *context, uint64_t header_address,
                             const variable_table *table)
{
    unsigned char header[16];
    return memory_read(context, header_address, header, sizeof header) &&
           decode_u64(header) == table->bucket_array &&
           decode_u64(header + 8) == table->bucket_count;
}

int variable_node_value(const unsigned char *node_bytes, int64_t *value)
{
    unsigned char type = node_bytes[LAYOUT_VARIABLE_NODE_TYPE_OFFSET];
    const unsigned char *payload = node_bytes + LAYOUT_VARIABLE_NODE_VALUE_OFFSET;
    if (type == VARIABLE_TYPE_INT32)
    {
        *value = decode_i32(payload);
        return 1;
    }
    if (type == VARIABLE_TYPE_INT64)
    {
        memcpy(value, payload, sizeof *value);
        return 1;
    }
    return 0;
}

int variable_table_find(rs3_context *context, const variable_table *table, uint32_t variable_id,
                        int64_t *value, int *present)
{
    *present = 0;
    uint64_t node;
    uint64_t bucket_address =
        table->bucket_array + (variable_id % table->bucket_count) * sizeof(uint64_t);
    if (!memory_read(context, bucket_address, &node, sizeof node))
    {
        return 0;
    }

    uint64_t visited[MAX_VARIABLE_CHAIN_LENGTH];
    unsigned visited_count = 0;
    while (node != 0 && node != table->sentinel)
    {
        if (visited_count == MAX_VARIABLE_CHAIN_LENGTH)
        {
            return context_fail(context, RS3_READ_FAILED, "Variable chain exceeds its bound");
        }
        if (node_was_visited(visited, visited_count, node))
        {
            return context_fail(context, RS3_READ_FAILED, "Variable chain cycle");
        }
        visited[visited_count++] = node;

        unsigned char node_bytes[VARIABLE_NODE_BYTES];
        if (!memory_read(context, node, node_bytes, sizeof node_bytes))
        {
            return 0;
        }
        if (decode_u32(node_bytes + LAYOUT_VARIABLE_NODE_KEY_OFFSET) == variable_id)
        {
            if (!variable_node_value(node_bytes, value))
            {
                return context_fail(context, RS3_READ_FAILED, "Non-numeric gameplay variable");
            }
            /* Reject a node that was freed or reused while it was being read. */
            uint32_t key_after_read;
            if (!memory_read(context, node + LAYOUT_VARIABLE_NODE_KEY_OFFSET, &key_after_read,
                             sizeof key_after_read) ||
                key_after_read != variable_id)
            {
                return context_fail(context, RS3_READ_FAILED, "Variable changed during read");
            }
            *present = 1;
            return 1;
        }
        node = decode_u64(node_bytes + LAYOUT_VARIABLE_NODE_NEXT_OFFSET);
    }
    return 1;
}
