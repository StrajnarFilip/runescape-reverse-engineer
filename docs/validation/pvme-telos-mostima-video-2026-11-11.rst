PVME melee Telos video timing: Mostima, 2,449%
==============================================

Source: `Mostima's 2:15 kill`_, the first example in the `PVME melee guide`_.
Status: video reference only. No input timing or live runner validation.

The 1080p stream has 3,546 frames at 24 fps. The table uses zero-based frame
numbers and seconds from the start of the upload, including the pre-fight
setup. To find cast cues, compare the action bar immediately before and after
each global cooldown starts, then check which ability gains its own cooldown.
Use the buff bar, inventory stack, and movement for actions without a global
cooldown. A frame is about 42 ms. Capture, rendering, buffering and ability
queueing can move the visible cue relative to the physical input by more than
a frame. Effects that first appear in the same frame have unknown input order
and spacing.

Of 44 intervals between successive observed global cooldown starts within
the same segment, 40 are 1.708–1.875 s, consistent with a three tick cadence
at 600 ms per tick. The exceptions are:

* Font 1: Adaptive Strike to Greater Fury is 2.417 s, and Overpower to
  Punish is 3.000 s.
* Font 2: Freedom to Greater Barge is 3.625 s, with the bomb stack dropping
  0.625 s before Barge. This does not measure the interval from the guide's
  cue or from the bomb input.
* Font 3: the inferred Ek-ZekKil stall to Greater Fury is 3.625 s, including
  a Dive. The stalled special release has no separately verified timestamp.

The video starts inside the encounter area and does not show the War's
Retreat preparation. The P1 opener shows a Dive icon flash and subsequent
movement; Surge and the exact input time are unverified. The P2 label is
already visible at 27.375 s, but the point where Telos becomes targetable
and the equivalent of the runner's F8 cue are not established. At Font 1,
the inventory shows two Sticky bombs used, one before Reflect and one after
Greater Fury. No separate opening melee basic was identified at Font 3.
In P5, the clip shows the red beam route. Vitality appears before Greater
Barge and the renewal buff starts after Overpower. The green route is absent.

For each row, the gap is from the preceding **listed cue** in that segment.
A blank gap means the first cue in a segment, with no measured checkpoint
delay. Shared cooldowns cannot by themselves distinguish Resonance and Divert.
Inferred weapon specials are labeled as such. Targeting, gear switches,
destination clicks and unlisted inputs do not have reliable timings here.

.. csv-table::
   :header: Segment, Action or cue, Frame, Video time (s), Gap (s), Evidence
   :widths: 10, 25, 7, 10, 8, 40

   "P1","Devotion",167,6.958,"","Cooldown"
   "P1","Dive cue",170,7.083,0.125,"Icon flash; movement follows"
   "P1","Berserk + Adrenaline renewal",210,8.750,1.667,"Cooldown and potion buff"
   "P1","Greater Barge",254,10.583,1.833,"Cooldown"
   "P1","Meteor Strike",296,12.333,1.750,"Cooldown"
   "P1","Assault",339,14.125,1.792,"Cooldown"
   "P1","Overpower",382,15.917,1.792,"Cooldown"
   "P1","Ek-ZekKil special",426,17.750,1.833,"Inferred from weapon and effects"
   "P1","Dragon scimitar special",469,19.542,1.792,"Inferred from weapon and effects"
   "P2","Greater Barge",701,29.208,"","Cooldown"
   "P2","Assault",742,30.917,1.708,"Cooldown"
   "P2","Overpower",786,32.750,1.833,"Cooldown"
   "P2","Hurricane",829,34.542,1.792,"Cooldown"
   "P2","Greater Fury",872,36.333,1.792,"Cooldown"
   "P3","Zaros godsword special",1088,45.333,"","Inferred from weapon and effects"
   "P3","Greater Barge",1131,47.125,1.792,"Cooldown"
   "P3","Greater Flurry",1174,48.917,1.792,"Cooldown"
   "P3","Chaos Roar",1218,50.750,1.833,"Cooldown"
   "P3","Overpower",1261,52.542,1.792,"Cooldown"
   "P3","Rend",1304,54.333,1.792,"Cooldown"
   "P4","Greater Barge",1536,64.000,"","Cooldown"
   "P4","Assault",1578,65.750,1.750,"Cooldown"
   "P4","Hurricane",1621,67.542,1.792,"Cooldown"
   "P4","Adaptive Strike",1664,69.333,1.792,"Cooldown"
   "Font 1","Preparation",1707,71.125,"","Cooldown"
   "Font 1","Sticky bomb (10 to 9)",1736,72.333,1.208,"Inventory"
   "Font 1","Reflect",1750,72.917,0.583,"Cooldown"
   "Font 1","Anticipation",1794,74.750,1.833,"Cooldown"
   "Font 1","Divert",1837,76.542,1.792,"Shared cooldown; identity from guide order"
   "Font 1","Adaptive Strike",1880,78.333,1.792,"Cooldown"
   "Font 1","Greater Fury",1938,80.750,2.417,"Cooldown"
   "Font 1","Sticky bomb (9 to 8)",1952,81.333,0.583,"Inventory"
   "Font 1","Overpower",1981,82.542,1.208,"Cooldown"
   "Font 1","Punish",2053,85.542,3.000,"Cooldown"
   "Font 2","Adaptive Strike",2140,89.167,"","Cooldown"
   "Font 2","Melee basic",2182,90.917,1.750,"Cooldown on sword icon"
   "Font 2","Meteor Strike",2226,92.750,1.833,"Cooldown"
   "Font 2","Resonance",2269,94.542,1.792,"Shared cooldown; identity from guide order"
   "Font 2","Freedom",2312,96.333,1.792,"Cooldown"
   "Font 2","Sticky bomb (8 to 7)",2384,99.333,3.000,"Inventory"
   "Font 2","Greater Barge",2399,99.958,0.625,"Cooldown"
   "Font 2","Greater Flurry",2442,101.750,1.792,"Cooldown"
   "Font 2","EoF special",2485,103.542,1.792,"EoF icon and global cooldown"
   "Font 2","Dive",2500,104.167,0.625,"Cooldown and movement"
   "Font 3","Anticipation",2615,108.958,"","Cooldown"
   "Font 3","Adaptive Strike",2660,110.833,1.875,"Cooldown"
   "Font 3","Devotion",2702,112.583,1.750,"Cooldown"
   "Font 3","Dismember",2745,114.375,1.792,"Cooldown"
   "Font 3","Chaos Roar",2790,116.250,1.875,"Cooldown"
   "Font 3","Ek-ZekKil stall candidate",2831,117.958,1.708,"Inferred from weapon and global cooldown"
   "Font 3","Dive",2845,118.542,0.583,"Cooldown and movement"
   "Font 3","Greater Fury",2918,121.583,3.042,"Cooldown"
   "P5 red","Berserk + Disruption Shield",3221,134.208,"","Cooldowns in the same frame"
   "P5 red","Powerburst of vitality",3248,135.333,1.125,"Current and max HP double"
   "P5 red","Greater Barge",3263,135.958,0.625,"Cooldown"
   "P5 red","Assault",3306,137.750,1.792,"Cooldown"
   "P5 red","Dive",3320,138.333,0.583,"Cooldown and movement"
   "P5 red","Overpower",3350,139.583,1.250,"Cooldown"
   "P5 red","Adrenaline renewal",3364,140.167,0.583,"Potion buff"
   "P5 red","EoF special",3394,141.417,1.250,"EoF icon and global cooldown"
   "P5 red","Hurricane",3436,143.167,1.750,"Cooldown"

Use this as a comparison for a controlled capture from the target account.
To set runner timing, record both key down and the resulting cast or item
change against the same client clock, with the actual checkpoint anchors
and route. The video does not establish any local binding, ``after_ticks``,
``at_ms``, or Track L pass.

.. _Mostima's 2:15 kill: https://www.youtube.com/watch?v=_51pCNARwhQ
.. _PVME melee guide: https://pvme.io/pvme-guides/rs3-full-boss-guides/telos/melee/
