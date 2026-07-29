"""In-memory doubles for DB-free service-layer tests.

These belong to the library rather than to each app because a test double whose
semantics drift from the real thing is worse than no double: it makes handler
tests *confidently* wrong. Three divergences that had already appeared in a
hand-written copy of this file, all of which this one closes:

* the fake drained domain events from every object it held, rather than only the
  roots actually touched — so a handler that mutated an aggregate it never loaded
  through a repository looked correct in tests and dropped its events in
  production;
* the fake's `list()` ignored the cursor and always reported "no more pages", so
  no handler test could exercise pagination at all;
* the fake never cleared collected events on re-entry, so a second command
  dispatched on one UoW saw the first one's events.

`RepositoryContractTests` in `contract.py` is what keeps them from drifting again.
"""
from typing import Any, Optional

from cosmic.domain import Context
from cosmic.errors import NotFound
from cosmic.repositories import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, _decode_cursor, _encode_cursor


class FakeRepo:
    """An `AggregateRepository` over a list.

    **It does not evaluate `scope()`.** Predicates are SQLAlchemy expressions and
    there is no way to run them against plain objects. What the fake models is
    the *result* of scoping: put in it exactly what the principal can see. Which
    is why row-level authorization is asserted against the real database, in
    integration tests, and never here.
    """

    def __init__(self, items=None, *, sort: Optional[tuple[str, bool]] = None):
        self.items: list = list(items or [])
        #: (attribute name, descending) — mirrors the real repo's (column, descending).
        self.sort = sort
        self.seen: dict = {}

    # --- scope ---

    def unscoped(self) -> "FakeRepo":
        """Itself: a fake holds only visible rows, so there is nothing to lift.

        Sharing the instance also shares `seen`, which is what the real
        `unscoped()` view does.
        """
        return self

    # --- writes ---

    def track(self, root):
        self.seen[id(root)] = root
        return root

    def add(self, root) -> None:
        self.items.append(root)
        self.track(root)

    def remove(self, root) -> None:
        self.items.remove(root)
        self.track(root)

    # --- reads ---

    def get(self, pk) -> Any:
        try:
            root = next(o for o in self.items if o.id == pk)
        except StopIteration:
            raise NotFound(f"not found: {pk}")
        return self.track(root)

    def find_one(self, **equals) -> Optional[Any]:
        root = next(
            (
                o
                for o in self.items
                if all(getattr(o, name, None) == value for name, value in equals.items())
            ),
            None,
        )
        return self.track(root) if root is not None else None

    def _sort_key(self, root) -> tuple:
        name = self.sort[0] if self.sort else "id"
        return (getattr(root, name), str(root.id))

    def list(
        self, *, limit: int = DEFAULT_PAGE_SIZE, cursor: Optional[str] = None
    ) -> tuple[list, Optional[str]]:
        """Keyset pagination over ``(sort attr, id)``, same as the real repo.

        Shares the real cursor codec, so a cursor is opaque here too and a test
        cannot accidentally pass one the real repository would reject.
        """
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        descending = bool(self.sort and self.sort[1])
        rows = sorted(self.items, key=self._sort_key, reverse=descending)

        if cursor:
            # `str` as the sort type: nothing is re-hydrated, so the comparison
            # happens in the same encoded domain `_comparable` produces.
            anchor = _decode_cursor(cursor, str)
            if descending:
                rows = [r for r in rows if _comparable(self._sort_key(r)) < anchor]
            else:
                rows = [r for r in rows if _comparable(self._sort_key(r)) > anchor]

        has_more = len(rows) > limit
        rows = rows[:limit]
        for root in rows:
            self.track(root)

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            name = self.sort[0] if self.sort else "id"
            next_cursor = _encode_cursor(getattr(last, name), last.id)
        return rows, next_cursor


def _comparable(key: tuple) -> tuple:
    """Render a sort key the way the cursor round-trip does.

    `_encode_cursor` turns datetimes into ISO strings and `_decode_cursor` is
    told (by the real repo) what type to rebuild. The fake has no column to ask,
    so it compares in the encoded domain — which is order-preserving for ISO
    timestamps and for the string ids used as tiebreakers.
    """
    value, pk = key
    return (value.isoformat() if hasattr(value, "isoformat") else value, pk)


class InMemoryUnitOfWork:
    """A `SqlAlchemyUnitOfWork` without the SQL.

    Declares `repos` the same way, drains events from the same place (roots a
    repository actually touched), and exposes the same `context` /
    `require_principal` / `track` surface, so handlers cannot tell the two apart.
    """

    #: name → optional (sort attribute, descending), mirroring the real repos.
    repos: dict[str, Optional[tuple[str, bool]]] = {}

    def __init__(self, context: Optional[Context] = None, **items):
        self.context = context if context is not None else Context()
        unknown = set(items) - set(self.repos)
        if unknown:
            raise TypeError(f"no such repo(s): {sorted(unknown)}")

        self._repos = {
            name: FakeRepo(items.get(name), sort=sort)
            for name, sort in self.repos.items()
        }
        for name, repo in self._repos.items():
            setattr(self, name, repo)

        self._tracked: dict[int, Any] = {}
        self.collected_events: list = []
        self.committed = False

    def __enter__(self) -> "InMemoryUnitOfWork":
        self.collected_events = []
        self._tracked = {}
        return self

    def __exit__(self, *args) -> None:
        return None

    def require_principal(self) -> str:
        return self.context.require_principal()

    def track(self, root):
        self._tracked[id(root)] = root
        return root

    def _seen_roots(self) -> list:
        roots: dict[int, Any] = dict(self._tracked)
        for repo in self._repos.values():
            roots.update(repo.seen)
        return list(roots.values())

    def commit(self) -> None:
        """Drain events from touched roots only — exactly as the real UoW does.

        A handler that forgets to `record` an event, or mutates a root it never
        loaded through a repository, has to fail here too or these tests are
        asserting against a more forgiving world than production.
        """
        self.committed = True
        for root in self._seen_roots():
            if hasattr(root, "pull_events"):
                self.collected_events.extend(root.pull_events())

    def rollback(self) -> None:
        return None

    def collect_new_events(self):
        events = list(self.collected_events)
        self.collected_events.clear()
        yield from events
