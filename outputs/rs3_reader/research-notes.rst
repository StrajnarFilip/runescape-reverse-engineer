Research notes for RS3 951-3
============================

Identity and method
-------------------

Client SHA-256::

    fd8afd32f9829b1fb0a953b238c504e75526b7af5e2920120aa027777a358d45

Static PE image base: ``0x140000000``. Profile offsets are relative to the loaded
module or a verified object. Dynamic addresses/PIDs are not stored in the profile.
Game functions are not executed: their data paths were reconstructed in Ghidra
and checked with read-only memory access. The C collector uses the same profile;
``generate_profile.py`` produces constants and a source-profile hash. The Python
binding refuses a DLL built from a different profile.

Domain registry and variable tables
-----------------------------------

The registry is a tree at RVA ``0x1721ee0``. Root ``+0x10``; node key ``+0x20``;
right/left pointers ``+0``/``+8``. Function object: node ``+0x60``; captured
context: function ``+8``. The vtable callback at ``+0x10`` must match:

* Domain 61: VarPlayer, handler RVA ``0x4819d0``.
* Domain 57: VarClient, handler RVA ``0x481ac0``; captured context is Client.

VarPlayer table/count: context ``+0x1c0c0`` / ``+0x1c0c8``.
VarClient table/count: Client ``+0x7308`` / ``+0x7310``.
Bucket: ``id % count``. Node uint32 key ``+0``; numeric value ``+8``; type
``+0x20``; next pointer ``+0x28``. Type 0 is int32, type 1 int64. The end sentinel
at the end of the bucket table may be ``UINTPTR_MAX``; never dereference it.
An absent sparse entry is not automatically zero.

Confirmed player measurements
-----------------------------

* VARP 13537 / 13538: current/max HP. User confirmed 8700/8700; subsequent
  captures observed combat and regeneration changes.
* VARP 3274 bits 0..14 / 10: prayer. Transition 840 -> 763.5; user UI displayed 763.
* Client ``+0x72e8``: skill manager; manager ``+0x10``: entries. Stride 24;
  base level ``+16``. Prayer skill index 5, level 84; max prayer derived as level*10.
* VARP 679 / 10: adrenaline percent; live changes through 100% were captured.
* VARP 4501 / 4502 / 9847: server GCD start/end/duration.
* VARC 2091 / 2092: client GCD start/end. Client ``+8`` points to state;
  state ``+0x528`` is logic clock. Observed 90 ticks at 20 ms/tick. Five starts
  were captured at transitions together with this clock.

GCD uses signed 32-bit differences to handle counter wrap. ``last_change_age_ms``
means elapsed time since an observed change, not server data age. Latest server
update time remains unknown. Native snapshot ages use the DLL's own QPC time
base; foreign runtime clocks may have different origins.

NPC identity, HP and hits
------------------------

Confirmed manager path: ``Client +8 -> state +0x19940 -> manager``. Construction
of the native bindings passes this same manager to NPCList.All. Manager vtable
RVA ``0x153ae88``, NPC vtable RVA ``0x15dc938``. Index list manager ``+0xa0a0``;
count ``+0xb0a0``. Lookup ``0x140038c00`` uses table ``+0x10`` and count ``+0x18``.
Node key ``+0``; entity pointer ``+0x10``; next ``+0x18``.

NPC runtime index: uint16 ``+0x88``. Vtable getter ``0x140c50b70`` copies it into
the entity UID with NPC type. Display name is a fixed string at ``+0x90``.
Config ID getter ``0x14044bc00`` first uses config ``+0x10b8``; otherwise base
config ``+0x10a8``. ID is config ``+8``. This live path returns Giant spider 20499
and Goblins 11232..11240 / 12353..12357. Runtime index distinguishes instances.

NPC has two seven-element int32 statistic arrays: current ``+0x1150``, max
``+0x116c``. HP index 3 gives current ``+0x115c``, max ``+0x1178``.
Native get_npc_stat ``0x14072401e`` reads both arrays with a CS2 index. Update
instructions ``0x1403f7e5f`` / ``0x1403f7e67`` write them using the server update
index. Live checks establish units and meaning: Goblin 550/550, Giant spider
1000/1000. The user's 224 hit appears in a hitmark and reduces Goblin runtime
22749 to 326/550. These are direct numeric statistics, without healthbar estimates.

Pathing actor ``+0xf18`` points to HitmarksAndHeadbars, confirmed by forwarders
``0x140c42f80`` / ``0x140c42fc0``. AddHitmark: ``0x140cd0870``. Ring capacity:
uint32 ``+4``; count: uint64 ``+0x18``; records pointer ``+0x20``. Record stride 24:

* ``+0`` int32 type ID; ``+4`` int32 amount.
* ``+8`` int32 end client tick.
* ``+0xc`` / ``+0x10`` secondary type/amount, otherwise -1.
* ``+0x14`` duration in client ticks.

Start = end - duration. Filtering uses wrapping 32-bit differences and the same
state ``+0x528`` logic clock. Renderer ``0x1403c1d50`` reads the same six numbers
and time interval. Already-active records at attachment can be newly observed;
this is polling, not a server-event hook. The ring does not identify the attacker.
Raw type IDs are retained: an amount is not automatically damage.

The earlier combat capture contains user-reported 224, 550 and 1000 hits. Examples
and its hash are in ``npc-validation-summary.json``. Both decoders recheck ring
contents, entity identity and the index list. A list transition retries once.
Transient individual NPC errors do not reset other NPCs' deduplication. Despawned
NPCs disappear from live display. The C port also checks the enclosing world
context before committing any identities or queued observations. The native
worker's separate 4096-entry event queue reports overflow to independent consumers.

AddHeadbar ``0x140cd0e90`` and renderer ``0x1403c3120`` were also inspected. Fill
uses division by 255 and interpolation. Runtime uses exact statistics instead;
if max HP is not transmitted, HP remains null.

Telos: open research
--------------------

The local VARBIT 32640 definition was confirmed as VARP 6363 bits 21..23.
Stage-number semantics have not been checked in a Telos fight. An absent VARP
6363 outside combat is not interpreted as phase zero.

Public NPC config IDs 22891, 22892, 22908 and 23137 are research leads for Telos
forms. Sequence IDs 28929, 28931..28935, 28938, 28942, 28950, 28952, 28959 and
28963..28966 are animation leads. They are unconfirmed in this live reader and
do not constitute a working attack classifier.

Function ``0x14044d8b0`` belongs to ground objects, not NPCEntity registration.
Registrations containing configID at ``0x14046d840`` / ``0x14046db20`` /
``0x14046de80`` are LocEntityProxy / LocSectionEntityProxy / NPCEntityProxy.
Ghidra exports from arbitrarily selected mid-function addresses can be invalid;
they are not evidence of a confirmed offset.

Remaining validation
--------------------

* Controlled logout, world-hop and restart tests.
* Logged-in-state mapping to prevent retained client values after logout.
* NPC animation mapping and a Telos-specific live check.
* Attacker identity, if the client provides it.
* Telos combat to confirm stage semantics and animation IDs.
* Native high-frequency performance under a full boss encounter; the current
  parity/timing report describes the scene present during the port, not Telos.

Sources and tools
-----------------

* Official Ghidra: https://github.com/NationalSecurityAgency/ghidra
* Public gameval identifiers used as leads:
  https://github.com/BotWithUs/NXTDebugger/tree/c771c1d374c30373662f55776760762300097d8d
* Local cache-format reference for our independent decoder:
  https://github.com/skillbert/rsmv/tree/a92b8920b9b9842adde78822646ecad1a6f4fc15
* Official API documentation: https://developer.runescape.com/rs
* Public class/offset notes used as leads and checked against this client's
  native code/live reads (old offsets must not be copied blindly):
  https://github.com/iEasyScript/reclass-data/tree/2ae22f569b529e71e97361039976e3b5c53073a3

The BotWithUs agent is not installed. Our runtime does not use its agent, JNI
module or communication protocol.
