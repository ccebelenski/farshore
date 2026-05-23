"""`EventBusProtocol` — the abstract contract Game emits events through.

The concrete in-process pub/sub implementation (`EventBus`) lives in
`empire.events.bus`. Defining only the Protocol here keeps `core` free of
pub/sub plumbing while letting `Game.event_bus` be typed.

`NullEventBus` is a no-op default suitable for headless engine runs and
tests that don't care about events.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar, runtime_checkable

_E = TypeVar("_E")


@runtime_checkable
class EventBusProtocol(Protocol):
    """Anything `Game`/`TurnManager` talks to as an event sink.

    `subscribe` is generic over event type so callers can register handlers
    typed precisely (e.g., `Callable[[TurnAdvancedEvent], None]`) without
    needing a `Callable[[object], None]` adapter.
    """

    def publish(self, event: object) -> None: ...

    def subscribe(
        self,
        event_type: type[_E],
        handler: Callable[[_E], None],
    ) -> None: ...


class NullEventBus:
    """A no-op `EventBusProtocol` implementation.

    Used as the default when no real bus is provided. `publish` discards
    events; `subscribe` accepts handlers but never invokes them.
    """

    def publish(self, event: object) -> None:
        del event

    def subscribe(
        self,
        event_type: type[_E],
        handler: Callable[[_E], None],
    ) -> None:
        del event_type, handler
