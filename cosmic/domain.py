import dataclasses
from typing import Any, List, Optional

from cosmic.errors import NotEditable, PermissionDenied


@dataclasses.dataclass(frozen=True)
class Command:
    """An imperative intent — one handler, may fail loudly."""

    def data(self, *exclude: str) -> dict:
        """This command's fields as a dict, minus `exclude`.

        Handlers routinely splat a command into an aggregate factory, and
        writing the field list out again at the call site means every new field
        has to be added in two places — where "forgot one" looks like a field
        that silently never gets set.
        """
        return {
            f.name: getattr(self, f.name)
            for f in dataclasses.fields(self)
            if f.name not in exclude
        }


@dataclasses.dataclass(frozen=True)
class PartialUpdate(Command):
    """A command carrying only the fields the caller actually sent.

    `changes` is a dict rather than one optional attribute per field because an
    explicit ``None`` has to mean "clear this" while an absent key means "leave
    it alone". Optional attributes cannot express that difference: every unsent
    field arrives as ``None``, indistinguishable from a null, and no field could
    ever be cleared. Adapters build `changes` with pydantic's `exclude_unset`.
    """

    id: str
    changes: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class Event:
    """A domain event — a notification of something that happened."""


@dataclasses.dataclass(frozen=True)
class Context:
    """Who is asking. Passed to every repository; `scope()` reads it.

    A dataclass rather than a dict because the whole row-level authorization
    scheme keys off this: a mistyped ``ctx["principle"]`` yields `None`, which
    scopes to "rows owned by nobody" and returns 404 everywhere — a failure that
    reads as missing data rather than a bug. An attribute typo raises instead.

    Apps needing more than a principal subclass this and read the extra fields
    from their own `scope()` implementations.
    """

    principal: Optional[str] = None

    def require_principal(self) -> str:
        """The acting principal, for operations that mandate authentication."""
        if self.principal is None:
            raise PermissionDenied("this operation requires an authenticated principal")
        return self.principal


class AggregateRoot:
    """Mixin for aggregate roots.

    Roots record domain events during behavior; the UnitOfWork drains them from
    the roots its repositories touched (``repo.seen``) at commit time. ``_events``
    is a plain instance attribute (never an ORM column), created lazily.
    """

    def record(self, event: Event) -> None:
        try:
            self._events.append(event)
        except AttributeError:
            self._events = [event]

    def pull_events(self) -> List[Event]:
        events = getattr(self, "_events", [])
        if events:
            self._events = []
        return list(events)


def apply_changes(root: Any, changes: dict, allowed: frozenset) -> tuple[str, ...]:
    """Apply a `PartialUpdate`'s `changes` to `root`, rejecting unknown keys.

    Returns the names actually applied (sorted), so the caller can record a
    domain event naming them — or skip recording when nothing changed.

    The allowlist is not defensive politeness. Aggregate roots here *are* ORM
    models, so a blind `setattr` loop over client input writes columns: it would
    let a caller flip an internal status field or reassign the row to another
    owner. What a root does not name in `allowed` is not editable by input.
    """
    unknown = set(changes) - allowed
    if unknown:
        raise NotEditable(f"Cannot edit: {', '.join(sorted(unknown))}")
    for key, value in changes.items():
        setattr(root, key, value)
    return tuple(sorted(changes))
