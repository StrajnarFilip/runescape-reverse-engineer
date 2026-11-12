#include <Arduino.h>
#include <Keyboard.h>
#include <Mouse.h>
#include <stdlib.h>
#include <string.h>

static constexpr unsigned long serialBaud = 115200;
static constexpr size_t lineCapacity = sizeof("MOVE -127 -127\r");
static constexpr long mouseDeltaLimit = 127;
static constexpr unsigned long mouseClickMillis = 25;
static constexpr uint8_t firstKeyUsage = 0x04;
static constexpr uint8_t lastKeyUsage = 0x73;
static constexpr uint8_t firstModifierUsage = 0xE0;
static constexpr uint8_t lastModifierUsage = 0xE7;
static constexpr uint8_t keyboardUsageOffset = 136;
static constexpr uint8_t bitsPerByte = 8;

static char commandLine[lineCapacity];
static size_t lineLength;
static bool discardLine;
static bool portWasOpen;
static uint8_t heldKeys[lastModifierUsage / bitsPerByte + 1];

static int hexDigit(char digit)
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

static int parseUsage(const char *argument)
{
    if (strlen(argument) != 2)
    {
        return -1;
    }
    const int highDigit = hexDigit(argument[0]);
    const int lowDigit = hexDigit(argument[1]);
    if (highDigit < 0 || lowDigit < 0)
    {
        return -1;
    }
    const int usage = (highDigit << 4) | lowDigit;
    if ((usage >= firstKeyUsage && usage <= lastKeyUsage) ||
        (usage >= firstModifierUsage && usage <= lastModifierUsage))
    {
        return usage;
    }
    return -1;
}

static void releaseAllKeys()
{
    Keyboard.releaseAll();
    memset(heldKeys, 0, sizeof(heldKeys));
}

static bool parseMouseDelta(const char *argument, char **end, long *value)
{
    *value = strtol(argument, end, 10);
    return *end != argument && *value >= -mouseDeltaLimit && *value <= mouseDeltaLimit;
}

static const __FlashStringHelper *moveMouse(const char *argument)
{
    long horizontal;
    long vertical;
    char *end;
    if (!parseMouseDelta(argument, &end, &horizontal) || *end != ' ' ||
        !parseMouseDelta(end + 1, &end, &vertical) || *end != '\0')
    {
        return F("ERR MOUSE");
    }
    Mouse.move(horizontal, vertical, 0);
    return F("OK");
}

static const __FlashStringHelper *clickMouse(const char *argument)
{
    uint8_t button;
    if (strcmp(argument, "L") == 0)
    {
        button = MOUSE_LEFT;
    }
    else if (strcmp(argument, "R") == 0)
    {
        button = MOUSE_RIGHT;
    }
    else if (strcmp(argument, "M") == 0)
    {
        button = MOUSE_MIDDLE;
    }
    else
    {
        return F("ERR MOUSE");
    }
    Mouse.press(button);
    delay(mouseClickMillis);
    Mouse.release(button);
    return F("OK");
}

static const __FlashStringHelper *runCommand()
{
    if (strcmp(commandLine, "RELEASE_ALL") == 0)
    {
        releaseAllKeys();
        return F("OK");
    }

    char *argument = strchr(commandLine, ' ');
    if (argument == nullptr)
    {
        return F("ERR COMMAND");
    }
    *argument = '\0';
    ++argument;

    if (strcmp(commandLine, "MOVE") == 0)
    {
        return moveMouse(argument);
    }
    if (strcmp(commandLine, "CLICK") == 0)
    {
        return clickMouse(argument);
    }
    if (strcmp(commandLine, "WHEEL") == 0)
    {
        long delta;
        char *end;
        if (!parseMouseDelta(argument, &end, &delta) || *end != '\0')
        {
            return F("ERR MOUSE");
        }
        Mouse.move(0, 0, delta);
        return F("OK");
    }

    const bool isPress = strcmp(commandLine, "PRESS") == 0;
    const bool isRelease = strcmp(commandLine, "RELEASE") == 0;
    const bool isTap = strcmp(commandLine, "TAP") == 0;
    if (!isPress && !isRelease && !isTap)
    {
        return F("ERR COMMAND");
    }
    const int usage = parseUsage(argument);
    if (usage < 0)
    {
        return F("ERR KEY");
    }

    // Use raw HID positions, avoiding the library's ASCII layout and implicit modifiers.
    const uint8_t key = usage >= firstModifierUsage ? KEY_LEFT_CTRL + usage - firstModifierUsage
                                                    : keyboardUsageOffset + usage;
    uint8_t &heldByte = heldKeys[usage / bitsPerByte];
    const uint8_t heldMask = 1u << (usage % bitsPerByte);
    const bool isHeld = (heldByte & heldMask) != 0;

    if (isRelease)
    {
        if (isHeld)
        {
            Keyboard.release(key);
            heldByte &= ~heldMask;
        }
        return F("OK");
    }
    if (isHeld)
    {
        return isTap ? F("ERR HELD") : F("OK");
    }
    if (Keyboard.press(key) == 0)
    {
        return F("ERR ROLLOVER");
    }
    if (isTap)
    {
        Keyboard.release(key);
    }
    else
    {
        heldByte |= heldMask;
    }
    return F("OK");
}

/// Initialize the Leonardo's native USB serial and HID interfaces.
void setup()
{
    Serial.begin(serialBaud);
    Keyboard.begin();
    Mouse.begin();
}

/// Read serial commands while DTR is asserted and clear keyboard state on close.
void loop()
{
    if (!USBDevice.configured() || !Serial.dtr())
    {
        if (portWasOpen)
        {
            releaseAllKeys();
            lineLength = 0;
            discardLine = false;
            portWasOpen = false;
        }
        Serial.read();
        return;
    }
    portWasOpen = true;

    const int received = Serial.read();
    if (received == '\n')
    {
        if (lineLength != 0 && commandLine[lineLength - 1] == '\r')
        {
            --lineLength;
        }
        commandLine[lineLength] = '\0';
        Serial.println(discardLine ? F("ERR FRAME") : runCommand());
        lineLength = 0;
        discardLine = false;
    }
    else if (received >= 0 && !discardLine)
    {
        if (received == '\0' || lineLength == sizeof(commandLine) - 1)
        {
            discardLine = true;
        }
        else
        {
            commandLine[lineLength++] = static_cast<char>(received);
        }
    }
}
