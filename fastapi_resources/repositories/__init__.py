"""Aggregate repositories — one per aggregate root.

A repository loads a root (plus its declared child collections) as a unit,
applies row-level `scope` predicates to the *root*, and tracks every root it
touches in `.seen` so the UnitOfWork can drain domain events at commit.

There is no repository for child entities: children are reached through the root.
"""
import base64
import binascii
import json
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import inspect as sa_inspect, select, tuple_
from sqlalchemy.orm import Session, selectinload

from fastapi_resources.exceptions import NotFound

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class InvalidCursor(Exception):
    """A pagination cursor was malformed or not issued by this repository."""


def _encode_cursor(sort_value: Any, pk_value: Any) -> str:
    if isinstance(sort_value, (datetime, date)):
        sort_value = sort_value.isoformat()
    payload = json.dumps([sort_value, str(pk_value)], separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str, sort_python_type: type) -> tuple[Any, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        sort_value, pk_value = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError, binascii.Error) as exc:
        raise InvalidCursor(f"malformed cursor: {cursor!r}") from exc

    # Re-hydrate the sort value so it binds as the column's real type; SQLite
    # compares a TEXT-bound datetime differently from a DATETIME-bound one.
    try:
        if sort_python_type is datetime and isinstance(sort_value, str):
            sort_value = datetime.fromisoformat(sort_value)
        elif sort_python_type is date and isinstance(sort_value, str):
            sort_value = date.fromisoformat(sort_value)
    except ValueError as exc:
        raise InvalidCursor("cursor sort value does not match the column type") from exc

    return sort_value, pk_value


class AggregateRepository:
    """Repository for a single aggregate root.

    Subclasses (usually built by `build_aggregate_repo`) declare:
      ``Db``      — the root model
      ``load``    — child relationship attrs to eager-load with the root
      ``scope()`` — row-level predicates on the root
      ``sort``    — ``(column, descending)`` ordering for `list()`; PK by default
    """

    Db: type                      # the aggregate root model
    load: tuple = ()              # child relationship attribute names to eager-load
    sort: Optional[tuple] = None  # (column, descending)

    def __init__(self, session: Session, context: Optional[dict] = None, id_field: Optional[str] = None):
        self.session = session
        self.context = context or {}
        self._id_field_name = id_field
        # Keyed by id() because aggregate roots are MappedAsDataclass (unhashable).
        self.seen: dict = {}

    def scope(self) -> list:
        """Row-level predicates applied to the root. Override / inject via factory."""
        return []

    def _pk(self):
        if self._id_field_name:
            return getattr(self.Db, self._id_field_name)
        inspected = sa_inspect(self.Db)
        assert inspected is not None  # a mapped class always inspects to a Mapper
        pk_name = inspected.mapper.primary_key[0].key
        return getattr(self.Db, pk_name)

    def _sort(self) -> tuple:
        """The ``(column, descending)`` pair `list()` orders and paginates by."""
        if self.sort is not None:
            return self.sort
        return (self._pk(), False)

    def _base_select(self):
        """A scoped select of the root with its child collections eager-loaded.

        Shared by `get` and `list` so a root can never be visible to one and
        hidden from the other.
        """
        stmt = select(self.Db)
        for rel in self.load:
            stmt = stmt.options(selectinload(getattr(self.Db, rel)))
        for predicate in self.scope():
            stmt = stmt.where(predicate)
        return stmt

    def add(self, root) -> None:
        self.session.add(root)  # no flush — PKs are app-assigned (uuid7)
        self.seen[id(root)] = root

    def remove(self, root) -> None:
        self.session.delete(root)
        self.seen[id(root)] = root

    def get(self, pk) -> Any:
        stmt = self._base_select().where(self._pk() == pk)
        root = self.session.scalars(stmt).unique().one_or_none()
        if root is None:
            raise NotFound(f"{self.Db.__name__.lower()} not found")
        self.seen[id(root)] = root
        return root

    def list(
        self, *, limit: int = DEFAULT_PAGE_SIZE, cursor: Optional[str] = None
    ) -> tuple[list, Optional[str]]:
        """Return one scoped page of roots plus the cursor for the next page.

        Keyset ("seek") pagination over ``(sort_column, pk)`` — the PK breaks
        ties so the ordering is total and a page boundary can neither duplicate
        nor skip a row, which OFFSET cannot guarantee under concurrent writes.
        Returns ``(roots, next_cursor)``; `next_cursor` is None on the last page.
        """
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        sort_col, descending = self._sort()
        pk_col = self._pk()

        stmt = self._base_select()
        if descending:
            stmt = stmt.order_by(sort_col.desc(), pk_col.desc())
        else:
            stmt = stmt.order_by(sort_col.asc(), pk_col.asc())

        if cursor:
            last_sort, last_pk = _decode_cursor(cursor, self._sort_python_type(sort_col))
            keyset = tuple_(sort_col, pk_col)
            anchor = tuple_(last_sort, last_pk)
            stmt = stmt.where(keyset < anchor if descending else keyset > anchor)

        # Over-fetch by one to detect a further page without a second COUNT query.
        rows = list(self.session.scalars(stmt.limit(limit + 1)).unique().all())
        has_more = len(rows) > limit
        rows = rows[:limit]

        for root in rows:
            self.seen[id(root)] = root

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = _encode_cursor(
                getattr(last, sort_col.key), getattr(last, pk_col.key)
            )
        return rows, next_cursor

    @staticmethod
    def _sort_python_type(sort_col) -> type:
        try:
            return sort_col.type.python_type
        except NotImplementedError:  # custom types that declare no python_type
            return str


def build_aggregate_repo(
    Root: type, *, load=(), scope=None, sort=None, id_field=None
) -> type[AggregateRepository]:
    """Generate an AggregateRepository for a root.

    `load`     — child relationship attribute names loaded with the root.
    `scope`    — optional ``(context) -> [predicates]`` for row-level access.
    `sort`     — optional ``(column, descending)`` ordering used by `list()`.
    `id_field` — override the identity column (defaults to the PK).
    """
    attrs: dict = {"Db": Root, "load": tuple(load), "sort": sort}
    if scope is not None:
        attrs["scope"] = lambda self: scope(self.context)
    if id_field is not None:
        def __init__(self, session, context=None, id_field=id_field):
            AggregateRepository.__init__(self, session, context, id_field)

        attrs["__init__"] = __init__
    return type(f"{Root.__name__}Repository", (AggregateRepository,), attrs)
