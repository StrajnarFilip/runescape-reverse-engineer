"""Reference NPC and player tile decoding against an in-memory fake process."""

import json
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from reference_decoder import ProfileError, ReferenceDecoder
from reference_entities import NPCReader, decode_health, decode_hit
from reference_memory import MemoryReadError

PROFILES = Path(__file__).resolve().parents[2] / "profiles"
SENTINEL = 0xFFFFFFFFFFFFFFFF
BUCKET_COUNT = 953
RUNTIME_ID = 42
TICK_MS = 20


class FakeMemory:
    """Little-endian memory backed by one bytearray at a fixed base address."""

    def __init__(self):
        self.base = 0x100000
        self.data = bytearray(0x80000)
        self.module = SimpleNamespace(base=0x140000000)

    def put(self, address, layout, *values):
        struct.pack_into(layout, self.data, address - self.base, *values)

    def read(self, address, size):
        position = address - self.base
        if not 0 <= position or not size or position + size > len(self.data):
            raise MemoryReadError("Unmapped fake address")
        return bytes(self.data[position : position + size])

    def unpack(self, address, layout):
        return struct.unpack(layout, self.read(address, struct.calcsize(layout)))

    def pointer(self, address):
        return self.unpack(address, "<Q")[0]

    def i32(self, address):
        return self.unpack(address, "<i")[0]

    def u32(self, address):
        return self.unpack(address, "<I")[0]


class NPCReaderTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads((PROFILES / "profile_951_3.json").read_text(encoding="utf-8"))
        self.memory = memory = FakeMemory()
        self.world_state = 0x100000
        self.manager = 0x130000
        self.table = 0x140000
        self.node = 0x143000
        self.actor = 0x150000
        self.config = 0x160000
        self.ring = 0x161000
        self.records = 0x162000
        memory.put(self.world_state + 0x19940, "<Q", self.manager)
        memory.put(self.manager, "<Q", memory.module.base + 0x153AE88)
        memory.put(self.manager + 16, "<QQ", self.table, BUCKET_COUNT)
        memory.put(self.manager + 0xB0A0, "<I", 1)
        memory.put(self.manager + 0xA0A0, "<I", RUNTIME_ID)
        memory.put(self.table + BUCKET_COUNT * 8, "<Q", SENTINEL)
        memory.put(self.table + RUNTIME_ID * 8, "<Q", self.node)
        memory.put(self.node, "<IIQQQ", RUNTIME_ID, 0, 0x144000, self.actor, 0)
        memory.put(self.actor, "<Q", memory.module.base + 0x15DC938)
        memory.put(self.actor + 0x88, "<H", RUNTIME_ID)
        memory.put(self.actor + 0x90, "<QQQ", 0x170000, 6, 0x800000000000003F)
        memory.put(0x170000, "<6s", b"Goblin")
        memory.put(self.actor + 0x10A8, "<Q", self.config)
        memory.put(self.config + 8, "<I", 12357)
        memory.put(self.actor + 0x115C, "<i", 326)
        memory.put(self.actor + 0x1178, "<i", 550)
        memory.put(self.actor + 0xF18, "<Q", self.ring)
        memory.put(self.ring + 4, "<I", 6)
        memory.put(self.ring + 24, "<QQ", 6, self.records)
        for slot in range(6):
            memory.put(self.records + slot * 24, "<6i", -1, -1, 0, -1, -1, 0)
        memory.put(self.records, "<6i", 133, 224, 1060, -1, -1, 60)
        self.reader = NPCReader(memory, self.profile)

    def test_native_fields_and_one_observation_per_hit(self):
        npcs, hits = self.reader.read(self.world_state, 1005)
        entity = npcs["entities"][0]
        identity = (entity["name"], entity["config_id"], entity["runtime_id"])
        self.assertEqual(identity, ("Goblin", 12357, 42))
        self.assertEqual((entity["health"]["current"], entity["health"]["max"]), (326, 550))
        self.assertEqual(hits["newly_observed"][0]["amount"], 224)
        self.assertEqual(self.reader.read(self.world_state, 1006)[1]["newly_observed"], [])
        self.assertEqual(self.reader.read(self.world_state, 1060)[1]["active"], [])

    def test_removed_npc_is_never_kept(self):
        self.reader.read(self.world_state, 1005)
        self.memory.put(self.manager + 0xB0A0, "<I", 0)
        npcs, hits = self.reader.read(self.world_state, 1006)
        self.assertEqual(npcs["entities"], [])
        self.assertEqual(hits["active"], [])

    def test_list_transition_retries_without_losing_the_hit(self):
        original_u32 = self.memory.u32
        counts = iter((1, 0, 1, 1))

        def changing_count(address):
            if address == self.manager + 0xB0A0:
                return next(counts)
            return original_u32(address)

        with patch.object(self.memory, "u32", side_effect=changing_count):
            npcs, hits = self.reader.read(self.world_state, 1005)
        self.assertEqual(npcs["read_count"], 1)
        self.assertEqual(len(hits["newly_observed"]), 1)
        self.assertEqual(self.reader.read(self.world_state, 1006)[1]["newly_observed"], [])

    def test_unavailable_entity_does_not_repeat_its_previous_hit(self):
        self.reader.read(self.world_state, 1005)
        original_pointer = self.memory.pointer

        def failing_ring(address):
            if address == self.actor + 0xF18:
                raise MemoryReadError("Actor changing")
            return original_pointer(address)

        with patch.object(self.memory, "pointer", side_effect=failing_ring):
            npcs, hits = self.reader.read(self.world_state, 1006)
            self.assertEqual(npcs["skipped_count"], 1)
            self.assertEqual(hits["newly_observed"], [])
        self.assertEqual(self.reader.read(self.world_state, 1007)[1]["newly_observed"], [])

    def test_bucket_cycle_is_rejected(self):
        self.memory.put(self.node, "<I", RUNTIME_ID + 1)
        self.memory.put(self.node + 24, "<Q", self.node)
        with self.assertRaises(MemoryReadError):
            self.reader.find_node(self.table, BUCKET_COUNT, SENTINEL, RUNTIME_ID)

    def test_torn_ring_is_not_a_hit(self):
        original_read = self.memory.read
        read_count = [0]

        def changing_records(address, size):
            if address == self.records:
                read_count[0] += 1
                if read_count[0] == 2:
                    self.memory.put(self.records + 4, "<i", 550)
            return original_read(address, size)

        with patch.object(self.memory, "read", side_effect=changing_records):
            with self.assertRaises(MemoryReadError):
                self.reader.read_hits(self.actor, 1005)

    def test_profile_mismatch_is_rejected(self):
        self.memory.put(self.manager, "<Q", 0x140000000)
        with self.assertRaises(MemoryReadError):
            self.reader.read(self.world_state, 1005)

    def test_unknown_health_is_null_and_zero_health_is_valid(self):
        self.assertIsNone(decode_health(0, 0)["current"])
        self.assertIsNone(decode_health(600, 550)["current"])
        self.assertEqual(decode_health(0, 550)["current"], 0)

    def test_hit_schedule_expiry_zero_amount_and_counter_wrap(self):
        record = (133, 0, 1060, -1, -1, 60)
        self.assertEqual(decode_hit(record, 999, TICK_MS)["state"], "scheduled")
        self.assertEqual(decode_hit(record, 1000, TICK_MS)["amount"], 0)
        self.assertIsNone(decode_hit(record, 1060, TICK_MS))
        wrapped = (133, 224, -2147483600, -1, -1, 60)
        self.assertEqual(decode_hit(wrapped, 2147483647, TICK_MS)["state"], "active")


class PlayerPositionTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads((PROFILES / "profile_6ada3f59.json").read_text(encoding="utf-8"))
        self.profile["player_position"]["client_ptr_rva"] = "0x100"
        self.profile["signatures"] = [{"rva": "0x108", "bytes": "488b"}]
        self.memory = memory = FakeMemory()
        memory.pid = 1
        memory.identity = lambda: {"sha256": self.profile["sha256"]}
        memory.module.base = memory.base
        addresses = range(0x110000, 0x170000, 0x10000)
        self.client, self.player_context, table, slots, slot, self.actor = addresses
        memory.put(memory.base + 0x100, "<Q", self.client)
        memory.put(memory.base + 0x108, "<2s", bytes.fromhex("488b"))
        memory.put(self.client + 0x19FC0, "<i", 30)
        memory.put(self.client + 0x19FC8, "<Q", self.player_context)
        memory.put(self.client + 0x19968, "<Q", table)
        memory.put(self.player_context, "<Q", self.client)
        memory.put(self.player_context + 0x48, "<i", 1)
        memory.put(table + 0x10, "<QQ", slots, slots + 16)
        memory.put(slots + 8, "<Q", slot)
        memory.put(slot + 0x38, "<Q", self.actor)
        memory.put(self.actor + 0x50, "<i", 2)
        memory.put(self.actor + 0x18C, "<i", 1)
        memory.put(self.actor + 0x280, "<3f", 3165.5 * 512, 1285, 3482.5 * 512)

    def test_tile_updates_and_clears_after_leaving_the_scene(self):
        decoder = ReferenceDecoder(self.memory, self.profile)
        record = decoder.snapshot(include_raw=True)
        self.assertEqual(record["position"], {"x": 3165, "y": 3482, "plane": 2})
        self.assertIsNone(record["health"])
        self.memory.put(self.actor + 0x280, "<3f", 3166 * 512, 0, 3482.5 * 512)
        self.assertEqual(decoder.snapshot()["position"], {"x": 3166, "y": 3482, "plane": 2})
        self.memory.put(self.player_context + 0x48, "<i", 2)
        self.assertIsNone(decoder.snapshot()["position"])
        self.memory.put(self.player_context + 0x48, "<i", 1)
        self.memory.put(self.client + 0x19FC0, "<i", 20)
        self.assertIsNone(decoder.snapshot()["position"])

    def test_position_requires_a_matching_code_signature(self):
        self.memory.put(self.memory.base + 0x108, "<2s", b"\0\0")
        with self.assertRaises(ProfileError):
            ReferenceDecoder(self.memory, self.profile)


if __name__ == "__main__":
    unittest.main()
