"""Ports — the abstractions consumers depend on (dependency inversion).

These Protocols describe the surface the *machinery* actually consumes, so an
application can drive its handlers with in-memory fakes and still be sure the
real objects fit. Keep them honest: if `MessageBus` or a handler calls a method,
it belongs here; if nothing calls it, it does not.
"""
from typing import Any, Iterator, Optional, Protocol, runtime_checkable

from cosmic.domain import Context, Event


@runtime_checkable
class Repository(Protocol):
    """The aggregate-repository surface. Mirrors ``AggregateRepository``."""

    seen: dict

    def add(self, root: Any) -> None: ...
    def remove(self, root: Any) -> None: ...
    def get(self, pk: Any) -> Any: ...
    def find_one(self, **equals: Any) -> Optional[Any]: ...
    def track(self, root: Any) -> Any: ...
    def unscoped(self) -> "Repository": ...
    def list(
        self, *, limit: int = ..., cursor: Optional[str] = ...
    ) -> tuple[list, Optional[str]]: ...


@runtime_checkable
class UnitOfWork(Protocol):
    """The transactional boundary, as ``MessageBus.handle`` consumes it.

    ``collect_new_events`` is part of the port because the bus calls it after
    every command (see ``MessageBus._handle_command``) — a UoW that omits it
    silently drops every domain event.

    Repositories are deliberately *not* declared here: they are named per
    application (`uow.events`, `uow.calendars`), and a Protocol cannot express
    "whichever attributes the app declared in `repos`". What the machinery calls
    is what is listed; an app wanting its repo names checked declares them on its
    own UoW subclass, which is where they are already written down once.
    """

    context: Context

    def __enter__(self) -> "UnitOfWork": ...
    def __exit__(self, *args: Any) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def collect_new_events(self) -> Iterator[Event]: ...
    def require_principal(self) -> str: ...
    def track(self, root: Any) -> Any: ...
