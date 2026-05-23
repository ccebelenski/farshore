"""Concrete in-process synchronous `EventBus`.

Satisfies the `empire.core.event_bus.EventBusProtocol` structurally; nothing
imports the Protocol here because Python checks structural typing at the
use site.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

_E = TypeVar("_E")


class EventBus:
    """Synchronous in-process pub/sub.

    Handlers run in subscription order on the publishing thread. There is
    no async dispatch, no priority, and no error isolation: an exception in
    a handler propagates to the publisher. Phase-4 scope: keep it simple
    and predictable; if we need async or isolation later, swap in a
    different implementation behind the same Protocol.

    `subscribe` is generic over event type so callers can pass strongly-
    typed handlers (including bound methods like `list.append`) without an
    adapter lambda.
    """

    def __init__(self) -> None:
        # Storage is heterogeneous (any event type → its handler list); the
        # generic typing happens at the public API level via `subscribe`.
        self._subscribers: dict[type, list[Callable[[Any], None]]] = {}

    def publish(self, event: object) -> None:
        # Snapshot the handler list before iterating in case a handler
        # subscribes/unsubscribes during dispatch.
        for handler in list(self._subscribers.get(type(event), [])):
            handler(event)

    def subscribe(
        self,
        event_type: type[_E],
        handler: Callable[[_E], None],
    ) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)
