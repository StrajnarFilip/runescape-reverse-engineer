Spatial perception (P1)
=======================

The C collector stages actor poses and camera data before the parent frame's
final context check. Commit binds them to the scene and NPC instance IDs.
The records are copied into caller-owned snapshots; no game function is called.
Existing ABI structures and event queues are unchanged.

Current scope
-------------

The installed, running executable on 2026-11-11 is the original 951-3 profile
(``fd8afd32...358d45``), not the Compatibility Mode executable selected by S0b.
P1 currently enables these new fields only on that original profile, after
checking additional position, facing and projection instruction signatures.
Compatibility Mode remains explicitly unsupported by P1 until its additional
spatial layouts/signatures can be checked; its existing position reader remains
unchanged. This does not change the roadmap's chosen eventual runtime profile.

Actor fields on the original profile:

* The vtable and 16-bit runtime ID at ``+0x88`` are checked twice.
* Plane ``+0x50``, footprint ``+0x18c`` and fine coordinates ``+0x280`` are
  copied twice. Fine units are 512 per tile. The footprint-origin tile follows
  the client's integer half-footprint convention.
* Facing uses the unit quaternion at ``+0x1cc``. Native proxy getter
  ``0x44c910`` calls ``0x44b690`` on the actor's transform. The yaw is converted
  to degrees, zero south and increasing towards west. Non-finite or non-unit
  quaternions leave facing unknown without invalidating an otherwise usable tile.
* ``facingTarget`` is a desired heading, not a combat-target entity. It must not
  populate ``WorldState.*.target``. Combat-target decoding remains unmapped.

Camera and projection
---------------------

The actor's world pointer at ``+0x60`` supplies the column-major world-to-clip
matrix at ``+0x13900``. This already contains the view transform; multiplying a
view matrix into it again would be incorrect. The scene manager at client world
state ``+0x199e8`` supplies viewport x/y/width/height at ``+0x84..+0x90``.
Native functions ``0x05a260`` and ``0x6dec20`` establish this path and Y inversion.
Matrix, viewport and owner pointers must agree across repeated reads.
An actor must also belong to the player's current world object before actor
projection is allowed. The loaded registry may retain actors from another
world/instance; a matching plane alone is insufficient.

Physical client origin is read from the unique visible, non-minimized process
window using a temporarily per-monitor-aware thread DPI context. The previous
DPI context is restored. A viewport outside the physical client rectangle,
multiple windows or an unavailable origin invalidates physical projection.
This does not move/focus the window or pointer and reads no screenshot.

``rs3_project_actor`` projects the observed actor position plus a supplied
vertical offset. ``rs3_project_tile`` requires an explicit terrain height in
fine units: the height map is not yet mapped, and unknown height must not
default to zero. Both reject an unknown camera, wrong plane, non-finite inputs,
nonpositive clip W and points outside the viewport. A projected point does not
establish visibility through occluding geometry or UI overlays.

Public C interface
------------------

Include ``rs3/spatial.h``. The additional sizes are actor 88, camera 136,
spatial-info 272 and screen-point 24 bytes, verified with
``rs3_spatial_sizeof(1..4)``. The two snapshot accessors are
``rs3_snapshot_spatial`` and ``rs3_snapshot_npc_spatial``. The latter follows
the existing capacity/required-count convention and NPC snapshot order.
Each record carries identity/time or belongs to the accompanying spatial-info
identity. Consumers must check process creation, scene epoch and observation
age before using a projected pixel. Successful projection sends no input.

Python ``Reader.spatial`` and ``Reader.npc_spatial`` mirror these C records.
Telemetry adds ``player.tile/facing/target`` and the corresponding NPC fields
using S0a observation envelopes. Unknown values have reasons; targets remain
null. Actor ``screen_point`` fields contain C-computed physical pixels.

Validation
----------

``validation/spatial-validation.json`` separates fixture/static evidence,
read-only live observations and controlled checks. On 2026-11-11 the user
confirmed 22 projected ground points on a Training dummy through 22 distinct
camera orientations using a click-through marker. The observer sent no game
input. This validates the checked physical projections, not occlusion handling,
body-click hitboxes or combat-target identities. Target fields remain unmapped.
