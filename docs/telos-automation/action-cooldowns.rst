Action cooldowns and same-tick actions
======================================

Reviewed: 2026-11-11. Status: **research leads only**. Nothing on this page has
been validated against the pinned executable or a live session.

Purpose
-------

The arbiter (track D6) and the actuators (track A1) decide how many inputs may
go out in one game tick. That depends on which actions have a timer of their
own and which share one. This page collects what published sources say, marks
where they disagree, and lists what still has to be measured.

Rules for using this page:

* Every value here is a lead, in the sense of ``AGENTS.md``. Native timers on
  the pinned executable win wherever the DLL exposes them (ability cooldowns,
  server and client GCD, see ``docs/native-api.rst`` and
  ``docs/inventory-and-abilities.rst``).
* Combat Style Modernisation (2 March 2026, with patches on 9 March, 16 March
  and 7 April 2026) changed many of these rules. PVME states that most of its
  content is outdated since that release. Older guide text is not imported.
* Source tags: **user** (stated by the user on 2026-11-11), **jagex**
  (official patch notes), **wiki** (RuneScape Wiki), **pvme** (PVME guides),
  **ours** (our engineering reading, not stated by any source).

Clocks
------

* One game tick is 0.6 s. Actions registered during a tick start by the
  beginning of the next tick. [wiki `Game tick`_]
* The global cooldown (GCD) is a fixed 3 ticks (1.8 s). It starts when a player
  begins an ability or most spells. A cast-time spell that is interrupted
  cancels its GCD. [wiki `Global cooldown`_, jagex `Part 1 notes`_]
* Basic attacks replaced auto-attacks on 2 March 2026 and run on the GCD.
  Weapon attack speed no longer affects timing. Separate auto-attack timers and
  4-tick auto-attack tricks no longer exist. [jagex `Part 1 notes`_,
  wiki `Modernisation comparison`_]
* The client logic clock (20 ms) and the collector's sampling rate are not game
  ticks. Faster sampling does not create more actions per tick. [ours,
  ``docs/telos-melee-research.rst``]

Independent timers
------------------

Each row is a separate timer. Actions in different rows do not wait for each
other unless a row says so. An item can be subject to two rows at once: an
adrenaline potion uses the potion row *and* the adrenaline-potion group.

.. list-table::
   :header-rows: 1
   :widths: 18 30 30 22

   * - Timer
     - Length and trigger
     - What it blocks or shares
     - Source
   * - GCD
     - 3 ticks from the start of an on-GCD ability, basic attack or spell.
     - Every other on-GCD ability. Each ability also keeps its own cooldown.
     - wiki, jagex
   * - Ability cooldown
     - Per ability. Read from the DLL where mapped.
     - Only that ability, or its shared group (next section).
     - wiki, pvme
   * - Off-GCD ability
     - Own cooldown. Does not start the GCD and is not blocked by it.
     - Several can be used together, then followed by an on-GCD ability.
     - wiki
   * - Off-GCD spell
     - Vengeance 50 ticks; Disruption Shield 100 ticks. Off the GCD since
       26 March 2018.
     - Each its own timer. No source says they share one.
     - wiki
   * - Food (solid)
     - Most food 3 ticks. Pies 1 tick per bite. Drains 3% adrenaline per
       solid piece in combat (was 10% before 2 March 2026).
     - Next solid food.
     - wiki, jagex
   * - Combo food
     - Cooked karambwan, green and blue blubber jellyfish, pizza. No delay
       when combined with other food. Non-solid, no adrenaline drain
       (pizza's drain is not stated).
     - Not blocked by the solid-food delay.
     - wiki
   * - Potion
     - Normally one consumable per 3 ticks, but a potion can be chained with
       food in the same tick. Saradomin brews are potions.
     - Next potion. Whether food and potions share one 3-tick timer or have
       two is not stated for RS3.
     - wiki, pvme
   * - Adrenaline potion group
     - 200 ticks (2 min). Adrenaline, super adrenaline and adrenaline renewal
       potions trigger it.
     - Every potion in the group.
     - wiki
   * - Powerburst group
     - 200 ticks (2 min), shared by all powerbursts. Separate from the
       adrenaline-potion group since 13 July 2020.
     - Every powerburst.
     - wiki, pvme
   * - Healing-special group
     - 500 ticks (5 min). Enhanced Excalibur, Rejuvenate, Guthix's Blessing,
       Ice Asylum.
     - All four.
     - wiki, pvme
   * - Ancient elven ritual shard
     - 500 ticks (5 min).
     - Sharing with other items is not documented.
     - wiki
   * - Weapon special attack
     - Per weapon. An EoF special uses the stored weapon's cooldown.
     - The physical weapon and the EoF copy block each other.
     - wiki
   * - Prayer
     - No cooldown documented. Prayers that cannot be combined switch each
       other off; drain starts after the first tick.
     - Nothing outside prayer.
     - wiki, user
   * - Equipment switch
     - No cooldown or per-tick limit found in any source.
     - Unknown.
     - no source
   * - Thrown bombs
     - Effects land 3 ticks after the throw.
     - Throw delay and cooldown not documented.
     - pvme

Off-GCD abilities and spells
----------------------------

Listed by the wiki as castable during the GCD [wiki `Abilities`_]:

* Movement: Surge, Escape, Dive, Bladed Dive. Off the GCD, Bladed Dive deals no
  damage and generates no adrenaline. Surge generates no adrenaline since
  4 March 2024.
* Defence: Provoke, without adrenaline. PVME adds that Provoke starts a GCD
  when used outside one; this is unconfirmed.
* Other: Runic Charge (magic), quiver ammo slots, Golden Touch, Sanguine
  offering, Limitless, Vital Drain, Ingenuity of the Humans, Kuradal's Favour,
  Slayer's Insight and the slayer perks (Demon, Dragon, Undead, Vampyre).
* Spells: Vengeance, Disruption Shield. With ability queueing on, debuff spells
  such as Vulnerability and Entangle can also be cast during the GCD.
  [wiki `Ability queueing`_]

**Not off-GCD:** Anticipation was trialled off the GCD in a combat beta; the
trial was reverted, and live sources do not list it as off-GCD. No source lists
Freedom, Resonance, Divert, Barricade, Devotion, Reflect, Debilitate,
Immortality, Natural Instinct, Berserk or the melee damage abilities as off-GCD.
Treat them as on-GCD until a native observation says otherwise. [wiki
`Anticipation`_, `Freedom`_, jagex `Beta update 4`_]

Shared ability cooldowns
------------------------

.. list-table::
   :header-rows: 1
   :widths: 30 45 25

   * - Group
     - Notes
     - Source
   * - Resonance, Divert
     - One shared timer. Preparation shortens both by 3 s (5 ticks) per
       attack received.
     - wiki, pvme
   * - Dive, Bladed Dive
     - Shared, restated in the 7 April 2026 notes.
     - wiki
   * - Rejuvenate, Enhanced Excalibur, Guthix's Blessing, Ice Asylum
     - 5-minute shared timer.
     - wiki, pvme
   * - EoF special, stored weapon's physical special
     - Either use triggers the cooldown where the weapon has one. Since
       August 2020, swapping to a second EoF cannot give a second special in
       the same GCD cycle.
     - wiki
   * - Ability and its targeted or Greater variant
     - The wiki gives Magma Tempest and its targeted form as an example of
       variants sharing a cooldown. Melee Greater variants are assumed to
       replace their base ability; not verified.
     - wiki, ours

**No longer shared:**

* Surge and Escape: separate since 16 March 2026, except in PvP. The wiki's
  ability overview still says they share; that line is stale. [jagex, wiki
  `Surge`_]
* Surge and Dive: the Surge article says they share in PvP areas and elsewhere
  says they do not share. Treat as separate in PvM, unverified.
* Barge and Surge or Escape: separate since 13 July 2015.
* Sonic Wave and Concentrated Blast: separate since 2 March 2026 (magic).
* Hurricane and Destroy: shared in 2015, but Destroy was removed in 2026.

**No shared timer documented:** Anticipation and Freedom have separate timers.
No source links them.

Defensive cooldown leads
------------------------

Values in ticks. Where the wiki and PVME disagree, both are shown. The native
cooldown timer decides.

==================== ========== ========== =====================================
Ability              Wiki       PVME       Note
==================== ========== ========== =====================================
Anticipation         41         50         Disagreement
Freedom              50         50
Resonance            50         25         Disagreement; shared with Divert
Divert               --         34         Shared with Resonance
Preparation          --         34
Provoke              --         17         Off-GCD
Debilitate           --         50
Reflect              --         50
Devotion             --         100
Barricade            --         100-140    Turtling perk
Immortality          --         200
Natural Instinct     --         200
Rejuvenate           --         500        Healing-special group
Surge                34         --         Off-GCD; Mobile perk halves
==================== ========== ========== =====================================

Same-tick combinations
----------------------

Stated:

* Eating food, changing prayer and casting an ability can all happen in the
  same tick, each with its own cooldown. [user]
* Food followed by a potion in the same tick. Example: sailfish soup, then a
  super Saradomin brew, then a blue blubber jellyfish heals 4650 life points in
  one tick. [wiki `Tick manipulation`_]
* A potion can be combined with food in the order potion, then food. [pvme
  `Consumables`_]
* Several off-GCD abilities can be used together, followed by an on-GCD
  ability. [wiki `Global cooldown`_]

Order matters, but no RS3 source states the full rule. The wiki example is
food, potion, combo food; PVME names potion, food. Old School rules (for
example, a brew not directly after combo food) are a different game and are
not imported.

Unknown, to be measured:

* Whether food and potions share one 3-tick timer or keep two.
* The full set of valid eating and drinking orders within one tick.
* Whether two off-GCD abilities pressed in the same tick both fire.
* Whether more than one prayer change per tick has any effect beyond the last
  state.
* Whether equipment switches have a per-tick limit or interact with the GCD.
* Whether eating touches the GCD in PvM. No source says it does; a 2015 note
  only says eating no longer lowers the GCD in PvP.
* Pizza's adrenaline drain and the throw delay of bombs.

Ability queueing and stalling
-----------------------------

[wiki `Ability queueing`_]

* Queueing is an account setting, off by default for new players since
  12 January 2026. The bot must know the account's setting.
* A queued ability fires when the GCD and its own cooldown end. In combat it
  must be queued at least one tick before the cast.
* A queued offensive ability stops movement to cast (movement stall). Cease
  clears the queue. Since 16 March 2026, Surge, Escape and Runic Charge no
  longer clear a stalled ability.
* Queued abilities behave differently on the tick of incoming damage and on a
  buff's final tick.

What this means for the tracks
------------------------------

Our reading, for D6 and A1:

* Budget by timer, not by count. One sample may legitimately send an on-GCD
  ability, one or more off-GCD abilities, food, combo food, a potion and a
  prayer change in the same tick.
* A group timer (adrenaline potion, powerburst, healing special, Resonance
  and Divert) is checked in addition to the item's own consumable timer.
* Timers the DLL does not expose (food and potion delay) are tracked from the
  observed consumption, not from the send.
* Send order within a tick is part of the plan. Until the order rules are
  measured, use one documented order per combination and test it.
* An unknown row above blocks automatic use of that combination until track L
  validates it.

Validation to add to the track L ladder:

1. Food, potion and combo food in one tick: total healing matches the sum.
2. Food, prayer change and an on-GCD ability in one tick: all three observed.
3. Two off-GCD abilities in one tick.
4. Potion then food against food then potion.
5. A defensive pressed during the GCD, to settle on-GCD or off-GCD status.

Sources
-------

.. _Game tick: https://runescape.wiki/w/Game_tick
.. _Global cooldown: https://runescape.wiki/w/Global_cooldown
.. _Abilities: https://runescape.wiki/w/Abilities
.. _Ability queueing: https://runescape.wiki/w/Ability_queueing
.. _Tick manipulation: https://runescape.wiki/w/Tick_manipulation
.. _Anticipation: https://runescape.wiki/w/Anticipation
.. _Freedom: https://runescape.wiki/w/Freedom
.. _Surge: https://runescape.wiki/w/Surge
.. _Modernisation comparison: https://runescape.wiki/w/Combat_Style_Modernisation
.. _Part 1 notes: https://secure.runescape.com/m=news/patch-notes-part-1---combat-style-modernisation
.. _Consumables: https://pvme.io/pvme-guides/upgrading-info/consumables/
.. _Beta update 4: https://secure.runescape.com/m=news/combat-beta-update-4

Further pages read: wiki `Food <https://runescape.wiki/w/Food>`__,
`Powerburst <https://runescape.wiki/w/Powerburst>`__,
`Adrenaline potion <https://runescape.wiki/w/Adrenaline_potion>`__,
`Resonance <https://runescape.wiki/w/Resonance>`__,
`Vengeance <https://runescape.wiki/w/Vengeance>`__,
`Disruption Shield <https://runescape.wiki/w/Disruption_Shield>`__,
`Enhanced Excalibur <https://runescape.wiki/w/Enhanced_Excalibur>`__,
`Ancient elven ritual shard <https://runescape.wiki/w/Ancient_elven_ritual_shard>`__,
`Essence of Finality amulet <https://runescape.wiki/w/Essence_of_Finality_amulet>`__;
jagex `Part 2 notes
<https://secure.runescape.com/m=news/patch-notes-part-2---combat-style-modernisation>`__;
pvme `Defence abilities
<https://pvme.io/pvme-guides/miscellaneous-information/ability-information/defence-abilities/>`__.
