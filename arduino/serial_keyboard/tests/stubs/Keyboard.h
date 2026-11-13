#ifndef RS3_TEST_KEYBOARD_H
#define RS3_TEST_KEYBOARD_H
#include <stdint.h>
#include <string.h>
#define KEY_LEFT_CTRL 128
struct FakeKeyboard
{
    bool held[256] = {};
    unsigned releases = 0;
    bool failPress = false;
    void begin()
    {
    }
    int press(uint8_t key)
    {
        if (failPress)
        {
            return 0;
        }
        held[key] = true;
        return 1;
    }
    void release(uint8_t key)
    {
        held[key] = false;
    }
    void releaseAll()
    {
        memset(held, 0, sizeof held);
        releases++;
    }
};
extern FakeKeyboard Keyboard;
#endif
