/* Item and ability metadata catalog (rs3_catalog.bin). */
#include "catalog.h"

#include <windows.h>
#include <bcrypt.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "rs3/rs3.h"
#include "decode_utils.h"
#include "profile.h"

#define MAX_CATALOG_FILE_BYTES   (16u * 1024u * 1024u)
#define MAX_CATALOG_ITEMS        1000000u
#define MAX_CATALOG_RECORD_ID    1000000u
#define MAX_CATALOG_NAME_BYTES   511u
#define MAX_CATALOG_VARIABLE_ID  65535
#define MAX_CATALOG_COOLDOWN_MS  86400000u
#define MAX_CATALOG_REQUIREMENTS 15u
#define MODULE_PATH_CAPACITY     32768u

/** Section boundaries computed from the header counts. */
typedef struct catalog_layout
{
    uint32_t item_count;
    uint32_t ability_count;
    uint32_t strings_size;
    size_t item_records_offset;
    size_t ability_records_offset;
    size_t strings_offset;
} catalog_layout;

static void set_catalog_error(item_catalog *catalog, const char *message)
{
    snprintf(catalog->error_message, sizeof catalog->error_message, "%s", message);
}

int catalog_sha256(const unsigned char *data, uint32_t size,
                   unsigned char digest[CATALOG_SHA256_BYTES])
{
    BCRYPT_ALG_HANDLE algorithm = NULL;
    BCRYPT_HASH_HANDLE hash = NULL;
    int succeeded = 0;
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, NULL, 0) >= 0 &&
        BCryptCreateHash(algorithm, &hash, NULL, 0, NULL, 0, 0) >= 0 &&
        BCryptHashData(hash, (PUCHAR)data, size, 0) >= 0 &&
        BCryptFinishHash(hash, digest, CATALOG_SHA256_BYTES, 0) >= 0)
    {
        succeeded = 1;
    }
    if (hash != NULL)
    {
        BCryptDestroyHash(hash);
    }
    if (algorithm != NULL)
    {
        BCryptCloseAlgorithmProvider(algorithm, 0);
    }
    return succeeded;
}

static int hexadecimal_digit_value(char digit)
{
    if (digit >= '0' && digit <= '9')
    {
        return digit - '0';
    }
    if (digit >= 'a' && digit <= 'f')
    {
        return digit - 'a' + 10;
    }
    if (digit >= 'A' && digit <= 'F')
    {
        return digit - 'A' + 10;
    }
    return -1;
}

/** Returns nonzero when raw_digest equals the hexadecimal digest text. */
static int digest_matches_hexadecimal(const unsigned char *raw_digest, const char *hexadecimal)
{
    for (unsigned index = 0; index < CATALOG_SHA256_BYTES; index++)
    {
        int high = hexadecimal_digit_value(hexadecimal[index * 2]);
        int low = hexadecimal_digit_value(hexadecimal[index * 2 + 1]);
        if (high < 0 || low < 0 || raw_digest[index] != (unsigned char)(high * 16 + low))
        {
            return 0;
        }
    }
    return 1;
}

/** Validates the header, the checksums and the client fingerprint. */
static int validate_header(const unsigned char *data, size_t size, catalog_layout *layout)
{
    layout->item_count = decode_u32(data + CATALOG_HEADER_ITEM_COUNT);
    layout->ability_count = decode_u32(data + CATALOG_HEADER_ABILITY_COUNT);
    layout->strings_size = decode_u32(data + CATALOG_HEADER_STRINGS_SIZE);
    if (memcmp(data, CATALOG_MAGIC, CATALOG_MAGIC_BYTES) != 0 ||
        decode_u32(data + CATALOG_HEADER_FORMAT) != CATALOG_FORMAT_VERSION ||
        layout->item_count > MAX_CATALOG_ITEMS || layout->ability_count > RS3_MAX_ABILITIES ||
        layout->strings_size == 0)
    {
        return 0;
    }
    uint64_t expected_size =
        CATALOG_HEADER_BYTES + (uint64_t)layout->item_count * CATALOG_ITEM_RECORD_BYTES +
        (uint64_t)layout->ability_count * CATALOG_ABILITY_RECORD_BYTES + layout->strings_size;
    if (expected_size != size)
    {
        return 0;
    }
    layout->item_records_offset = CATALOG_HEADER_BYTES;
    layout->ability_records_offset =
        layout->item_records_offset + (size_t)layout->item_count * CATALOG_ITEM_RECORD_BYTES;
    layout->strings_offset = layout->ability_records_offset +
                             (size_t)layout->ability_count * CATALOG_ABILITY_RECORD_BYTES;

    unsigned char body_digest[CATALOG_SHA256_BYTES];
    if (!catalog_sha256(data + CATALOG_HEADER_BYTES, (uint32_t)(size - CATALOG_HEADER_BYTES),
                        body_digest) ||
        memcmp(body_digest, data + CATALOG_HEADER_BODY_SHA256, CATALOG_SHA256_BYTES) != 0)
    {
        return 0;
    }
    return digest_matches_hexadecimal(data + CATALOG_HEADER_CLIENT_SHA256, PROFILE_PRIMARY_SHA256);
}

/** Returns nonzero when offset names a bounded, terminated UTF-8 string. */
static int name_is_valid(const char *strings, uint32_t strings_size, uint32_t offset)
{
    if (offset >= strings_size)
    {
        return 0;
    }
    const char *terminator = memchr(strings + offset, '\0', strings_size - offset);
    if (terminator == NULL)
    {
        return 0;
    }
    size_t length = (size_t)(terminator - (strings + offset));
    return length <= MAX_CATALOG_NAME_BYTES && is_valid_utf8(strings + offset, (uint32_t)length);
}

static int variable_id_is_valid(int32_t variable_id)
{
    return variable_id >= -1 && variable_id <= MAX_CATALOG_VARIABLE_ID;
}

/** Validates the timer and requirement fields of one ability record. */
static int ability_fields_are_valid(const unsigned char *record)
{
    int32_t start_var = decode_i32(record + CATALOG_ABILITY_START_VAR);
    int32_t end_var = decode_i32(record + CATALOG_ABILITY_END_VAR);
    uint32_t timer_source = decode_u32(record + CATALOG_ABILITY_TIMER_SOURCE);
    if (!variable_id_is_valid(start_var) || !variable_id_is_valid(end_var) ||
        decode_u32(record + CATALOG_ABILITY_BASE_COOLDOWN_MS) > MAX_CATALOG_COOLDOWN_MS ||
        decode_u32(record + CATALOG_ABILITY_REQUIREMENTS) > MAX_CATALOG_REQUIREMENTS ||
        timer_source > RS3_TIMER_CLIENT_PAIR)
    {
        return 0;
    }
    /* A client timer pair must index the timer arrays; -1 is never allowed there. */
    return timer_source != RS3_TIMER_CLIENT_PAIR || (start_var >= 0 && end_var >= 0);
}

/** Validates the record order, IDs and name offsets of one record section. */
static int validate_records(const unsigned char *records, uint32_t record_count,
                            size_t record_bytes, const char *strings, uint32_t strings_size,
                            int records_are_abilities)
{
    uint32_t previous_id = 0;
    for (uint32_t index = 0; index < record_count; index++)
    {
        const unsigned char *record = records + index * record_bytes;
        uint32_t record_id = decode_u32(record);
        uint32_t name_offset = decode_u32(record + 4);
        if ((index != 0 && record_id <= previous_id) || record_id > MAX_CATALOG_RECORD_ID ||
            !name_is_valid(strings, strings_size, name_offset))
        {
            return 0;
        }
        if (records_are_abilities && !ability_fields_are_valid(record))
        {
            return 0;
        }
        previous_id = record_id;
    }
    return 1;
}

static int validate_catalog(const unsigned char *data, size_t size, catalog_layout *layout)
{
    if (!validate_header(data, size, layout))
    {
        return 0;
    }
    const char *strings = (const char *)data + layout->strings_offset;
    /* Offset zero is the empty string, and the table must end with a terminator. */
    if (strings[0] != '\0' || strings[layout->strings_size - 1] != '\0')
    {
        return 0;
    }
    return validate_records(data + layout->item_records_offset, layout->item_count,
                            CATALOG_ITEM_RECORD_BYTES, strings, layout->strings_size, 0) &&
           validate_records(data + layout->ability_records_offset, layout->ability_count,
                            CATALOG_ABILITY_RECORD_BYTES, strings, layout->strings_size, 1);
}

/**
 * Allocates the direct-address table of sorted item records. Returns nonzero on
 * success; *index is NULL when there are no items.
 */
static int create_item_index(const unsigned char *item_records, uint32_t item_count,
                             uint32_t **index, uint32_t *index_size)
{
    *index = NULL;
    *index_size = 0;
    if (item_count == 0)
    {
        return 1;
    }
    /* Records are sorted, so the last record holds the highest ID. */
    const unsigned char *last_record =
        item_records + (size_t)(item_count - 1) * CATALOG_ITEM_RECORD_BYTES;
    uint32_t highest_item_id = decode_u32(last_record + CATALOG_ITEM_ID);
    if (highest_item_id > MAX_CATALOG_RECORD_ID)
    {
        return 0;
    }
    uint32_t *table = calloc((size_t)highest_item_id + 1, sizeof *table);
    if (table == NULL)
    {
        return 0;
    }
    for (uint32_t record_index = 0; record_index < item_count; record_index++)
    {
        const unsigned char *record =
            item_records + (size_t)record_index * CATALOG_ITEM_RECORD_BYTES;
        table[decode_u32(record + CATALOG_ITEM_ID)] = record_index + 1;
    }
    *index = table;
    *index_size = highest_item_id + 1;
    return 1;
}

int catalog_build_item_index(item_catalog *catalog)
{
    uint32_t *index;
    uint32_t index_size;
    int created =
        create_item_index(catalog->item_records, catalog->item_count, &index, &index_size);
    free(catalog->item_index);
    catalog->item_index = index;
    catalog->item_index_size = index_size;
    return created;
}

int32_t catalog_load_file(item_catalog *catalog, const wchar_t *path)
{
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (file == INVALID_HANDLE_VALUE)
    {
        set_catalog_error(catalog, "rs3_catalog.bin not found beside rs3_native.dll; "
                                   "build the rs3_catalog CMake target");
        return RS3_E_ABI;
    }

    int32_t result = RS3_E_ABI;
    unsigned char *data = NULL;
    LARGE_INTEGER file_size;
    if (!GetFileSizeEx(file, &file_size) || file_size.QuadPart <= CATALOG_HEADER_BYTES ||
        file_size.QuadPart > MAX_CATALOG_FILE_BYTES)
    {
        goto cleanup;
    }
    size_t size = (size_t)file_size.QuadPart;
    data = malloc(size);
    if (data == NULL)
    {
        result = RS3_E_MEMORY;
        goto cleanup;
    }
    DWORD bytes_read = 0;
    catalog_layout layout;
    if (!ReadFile(file, data, (DWORD)size, &bytes_read, NULL) || bytes_read != (DWORD)size ||
        !validate_catalog(data, size, &layout))
    {
        goto cleanup;
    }
    uint32_t *item_index;
    uint32_t item_index_size;
    if (!create_item_index(data + layout.item_records_offset, layout.item_count, &item_index,
                           &item_index_size))
    {
        result = RS3_E_MEMORY;
        goto cleanup;
    }

    /* Publish the new backing buffer only after every record and offset is validated. */
    free(catalog->data);
    free(catalog->item_index);
    catalog->data = data;
    data = NULL;
    catalog->item_index = item_index;
    catalog->item_index_size = item_index_size;
    catalog->size = size;
    catalog->item_count = layout.item_count;
    catalog->ability_count = layout.ability_count;
    catalog->strings_size = layout.strings_size;
    catalog->item_records = catalog->data + layout.item_records_offset;
    catalog->ability_records = catalog->data + layout.ability_records_offset;
    catalog->strings = (const char *)catalog->data + layout.strings_offset;
    catalog->status = RS3_AVAILABLE;
    catalog->error_message[0] = '\0';
    result = RS3_OK;

cleanup:
    free(data);
    CloseHandle(file);
    if (result == RS3_E_MEMORY)
    {
        set_catalog_error(catalog, "Metadata allocation failed");
    }
    else if (result != RS3_OK)
    {
        set_catalog_error(catalog, "Invalid metadata format, checksum or client fingerprint");
    }
    return result;
}

void catalog_load_default(item_catalog *catalog)
{
    wchar_t path[MODULE_PATH_CAPACITY];
    HMODULE module = NULL;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                                GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            (LPCWSTR)(uintptr_t)&rs3_create, &module) ||
        !GetModuleFileNameW(module, path, MODULE_PATH_CAPACITY))
    {
        return;
    }
    wchar_t *last_separator = wcsrchr(path, L'\\');
    size_t file_name_length = wcslen(CATALOG_FILE_NAME);
    if (last_separator == NULL ||
        (size_t)(last_separator - path) + 1 + file_name_length >= MODULE_PATH_CAPACITY)
    {
        return;
    }
    wcscpy(last_separator + 1, CATALOG_FILE_NAME);
    catalog_load_file(catalog, path);
}

void catalog_free(item_catalog *catalog)
{
    free(catalog->data);
    catalog->data = NULL;
    free(catalog->item_index);
    catalog->item_index = NULL;
    catalog->item_index_size = 0;
}

int catalog_find_item(const item_catalog *catalog, int32_t item_id,
                      catalog_item_definition *definition)
{
    if (item_id < 0 || (uint32_t)item_id >= catalog->item_index_size)
    {
        return 0;
    }
    uint32_t entry = catalog->item_index[item_id];
    if (entry == 0)
    {
        return 0;
    }
    const unsigned char *record =
        catalog->item_records + (size_t)(entry - 1) * CATALOG_ITEM_RECORD_BYTES;
    definition->item_id = decode_u32(record + CATALOG_ITEM_ID);
    definition->name_offset = decode_u32(record + CATALOG_ITEM_NAME_OFFSET);
    definition->family_id = decode_u32(record + CATALOG_ITEM_FAMILY_ID);
    definition->doses = decode_i32(record + CATALOG_ITEM_DOSES);
    definition->next_dose_item_id = decode_i32(record + CATALOG_ITEM_NEXT_DOSE_ITEM_ID);
    definition->equip_slot = decode_i32(record + CATALOG_ITEM_EQUIP_SLOT);
    definition->combat_style = decode_u32(record + CATALOG_ITEM_COMBAT_STYLE);
    definition->equipment_flags = decode_u32(record + CATALOG_ITEM_EQUIPMENT_FLAGS);
    definition->effect_flags = decode_u32(record + CATALOG_ITEM_EFFECT_FLAGS);
    definition->base_heal = decode_u32(record + CATALOG_ITEM_BASE_HEAL);
    return 1;
}

void catalog_get_ability(const item_catalog *catalog, uint32_t index,
                         catalog_ability_definition *definition)
{
    const unsigned char *record = catalog->ability_records + index * CATALOG_ABILITY_RECORD_BYTES;
    definition->struct_id = decode_u32(record + CATALOG_ABILITY_STRUCT_ID);
    definition->name_offset = decode_u32(record + CATALOG_ABILITY_NAME_OFFSET);
    definition->start_var = decode_i32(record + CATALOG_ABILITY_START_VAR);
    definition->end_var = decode_i32(record + CATALOG_ABILITY_END_VAR);
    definition->base_cooldown_ms = decode_u32(record + CATALOG_ABILITY_BASE_COOLDOWN_MS);
    definition->requirements = decode_u32(record + CATALOG_ABILITY_REQUIREMENTS);
    definition->combat_style = decode_u32(record + CATALOG_ABILITY_COMBAT_STYLE);
    definition->timer_source = decode_u32(record + CATALOG_ABILITY_TIMER_SOURCE);
    definition->level_requirement = decode_u32(record + CATALOG_ABILITY_LEVEL_REQUIREMENT);
    definition->adrenaline_requirement_raw =
        decode_u32(record + CATALOG_ABILITY_ADRENALINE_REQUIREMENT);
    definition->definition_flags = decode_u32(record + CATALOG_ABILITY_DEFINITION_FLAGS);
}

void catalog_copy_name(const item_catalog *catalog, uint32_t name_offset,
                       char output[CATALOG_NAME_CAPACITY])
{
    const char *name = catalog->strings + name_offset;
    size_t length = strlen(name);
    if (length > CATALOG_NAME_CAPACITY - 1)
    {
        length = CATALOG_NAME_CAPACITY - 1;
    }
    /* Truncate on a UTF-8 character boundary when the ABI name field fills up. */
    while (length != 0 && !is_valid_utf8(name, (uint32_t)length))
    {
        length--;
    }
    memcpy(output, name, length);
    output[length] = '\0';
}
