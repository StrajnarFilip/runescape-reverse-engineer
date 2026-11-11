/* Decoding of one complete frame from the attached client. */
#ifndef RS3_FRAME_H
#define RS3_FRAME_H

#include "context.h"

/**
 * Decodes the player, NPC, inventory and combat state into snapshot.
 *
 * Every field is staged first. Identities, events and change times are
 * committed only after the world context is verified unchanged. Returns 0 when
 * the player fields are unavailable; NPC and inventory failures only make the
 * frame partial.
 */
int frame_decode(rs3_context *context, rs3_snapshot *snapshot);

#endif /* RS3_FRAME_H */
