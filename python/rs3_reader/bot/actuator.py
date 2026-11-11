"""Actuator interface and recording fake. This module can never send game input.

Only the arbiter calls send. A backend serializes actual input and enforces
freshness, identity, deadlines and configured action budgets. Accepted input
MUST await a later confirming observation; send success is not game success.
The fake models acceptance/deadline rejection only, with no tick-wide cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Callable, Optional, Protocol, runtime_checkable

from .intents import Intent


@dataclass(frozen=True)
class SendResult:
    intent_id: str
    accepted: bool
    sent_ns: int
    reason: Optional[str] = None


@runtime_checkable
class Actuator(Protocol):
    """A1 implements real backends; close must release held keys, idempotently."""

    def send(self, intent: Intent) -> SendResult:
        """Report input acceptance only; reject expired/stale/wrong-session input."""
        ...

    def close(self) -> None:
        """Stop input, release held keys and close owned resources."""
        ...


@dataclass(frozen=True)
class SendAttempt:
    intent: Intent
    result: SendResult


class RecordingActuator:
    """A deterministic fake with serialized attempts and no hardware/DLL access.

    With no clock, each intent's issued_ns is the attempted send time. Inject a
    native-domain clock to test delayed/expired sends. Rejections are recorded
    too. Acceptance never changes WorldState or fabricates an acknowledgement.
    """

    def __init__(self, *, clock: Optional[Callable[[], int]] = None, accept=True):
        if not isinstance(accept, bool):
            raise TypeError("Fake acceptance must be boolean")
        self._clock, self._accept = clock, accept
        self._attempts = []
        self._closed = False
        self._lock = Lock()

    @property
    def attempts(self):
        with self._lock:
            return tuple(self._attempts)

    def send(self, intent):
        """Record one attempt; no per-tick limit and no simulated game success."""
        if not isinstance(intent, Intent):
            raise TypeError("send requires an Intent")
        with self._lock:
            now_ns = self._clock() if self._clock else intent.issued_ns
            if not isinstance(now_ns, int) or isinstance(now_ns, bool) or now_ns < 0:
                raise ValueError("Clock must return a nonnegative native-domain timestamp")
            if self._closed:
                reason = "closed"
            elif intent.expired(now_ns):
                reason = "deadline_expired"
            elif now_ns < intent.issued_ns:
                reason = "before_issue_time"
            elif not self._accept:
                reason = "recording_rejected"
            else:
                reason = None
            result = SendResult(intent.intent_id, reason is None, now_ns, reason)
            self._attempts.append(SendAttempt(intent, result))
            return result

    def close(self):
        """Idempotently stop accepting attempts; the fake has no held keys."""
        with self._lock:
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exception_info):
        self.close()
