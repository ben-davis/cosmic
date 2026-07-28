import dataclasses
import types as builtin_types
from dataclasses import field, make_dataclass
from typing import Any

from sqlalchemy import Integer, String, inspect as sa_inspect
from sqlalchemy.orm import MANYTOONE


@dataclasses.dataclass(frozen=True)
class Command:
    pass


@dataclasses.dataclass(frozen=True)
class Event:
    pass


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

    def pull_events(self) -> list[Event]:
        events = getattr(self, "_events", [])
        if events:
            self._events = []
        return list(events)


def _pk_info(Db):
    """Returns (pk_attr_name, python_type, user_assigned)."""
    inspected = sa_inspect(Db)
    pk_col = inspected.mapper.primary_key[0]
    pk_name = pk_col.key

    try:
        from sqlalchemy.types import Uuid
        if isinstance(pk_col.type, Uuid):
            import uuid
            py_type = uuid.UUID
        elif isinstance(pk_col.type, Integer):
            py_type = int
        elif isinstance(pk_col.type, String):
            py_type = str
        else:
            py_type = Any
    except Exception:
        py_type = Any

    try:
        dc_fields = {f.name: f for f in dataclasses.fields(Db)}
        pk_field = dc_fields.get(pk_name)
        user_assigned = pk_field.init if pk_field else True
    except TypeError:
        user_assigned = True

    return pk_name, py_type, user_assigned


def build_commands(Db, Create=None, Update=None):
    """Generate command and event dataclasses for a SQLAlchemy model.

    Returns a namespace with .Create, .Update, .Delete (commands) and
    .Created, .Updated, .Deleted (events).
    """
    db_name = Db.__name__
    inspected = sa_inspect(Db)
    pk_name, pk_type, user_assigned_pk = _pk_info(Db)
    db_rels = dict(inspected.relationships)

    db_init_fields = set()
    try:
        db_init_fields = {f.name for f in dataclasses.fields(Db) if f.init}
    except TypeError:
        pass

    def _parse_schema(schema, all_optional=False):
        """Return (required_fields, optional_fields, rel_fields) for a Pydantic schema."""
        if schema is None:
            return [], [], []

        rel_names = set(getattr(schema, "__relationships__", []))
        required, optional, rel_fields = [], [], []

        for fname, finfo in schema.model_fields.items():
            if fname in rel_names:
                continue
            annotation = finfo.annotation
            if not all_optional and finfo.is_required():
                required.append((fname, annotation))
            else:
                default = finfo.default if not finfo.is_required() else None
                optional.append((fname, annotation, field(default=default)))

        for rel_name in rel_names:
            rel = db_rels.get(rel_name)
            if rel is None:
                continue
            if rel.direction == MANYTOONE:
                rel_fields.append(
                    (f"{rel_name}_id", int | None, field(default=None))
                )
            else:
                singular = rel_name[:-1] if rel_name.endswith("s") else rel_name
                rel_fields.append(
                    (f"{singular}_ids", list[int], field(default_factory=list))
                )

        return required, optional, rel_fields

    # --- Create command ---
    c_req, c_opt, c_rel = _parse_schema(Create)
    create_fields = []
    if user_assigned_pk:
        create_fields.append((pk_name, pk_type))
    create_fields.extend(c_req)
    create_fields.extend(c_opt)
    create_fields.extend(c_rel)

    CreateCmd = make_dataclass(
        f"Create{db_name}", create_fields, bases=(Command,), frozen=True
    )

    # --- Update command ---
    u_req, u_opt, u_rel = _parse_schema(Update, all_optional=True)
    update_fields = [(pk_name, pk_type)]  # id always required
    update_fields.extend(u_req)
    update_fields.extend(u_opt)
    # Relationship updates: nullable list for to-many
    for name, ann, f_spec in u_rel:
        if ann == list[int]:
            update_fields.append((name, list[int] | None, field(default=None)))
        else:
            update_fields.append((name, ann, f_spec))

    UpdateCmd = make_dataclass(
        f"Update{db_name}", update_fields, bases=(Command,), frozen=True
    )

    # --- Delete command ---
    DeleteCmd = make_dataclass(
        f"Delete{db_name}", [(pk_name, pk_type)], bases=(Command,), frozen=True
    )

    # --- Events (each carries just the pk) ---
    CreatedEvent = make_dataclass(
        f"{db_name}Created", [(pk_name, pk_type)], bases=(Event,), frozen=True
    )
    UpdatedEvent = make_dataclass(
        f"{db_name}Updated", [(pk_name, pk_type)], bases=(Event,), frozen=True
    )
    DeletedEvent = make_dataclass(
        f"{db_name}Deleted", [(pk_name, pk_type)], bases=(Event,), frozen=True
    )

    return builtin_types.SimpleNamespace(
        Create=CreateCmd,
        Update=UpdateCmd,
        Delete=DeleteCmd,
        Created=CreatedEvent,
        Updated=UpdatedEvent,
        Deleted=DeletedEvent,
        _pk_name=pk_name,
        _pk_type=pk_type,
        _user_assigned_pk=user_assigned_pk,
        _db_init_fields=db_init_fields,
    )
