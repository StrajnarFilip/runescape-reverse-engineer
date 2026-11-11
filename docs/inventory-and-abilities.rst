Inventory, equipment and individual abilities
==============================================

The C collector now reads exact logical inventory slots, equipment slots and
individual ability cooldown variables. Both new tracker tabs and JSON Lines use
these native observations. Python converts records for display; it does not scan
game memory or maintain the high-frequency item identities/cooldowns.

Live checks
-----------

The user moved a Wicked hood and consumed a restore dose, then used Hurricane,
Flurry and Resonance with different equipment. The C calibration recorded:

* Wicked hood (ID 22332) moving from displayed slot 1 to slot 25, retaining token 8.
* Super restore (2), ID 3028, moving from slot 5 to slot 11, then becoming
  Super restore (1), ID 3030. Token 12 stayed the same through both observations.
* Hurricane: two-handed Dragon spear (ID 1249), allowed equipment match,
  observed base client interval 1020 ticks / 20.4 seconds.
* Flurry: Reese's sword (15596) and Reese's off-hand sword (30053), allowed
  dual-wield match, 1020 client ticks / 20.4 seconds.
* Resonance: the main-hand sword and Anti-dragon shield (1540), allowed shield
  match, 1500 client ticks / 30 seconds.

The client adjusted Hurricane's start and end by -600 client ticks during the
capture. The collector reflected the changed remaining cooldown. The reason for
the adjustment was not identified; it is not replaced with a static countdown.
See ``validation/inventory-validation-summary.json`` for actual IDs, slots, tokens, timer
pairs, equipped items, timestamps and the raw local capture's SHA-256. The earlier
research capture independently showed hood slot 2 -> 13 and restore slot 6
changing 3028 -> 3030. These were separate actions, not conflicting slot readings.

Using the tracker
-----------------

Run the normal tracker, then select **Inventory and equipment** or **Abilities
and cooldowns**. The inventory grid shows 28 native slots, item names/IDs,
quantities and known dose counts. Equipment and recent moves/dose changes appear
alongside it. The ability tab filters by name and can restrict results to the
game's ability-book entries. Cooldown, base cooldown, weapon requirements and
equipment match are separate columns.

Native slot indices are zero-based (0..27); the UI also shows human slot numbers
1..28. The grid uses four columns for readability. The game can resize/reflow its
inventory, so these are logical slot positions, not screen coordinates. Equipment
indices include main hand 3 and off hand 5. Runtime does not inspect screenshots.

Build-time metadata
-------------------

The build reads the installed cache through an independent Python standard-library
decoder, in SQLite read-only mode. No agent or game code is loaded. It extracts
names, equip slots/hide slots, weapon-category parameters, potion dose chains,
effect tags and ability parameters into **our own** ``build/rs3_catalog.bin``.
This generated file stays local and is not published to GitHub. The C DLL loads
it once when creating a context; it is not parsed in the sampling loop.

Current extraction: **64,175 item definitions**, **189 potion families**, and
**169 ability definitions** (128 in the ability book). **140** have symbolic
VarClient timer pairs. Other entries include actions with shared, special or
unmapped cooldown logic. They remain unknown until their timing is mapped.
Three mapped pairs were exercised live in this change; others were recovered
from revision-951 gameval names and have not each been combat-tested.

The compact ``../profiles/ability_bindings_951_3.json`` records timer IDs, names, requirements,
base intervals and reference hashes. It excludes full descriptions and cache
blobs. ``work/build_ability_bindings.py`` rebuilds it using local cache data and
the pinned reference JSON under ``work/references/``. Those reference downloads
are research inputs, not runtime dependencies.

When ``js5-19.jcache`` (items) and ``js5-22.jcache`` (structs) exist under
``RS3_CACHE_ROOT``, the default build writes ``rs3_catalog.bin`` and its manifest
beside the DLL. A fingerprint of the archives' sizes and modification times
(``build/generated/catalog_archives.txt``) reruns the generator after a game
cache update; unchanged builds skip it. ``cmake --build build --config Release
--target rs3_catalog`` builds it explicitly. Set
``-DRS3_CACHE_ROOT=C:/path/to/cache`` during configuration to use a different
cache, or ``-DRS3_BUILD_CATALOG=OFF`` to skip it. Raw inventory IDs, quantities
and slots remain available without a valid catalog. The loader verifies the
format, body SHA-256 and client fingerprint before it accepts metadata.

Templated item variants carry no name of their own. A banknote (opcodes 201/202)
and the three other link/template pairs (203/204, 205/206, 207/208) take the
name of their linked item, as the client does; 13,849 of the 64,175 definitions
are named this way. Definitions without a name or a link stay unnamed.

The C library looks items up through a direct-address table built at load time:
``item_index[item_id]`` holds the record position, so each lookup is one array
read. Item IDs are dense (highest 64,514 for 64,175 definitions), so the table
costs 4 bytes per ID, about 252 KB for the current cache.

Potions and item identity
-------------------------

Doses come from explicit variant IDs, not a name suffix. For example the installed
Saradomin brew family links flask (6) **23351** to flask (5) **23353**. Both retain
the same family and healing tag; the dose count changes. Its effect definition
includes a base flat heal of 1000. This is definition metadata, not a guarantee
of the actual HP gained after caps, modifiers or other effects.

Super restore variants likewise retain their family and prayer/stat restoration
tags. Fourteen item IDs have conflicting potion-family definitions in the cache;
their family/dose mapping is left unknown. Missing metadata never becomes an
invented item name, dose count or healing effect.

The collector compares consecutive verified observations across backpack and
equipment, preferring unchanged slots, then unambiguous ID/quantity matches,
then explicit adjacent dose variants. A local tracking token follows a unique
move, an equipment transfer or a verified dose transition. Baselines and world
context replacements do not emit a bag full of false additions. A failed/torn
read does not commit tokens/events. Replacements get a new token and contain
both before/after records; quantities and empty cells are explicit.

The client does not expose a unique serial number for each identical item. When
several indistinguishable copies move together, identity can be ambiguous and is
flagged. Stack splits/merges, multiple unobserved changes and an item leaving then
returning between polls cannot establish physical identity. Current slot contents
remain directly observed. A 1024-entry native item-event queue preserves changes
between slower UI refreshes and reports overwritten records via ``events_lost``.

Ability requirements and readiness
----------------------------------

Definitions supply explicit dual-wield, two-handed, shield and weapon flags.
Weapon categories supply melee/ranged/magic/necromancy style; a main-hand item
that hides off-hand slot 5 is classified as two-handed. Defenders are classified
as both an off-hand weapon and a shield. Ordinary off-hand items, such as a
stethoscope, are not treated as weapons merely because they occupy slot 5.
The client cache is used instead of assuming historical weapon restrictions
still apply to every ability with the same name.

``equipment_match`` is allowed, blocked or unknown. It checks the observed setup
against weapon requirements; it is not a claim that the ability can be cast.
Level, unlocks, adrenaline, target, runes, charges, combat state and ability-specific
exceptions may impose additional conditions. JSON therefore leaves ``castable``
null. Bone Shield's active tier is read from VARP 11212 bits 30..31 (VARBIT 53217).
If it might substitute for a missing physical shield, equipment match stays
unknown until that ability's substitution/rune rules are established. A real
shield or defender can satisfy the ordinary shield requirement directly.

Live cooldowns use VarClient start/end and the same 20 ms logic clock as GCD.
Wrapping signed differences are used, and relevant variable nodes are re-read
to reject changed/reused values. Shared node pages are batched to reduce kernel
calls. An absent sparse entry, unmapped timer or inconsistent clock yields null
with a reason, not a fabricated ready state. Unknown base cooldowns are also null.
Base definition intervals use 600 ms game ticks; live client intervals use 20 ms.
Newer server-only parameter pairs are retained in the binding research data but
are not mixed with the client clock without verifying their conversion.

API and validation
------------------

The extension adds C exports without changing existing ABI-v1 struct layouts:
``rs3_snapshot_inventory_info``, ``rs3_snapshot_items``,
``rs3_snapshot_abilities``, ``rs3_item_events_since`` and ``rs3_load_catalog``.
See ``../include/rs3/rs3.h`` and ``native-api.rst`` for ownership and
record layouts. Python Reader exposes typed buffers and a separate item cursor.
The Java FFM example exposes ``inventory()`` returning Java-owned item, ability
and change records. It checks all ten ABI structure sizes.

Eleven C test groups and 30 Python tests passed (including the merged player-tile tests). New tests cover movement, dose
changes (including a valid family ID zero), simultaneous move/consume, quantity
changes, replacement, duplicate ambiguity, failed reads, world baselines, gear
switches/defenders, cooldown wrap/expiry/missing values, catalog bounds/checksums,
item queue gaps, parser failure and actual read-only SQLite access. C/Python/Java
live consumers work. The tracker window and error-free live logging were checked;
no visual layout inspection is claimed.

``validation/inventory-performance.json`` records the expanded workload's
timings/rates. Its C timings include inventory and 169 ability definitions/timers;
the Python reference decoder does not, so they are not equivalent workloads.
The 100/500/1000 Hz settings are requests, not guaranteed achieved rates. The
default remains a 100 Hz collector with a 20 Hz Python display/log consumer.

Source paths checked for this client
-----------------------------------

Inventory manager: ``Client +8 -> state +0x199e0``. Sorted vector at manager
``+8/+0x10/+0x18``, 72-byte entries. Key is ``container_id * 2 | key_flag``.
Ordinary backpack/equipment use flag zero and IDs 93/94. The embedded inventory
starts at entry ``+8``; ID at object ``+8`` and item vector at object ``+0x10``.
Each slot is ``{int32 item_id, uint32 quantity}`` with stride 8; item ID -1 is empty.
Both slot bytes and registry descriptors are rechecked before publication.

Native GetInventory ``0x140c36ea0`` and CS2 item-total reader ``0x14071b590``
confirm these paths and are signature-pinned in the profile. No game function is
called. Definition format leads came from the pinned primary-source projects:

* https://github.com/iEasyScript/reclass-data/tree/2ae22f569b529e71e97361039976e3b5c53073a3
* https://github.com/skillbert/rsmv/tree/a92b8920b9b9842adde78822646ecad1a6f4fc15

Offsets and interpretations were checked against this client's native functions,
installed cache and live observations. The independent item parser uses revision
951's uint16 quest list entries, rather than blindly copying an older schema.
