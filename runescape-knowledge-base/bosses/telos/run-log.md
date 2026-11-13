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
