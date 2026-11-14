# Telos beam acquisition

| Phase | Beam | Mechanic evidence |
| --- | --- | --- |
| 1 | Green | Scene locations `103545` and `103546` matched the visible beam. At 1000%, the visible line held a fixed direction as Telos moved. The meter rose by 3 on some ticks while the beam was visible and stayed flat on others. |
| 2 | Black | Scene locations `103547` and `103548` matched the visible beam in two placements during the same 500% attempt. Walking into the beam is the account owner's confirmed black-virus cleanse. |
| 3 | Red | The red beam is the route for the red virus. Cache definitions `103549` and `103550` share the same field layout as the green and black definitions. These two IDs are leads pending a labelled P3 capture. |

The captured phase 1 line was roughly at world X 15698 and spanned approximately Z 2826–2850. These are estimates from the rendered beam and the arena camera. They describe one instance and one beam placement. The ends of the visible effect and the tiles that clear a virus still need independent confirmation.

Phase 1 and 2 captures are indexed by the received game tick in the [1000% attempt](../../../training-data/telos-20261114T132221.181601Z.jsonl) and [500% attempt](../../../training-data/telos-20261114T132803.123153Z.jsonl). The P1 visual sample covers the interval around ticks 5753–5768. The P2 visual sample overlaps the raw phase-2 meter changes at ticks 6422–6438. The scripted log for the 500% attempt ends at tick 6382, when the stage first changed to P2. Subsequent observations were read-only.

In the follow-up [500% attempt](../../../training-data/telos-20261114T134849.728786Z.jsonl), the location probe recorded the actual green strip at X 13397 and Z 3215–3233. It first sampled the green IDs at probe tick 8405, and sampled their removal at 8425. It sampled the black IDs at X 13463 starting at probe tick 8473 and at X 13461 as the beam relocated. Probe tick counters can have a constant offset from the fight runner during a segment. Align records by `sample_ns` and the stage word. The player died in phase 2 before a labelled virus cleanse.

## Native observations and research probe

- Compatibility Mode exposes the projection matrix at actor world `+0x13900` and the viewport at scene manager `+0x84`. The native reader checks the corresponding accesses at RVAs `0x5a31e`, `0x5a371` and `0x4042e0`. The scene manager comes from world state `+0x199e8`. The live client returned a 2560×1377 viewport and projected both the player and dormant Telos to the corresponding rendered positions.
- The 1000% attempt started on player tile `(15712, 2829, 0)`. Native pose, regular position, and movement validation now accept instance coordinates above the old 12800 limit.
- The client location-event path at RVA `0x4042d0` follows the scene manager's selected world and its map-square vectors to animate locations. The client's Lua `LocList` traversal at RVA `0xc219c0` follows the same map-square vectors. [Jagex LocList API](https://developer.runescape.com/rs/game/list/loclist.html) · [Jagex location events](https://developer.runescape.com/rs/core/events.html)
- The runtime uses the Compatibility Mode scene-location layout to publish the green and black tile IDs through `rs3_snapshot_hazard_info` and `rs3_snapshot_hazards`. Each location row carries `RS3_HAZARD_SCENE_LOCATION`, a config ID and a tile. The attempt log records these tuples as `beam_tiles` in state deltas, along with the hazard observation reason and dropped count. The rows have unknown server interaction area and timing. Only independently verified arrival and debuff removal can establish successful virus cleansing.
- Live staging returned location IDs `103521` and `103567`. Keep the occasional additional strip-adjacent configs `103552`, `103555` and `103583` as research evidence until their roles are clear. The positional effect pool at scene `+0x19a50` is a supplementary research channel.

To collect a labelled sample alongside a practice attempt, run the read-only [location probe](../../../work/probe_telos_locations.py) from the repository root. Write its output under the ignored build directory.

```powershell
python work/probe_telos_locations.py --pid <game-pid> --duration 180 > build/telos-locations.ndjson
```

Compare `stage_meter_raw`, `game_tick`, and `sample_ns` with a synchronized screen capture. Location rows contain class RVA, plane, X, Y, config ID and candidate sequence. Positional-effect rows contain plane, X, Y, config ID and effect kind. A changed location or effect is a candidate until it matches the beam at appearance, after relocation, and at expiry. A disappearing damage sequence is also insufficient to identify a cleanse because death, phase changes, and natural expiry can end the same sequence.
