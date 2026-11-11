/* Local player tile of the Compatibility Mode client. */
#ifndef RS3_POSITION_H
#define RS3_POSITION_H

#include <stdint.h>

#include "context.h"

/**
 * Reads the client scene object and returns nonzero while it reports the
 * in-game state. *scene_client receives the scene object address.
 */
int position_player_in_scene(rs3_context *context, uint64_t *scene_client);

/**
 * Reads the player tile. Consecutive reads must agree on both the actor and
 * its tile; otherwise the position stays unchanged and 0 is returned.
 */
int position_decode(rs3_context *context, rs3_position *position);

#endif /* RS3_POSITION_H */
