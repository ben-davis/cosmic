from abc import ABC, abstractmethod
from typing import Any, Iterator, Optional

from cosmic.domain import Context, Event
from cosmic.repositories import AggregateRepository


class AbstractUnitOfWork(ABC):
    def __enter__(self) -> "AbstractUnitOfWork":
        return self

    def __exit__(self, *args):
        self.rollback()

    @abstractmethod
    def commit(self) -> None: ...

    @abstractmethod
    def rollback(self) -> None: ...

    @abstractmethod
    def collect_new_events(self) -> Iterator[Event]: ...


class SqlAlchemyUnitOfWork(AbstractUnitOfWork):
    """The transactional boundary, carrying one repository per aggregate.

    Subclasses declare their repositories once::

        class MyUnitOfWork(SqlAlchemyUnitOfWork):
            repos = {"events": EventRepo, "calendars": CalendarRepo}

    They are built in `__enter__` against this UoW's session and `context`, and
    exposed as attributes (``uow.events``). Declaring them rather than assigning
    them in an overridden `__enter__` is what lets `commit()` know exactly which
    repositories exist: it used to find them by scanning ``vars(self)`` for
    anything with a ``seen`` dict, so a repository attached any other way (a
    property, a lazily-built mapping) had its aggregates' domain events dropped
    without a word.
    """

    #: name → AggregateRepository subclass, built fresh on each `__enter__`.
    repos: dict[str, type[AggregateRepository]] = {}

    def __init__(self, session_factory, context: Optional[Context] = None):
        self.session_factory = session_factory
        self.context = context if context is not None else Context()
        self.collected_events: list[Event] = []
        self._repos: dict[str, AggregateRepository] = {}
        self._tracked: dict[int, Any] = {}

    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        self.session = self.session_factory()
        self.collected_events = []
        self._tracked = {}
        self._repos = {
            name: repo_cls(self.session, self.context)
            for name, repo_cls in self.repos.items()
        }
        for name, repo in self._repos.items():
            setattr(self, name, repo)
        return self

    def __exit__(self, *args):
        super().__exit__(*args)
        self.session.close()

    def require_principal(self) -> str:
        """The acting principal, for handlers that mandate authentication."""
        return self.context.require_principal()

    def track(self, root):
        """Record a root so its domain events are collected at commit.

        Repositories track what they load. This is for a root reached some other
        way — an object built by a handler and handed straight to the session,
        say. Untracked means its events are silently discarded.
        """
        self._tracked[id(root)] = root
        return root

    def _seen_roots(self) -> list:
        """Every root this UoW touched, de-duplicated by identity.

        Keyed by `id()` because aggregate roots are `MappedAsDataclass` and so
        unhashable — and because two repositories (a scoped one and its
        `unscoped()` view) deliberately share one `seen` dict.
        """
        roots: dict[int, Any] = dict(self._tracked)
        for repo in self._repos.values():
            roots.update(repo.seen)
        return list(roots.values())

    def _unregistered_repos(self) -> list[str]:
        """Repository-shaped attributes this UoW would not drain events from.

        Declaring `repos` replaced an older scheme where `commit()` found
        repositories by scanning `vars(self)`. Anything that scan missed lost its
        aggregates' domain events in silence, which is the worst possible failure
        for this particular mechanism — so the scan is kept, inverted, as a
        tripwire: a repository attached any way *other* than `repos` is an error
        rather than a quiet omission.

        An `unscoped()` view shares its parent's `seen` dict, so assigning one is
        fine and matched here by identity of that dict, not of the repository.
        """
        drained = {id(repo.seen) for repo in self._repos.values()}
        return [
            name
            for name, value in vars(self).items()
            if not name.startswith("_")
            and isinstance(getattr(value, "seen", None), dict)
            and id(value.seen) not in drained
        ]

    def commit(self) -> None:
        stray = self._unregistered_repos()
        if stray:
            raise RuntimeError(
                f"{type(self).__name__} has repositories this unit of work does not "
                f"drain domain events from: {sorted(stray)}. Declare them in "
                f"`repos = {{...}}` instead of assigning them."
            )

        # Re-add every touched root so children appended *after* the initial
        # add() (e.g. an account issuing a token) get cascaded into the session.
        # Skip roots pending deletion — re-adding would resurrect them.
        seen_roots = self._seen_roots()
        for root in seen_roots:
            if root not in self.session.deleted:
                self.session.add(root)

        # Flush so freshly-added (pending) roots get their PKs / FKs resolved.
        self.session.flush()

        # Aggregate-correct event collection: drain events from exactly the roots
        # the repositories loaded or added. Anything not reached through a
        # repository (or `track`ed explicitly) is not an aggregate root here and
        # contributes nothing.
        for root in seen_roots:
            if hasattr(root, "pull_events"):
                self.collected_events.extend(root.pull_events())

        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def collect_new_events(self) -> Iterator[Event]:
        events = list(self.collected_events)
        self.collected_events.clear()
        yield from events
