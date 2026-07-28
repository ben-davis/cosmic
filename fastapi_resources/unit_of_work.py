from abc import ABC, abstractmethod
from typing import Iterator

from fastapi_resources.domain import Event


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

    def repo_for(self, db_class):
        """Find the repository on this UoW whose Db class matches db_class."""
        for attr_name in vars(self):
            repo = getattr(self, attr_name)
            if hasattr(repo, "Db") and repo.Db is db_class:
                return repo
        raise ValueError(
            f"No repository found on {type(self).__name__} for {db_class.__name__}. "
            f"Ensure your UoW assigns a repo with Db={db_class.__name__} in __enter__."
        )


class SqlAlchemyUnitOfWork(AbstractUnitOfWork):
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.collected_events: list[Event] = []

    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        self.session = self.session_factory()
        self.collected_events = []
        return self

    def __exit__(self, *args):
        super().__exit__(*args)
        self.session.close()

    def _seen_repos(self):
        """Repositories on this UoW that track the aggregate roots they touched."""
        for attr_name in vars(self):
            repo = getattr(self, attr_name)
            if isinstance(getattr(repo, "seen", None), dict):
                yield repo

    def commit(self) -> None:
        # Re-add every touched root so children appended *after* the initial
        # add() (e.g. an account issuing a token) get cascaded into the session.
        seen_roots = [root for repo in self._seen_repos() for root in repo.seen.values()]
        for root in seen_roots:
            self.session.add(root)

        # Flush so freshly-added (pending) roots get their PKs / FKs resolved.
        self.session.flush()

        # Aggregate-correct event collection: drain events from the roots the
        # repositories loaded/added (repo.seen). Falls back to scanning the
        # identity_map for a `domain_events` attribute when no repo tracks `seen`.
        if seen_roots:
            for root in seen_roots:
                if hasattr(root, "pull_events"):
                    self.collected_events.extend(root.pull_events())
        else:
            for obj in list(self.session.identity_map.values()):
                events = list(getattr(obj, "domain_events", []))
                self.collected_events.extend(events)
                if hasattr(obj, "domain_events"):
                    obj.domain_events.clear()

        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()

    def collect_new_events(self) -> Iterator[Event]:
        events = list(self.collected_events)
        self.collected_events.clear()
        yield from events
