/* Helpers for interpreting bytes copied out of the client process. */
#include "decode_utils.h"

#include <windows.h>

int is_valid_utf8(const char *text, uint32_t length)
{
    if (length == 0)
    {
        return 1;
    }
    return MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text, (int)length, NULL, 0) > 0;
}
