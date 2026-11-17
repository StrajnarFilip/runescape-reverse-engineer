#ifndef RS3_TEST_MOUSE_H
#define RS3_TEST_MOUSE_H

#include <stdint.h>

#define MOUSE_LEFT 1
#define MOUSE_RIGHT 2
#define MOUSE_MIDDLE 4
#define MOUSE_ALL (MOUSE_LEFT | MOUSE_RIGHT | MOUSE_MIDDLE)

struct FakeMouse
{
    uint8_t held = 0;
    int x = 0;
    int y = 0;
    int wheel = 0;

    void begin()
    {
    }

    void press(uint8_t buttons)
    {
        held |= buttons;
    }

    void release(uint8_t buttons)
    {
        held &= ~buttons;
    }

    void move(int8_t horizontal, int8_t vertical, int8_t scroll)
    {
        x += horizontal;
        y += vertical;
        wheel += scroll;
    }
};

extern FakeMouse Mouse;
#endif
