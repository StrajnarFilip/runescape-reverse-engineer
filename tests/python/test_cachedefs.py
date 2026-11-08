"""Definition parsing and read-only cache tests use small synthetic fixtures."""
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
import zlib

from cachedefs import Archive, Buffer, DecodeError, decode_item, decode_struct, decode_enum, decompress


def compressed(data):
    return b"ZLB\1"+struct.pack(">I",len(data))+zlib.compress(data)


class CacheDefinitionTests(unittest.TestCase):
    def test_item_slots_names_params_and_revision_951_quests(self):
        # Two-handed equipment slot/hide slot and a typed parameter map.
        data=(b"\x02Example sword\0\x0d\x03\x0e\x05\x84\x01\x00\x0f\xf9\x02"+
              b"\x00\x00\x02\xae"+struct.pack(">i",14930)+b"\x01\x00\x00\x01Text\0\0")
        d=decode_item(data)
        self.assertEqual((d["name"],d["equip_slot"],d["hide_slot"]),("Example sword",3,5))
        self.assertEqual(d["params"],{686:14930,1:"Text"})

    def test_unknown_and_truncated_definitions_fail(self):
        for data in (b"\xff\0", b"\x02unterminated", b"\x0d", b"\0trailing"):
            with self.assertRaises(DecodeError):
                decode_item(data)
        with self.assertRaises(DecodeError):
            decompress(compressed(b"abcd")[:-2])
        with self.assertRaises(DecodeError):
            decompress(b"ZLB\1"+struct.pack(">I",0x10000000))

    def test_struct_and_enum_require_complete_consumption(self):
        params=b"\xf9\x01\x00\x00\x0a\xec"+struct.pack(">i",50)+b"\0"
        self.assertEqual(decode_struct(params),{2796:50})
        enum=b"\x65\x00\x66\x49\x08\x00\x02\x00\x01\x00\x01"+struct.pack(">i",14713)+b"\0"
        self.assertEqual(decode_enum(enum)["values"],{1:14713})
        with self.assertRaises(DecodeError):
            decode_struct(params+b"\0")

    def test_archive_extracts_slots_and_opens_sqlite_read_only(self):
        reference=(b"\x07"+b"\0"*4+b"\x00\x00\x01\x00\x00"+b"\0"*8+
                   b"\x00\x02\x00\x01\x00\x02")
        group=b"\x01"+struct.pack(">III",13,16,20)+b"abc"+b"defg"
        with tempfile.TemporaryDirectory(prefix="rs3-cache-test-") as temp:
            path=Path(temp)/"js5-19.jcache"
            db=sqlite3.connect(path)
            db.execute("CREATE TABLE cache_index (DATA BLOB)")
            db.execute("CREATE TABLE cache (KEY INTEGER, DATA BLOB)")
            db.execute("INSERT INTO cache_index VALUES (?)",(compressed(reference),))
            db.execute("INSERT INTO cache VALUES (0,?)",(compressed(group),));db.commit();db.close()
            cache=Archive(19,temp)
            try:
                self.assertEqual(cache.group(0),{1:b"abc",3:b"defg"})
                with self.assertRaises(sqlite3.OperationalError):
                    cache.db.execute("DELETE FROM cache")
            finally:
                cache.close()


if __name__=="__main__":unittest.main()
