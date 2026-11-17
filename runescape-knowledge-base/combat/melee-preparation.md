# Melee preparation and encounter controls

## Current account and retained adrenaline (2026-11-17)

**User-confirmed:** the intended account has Relentless 5. Equipped item IDs are
available, but augmented-item gizmos, perk ranks and the current Relentless proc
have not been decoded. The carrier item and gizmo slot remain unknown. The saved
P1 profile records this distinction; it does not infer perks from an item name.

**Guide reference:** [Relentless](https://runescape.wiki/w/Relentless) can prevent
adrenaline consumption by abilities that ordinarily consume it. This is a reason
to use observed adrenaline for subsequent eligibility rather than subtracting an
expected cost. An unchanged adrenaline reading alone does not identify its cause.

**Observed:** in the 2026-11-17 P1 opening at native enrage 1000%, the native input
batch accepted Berserk followed by adrenaline renewal. Berserk's cooldown started,
while adrenaline remained 120% and the renewal remained item 49079, four doses.
The old runner timed out on the missing dose decrement. A Relentless proc was not
read directly and is not confirmed by this observation.

**Controller rule:** press renewal once after Berserk in the same ordered input
batch. Require the Berserk-specific cooldown acknowledgement, but do not wait for
renewal consumption before handling Telos's wake. Record accepted input and an
observed dose transition separately. If no transition is observed, retain that
unknown outcome and continue using the actual adrenaline, cooldowns and equipment.
Vulnerability bombs and other consumables retain their own acknowledgement rules.

Current native UI labels include Berserk F, renewal Page Down, target cycle A,
vulnerability S, Greater Barge G, EZK equip Page Up, weapon special =, red EoF equip
7 and EoF special 6. Prayers use Ctrl+F1/F2/F3/F4 for Magic/Ranged/Soul Split/Melee.
These observations are saved in the P1 profile and rechecked by role before input.
Other accounts' unmodified function keys and fallback keys below do not apply.

## Prayer supplies and input integration (2026-11-17)

**Observed, read-only:** the intended client's native UI reports Spiritual prayer
potion on **C**. The backpack contains item 49027 in logical slot 3 (displayed
slot 4), six doses, family 177, next variant 49025. The C metadata lookup returns
the same family for the bar seed. These are acquisition checks outside combat;
no potion press or resource restoration was tested in this check.

**Controller rule:** resolve the restorative from a fresh native bar label,
positive prayer-effect metadata and the carried potion family. Keep all eligible
tokens when identical potions are carried. Re-resolve after slot, dose or bar
changes; do not substitute another account's Super restore key. The existing
threshold is 50 prayer points, with a 2.4-second retry delay. Urgent protection
and defensive work is processed first. Log accepted input separately from the
later native dose transition and prayer-point increase. An increase alone does
not prove which item restored prayer. Inventory queue loss or session changes
leave consumption inconclusive.

**Clock detail:** native inventory events use Unix wall time in
`observed_at_ns`. Attack events and input timing use the native monotonic clock.
Associate inventory events by process lifetime, event cursor and a newer sampled
frame; do not subtract their wall timestamp from a monotonic input timestamp.

**Implemented, offline-tested:** the experimental practice controller now accepts
an explicit Win32 keyboard/mouse backend as well as Arduino. Both require a
pinned PID for live input. Win32 packet acceptance and release-on-failure tests
are transport checks. In-game mouse movement and ability acceptance still need
live evidence. The ordered P1 rotation is now composed with this path and
offline-tested. Complete P5 combat and full prayer/healing verification remain open.

## Special acknowledgement correction (2026-11-17)

**User-confirmed:** physical weapon-special cooldowns appear on the debuff bar.
EoF specials have no separate personal cooldown beyond the ordinary ability
delay. Do not wait for a nonexistent EoF cooldown or acknowledge its stored
special from a generic button timer or GCD change.

**Research leads, now acquired in C:** revision-951 VarClient 8301 is named
`combatv2_cooldown_weapon_igneous_showdown_end_client`; 7092 is named
`combatv2_buff_dragon_scimitar_effect_end_client`. Both were readable on the
pinned original client. A sample at logic tick 457747 retained endpoints
445632 and 445843, so positive raw values alone do not establish active effects.
C effect IDs 15 and 16 keep units, presence and semantic expiry unknown until
labelled transitions establish them. The Dragon scimitar effect is a cast lead,
not an EoF cooldown.

**Integration rule:** the ordered opening and mechanics share one backend and
four event drains. Protection and defensives precede rotation dispatch. A
delayed wake response is reported rather than rebased to a later sample. Win32
submits each balanced ordered batch without per-key sleeps. Readiness and
outcome verification remain separate. See the
[combined controller](../../docs/telos-combined-controller.rst).

## Earlier account setup and research history

The following entries describe the November 13 session, not current bindings or
permission to acquire combat state from screenshots. Runtime acquisition is C.

- Confirm an equipped melee weapon and matching primary action bar. Check Revolution coverage and hover the first damage abilities to confirm their weapon requirements.
- Reserve strong damage for tendrils and font exit. At 0%, use Soul Split for healing. Bring prayer restoration and an overload if available. Put the chosen stun defensive on a known input and check whether shield defensives are castable with the current equipment and runes.
- Existing bindings recorded by the project are `Ctrl+F1` Deflect Magic, `Ctrl+F2` Deflect Ranged, `Ctrl+F3` Soul Split and `Ctrl+F4` Deflect Melee. Confirm the overhead after a switch. At 0%, use sustained protection when needed and keep prayer timing separate from damage optimisation.
- Inspect fresh client screenshots through MSS. Send confirmed actions through the Leonardo and check the resulting game state. The serial acknowledgement confirms a device command. The game can still reject an ability due to range, cooldown, resources or targetability. [Capture and input guide](../../docs/arduino-keyboard.rst)

**Observed on 2026-11-13.** The bank preset named Telos equips Necromancy. Loading it produced missing ectoplasm and empty ushabti warnings plus a beast-of-burden warning. Inspect equipment and supplies before leaving the bank even when a preset is named for the boss.

The account owner later replaced that preset with an augmented Zaros godsword, Masterwork melee armour and melee accessories. The working bar is preset 18. Select it explicitly after loading gear. Revolution covers seven slots, ordered Adaptive Strike, Greater Fury, Rend, Backhand, Attack, Chaos Roar and Assault. Hurricane, Overpower, Berserk, Soul Split and Deflect Melee are outside the automatic range. Verify bindings from the current layout before using shortcuts. A bar click reliably activated the intended prayer when the previously recorded chord did not.
