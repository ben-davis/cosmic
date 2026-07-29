"""`InMemoryUnitOfWork` must drain events the way `SqlAlchemyUnitOfWork` does.

Each case here is a divergence that had actually appeared in a hand-written copy
of this double, where it made handler tests assert against a more forgiving world
than production.
"""
import dataclasses

import pytest

from cosmic import Context, Event, PermissionDenied
from cosmic.testing import InMemoryUnitOfWork


@dataclasses.dataclass(frozen=True)
class Discovered(Event):
    name: str


class Thing:
    def __init__(self, id, name="", rank=0):
        self.id = id
        self.name = name
        self.rank = rank
        self._events: list = []

    def record(self, event):
        self._events.append(event)

    def pull_events(self):
        events, self._events = self._events, []
        return events


class ThingUnitOfWork(InMemoryUnitOfWork):
    repos = {"things": ("rank", False), "others": None}


def _thing(id, name="x"):
    thing = Thing(id, name)
    thing.record(Discovered(name=name))
    return thing


def test_events_come_only_from_touched_roots():
    """Holding a root is not touching it.

    Draining from everything in the repo hides a handler that mutates an
    aggregate it never loaded — which in production emits nothing at all.
    """
    untouched = _thing("1")
    uow = ThingUnitOfWork(things=[untouched])
    with uow:
        uow.commit()
    assert list(uow.collect_new_events()) == []


def test_events_come_from_a_root_the_repo_loaded():
    uow = ThingUnitOfWork(things=[_thing("1")])
    with uow:
        uow.things.get("1")
        uow.commit()
    assert [type(e) for e in uow.collect_new_events()] == [Discovered]


def test_events_come_from_a_root_the_repo_added():
    uow = ThingUnitOfWork()
    with uow:
        uow.things.add(_thing("1"))
        uow.commit()
    assert [type(e) for e in uow.collect_new_events()] == [Discovered]


def test_explicitly_tracked_root_is_drained():
    uow = ThingUnitOfWork()
    with uow:
        uow.track(_thing("1"))
        uow.commit()
    assert [type(e) for e in uow.collect_new_events()] == [Discovered]


def test_re_entering_clears_events_from_the_previous_pass():
    """Matches the real UoW's `__enter__`, which resets `collected_events`.

    Without it a second command dispatched on one UoW re-emits the first one's
    events, and a test that dispatches twice sees ghosts.
    """
    uow = ThingUnitOfWork()
    with uow:
        uow.things.add(_thing("1"))
        uow.commit()
    with uow:
        uow.commit()
    assert list(uow.collect_new_events()) == []


def test_events_are_drained_once():
    uow = ThingUnitOfWork()
    with uow:
        uow.things.add(_thing("1"))
        uow.commit()
    assert len(list(uow.collect_new_events())) == 1
    assert list(uow.collect_new_events()) == []


def test_require_principal_matches_the_real_uow():
    with pytest.raises(PermissionDenied):
        ThingUnitOfWork().require_principal()
    assert ThingUnitOfWork(Context(principal="alice")).require_principal() == "alice"


def test_unknown_repo_name_is_rejected():
    """A typo'd repo name would otherwise silently seed nothing."""
    with pytest.raises(TypeError, match="thigns"):
        ThingUnitOfWork(thigns=[_thing("1")])
