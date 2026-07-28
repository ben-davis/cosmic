import pytest

from fastapi_resources.domain import Command, Event
from fastapi_resources.message_bus import MessageBus
import dataclasses


@dataclasses.dataclass(frozen=True)
class DoTheThing(Command):
    value: int


@dataclasses.dataclass(frozen=True)
class ThingDone(Event):
    value: int


@dataclasses.dataclass(frozen=True)
class ThingFailed(Event):
    reason: str


class TestMessageBusCommands:
    def test_handles_registered_command(self):
        bus = MessageBus()
        results = []

        def handler(cmd: DoTheThing):
            results.append(cmd.value)
            return cmd.value

        bus.register(DoTheThing, handler)
        result = bus.handle(DoTheThing(value=42))

        assert results == [42]
        assert result == 42

    def test_raises_for_unregistered_command(self):
        bus = MessageBus()
        with pytest.raises(ValueError, match="No handler registered"):
            bus.handle(DoTheThing(value=1))

    def test_command_exception_propagates(self):
        bus = MessageBus()

        def bad_handler(cmd):
            raise RuntimeError("oops")

        bus.register(DoTheThing, bad_handler)
        with pytest.raises(RuntimeError, match="oops"):
            bus.handle(DoTheThing(value=1))

    def test_returns_command_handler_result(self):
        bus = MessageBus()
        bus.register(DoTheThing, lambda cmd: cmd.value * 2)
        assert bus.handle(DoTheThing(value=5)) == 10


class TestMessageBusEvents:
    def test_routes_event_to_all_handlers(self):
        bus = MessageBus()
        received = []

        bus.register(ThingDone, lambda e: received.append(("a", e.value)))
        bus.register(ThingDone, lambda e: received.append(("b", e.value)))
        bus.handle(ThingDone(value=7))

        assert ("a", 7) in received
        assert ("b", 7) in received

    def test_event_exception_does_not_propagate(self):
        bus = MessageBus()

        def bad_handler(e):
            raise RuntimeError("silent failure")

        bus.register(ThingDone, bad_handler)
        bus.handle(ThingDone(value=1))  # should not raise

    def test_no_handlers_for_event_is_ok(self):
        bus = MessageBus()
        bus.handle(ThingDone(value=1))  # should not raise


class TestMessageBusDomainEventDraining:
    def test_domain_events_from_uow_are_queued(self):
        """Events in uow.collect_new_events() are dispatched after the handler."""
        bus = MessageBus()
        received_events = []

        class FakeUoW:
            def __init__(self):
                self._events = []

            def collect_new_events(self):
                yield from self._events
                self._events.clear()

        uow = FakeUoW()

        def handler(cmd, uow):
            uow._events.append(ThingDone(value=cmd.value))
            return cmd.value

        bus.register(DoTheThing, handler)
        bus.register(ThingDone, lambda e: received_events.append(e.value))

        bus.handle(DoTheThing(value=99), uow=uow)

        assert received_events == [99]
