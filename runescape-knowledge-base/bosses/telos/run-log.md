# Telos run log

## 2026-11-13 preparation

- Verified the Draynor lodestone shortcut and War's Insignia entrance to War's Retreat.
- Confirmed the hub portal is already attuned to Telos.
- Loaded the saved Telos preset at the hub bank. It was a Necromancy setup and reported missing supplies. A melee loadout is being checked separately.

## First 0% attempt

- The account owner prepared an augmented Zaros godsword, Masterwork melee body and legs, Jaws of the Abyss, Enhanced gloves of passage, Essence of Finality, and Rocktails, brews and overload salve. Native inventory and equipment observations confirmed the setup.
- Built melee action bar 18 and changed the Revolution range from 11 slots to 7. The game later returned to magic bar 1, so check that bar 18 is selected before engaging.
- The boss UI confirmed 0% and 400,000 starting HP. The Tendrils chat cue required 3,000 damage to break free. Hold Still led to a movement lock and a period where deflect curses were unavailable. The attempt reached P2 at 300,000 boss HP and about 2,200 player HP. The next observed state was back at War's Retreat. No kill was recorded.
- Use Practice Mode next. Set the melee protection baseline before waking the boss and prepare a direct stun-break key.

## First 0% practice attempt

- The client confirmed Practice Mode, 0% and phase 1. Confirmed Deflect Melee in both the native prayer sample and the on-screen overhead before waking Telos.
- Hit the first tendrils damage requirement and reached 325,394 boss HP in the last inspected P1 frame. A Rocktail was consumed in that frame. After losing foreground, the next frame showed the character outside the fight and a Defence cape life-saving message. The end of the fight was unobserved.
- The opening shadow trace recorded one `28929` application and one native player hitmark with zero queue losses. The recording ended early in combat. These data establish neither the incoming hit's identity nor a safe flick interval.
- Subsequent attempts must use prayer for healing and leave food and brews unused at 0%.

## First no-food 0% practice attempt

- Ran Revolution with an augmented Zaros godsword and used Turmoil, Soul Split and Deflect Melee. Drank overload salve and a prayer restore. No Rocktails or brews were used.
- Hold Still interrupted Assault and prevented movement. The P2 transition frame showed 1,037 player HP. Soul Split then recovered some HP while Revolution continued attacking. After switching to Deflect Melee, the last fight frame showed 232,470 boss HP and 3,167 player HP. The next frame was back at the practice staging platform.
- Continuous Soul Split through Hold Still left too little HP for the next phase. Reserve a stun break and switch to melee protection before the special when its cue is confirmed. The prototype still needs a longer capture spanning multiple specials.

## 0% practice defensive capture

- The trace captured two Tendrils-to-Uppercut loops, one Hold Still and entry into P2. The phase field changed from `0` to `1` in bits 21–23 at that handoff. Latest recorded P2 player HP was 251. No kill was recorded.
- The attack count and cue order were reproduced offline by `work/telos_zero.py` from the local capture. [Animation observations and replay instructions](animation-cues.md)

## Scripted 0% practice startup

- Re-entered through the Telos portal at War's Retreat. The client chat confirmed 0% enrage and Practice Mode. The native reader could see dormant Telos from the staging platform across the gap.
- The Python controller clicked the Deflect Melee slot via the Arduino and confirmed the active overhead. Its approach clicks moved the character on the staging platform, but no attempt changed Telos from dormant to active. Rejoin on the Roots reported that the character was already inside the instance. The arena crossing still needs validation before using an approach point.
- Prayer points ran out during staging after Deflect Melee was left active. Recharge prayer before retrying and wait to activate the overhead until the route into the arena is established. No food was used and no fight or kill was recorded.

## First scripted phase 1 clear

- Entered `0` at the second Roots prompt and crossed onto the arena. Used one Super restore dose before engagement to restore prayer points. No food or brews were used.
- The controller activated Deflect Melee and woke Telos at 400,000 HP. Auto-retaliate and Revolution dealt damage. Freedom was acknowledged by its cooldown during Hold Still. Hurricane and Anticipation inputs had no observed cooldown acknowledgement.
- Entered P2 at 300,000 boss HP and 3,412 player HP. Read-only observation continued while Revolution brought Telos to 271,753 HP. The last sample had 2,493 player HP and no active overhead. No full kill was recorded.

## 2026-11-14 0% Practice Mode attempts

- Entered P2 at 300,000 boss HP and 7,771 player HP with Deflect Melee active. The controller sent Anticipation from its P1 forecast and Freedom for `28935` in P2. Freedom had no cooldown acknowledgement. Input stopped at P3 with 2,511 player HP. Observation continued to zero player HP at 159,715 boss HP. The client showed the Defence cape save and returned the character to staging. [First attempt](../../../training-data/telos-20261114T075101.613778Z.jsonl)
- The updated DLL published enrage 0 and native phases 1 and 2. Compatibility Mode reported same-world actor tiles during both phases. The character entered P2 at 8,294 HP. Anticipation and Freedom produced no cooldown acknowledgement. Player HP reached zero at 206,503 boss HP. Both logs show zero NPC animation or player hitmark queue losses. [Second attempt](../../../training-data/telos-20261114T075957.862524Z.jsonl)
- Two received-tick captures reached P2 and ended at zero HP. Tooltip inspection identified Freedom as displayed `Z` and Anticipation as `T`. In staging, physical `Y` and `T` started the respective cooldowns. In the second capture the script used the layout-correct keys, but the immediate Freedom input coincided with an active GCD. [Tick capture](../../../training-data/telos-20261114T081053.312859Z.jsonl) · [Layout-correct attempt](../../../training-data/telos-20261114T081521.298786Z.jsonl)
- Practice Mode at 100% enrage locked a 500,000 maximum Telos HP. The first melee flick trial pressed Ctrl+F3 for Soul Split and the overhead stayed on Deflect Melee. The account binds Deflect Melee to F3 and Soul Split to F4 on bar 9. [Ctrl attempt](../../../training-data/telos-20261114T101934.331572Z.jsonl)
- With F3/F4, the flick held Soul Split between cues and showed Deflect Melee for one tick after each animation. Tendrils autos landed as zero hits. Hold Still's +4 (3,625) and +9 (5,144 typeless) hits landed on Soul Split, and the uppercut hit 2,634 at +7. Four Deflect Melee presses during the P1→P2 switch had no effect. The account owner confirmed that no action is possible during the switch. Player HP reached zero 12 s into P2 without food. [F3/F4 flick attempt](../../../training-data/telos-20261114T102118.663533Z.jsonl)
- At 100% with `--escape U`, Escape fired 4 received ticks after Uppercut, moved the character 7 tiles and no Uppercut hitmark followed. Hold Still then hit 4,664 at +4 on Soul Split and 5,350 typeless at +9, and an auto ended the attempt in P1. [Escape attempt](../../../training-data/telos-20261114T102624.888975Z.jsonl)
- At 250% (600,000 maximum HP) the second Anticipation press was acknowledged before Hold Still. No +9 landing hit appeared, but the +4 hit of 5,606 landed on Soul Split. Escape did not fire because the player tile was unavailable, and Uppercut hit 3,089. The character reached P2 and died to rising `28959` magic hits. [250% attempt](../../../training-data/telos-20261114T103044.874526Z.jsonl)
- At 250% the attempt lasted 103 s and entered P2 at 2,347 player HP. The first Escape avoided Uppercut. The second Uppercut began at 454,018 boss HP. The controller paused at the 450,000 boundary, so Escape was skipped and a 3,181 typeless hit landed. The pause is removed. Hold Still hit +2 for about 1,100 and +4 for 4,384 and 4,488, both on Soul Split. Anticipation was acknowledged on its retry. The second Tendrils bind lasted 12 ticks, with eight typeless hits of 423 to 570. No `28959` appeared. P2 Hold Still had no Anticipation forecast, and its +9 typeless hit ended the attempt. [103 s attempt](../../../training-data/telos-20261114T103455.340108Z.jsonl)
- At 500% the locked maximum HP was again 600,000. The attempt lasted 121 s, the longest so far. Escape avoided both Uppercuts, and the Anticipation retry was acknowledged before each P1 Hold Still. The first `28959` with Deflect Magic flicked hit for 552. Resonance was never sent. The DLL reports its equipment match as unknown with the two-handed weapon, because the cache marks it shield-only, and the controller dropped it. Hold Still hit 2,295 then 6,998 in P1 and 2,168 then 6,517 in P2. From 108 s, typeless hits of 540 to 570 landed almost every tick until death. The account owner identified them as the virus, which the P2 black beam cleanses. The account owner had a shield equipped and Resonance was off cooldown. The log has no Resonance request, so the unknown equipment match, not the GCD, blocked it. [500% attempt](../../../training-data/telos-20261114T103953.792051Z.jsonl)
- At 500% with the unknown-equipment fix, Resonance was requested at Hold Still +3 and +2. Each time Revolution had just started a GCD (1,780 ms left), and Resonance had no cooldown acknowledgement before the +4 hits of 8,612 and 8,265. This confirms that the GCD delayed it. Escape and the Anticipation retry worked again. A P2 Hold Still +9 typeless hit of 3,233 ended the attempt at 99 s. [GCD-blocked Resonance attempt](../../../training-data/telos-20261114T105331.294599Z.jsonl)
- At 500% with Resonance queued during the GCD, the P1 Hold Still press at +0 (1,740 ms of GCD left) cast at +3. The +4 hit turned into 4,195 healing, taking HP from 5,687 to 9,899. Both Uppercuts were escaped and P2 began at 7,141 HP. The P2 Hold Still press at +0 (1,780 ms) did not cast when the slot freed. The +2 and +4 hits of 2,245 and 5,600 ended the attempt at 98 s. One retry at the free slot is now added. [First Resonance success](../../../training-data/telos-20261114T105851.660982Z.jsonl)
- At 500% with the P2 rotation, both Hold Stills were handled. In P1 the Anticipation retry was acknowledged, Resonance queued at +0 cast by +3, and the +4 hit healed 5,081. In P2 the controller forecast Hold Still after the `28959` Onslaught. Anticipation was acknowledged at the second auto, Resonance was acknowledged at +1, and the +4 hit healed 1,833. The attempt lasted 128 s. The black virus began 3 autos after the P2 Hold Still and ended it, along with autos. In P2 the log showed Telos (`22891`) and two `8182` actors, one of which appeared at P2 start next to Telos. Neither has been tied to the black beam. [Both Hold Stills handled](../../../training-data/telos-20261114T110538.853403Z.jsonl)
- With the post-hit Freedom trial, P2 started at 6,175 player HP. The controller sent Freedom at `28935` +4 received ticks, and the cooldown started at +5. The overhead read `none` at +8 and the large landing mark appeared at +9. Player HP reached zero at 284,788 boss HP. [Post-hit Freedom attempt](../../../training-data/telos-20261114T081931.814573Z.jsonl)
