"""Definition parsing and read-only cache access, using small synthetic fixtures."""

import sqlite3
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from rs3_reader.cache_definitions import (
    Archive,
    DecodeError,
    decode_enum,
    decode_item,
    decode_struct,
    decompress,
    resolve_item_names,
)

NOTE_TEMPLATE_ITEM_ID = 799


def three_byte_opcode(opcode, value):
    return bytes([opcode]) + value.to_bytes(3, "big")


def compress(data):
    return b"ZLB\x01" + struct.pack(">I", len(data)) + zlib.compress(data)


class CacheDefinitionTests(unittest.TestCase):
    def test_item_slots_names_params_and_revision_951_quests(self):
        # Name, equip slot 3, hide slot 5, two quest IDs, a flag and a typed parameter map.
        data = (
            b"\x02Example sword\0\x0d\x03\x0e\x05\x84\x01\x00\x0f\xf9\x02"
            + b"\x00\x00\x02\xae"
            + struct.pack(">i", 14930)
            + b"\x01\x00\x00\x01Text\0\0"
        )
        definition = decode_item(data)
        self.assertEqual(
            (definition["name"], definition["equip_slot"], definition["hide_slot"]),
            ("Example sword", 3, 5),
        )
        self.assertEqual(definition["params"], {686: 14930, 1: "Text"})

    def test_templated_variants_link_to_their_named_item(self):
        noted = three_byte_opcode(201, 6) + three_byte_opcode(202, NOTE_TEMPLATE_ITEM_ID) + b"\0"
        definition = decode_item(noted)
        self.assertIsNone(definition["name"])
        self.assertEqual(definition["linked_item_id"], 6)
        self.assertEqual((definition["note_id"], definition["note_template"]), (6, 799))
        lent = three_byte_opcode(203, 11235) + three_byte_opcode(204, 13009) + b"\0"
        self.assertEqual(decode_item(lent)["linked_item_id"], 11235)
        # A link without its template is a plain cross-reference, not a variant.
        self.assertIsNone(decode_item(three_byte_opcode(205, 20929) + b"\0")["linked_item_id"])
        self.assertIsNone(decode_item(b"\x02Sword\0\0")["linked_item_id"])

    def test_variant_names_follow_links_within_bounds(self):
        def definition(name, linked_item_id=None):
            return {"name": name, "linked_item_id": linked_item_id}

        definitions = {
            6: definition("Grimy toadflax"),
            7: definition(None, 6),
            8: definition(None, 9),
            9: definition(None, 8),
            10: definition(None, 404),
            11: definition(None, 12),
            12: definition(None, 7),
            13: definition("Own name", 6),
        }
        names = resolve_item_names(definitions, max_link_depth=2)
        self.assertEqual(names[7], "Grimy toadflax")
        self.assertEqual(names[12], "Grimy toadflax")
        self.assertEqual(names[13], "Own name")
        # Cycles, missing links and chains longer than the bound stay unnamed.
        self.assertEqual([names[item_id] for item_id in (8, 9, 10)], [None, None, None])
        self.assertIsNone(resolve_item_names(definitions, max_link_depth=1)[11])
        self.assertEqual(resolve_item_names(definitions, max_link_depth=3)[11], "Grimy toadflax")

    def test_unknown_and_truncated_definitions_fail(self):
        for data in (b"\xff\0", b"\x02unterminated", b"\x0d", b"\0trailing"):
            with self.subTest(data=data), self.assertRaises(DecodeError):
                decode_item(data)
        with self.assertRaises(DecodeError):
            decompress(compress(b"abcd")[:-2])
        with self.assertRaises(DecodeError):
            decompress(b"ZLB\x01" + struct.pack(">I", 0x10000000))

    def test_struct_and_enum_require_complete_consumption(self):
        parameters = b"\xf9\x01\x00\x00\x0a\xec" + struct.pack(">i", 50) + b"\0"
        self.assertEqual(decode_struct(parameters), {2796: 50})
        enum = b"\x65\x00\x66\x49\x08\x00\x02\x00\x01\x00\x01" + struct.pack(">i", 14713) + b"\0"
        self.assertEqual(decode_enum(enum)["values"], {1: 14713})
        with self.assertRaises(DecodeError):
            decode_struct(parameters + b"\0")

    def test_archive_extracts_files_and_opens_sqlite_read_only(self):
        reference_table = (
            b"\x07" + b"\0" * 4 + b"\x00\x00\x01\x00\x00" + b"\0" * 8 + b"\x00\x02\x00\x01\x00\x02"
        )
        group = b"\x01" + struct.pack(">III", 13, 16, 20) + b"abc" + b"defg"
        with tempfile.TemporaryDirectory(prefix="rs3-cache-test-") as directory:
            path = Path(directory) / "js5-19.jcache"
            database = sqlite3.connect(path)
            database.execute("CREATE TABLE cache_index (DATA BLOB)")
            database.execute("CREATE TABLE cache (KEY INTEGER, DATA BLOB)")
            database.execute("INSERT INTO cache_index VALUES (?)", (compress(reference_table),))
            database.execute("INSERT INTO cache VALUES (0, ?)", (compress(group),))
            database.commit()
            database.close()
            archive = Archive(19, directory)
            try:
                self.assertEqual(archive.read_group(0), {1: b"abc", 3: b"defg"})
                with self.assertRaises(sqlite3.OperationalError):
                    archive.database.execute("DELETE FROM cache")
            finally:
                archive.close()


if __name__ == "__main__":
    unittest.main()
