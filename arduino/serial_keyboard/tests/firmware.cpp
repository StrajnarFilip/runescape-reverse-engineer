/* Run the actual sketch against non-HID host stubs. No serial device is opened. */
#include <Arduino.h>
#include <Keyboard.h>

unsigned long testMillis = 0;
FakeUSB USBDevice;
FakeSerial Serial;
FakeKeyboard Keyboard;

#include "../serial_keyboard.ino"

static void command(const char *line, const char *expected)
{
    Serial.input = line;
    while (*Serial.input)
    {
        loop();
    }
    assert(strcmp(Serial.lastReply, expected) == 0);
}

int main()
{
    setup();
    command("HELLO\n", "RS3_KEYBOARD 1");
    command("PRESS E0\n", "OK");
    assert(Keyboard.held[KEY_LEFT_CTRL]);
    command("TAP 04\n", "OK");
    assert(!Keyboard.held[140]);
    testMillis = 999;
    loop();
    assert(Keyboard.held[KEY_LEFT_CTRL]);
    command("PING\n", "OK");
    testMillis = 1998;
    loop();
    assert(Keyboard.held[KEY_LEFT_CTRL]);
    testMillis = 1999;
    loop();
    assert(!Keyboard.held[KEY_LEFT_CTRL]);
    command("PRESS 04\n", "OK");
    command("TAP 04\n", "ERR HELD");
    Serial.open = false;
    loop();
    assert(!Keyboard.held[140]);
    Serial.open = true;
    command("RELEASE_ALL\r\n", "OK");
    command("PRESS 123456789012345\n", "ERR FRAME");
    command("TAP FF\n", "ERR KEY");
    Keyboard.failPress = true;
    command("TAP 04\n", "ERR ROLLOVER");
    Keyboard.failPress = false;
    testMillis = 0xffffff00ul;
    command("PRESS E0\n", "OK");
    testMillis = 0x300ul;
    loop();
    assert(!Keyboard.held[KEY_LEFT_CTRL]);
    return 0;
}
