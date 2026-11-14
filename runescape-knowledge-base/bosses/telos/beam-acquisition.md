# Telos beam acquisition

| Phase | Beam | Mechanic evidence |
| --- | --- | --- |
| 1 | Green | Capture the beam even outside the Telos footprint. At 1000%, the visible line held a fixed direction as Telos moved. The meter rose by 3 on some ticks while the beam was visible and stayed flat on others. |
| 2 | Black | A beam was visible across the platform in the 500% session after the runner stopped at the handoff. Walking into the beam is the account owner's confirmed black-virus cleanse. |
| 3 | Red | The red beam is the route for the red virus. The new sessions have no labelled P3 beam or cleanse. |

The captured phase 1 line was roughly at world X 15698 and spanned approximately Z 2826–2850. These are estimates from the rendered beam and the arena camera. They describe one instance and one beam placement. The ends of the visible effect and the tiles that clear a virus still need independent confirmation.

Phase 1 and 2 captures are indexed by the received game tick in the [1000% attempt](../../../training-data/telos-20261114T132221.181601Z.jsonl) and [500% attempt](../../../training-data/telos-20261114T132803.123153Z.jsonl). The P1 visual sample covers the interval around ticks 5753–5768. The P2 visual sample overlaps the raw phase-2 meter changes at ticks 6422–6438. The scripted log for the 500% attempt ends at tick 6382, when the stage first changed to P2. Subsequent observations were read-only.

## Native observations and research probe

- Compatibility Mode exposes the projection matrix at actor world `+0x13900` and the viewport at scene manager `+0x84`. The native reader checks the corresponding accesses at RVAs `0x5a31e`, `0x5a371` and `0x4042e0`. The scene manager comes from world state `+0x199e8`. The live client returned a 2560×1377 viewport and projected both the player and dormant Telos to the corresponding rendered positions.
- The 1000% attempt started on player tile `(15712, 2829, 0)`. Native pose, regular position, and movement validation now accept instance coordinates above the old 12800 limit.
- The client location-event path at RVA `0x4042d0` follows the scene manager's selected world and its map-square vectors to animate locations. The client's Lua `LocList` traversal at RVA `0xc219c0` follows the same map-square vectors. [Jagex LocList API](https://developer.runescape.com/rs/game/list/loclist.html) · [Jagex location events](https://developer.runescape.com/rs/core/events.html)
- Live staging returned location config IDs `103521` and `103567`. The positional effect pool at scene `+0x19a50` returned two entries with config ID `20358` at different tiles. Treat all three IDs as staging observations until a captured appearance, relocation or expiry supports a beam mapping.

To collect a labelled sample alongside a practice attempt, run the read-only [location probe](../../../work/probe_telos_locations.py) from the repository root. Write its output under the ignored build directory.

```powershell
python work/probe_telos_locations.py --pid <game-pid> --duration 180 > build/telos-locations.ndjson
```

Compare `stage_meter_raw`, `game_tick`, and `sample_ns` with a synchronized screen capture. Location rows contain class RVA, plane, X, Y, config ID and candidate sequence. Positional-effect rows contain plane, X, Y, config ID and effect kind. A changed location or effect is a candidate until it matches the beam at appearance, after relocation, and at expiry. A disappearing damage sequence is also insufficient to identify a cleanse because death, phase changes, and natural expiry can end the same sequence.
