#ifndef RS3_TEST_ARDUINO_H
#define RS3_TEST_ARDUINO_H
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <assert.h>

struct __FlashStringHelper;
#define F(value) reinterpret_cast<const __FlashStringHelper *>(value)

extern unsigned long testMillis;
inline unsigned long millis()
{
    return testMillis;
}

inline void delay(unsigned long milliseconds)
{
    testMillis += milliseconds;
}

struct FakeUSB
{
    bool connected = true;
    bool configured()
    {
        return connected;
    }
};

struct FakeSerial
{
    bool open = true;
    const char *input = "";
    char lastReply[64] = {};
    void begin(unsigned long)
    {
    }
    bool dtr()
    {
        return open;
    }
    int read()
    {
        return *input ? static_cast<unsigned char>(*input++) : -1;
    }
    void println(const __FlashStringHelper *value)
    {
        const char *text = reinterpret_cast<const char *>(value);
        assert(strlen(text) < sizeof lastReply);
        strcpy(lastReply, text);
    }
};
extern FakeUSB USBDevice;
extern FakeSerial Serial;
#endif
