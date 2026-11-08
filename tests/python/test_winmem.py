"""Exercise real read-only Win32 access against this test process."""
import ctypes
import os
import struct
import unittest

from winmem import ProcessMemory, MemoryReadError

class ReadOnlyProcessTests(unittest.TestCase):
    def setUp(self):
        self.memory = ProcessMemory(os.getpid())

    def tearDown(self):
        self.memory.close()

    def test_reads_exact_values_and_observes_change(self):
        value = ctypes.c_int32(123456789)
        address = ctypes.addressof(value)
        self.assertEqual(self.memory.i32(address), value.value)
        value.value = -987654
        self.assertEqual(self.memory.i32(address), value.value)

    def test_pointer_width_and_zero(self):
        target = ctypes.c_int32(42)
        pointer = ctypes.c_void_p(ctypes.addressof(target))
        self.assertEqual(self.memory.pointer(ctypes.addressof(pointer)), ctypes.addressof(target))
        pointer.value = None
        self.assertEqual(self.memory.pointer(ctypes.addressof(pointer)), 0)

    def test_invalid_and_unmapped_reads_fail(self):
        for address, size in [(0, 4), (1, 4), (0x7FFFFFFFFFFE, 4), (0x10000, 0)]:
            with self.subTest(address=address, size=size):
                with self.assertRaises(MemoryReadError):
                    self.memory.read(address, size)

    def test_module_and_region_discovery(self):
        self.assertEqual(self.memory.read(self.memory.module.base, 2), b"MZ")
        value = ctypes.c_int32(5)
        address = ctypes.addressof(value)
        self.assertTrue(any(base <= address < base + size for base, size, _, _ in self.memory.regions()))
        self.assertTrue(self.memory.alive())
        self.assertGreater(self.memory.creation_time, 0)

    def test_closed_handle_cannot_read(self):
        value = ctypes.c_int32(1)
        self.memory.close()
        self.assertFalse(self.memory.alive())
        with self.assertRaises(MemoryReadError):
            self.memory.i32(ctypes.addressof(value))

if __name__ == "__main__":
    unittest.main()
