import dataclasses
from typing import List


@dataclasses.dataclass(frozen=True)
class Command:
    """An imperative intent — one handler, may fail loudly."""


@dataclasses.dataclass(frozen=True)
class Event:
    """A domain event — a notification of something that happened."""


class AggregateRoot:
    """Mixin for aggregate roots.

    Roots record domain events during behavior; the UnitOfWork drains them from
    the roots its repositories touched (``repo.seen``) at commit time. ``_events``
    is a plain instance attribute (never an ORM column), created lazily.
    """

    def record(self, event: Event) -> None:
        try:
            self._events.append(event)
        except AttributeError:
            self._events = [event]

    def pull_events(self) -> List[Event]:
        events = getattr(self, "_events", [])
        if events:
            self._events = []
        return list(events)
