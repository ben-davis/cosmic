"""Tests for SqlAlchemyUnitOfWork event collection.

Events are drained from the aggregate roots the *repositories* touched
(`repo.seen`), never by scanning the session. That is what keeps collection
aggregate-correct: an object nobody loaded through a repository is not a root.
"""
import dataclasses

import pytest
from sqlalchemy.orm import sessionmaker

from fastapi_resources import Event, SqlAlchemyUnitOfWork
from tests.resources.sqlalchemy_base import Base
from tests.resources.sqlalchemy_models import Galaxy, GalaxyRepo, Star, engine


@dataclasses.dataclass(frozen=True)
class GalaxyDiscovered(Event):
    name: str


class GalaxyUnitOfWork(SqlAlchemyUnitOfWork):
    def __enter__(self):
        super().__enter__()
        self.galaxies = GalaxyRepo(self.session)
        return self


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
