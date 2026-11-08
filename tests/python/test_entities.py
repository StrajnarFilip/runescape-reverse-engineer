import json
from pathlib import Path
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from entities import NPCReader, decode_health, decode_hit
from rs3 import ProfileError, RS3
from tracker import HitHistory
from winmem import MemoryReadError

PROFILES = Path(__file__).resolve().parents[2] / "profiles"


class FakeMemory:
    def __init__(self):
        self.base = 0x100000
        self.data = bytearray(0x80000)
        self.module = SimpleNamespace(base=0x140000000)

    def put(self, address, fmt, *values):
        struct.pack_into(fmt, self.data, address-self.base, *values)

    def read(self, address, size):
        pos = address-self.base
        if not 0 <= pos or not size or pos+size > len(self.data):
            raise MemoryReadError("Unmapped fake address")
        return bytes(self.data[pos:pos+size])

    def unpack(self, address, fmt):
        return struct.unpack(fmt, self.read(address, struct.calcsize(fmt)))

    def pointer(self, address):
        return self.unpack(address, "<Q")[0]

    def i32(self, address):
        return self.unpack(address, "<i")[0]

    def u32(self, address):
        return self.unpack(address, "<I")[0]

class EntityTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads((PROFILES / "profile_951_3.json").read_text(encoding="utf-8"))
        self.m = m = FakeMemory()
        self.state, self.manager, self.table, self.node, self.actor = 0x100000, 0x130000, 0x140000, 0x143000, 0x150000
        self.config, self.ring, self.data = 0x160000, 0x161000, 0x162000
        self.uid = 42
        m.put(self.state+0x19940, "<Q", self.manager)
        m.put(self.manager, "<Q", m.module.base+0x153ae88)
        m.put(self.manager+16, "<QQ", self.table, 953)
        m.put(self.manager+0xb0a0, "<I", 1)
        m.put(self.manager+0xa0a0, "<I", self.uid)
        m.put(self.table+953*8, "<Q", 0xffffffffffffffff)
        m.put(self.table+42*8, "<Q", self.node)
        m.put(self.node, "<IIQQQ", self.uid, 0, 0x144000, self.actor, 0)
        m.put(self.actor, "<Q", m.module.base+0x15dc938)
        m.put(self.actor+0x88, "<H", self.uid)
        m.put(self.actor+0x90, "<QQQ", 0x170000, 6, 0x800000000000003f)
        m.put(0x170000, "<6s", b"Goblin")
        m.put(self.actor+0x10a8, "<Q", self.config)
        m.put(self.config+8, "<I", 12357)
        m.put(self.actor+0x115c, "<i", 326)
        m.put(self.actor+0x1178, "<i", 550)
        m.put(self.actor+0xf18, "<Q", self.ring)
        m.put(self.ring+4, "<I", 6)
        m.put(self.ring+24, "<QQ", 6, self.data)
        for slot in range(6):
            m.put(self.data+slot*24, "<6i", -1, -1, 0, -1, -1, 0)
        m.put(self.data, "<6i", 133, 224, 1060, -1, -1, 60)
        self.reader = NPCReader(m, self.profile)

    def test_native_fields_and_one_observation_per_hit(self):
        npcs, hits = self.reader.read(self.state, 1005)
        e = npcs["entities"][0]
        self.assertEqual((e["name"],e["config_id"],e["runtime_id"]), ("Goblin",12357,42))
        self.assertEqual((e["health"]["current"],e["health"]["max"]), (326,550))
        self.assertEqual(hits["newly_observed"][0]["amount"],224)
        self.assertEqual(self.reader.read(self.state,1006)[1]["newly_observed"],[])
        self.assertEqual(self.reader.read(self.state,1060)[1]["active"],[])

    def test_spawn_removal_never_keeps_old_npc(self):
        self.reader.read(self.state,1005)
        self.m.put(self.manager+0xb0a0,"<I",0)
        npcs, hits = self.reader.read(self.state,1006)
        self.assertEqual(npcs["entities"],[])
        self.assertEqual(hits["active"],[])

    def test_npc_list_transition_retries_without_losing_hit(self):
        original = self.m.u32
        counts = iter((1,0,1,1))
        def count_changes(address):
            if address == self.manager+0xb0a0:
                return next(counts)
            return original(address)
        with patch.object(self.m,"u32",side_effect=count_changes):
            npcs, hits = self.reader.read(self.state,1005)
        self.assertEqual(npcs["read_count"],1)
        self.assertEqual(len(hits["newly_observed"]),1)
        self.assertEqual(self.reader.read(self.state,1006)[1]["newly_observed"],[])

    def test_unavailable_entity_does_not_repeat_previous_hit(self):
        self.reader.read(self.state,1005)
        original = self.m.pointer
        def fail(address):
            if address == self.actor+0xf18:
                raise MemoryReadError("Actor changing")
            return original(address)
        with patch.object(self.m,"pointer",side_effect=fail):
            npcs, hits = self.reader.read(self.state,1006)
            self.assertEqual(npcs["skipped_count"],1)
            self.assertEqual(hits["newly_observed"],[])
        self.assertEqual(self.reader.read(self.state,1007)[1]["newly_observed"],[])

    def test_bucket_cycle_is_rejected(self):
        self.m.put(self.node,"<I",43)
        self.m.put(self.node+24,"<Q",self.node)
        with self.assertRaises(MemoryReadError):
            self.reader.node(self.table,953,0xffffffffffffffff,42)

    def test_torn_ring_is_not_a_hit(self):
        original = self.m.read
        calls = [0]
        def changing(address, size):
            if address == self.data:
                calls[0] += 1
                if calls[0] == 2:
                    self.m.put(self.data+4,"<i",550)
            return original(address,size)
        with patch.object(self.m,"read",side_effect=changing):
            with self.assertRaises(MemoryReadError):
                self.reader.hits(self.actor,1005)

    def test_profile_mismatch_rejected(self):
        self.m.put(self.manager,"<Q",0x140000000)
        with self.assertRaises(MemoryReadError):
            self.reader.read(self.state,1005)

    def test_unknown_hp_is_null_and_zero_hp_is_valid(self):
        self.assertIsNone(decode_health(0,0)["current"])
        self.assertIsNone(decode_health(600,550)["current"])
        self.assertEqual(decode_health(0,550)["current"],0)

    def test_hit_schedule_expiry_zero_and_counter_wrap(self):
        values = (133,0,1060,-1,-1,60)
        self.assertEqual(decode_hit(values,999,20)["state"],"scheduled")
        self.assertEqual(decode_hit(values,1000,20)["amount"],0)
        self.assertIsNone(decode_hit(values,1060,20))
        self.assertEqual(decode_hit((133,224,-2147483600,-1,-1,60),2147483647,20)["state"],"active")

    def test_ui_history_survives_coalesced_samples_and_deduplicates(self):
        history = HitHistory(2)
        hit = {"runtime_id":42,"config_id":12357,"slot":0,"type_id":133,
               "amount":224,"end_client_tick":1060}
        s = {"timestamp_ms":1000,"pid":1,"hit_splats":{"newly_observed":[hit]}}
        history.observe(s)
        history.observe({"timestamp_ms":1050,"pid":1,"hit_splats":{"newly_observed":[]}})
        history.observe(s)
        self.assertEqual(len(history.events),1)
        self.assertEqual(history.events[0]["amount"],224)
        for end in (1120,1180):
            history.observe(dict(s,hit_splats={"newly_observed":[dict(hit,end_client_tick=end)]}))
        self.assertEqual([e["end_client_tick"] for e in history.events],[1120,1180])

class PositionTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads((PROFILES / "profile_6ada3f59.json").read_text(encoding="utf-8"))
        self.profile["player_position"]["client_ptr_rva"] = "0x100"
        self.profile["signatures"] = [{"rva": "0x108", "bytes": "488b"}]
        self.m = m = FakeMemory()
        m.pid = 1
        m.identity = lambda: {"sha256": self.profile["sha256"]}
        m.module.base = m.base
        self.client, self.context, table, start, slot, self.actor = range(0x110000, 0x170000, 0x10000)
        m.put(m.base+0x100, "<Q", self.client)
        m.put(m.base+0x108, "<2s", bytes.fromhex("488b"))
        m.put(self.client+0x19fc0, "<i", 30)
        m.put(self.client+0x19fc8, "<Q", self.context)
        m.put(self.client+0x19968, "<Q", table)
        m.put(self.context, "<Q", self.client)
        m.put(self.context+0x48, "<i", 1)
        m.put(table+0x10, "<QQ", start, start+16)
        m.put(start+8, "<Q", slot)
        m.put(slot+0x38, "<Q", self.actor)
        m.put(self.actor+0x50, "<i", 2)
        m.put(self.actor+0x18c, "<i", 1)
        m.put(self.actor+0x280, "<3f", 3165.5*512, 1285, 3482.5*512)

    def test_tile_updates_and_clears_after_scene_exit(self):
        reader = RS3(self.m, self.profile)
        record = reader.snapshot(include_raw=True)
        self.assertEqual(record["position"], {"x": 3165, "y": 3482, "plane": 2})
        self.assertIsNone(record["health"])
        self.m.put(self.actor+0x280, "<3f", 3166*512, 0, 3482.5*512)
        self.assertEqual(reader.snapshot()["position"], {"x": 3166, "y": 3482, "plane": 2})
        self.m.put(self.context+0x48, "<i", 2)
        self.assertIsNone(reader.snapshot()["position"])
        self.m.put(self.context+0x48, "<i", 1)
        self.m.put(self.client+0x19fc0, "<i", 20)
        self.assertIsNone(reader.snapshot()["position"])

    def test_position_requires_matching_code_signature(self):
        self.m.put(self.m.base+0x108, "<2s", b"\0\0")
        with self.assertRaises(ProfileError):
            RS3(self.m, self.profile)

if __name__ == "__main__":
    unittest.main()
