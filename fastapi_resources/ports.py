"""Ports — the abstractions consumers depend on (dependency inversion).

These Protocols describe the surface the *machinery* actually consumes, so an
application can drive its handlers with in-memory fakes and still be sure the
real objects fit. Keep them honest: if `MessageBus` or a handler calls a method,
it belongs here; if nothing calls it, it does not.
"""
from typing import Any, Iterator, Optional, Protocol, runtime_checkable

from fastapi_resources.domain import Event


@runtime_checkable
class Repository(Protocol):
    """The aggregate-repository surface. Mirrors ``AggregateRepository``."""

    def add(self, root: Any) -> None: ...
    def remove(self, root: Any) -> None: ...
    def get(self, pk: Any) -> Any: ...
    def list(
        self, *, limit: int = ..., cursor: Optional[str] = ...
    ) -> tuple[list, Optional[str]]: ...


@runtime_checkable
class UnitOfWork(Protocol):
    """The transactional boundary, as ``MessageBus.handle`` consumes it.

    ``collect_new_events`` is part of the port because the bus calls it after
    every command (see ``MessageBus._handle_command``) — a UoW that omits it
    silently drops every domain event.
    """

    def __enter__(self) -> "UnitOfWork": ...
    def __exit__(self, *args: Any) -> None: ...
    def commit(self) -> None: ...
    def collect_new_events(self) -> Iterator[Event]: ...
