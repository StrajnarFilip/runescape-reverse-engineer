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

## Earlier account setup and research history

The following entries describe the November 13 session, not current bindings or
permission to acquire combat state from screenshots. Runtime acquisition is C.

- Confirm an equipped melee weapon and matching primary action bar. Check Revolution coverage and hover the first damage abilities to confirm their weapon requirements.
- Reserve strong damage for tendrils and font exit. At 0%, use Soul Split for healing. Bring prayer restoration and an overload if available. Put the chosen stun defensive on a known input and check whether shield defensives are castable with the current equipment and runes.
- Existing bindings recorded by the project are `Ctrl+F1` Deflect Magic, `Ctrl+F2` Deflect Ranged, `Ctrl+F3` Soul Split and `Ctrl+F4` Deflect Melee. Confirm the overhead after a switch. At 0%, use sustained protection when needed and keep prayer timing separate from damage optimisation.
- Inspect fresh client screenshots through MSS. Send confirmed actions through the Leonardo and check the resulting game state. The serial acknowledgement confirms a device command. The game can still reject an ability due to range, cooldown, resources or targetability. [Capture and input guide](../../docs/arduino-keyboard.rst)

**Observed on 2026-11-13.** The bank preset named Telos equips Necromancy. Loading it produced missing ectoplasm and empty ushabti warnings plus a beast-of-burden warning. Inspect equipment and supplies before leaving the bank even when a preset is named for the boss.

The account owner later replaced that preset with an augmented Zaros godsword, Masterwork melee armour and melee accessories. The working bar is preset 18. Select it explicitly after loading gear. Revolution covers seven slots, ordered Adaptive Strike, Greater Fury, Rend, Backhand, Attack, Chaos Roar and Assault. Hurricane, Overpower, Berserk, Soul Split and Deflect Melee are outside the automatic range. Verify bindings from the current layout before using shortcuts. A bar click reliably activated the intended prayer when the previously recorded chord did not.
