"""JSON:API compound-document serialization for aggregates.

An aggregate is serialized as a JSON:API compound document: the root as the
primary `data`, its child entities as `included` resources (each with `type`+`id`
but **no `self` link** and no endpoint of their own), linked from the root's
`relationships`. This is exactly the shape JSON:API read clients (e.g.
make-resource) normalize into their entity store — the children become cached
entities keyed by type/id, reachable through the root.
"""
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel


@dataclass
class Child:
    """Declares a child collection/relation on an aggregate root."""

    attr: str                 # relationship attribute on the root (e.g. "members")
    type: str                 # JSON:API type for the child (e.g. "calendar_member")
    read: type[BaseModel]     # pydantic read schema for the child (must include `id`)
    many: bool = True


@dataclass
class AggregateSchema:
    """How to serialize an aggregate root + its children as a compound document."""

    type: str                 # JSON:API type for the root (e.g. "calendar")
    read: type[BaseModel]     # pydantic read schema for the root (must include `id`)
    children: list[Child] = field(default_factory=list)


def _resource_object(obj: Any, type_: str, read: type[BaseModel]) -> dict:
    dumped = read.model_validate(obj, from_attributes=True).model_dump(mode="json")
    id_ = str(dumped.pop("id"))
    return {"type": type_, "id": id_, "attributes": dumped}


def serialize(root: Any, schema: AggregateSchema, *, self_url: Optional[str] = None) -> dict:
    """Serialize one aggregate root into a JSON:API single compound document."""
    data = _resource_object(root, schema.type, schema.read)
    relationships: dict = {}
    included: list = []

    for child in schema.children:
        value = getattr(root, child.attr)
        if child.many:
            items = list(value or [])
            relationships[child.attr] = {
                "data": [{"type": child.type, "id": str(i.id)} for i in items]
            }
            included.extend(_resource_object(i, child.type, child.read) for i in items)
        else:
            relationships[child.attr] = {
                "data": ({"type": child.type, "id": str(value.id)} if value else None)
            }
            if value is not None:
                included.append(_resource_object(value, child.type, child.read))

    if relationships:
        data["relationships"] = relationships

    document: dict = {"data": data}
    if included:
        document["included"] = included
    if self_url:
        document["links"] = {"self": self_url}
    return document


def serialize_many(
    roots: list, schema: AggregateSchema, *, self_url: Optional[str] = None
) -> dict:
    """Serialize a list of aggregate roots into a JSON:API list compound document."""
    data: list = []
    included: list = []
    seen: set = set()

    for root in roots:
        one = serialize(root, schema)
        data.append(one["data"])
        for inc in one.get("included", []):
            key = (inc["type"], inc["id"])
            if key not in seen:
                seen.add(key)
                included.append(inc)

    document: dict = {"data": data, "meta": {"count": len(data)}}
    if included:
        document["included"] = included
    if self_url:
        document["links"] = {"self": self_url}
    return document
