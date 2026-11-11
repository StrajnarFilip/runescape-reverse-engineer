"""Bounded, deduplicated history of observed hit marks for display."""

from __future__ import annotations

from collections import deque

DEFAULT_CAPACITY = 150

# Native sequence numbers restart with a new context, so the identity of a hit
# is its process, recipient and raw record. This also deduplicates history
# imported from an earlier collector of the same client.
HIT_IDENTITY_FIELDS = (
    "pid",
    "process_creation_time",
    "runtime_id",
    "instance_id",
    "config_id",
    "slot",
    "type_id",
    "amount",
    "end_client_tick",
    "secondary_type_id",
    "secondary_amount",
)


def hit_event_key(event):
    """Return the identity tuple of a hit observation record."""
    return tuple(event.get(field) for field in HIT_IDENTITY_FIELDS)


class HitHistory:
    """Keeps the newest unique hit observations across coalesced telemetry records."""

    def __init__(self, capacity=DEFAULT_CAPACITY):
        self.events = deque(maxlen=capacity)
        self.keys = set()

    def observe(self, record):
        """Add the newly observed hits of one telemetry record."""
        hit_splats = record.get("hit_splats") or {}
        for hit in hit_splats.get("newly_observed") or []:
            event = dict(hit)
            event.setdefault("observed_at_ms", record["timestamp_ms"])
            event.setdefault("pid", record.get("pid"))
            key = hit_event_key(event)
            if key in self.keys:
                continue
            if len(self.events) == self.events.maxlen:
                # Keep the key set in sync with the deque's automatic eviction.
                self.keys.discard(hit_event_key(self.events[0]))
            self.events.append(event)
            self.keys.add(key)
