"""The *request* half of the JSON:API contract.

`serialization.py` defines the documents this library emits. This module defines
the ones it accepts, and generates the input models from the commands they feed.

Both halves belong to the library for the same reason: the envelope shape is a
cross-boundary contract, and until it lived somewhere it was a sentence repeated
in the server's docs and the client's docs, agreeing only for as long as someone
remembered to update both. Now the client's ``{attributes: {...}}`` and the
server's ``body.data.attributes`` are the same declaration.
"""
import dataclasses
import typing
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, create_model

T = TypeVar("T")


class Attributes(BaseModel, Generic[T]):
    """The `data` member of a write document: `{"attributes": {...}}`."""

    attributes: T


class Envelope(BaseModel, Generic[T]):
    """A JSON:API write body: `{"data": {"attributes": {...}}}`.

    Used as ``body: Envelope[EventCreateInput]``; the handler reads
    ``body.data.attributes``.
    """

    data: Attributes[T]


def input_model(
    command: type,
    *,
    name: Optional[str] = None,
    only: Optional[typing.Iterable[str]] = None,
    exclude: typing.Iterable[str] = (),
    optional: bool = False,
) -> type[BaseModel]:
    """Build a pydantic input model from a `Command` dataclass.

    The command already *is* the write contract — its fields are exactly what the
    endpoint accepts. Declaring a parallel pydantic model beside it means the
    same field list exists twice, and adding a field to one but not the other
    fails silently: the endpoint accepts the key and quietly drops it.

    This deliberately derives from the **command**, not from the ORM model. A
    model-driven generator is how this library's ancestor grew into a CRUD
    framework — it exposes columns by default, so every new column is a new
    public field until someone remembers to opt out.

    `only` / `exclude` narrow the field set; `optional` makes every field
    nullable with a `None` default, for partial-update bodies dumped with
    `exclude_unset=True`.
    """
    hints = typing.get_type_hints(command)
    fields: dict[str, Any] = {}

    for f in dataclasses.fields(command):
        if only is not None and f.name not in only:
            continue
        if f.name in exclude:
            continue

        annotation = hints[f.name]
        if optional:
            annotation = Optional[annotation]
            default: Any = None
        elif f.default is not dataclasses.MISSING:
            default = f.default
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            default = f.default_factory()  # type: ignore[misc]
        else:
            default = ...  # required

        fields[f.name] = (annotation, default)

    missing = set(only or ()) - set(fields)
    if missing:
        raise ValueError(
            f"{command.__name__} has no field(s) {sorted(missing)} requested by `only`"
        )

    return create_model(name or f"{command.__name__}Input", **fields)
