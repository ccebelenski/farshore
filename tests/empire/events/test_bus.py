"""Phase-4 canary tests for `EventBus`."""

from __future__ import annotations

from dataclasses import dataclass

from empire.core.event_bus import EventBusProtocol, NullEventBus
from empire.events.bus import EventBus


@dataclass(frozen=True, slots=True)
class _PingEvent:
    n: int


@dataclass(frozen=True, slots=True)
class _PongEvent:
    msg: str


# --- Protocol satisfaction --------------------------------------------------


def test_concrete_bus_satisfies_protocol() -> None:
    assert isinstance(EventBus(), EventBusProtocol)


def test_null_bus_satisfies_protocol() -> None:
    assert isinstance(NullEventBus(), EventBusProtocol)


# --- Behavior ---------------------------------------------------------------


def test_publish_with_no_subscribers_is_silent() -> None:
    bus = EventBus()
    bus.publish(_PingEvent(n=1))  # should not raise


def test_subscriber_receives_matching_events() -> None:
    bus = EventBus()
    received: list[_PingEvent] = []
    bus.subscribe(_PingEvent, received.append)
    bus.publish(_PingEvent(n=1))
    bus.publish(_PingEvent(n=2))
    assert [e.n for e in received] == [1, 2]


def test_subscribers_only_receive_their_event_type() -> None:
    bus = EventBus()
    pings: list[_PingEvent] = []
    pongs: list[_PongEvent] = []
    bus.subscribe(_PingEvent, pings.append)
    bus.subscribe(_PongEvent, pongs.append)
    bus.publish(_PingEvent(n=7))
    bus.publish(_PongEvent(msg="hi"))
    assert len(pings) == 1 and pings[0].n == 7
    assert len(pongs) == 1 and pongs[0].msg == "hi"


def test_multiple_subscribers_all_receive() -> None:
    bus = EventBus()
    a: list[_PingEvent] = []
    b: list[_PingEvent] = []
    bus.subscribe(_PingEvent, a.append)
    bus.subscribe(_PingEvent, b.append)
    bus.publish(_PingEvent(n=3))
    assert len(a) == 1
    assert len(b) == 1


def test_subscribe_during_publish_does_not_fire_new_handler_this_cycle() -> None:
    """A handler that subscribes another handler mid-dispatch must not see
    the new subscriber fire for the in-flight event. EventBus snapshots its
    handler list before dispatch to make this predictable.
    """
    bus = EventBus()
    second_called: list[_PingEvent] = []

    def first(_e: _PingEvent) -> None:
        bus.subscribe(_PingEvent, second_called.append)

    bus.subscribe(_PingEvent, first)
    bus.publish(_PingEvent(n=1))
    assert second_called == []  # not fired for the current event
    bus.publish(_PingEvent(n=2))
    assert len(second_called) == 1  # fires for subsequent events


def test_null_bus_swallows_events_silently() -> None:
    bus = NullEventBus()
    called: list[_PingEvent] = []
    bus.subscribe(_PingEvent, called.append)
    bus.publish(_PingEvent(n=1))
    assert called == []  # subscribers never fire on a NullEventBus
