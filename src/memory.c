/* Bounded, accounted remote-memory reads. */
#include "memory.h"

#include "decode_utils.h"

int windows_process_read(void *user_data, uint64_t address, void *output, size_t size)
{
    rs3_context *context = user_data;
    SIZE_T bytes_read = 0;
    BOOL succeeded = ReadProcessMemory(context->target.process_handle,
                                       (const void *)(uintptr_t)address, output, size, &bytes_read);
    return succeeded && bytes_read == size;
}

int memory_read(rs3_context *context, uint64_t address, void *output, size_t size)
{
    /* Count attempted reads, including failures, for the per-sample diagnostics. */
    context->read_call_count++;
    context->read_byte_count += size;

    int range_is_valid = is_valid_remote_pointer(address) && size != 0 &&
                         size <= MAX_REMOTE_READ_BYTES && size <= REMOTE_POINTER_LIMIT - address;
    if (!range_is_valid ||
        !context->read_memory(context->read_memory_user_data, address, output, size))
    {
        return context_fail(context, RS3_READ_FAILED, "Process-memory read failed");
    }
    return 1;
}

int memory_read_pointer(rs3_context *context, uint64_t address, uint64_t *pointer)
{
    if (!memory_read(context, address, pointer, sizeof *pointer))
    {
        return 0;
    }
    if (*pointer != 0 && !is_valid_remote_pointer(*pointer))
    {
        return context_fail(context, RS3_READ_FAILED, "Invalid remote pointer");
    }
    return 1;
}
