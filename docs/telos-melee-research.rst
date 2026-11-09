Melee Telos: combat research and integration notes
==================================================

Reviewed: 2026-11-09. Requested combat style: **melee**.

This is a working knowledge base for the reader/tracker project, not a record of
a successful automated kill. Published mechanics, guide recommendations and
proposed implementation work are distinguished below. No gameplay input was
sent and no new memory field was validated during this research task.

Source access: PVME articles and official Jagex news were read directly. Direct
RuneScape Wiki access was blocked by robots.txt; Wiki findings below came from
indexed article text returned by web search. Source retrieval does not establish
that the user's executable implements every published value.

Current combat rules and legacy material
----------------------------------------

Jagex's `Combat Modernisation Part 1`_ establishes the March 2026 framework:
player basic attacks use the ordinary GCD, basics generally generate 9%
adrenaline, and the reworked core-style Enhanced abilities no longer have the old
50% activation floor. Melee gained Bloodlust. Greater Flurry extends Berserk;
Meteor Strike supplies adrenaline rather than its old critical-hit effect.
Berserk's launch values became +75% damage and +25% damage taken. Launch numbers
are historical evidence, not a substitute for subsequent balance patches.

The `modernisation comparison`_ also records removed abilities, including
Destroy, Forceful Backhand, Cleave and Smash. Slaughter and Massacre became
Dismember recasts. Backhand remains available. Cache definitions and old action
bars can still contain historical names: an extracted definition is not proof
that an ability is currently usable.

The `PVME stun guide`_ retains Destroy/Forceful Backhand combinations and old
player auto-attack techniques. Preserve its discussion of Telos's Freedom and
attack delays, but do not import those combinations into a current melee policy.
Its 250%-999% Freedom interval is 20.4s, whereas the basic guide lists 31 ticks
(18.6s). This disagreement needs encounter measurements.

Telos encounter model
---------------------

The `PVME basic guide`_ gives these phase-specific hazards:

* P1: melee-distance-sensitive attacks, tendrils and a green adrenaline beam.
* P2: black beam lowers player damage dealt/taken; Telos in it charges his meter.
  Onslaught becomes more dangerous as the meter grows. Cleanse black virus.
* P3: magic attacks, red beam increases damage dealt/taken, and golems affect
  the meter. Its extremes cause prayer/adrenaline drain or shockwaves.
* P4: three font intermissions, golems, lost targeting and defensive resets.
  Charge the correct font; the final font damage cannot use ordinary mitigation.
  Weak anima bomb also threatens the overload timer until cleansed.
* P5: six golems, random beam positions, virus cleansing, rocks and an eventual
  lethal bomb. Killing quickly and a recovery route are separate strategies.

Virus appears in P2/P3 from 50% enrage; P5 begins at 100%; Freedom begins at
150%; P5 Freedom grants temporary stun immunity from 250%. At 1000%+, rocks
and the loss of the P5 Immortality escape change recovery options substantially.

The `Wiki Telos strategies`_ describe attack progress carrying between phases.
P1 starts with seven ordinary attacks; later gaps are usually three. P2 can
inherit remaining attacks and its first special from P1. P3 has its own opening
and a conditional special skip. P4 also has conditional skips and meter-driven
interruptions. P5 starts a separate cycle. Consequently, phase number and elapsed
wall time alone do not determine the next attack.

Important multi-hit distinctions from that article: Hold Still includes an
ordinary attack, a heavy melee stun and a subsequent jump. So Much Power can
arrive with an ordinary attack. A one-hit block can be consumed by the wrong
hit. The uppercut is soft typeless; protection prayer alone is insufficient.

The `P5 movement reference`_ explains that beam entry depends on spawn position,
player position and facing. Its visual font-shift cue must eventually be replaced
by a verified native event for this project's image-free approach. It explicitly
warns that its example opening rotations can be outdated.

Melee rotation reference
------------------------

The `PVME melee Telos guide`_ is the primary rotation reference. It assumes
250%+ and is intended especially for 1000%+. It requires substantial gear,
unlocks and precise setup; it is not an unconditional rotation for any account.

Its structure is:

* Pre-fight: Bloodlust preparation, Natural Instinct and Dragon battleaxe spec.
* P1: Berserk/renewal burst, Greater Barge, Meteor Strike and strong spenders.
* P2: short delayed opening followed by Barge and direct damage.
* P3: Zaros godsword window, Greater Flurry and Chaos Roar into Overpower.
* P4: distinct opening and three font-specific branches, with target switches,
  defensives, sticky bombs and an EZK stall/release.
* P5: red-beam damage branch or green-beam recovery for insufficient adrenaline;
  the latter trades kill speed for resources and protection.

The guide distinguishes physical weapon specials from Varanus's Mercy through
EoF. Symbols for stall/release and simultaneous actions must survive translation
into a future policy. Link to the complete source for exact ability orders rather
than treating this outline as a cast script.

The `PVME melee DPM guide`_ makes rotation selection dependent on phase duration,
weapons, adrenaline, Bloodlust and practical switching. EZK's equipped passive,
Leng stacks, Greater Barge preparation, enhanced spenders and Berserk extension
all change priorities. Bleeds need enough remaining targetable time to pay off.
Long-fight and short-fight weapon recommendations differ.

The `PVME melee ability reference`_ describes Adaptive Strike's different 2H/DW
behaviour and Bloodlust consumption by Assault, Hurricane and Flurry. Greater
Barge can convert a qualifying subsequent channel into damage over time after
the required time without attacking. Chaos Roar affects the next eligible hit or
attack differently from a whole ordinary channel. These require buff state and
hit timing, not just ability cooldowns. The DPM guide and ability reference have
some inconsistent Berserk/gear-duration text; do not hardcode those figures.

Prayer and defensive decisions
------------------------------

The `PVME prayer guide`_ separates switching protection styles from Soul Split
flicking. Protection generally reduces matching typed damage by 50%, or 60% with
an amulet of souls/EoF; powder of protection does not add another 10% on top.
Devotion requires the correct protection prayer. Soul Split healing belongs in
the windows between incoming attacks, returning to protection before the
appropriate hit is resolved. This is not a fixed alternating timer.

The `Wiki Resonance article`_ distinguishes a one-hit conversion to healing from
general mitigation. Resonance and Divert share a cooldown; Preparation can reduce
it. Some powerful hits are blocked without healing. Physical shield tier affects
the benefit, and simultaneous hits need separate handling. Bone Shield can enable
many non-offensive shield abilities, but does not substitute for every shield
ability; rune availability and effective shield tier matter.

The `PVME defence reference`_ is useful for the roles of Anticipation, Freedom,
Preparation, Reflect, Devotion and Barricade, but contains inconsistent timings:
it currently lists Resonance at 15s and Divert at 20.4s despite their shared
cooldown. Wiki lists 30s for both, consistent with our earlier observed Resonance
timer. Use native timer observations for the executable under test.

Food, potions and inventory semantics
-------------------------------------

Jagex's `Combat Modernisation Part 2`_ reduced ordinary food's adrenaline drain
from 10% to **3%** and removed Constitution-based healing scaling. The
`Wiki food article`_ distinguishes non-solid food such as blubber jellyfish, which avoids
that drain. Avoid using pre-update 10% assumptions.

The `PVME consumables guide`_ records blue blubber's three 750-LP bites,
Saradomin brew's 1000-LP dose and combat-stat drain, and Guthix rest as a different
healing option. Overloads refresh stat boosts periodically, so a brew's drain
must not be treated as instantly cancelled. Prayer restoration depends on the
item and the player's level, not one universal fixed amount.

Adrenaline potions share an adrenaline-generation cooldown; renewal distributes
40% adrenaline over ten game ticks rather than granting it instantly. Potion and
food combination eating depends on order. Vulnerability bombs amplify damage;
sticky bombs can force Telos's Freedom. Bomb effects are delayed after throwing.

Implementation implication: record family, dose/bite count, slot and item changes
separately. An ID transition after drinking can preserve the item's role. Never
treat a restore dose, brew dose, renewal dose and vitality powerburst as
interchangeable merely because they are consumables. Exact combination-eating
lockouts and powerburst cooldown interactions still need direct validation.

Essence of Finality identity and special attacks
------------------------------------------------

The `Wiki EoF article`_ describes a stored weapon special executed using the
current weapon's stats and requiring a matching combat style. Physical weapon
spec and EoF ability are separate actions; weapon-specific cooldowns can be
shared between them. Inserting a weapon permanently consumes it.

Colour, ornamentation and charges are separate from stored contents. Dye is a
user's organisational label, not a guaranteed weapon identity. Two amulets with
the same visible item ID can need different behaviour. The `PVME EoF guide`_
identifies Varanus's Mercy as a melee burst option with a delayed final hit;
physical EZK, ZGS and preparation weapons have other roles in the Telos guide.
Some older spec damage ranges differ from the modernisation notes; this document
does not adopt them as current constants.

**Live-reader status:** inventory/equipment IDs and movements are implemented;
the per-amulet stored special has not been mapped or verified. Current ID/name
data alone cannot establish its contents. Proposed future identity includes
container/slot, best-effort instance token, item ID, stored weapon/spec identity,
charges and confidence. That is a design requirement, not an existing API.

Disruption Shield and Vengeance
-------------------------------

According to the `Wiki Disruption Shield article`_, this level-90 Lunar spell
blocks one eligible hitsplat, including soft typeless damage, rather than an
entire multi-hit attack. Its 60s cast cooldown and active shield are separate
states: precasting long enough before a hit can permit another cast immediately
after consumption. It does not stop binds or most hard typeless damage. It takes
priority over Resonance and Vengeance and operates outside the ordinary GCD.
Spellbook access, unlock and runes are required. The article's main text and old
patch history conflict on one-damage hits; that edge case remains unverified.

The correct spell name is **Vengeance**; "Taste vengeance!" is its activation
phrase. The `Wiki Vengeance article`_ describes a level-94 Lunar spell, a 30s
cast cooldown and a 75% reflected hit capped at 8000. The player still takes
the damage. Typeless hits and reflect-immune targets are excluded.

The `PVME mechanics guide`_ explains that Disruption Shield, Divert, Resonance,
properly prayed Devotion and Barricade can prevent Vengeance consumption. Damage
modifiers affect the rebound. It also distinguishes queued abilities from
stalled/released abilities: queueing can interrupt movement and alter defensive
timing. These are separate states from an ordinary GCD-ready ability.

Consequently, neither spell should be modelled as "cast every cooldown". The
intended incoming hit, active buffs, defensive priority and available spellbook
access must all be known. Eligibility of individual Telos hits remains a live
validation task.

What this means for the project
-------------------------------

The following are our engineering conclusions, not claims that PVME supplies
these native fields or that our reader already decodes them:

1. Add verified native observations for Telos animation/action, phase, enrage,
   ordinary-attack progress, Freedom, meter, golems, beams, fonts and hazards.
   Associate positions with arena/instance identity and check player facing.
2. Add active prayer/curses, stun/bind state, buffs with stacks/expiry, incoming
   hit identity, overload/renewal/vitality state, spell unlocks and rune supplies.
   Read an EoF's payload rather than inferring it from dye or inventory position.
3. Establish a measured game-tick reference. The 600ms game tick, the collector's
   faster sampling interval and the existing 20ms client timer unit are different
   clocks. Faster sampling does not create additional game action opportunities.
4. Build a read-only encounter timeline and recommendation view first. Each
   recommendation needs its evidence, expected effect and invalidation rules.
   Resolve guide conflicts using observed transitions on the pinned executable.
5. Validate a chosen gear/unlock/enrage configuration through manual encounters
   and replay captured events locally. Death prevention, correct hit protection
   and recovering from a missed action take priority over copying a speedkill.

These steps do not establish live recognition of Telos mechanics or complete
castability. Unknown/stale observations must remain unknown. No gameplay action
layer or changes to acquisition code were added in this research task.

The repository's ``blindfold-telos.md`` contains separate notes about a 2022
clear. Treat that as historical encounter context, not a current melee rotation.

Sources
-------

.. _Combat Modernisation Part 1: https://secure.runescape.com/m=news/patch-notes-part-1---combat-style-modernisation
.. _Combat Modernisation Part 2: https://secure.runescape.com/m=news/patch-notes-part-2---combat-style-modernisation
.. _modernisation comparison: https://runescape.wiki/w/Combat_Style_Modernisation
.. _PVME basic guide: https://pvme.io/pvme-guides/basic-guides/telos-basic/
.. _Wiki Telos strategies: https://runescape.wiki/w/Telos%2C_the_Warden/Strategies
.. _PVME stun guide: https://pvme.io/pvme-guides/rs3-full-boss-guides/telos/stuns-and-mechanics/
.. _P5 movement reference: https://pvme.io/pvme-guides/rs3-full-boss-guides/telos/p5-movement-and-timing/
.. _PVME melee Telos guide: https://pvme.io/pvme-guides/rs3-full-boss-guides/telos/melee/
.. _PVME melee DPM guide: https://pvme.io/pvme-guides/dpm-advice/dpm-advice-melee/
.. _PVME melee ability reference: https://pvme.io/pvme-guides/miscellaneous-information/ability-information/melee-abilities/
.. _PVME prayer guide: https://pvme.io/pvme-guides/new-to-bossing/prayer-flicking/
.. _Wiki Resonance article: https://runescape.wiki/w/Resonance
.. _PVME defence reference: https://pvme.io/pvme-guides/miscellaneous-information/ability-information/defence-abilities/
.. _PVME consumables guide: https://pvme.io/pvme-guides/upgrading-info/consumables/
.. _Wiki food article: https://runescape.wiki/w/Food
.. _Wiki EoF article: https://runescape.wiki/w/Essence_of_Finality_amulet
.. _PVME EoF guide: https://pvme.io/pvme-guides/miscellaneous-information/eof-specs/
.. _Wiki Disruption Shield article: https://runescape.wiki/w/Disruption_Shield
.. _Wiki Vengeance article: https://runescape.wiki/w/Vengeance
.. _PVME mechanics guide: https://pvme.io/pvme-guides/miscellaneous-information/mechanics/
