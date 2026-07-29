"""Tests for SqlAlchemyUnitOfWork event collection.

Events are drained from the aggregate roots the *repositories* touched
(`repo.seen`), never by scanning the session. That is what keeps collection
aggregate-correct: an object nobody loaded through a repository is not a root.
"""
import dataclasses

import pytest
from sqlalchemy.orm import sessionmaker

from cosmic import Event, SqlAlchemyUnitOfWork
from tests.resources.sqlalchemy_base import Base
from tests.resources.sqlalchemy_models import Galaxy, GalaxyRepo, Star, engine


@dataclasses.dataclass(frozen=True)
class GalaxyDiscovered(Event):
    name: str


class GalaxyUnitOfWork(SqlAlchemyUnitOfWork):
    repos = {"galaxies": GalaxyRepo}


@pytest.fixture(autouse=True)
def _db():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def uow():
    return GalaxyUnitOfWork(sessionmaker(engine))


def test_commit_collects_events_from_a_pending_root(uow):
    """A freshly-added, never-flushed root must still yield its events.

    Regression guard: pending objects are in session.new, not the identity map,
    so commit() has to flush before draining or the events are lost.
    """
    with uow:
        galaxy = Galaxy(name="Whirlpool")
        galaxy.record(GalaxyDiscovered(name="Whirlpool"))
        uow.galaxies.add(galaxy)  # no flush
        uow.commit()

    events = list(uow.collect_new_events())
    assert [type(e) for e in events] == [GalaxyDiscovered]
    assert events[0].name == "Whirlpool"


def test_events_are_drained_once(uow):
    with uow:
        galaxy = Galaxy(name="Sombrero")
        galaxy.record(GalaxyDiscovered(name="Sombrero"))
        uow.galaxies.add(galaxy)
        uow.commit()

    assert len(list(uow.collect_new_events())) == 1
    assert list(uow.collect_new_events()) == []  # drained, not replayed


def test_children_added_after_the_root_are_cascaded(uow):
    """commit() re-adds seen roots so children appended later still persist."""
    with uow:
        galaxy = Galaxy(name="Andromeda")
        uow.galaxies.add(galaxy)
        galaxy.stars.append(Star(name="Mirach"))  # appended after add()
        uow.commit()
        galaxy_id = galaxy.id

    with GalaxyUnitOfWork(sessionmaker(engine)) as fresh:
        assert [s.name for s in fresh.galaxies.get(galaxy_id).stars] == ["Mirach"]


def test_removed_root_is_not_resurrected_by_commit(uow):
    with uow:
        galaxy = Galaxy(name="Doomed")
        uow.galaxies.add(galaxy)
        uow.commit()
        galaxy_id = galaxy.id

    with GalaxyUnitOfWork(sessionmaker(engine)) as second:
        second.galaxies.remove(second.galaxies.get(galaxy_id))
        second.commit()

    with GalaxyUnitOfWork(sessionmaker(engine)) as third:
        rows, _ = third.galaxies.list()
        assert rows == []


def test_objects_never_seen_by_a_repo_contribute_no_events(uow):
    """Only roots reached through a repository are drained."""
    with uow:
        stray = Galaxy(name="Stray")
        stray.record(GalaxyDiscovered(name="Stray"))
        uow.session.add(stray)  # bypasses the repository
        uow.commit()

    assert list(uow.collect_new_events()) == []


def test_explicitly_tracked_root_is_drained(uow):
    """`track` is the supported way to reach a root outside a repository."""
    with uow:
        stray = Galaxy(name="Tracked")
        stray.record(GalaxyDiscovered(name="Tracked"))
        uow.session.add(stray)
        uow.track(stray)
        uow.commit()

    assert [type(e) for e in uow.collect_new_events()] == [GalaxyDiscovered]


def test_a_repo_attached_outside_repos_is_an_error_not_a_silent_drop(uow):
    """The failure mode this whole mechanism exists to avoid.

    A repository the UoW does not know about drains nothing, and a lost domain
    event looks exactly like a feature that was never wired up. Refuse to commit
    rather than let that pass.
    """
    with uow:
        uow.extra_galaxies = GalaxyRepo(uow.session)  # not declared in `repos`
        with pytest.raises(RuntimeError, match="extra_galaxies"):
            uow.commit()


def test_an_unscoped_view_may_be_assigned(uow):
    """It shares its parent's `seen`, so nothing is lost by holding onto one."""
    with uow:
        uow.any_galaxy = uow.galaxies.unscoped()
        galaxy = Galaxy(name="Wide")
        galaxy.record(GalaxyDiscovered(name="Wide"))
        uow.any_galaxy.add(galaxy)
        uow.commit()

    assert [type(e) for e in uow.collect_new_events()] == [GalaxyDiscovered]
