"""The repository contract, as a suite an app runs against both implementations.

A fake is only worth having if it behaves like the thing it replaces. Asserting
that by eye does not survive a change to either side, so the assertions live here
and get run twice — once against `FakeRepo`, once against the real
`AggregateRepository` on a real database.

Use it by mixing into a `Test*` class that supplies two fixtures:

    class TestEventRepositoryContract(RepositoryContractTests):
        @pytest.fixture(params=["fake", "real"])
        def repo(self, request, session):
            if request.param == "fake":
                return FakeRepo(sort=("created_at", True))
            return EventRepo(session, context=Context(principal="alice"))

        @pytest.fixture
        def make_root(self):
            return lambda **kw: Event.create(owner_id="alice", **kw)

`make_root(**kwargs)` must return a *new, unsaved* root and must accept the
attribute named by `title_field`, which the suite uses to tell roots apart.
"""
import pytest

from cosmic.errors import InvalidCursor, NotFound


class RepositoryContractTests:
    """Behaviours every repository implementation must share.

    Not covered here, deliberately: `scope()`. Scope is SQLAlchemy predicates, so
    an in-memory fake cannot evaluate it — the fake models the *result* of
    scoping by holding only visible rows. Scoping is asserted at the integration
    layer instead, against the database, where it actually runs.
    """

    #: An attribute `make_root` accepts, used to give roots distinct identities.
    title_field = "title"

    @pytest.fixture
    def flush(self):
        """Push pending writes far enough that the row exists.

        A no-op for in-memory doubles. The real implementation overrides it with
        ``session.flush``: SQLAlchemy refuses to `delete()` an instance that was
        never persisted, so "add then remove" needs a flush in between that the
        fake has no equivalent for.
        """
        return lambda: None

    def _add(self, repo, make_root, n) -> set:
        """Add `n` distinguishable roots; return their ids."""
        ids = set()
        for i in range(n):
            root = make_root(**{self.title_field: f"root-{i}"})
            repo.add(root)
            ids.add(root.id)
        return ids

    # --- identity ---

    def test_add_then_get_returns_the_root(self, repo, make_root):
        root = make_root()
        repo.add(root)
        assert repo.get(root.id).id == root.id

    def test_get_unknown_raises_not_found(self, repo):
        with pytest.raises(NotFound):
            repo.get("does-not-exist")

    def test_removed_root_is_no_longer_visible(self, repo, make_root, flush):
        root = make_root()
        repo.add(root)
        flush()
        repo.remove(root)
        with pytest.raises(NotFound):
            repo.get(root.id)

    # --- tracking: this is how domain events get out ---

    def test_add_tracks_the_root(self, repo, make_root):
        root = make_root()
        repo.add(root)
        assert root in repo.seen.values()

    def test_get_tracks_the_root(self, repo, make_root):
        root = make_root()
        repo.add(root)
        repo.seen.clear()
        repo.get(root.id)
        assert len(repo.seen) == 1

    def test_list_tracks_every_root_it_returns(self, repo, make_root):
        self._add(repo, make_root, 3)
        repo.seen.clear()
        rows, _ = repo.list()
        assert len(rows) == 3
        assert len(repo.seen) == 3

    def test_track_is_idempotent(self, repo, make_root):
        root = make_root()
        repo.track(root)
        repo.track(root)
        assert len(repo.seen) == 1

    # --- find_one ---

    def test_find_one_matches_on_every_kwarg(self, repo, make_root):
        ids = self._add(repo, make_root, 2)
        found = repo.find_one(**{self.title_field: "root-1"})
        assert found is not None
        assert found.id in ids
        assert getattr(found, self.title_field) == "root-1"

    def test_find_one_returns_none_when_nothing_matches(self, repo, make_root):
        self._add(repo, make_root, 1)
        assert repo.find_one(**{self.title_field: "absent"}) is None

    def test_find_one_tracks_what_it_returns(self, repo, make_root):
        self._add(repo, make_root, 1)
        repo.seen.clear()
        repo.find_one(**{self.title_field: "root-0"})
        assert len(repo.seen) == 1

    # --- unscoped ---

    def test_unscoped_shares_tracking_with_its_parent(self, repo, make_root):
        """Or roots reached through the escape hatch lose their domain events."""
        root = make_root()
        repo.add(root)
        repo.seen.clear()
        repo.unscoped().get(root.id)
        assert len(repo.seen) == 1

    # --- pagination ---

    def test_list_respects_limit_and_reports_more(self, repo, make_root):
        self._add(repo, make_root, 3)
        rows, cursor = repo.list(limit=2)
        assert len(rows) == 2
        assert cursor is not None

    def test_last_page_has_no_cursor(self, repo, make_root):
        self._add(repo, make_root, 2)
        _, cursor = repo.list(limit=5)
        assert cursor is None

    def test_walking_the_cursor_yields_every_root_exactly_once(self, repo, make_root):
        expected = self._add(repo, make_root, 7)

        seen_ids: list = []
        cursor = None
        for _ in range(10):  # bounded: a cursor that never terminates is a bug
            rows, cursor = repo.list(limit=3, cursor=cursor)
            seen_ids.extend(root.id for root in rows)
            if cursor is None:
                break

        assert cursor is None, "pagination did not terminate"
        assert len(seen_ids) == len(set(seen_ids)), "a root appeared on two pages"
        assert set(seen_ids) == expected

    def test_malformed_cursor_is_rejected(self, repo):
        with pytest.raises(InvalidCursor):
            repo.list(cursor="not-a-cursor!!")
