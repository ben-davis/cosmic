"""Tests for AggregateRepository: scoping, aggregate loading, keyset pagination."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from fastapi_resources import MAX_PAGE_SIZE, InvalidCursor, NotFound
from tests.resources.sqlalchemy_base import Base
from tests.resources.sqlalchemy_models import (
    Galaxy,
    GalaxyRepo,
    OwnedGalaxyRepo,
    Star,
    engine,
)

BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)


@pytest.fixture(autouse=True)
def _db():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def session():
    with Session(engine) as s:
        yield s
        s.rollback()


def _make(session, name, owner="alice", minutes=0):
    galaxy = Galaxy(
        name=name, owner=owner, discovered_at=BASE_TIME + timedelta(minutes=minutes)
    )
    session.add(galaxy)
    session.flush()
    return galaxy


class TestGet:
    def test_get_returns_root_with_children(self, session):
        galaxy = Galaxy(name="Pinwheel", stars=[Star(name="Alpha")])
        session.add(galaxy)
        session.flush()

        found = GalaxyRepo(session).get(galaxy.id)
        assert found.name == "Pinwheel"
        assert [s.name for s in found.stars] == ["Alpha"]

    def test_get_missing_raises_not_found(self, session):
        with pytest.raises(NotFound):
            GalaxyRepo(session).get(999999)

    def test_get_records_root_in_seen(self, session):
        galaxy = _make(session, "Seen")
        repo = GalaxyRepo(session)
        repo.get(galaxy.id)
        assert list(repo.seen.values()) == [galaxy]

    def test_out_of_scope_root_is_not_found(self, session):
        galaxy = _make(session, "Alice's", owner="alice")
        repo = OwnedGalaxyRepo(session, context={"owner": "bob"})
        with pytest.raises(NotFound):
            repo.get(galaxy.id)


class TestList:
    def test_list_applies_the_same_scope_as_get(self, session):
        _make(session, "Alice's", owner="alice")
        _make(session, "Bob's", owner="bob")

        rows, _ = OwnedGalaxyRepo(session, context={"owner": "alice"}).list()
        assert [g.name for g in rows] == ["Alice's"]

    def test_list_orders_by_declared_sort_descending(self, session):
        _make(session, "old", minutes=0)
        _make(session, "new", minutes=10)
        _make(session, "middle", minutes=5)

        rows, _ = OwnedGalaxyRepo(session, context={"owner": "alice"}).list()
        assert [g.name for g in rows] == ["new", "middle", "old"]

    def test_list_defaults_to_pk_order_when_no_sort_declared(self, session):
        first = _make(session, "a")
        second = _make(session, "b")

        rows, _ = GalaxyRepo(session).list()
        assert [g.id for g in rows] == [first.id, second.id]

    def test_no_next_cursor_on_last_page(self, session):
        _make(session, "only")
        rows, next_cursor = OwnedGalaxyRepo(session, context={"owner": "alice"}).list()
        assert len(rows) == 1
        assert next_cursor is None

    def test_pagination_walks_every_row_exactly_once(self, session):
        for i in range(7):
            _make(session, f"g{i}", minutes=i)

        repo = OwnedGalaxyRepo(session, context={"owner": "alice"})
        seen, cursor, pages = [], None, 0
        while True:
            rows, cursor = repo.list(limit=3, cursor=cursor)
            seen.extend(g.name for g in rows)
            pages += 1
            if cursor is None:
                break
            assert pages < 10, "pagination did not terminate"

        assert pages == 3
        assert seen == [f"g{i}" for i in reversed(range(7))]
        assert len(set(seen)) == 7  # no duplicates across page boundaries

    def test_pagination_is_stable_across_ties(self, session):
        # Identical sort values for every row: the PK tiebreaker must still give
        # a total order, or a page boundary can repeat or skip a row.
        for i in range(6):
            _make(session, f"tie{i}", minutes=0)

        repo = OwnedGalaxyRepo(session, context={"owner": "alice"})
        seen, cursor = [], None
        while True:
            rows, cursor = repo.list(limit=2, cursor=cursor)
            seen.extend(g.name for g in rows)
            if cursor is None:
                break

        assert sorted(seen) == sorted(f"tie{i}" for i in range(6))
        assert len(set(seen)) == 6

    def test_pagination_respects_scope_on_later_pages(self, session):
        for i in range(4):
            _make(session, f"alice{i}", owner="alice", minutes=i)
            _make(session, f"bob{i}", owner="bob", minutes=i)

        repo = OwnedGalaxyRepo(session, context={"owner": "alice"})
        seen, cursor = [], None
        while True:
            rows, cursor = repo.list(limit=2, cursor=cursor)
            seen.extend(g.name for g in rows)
            if cursor is None:
                break

        assert all(name.startswith("alice") for name in seen)
        assert len(seen) == 4

    def test_limit_is_clamped_to_max_page_size(self, session):
        _make(session, "one")
        rows, _ = OwnedGalaxyRepo(session, context={"owner": "alice"}).list(
            limit=MAX_PAGE_SIZE * 10
        )
        assert len(rows) == 1  # clamped, not rejected

    def test_malformed_cursor_raises_invalid_cursor(self, session):
        repo = OwnedGalaxyRepo(session, context={"owner": "alice"})
        with pytest.raises(InvalidCursor):
            repo.list(cursor="not-a-real-cursor!!")

    def test_listed_roots_are_recorded_in_seen(self, session):
        _make(session, "tracked")
        repo = OwnedGalaxyRepo(session, context={"owner": "alice"})
        rows, _ = repo.list()
        assert len(repo.seen) == len(rows) == 1


class TestAddRemove:
    def test_add_makes_root_findable_and_seen(self, session):
        galaxy = Galaxy(name="Findable")
        repo = GalaxyRepo(session)
        repo.add(galaxy)
        session.flush()

        assert repo.get(galaxy.id).name == "Findable"
        assert galaxy in repo.seen.values()

    def test_remove_deletes_root(self, session):
        galaxy = _make(session, "Doomed")
        repo = GalaxyRepo(session)
        repo.remove(galaxy)
        session.flush()

        with pytest.raises(NotFound):
            repo.get(galaxy.id)
