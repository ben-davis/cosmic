"""JSON:API compound-document serialization for aggregates.

An aggregate is serialized as a JSON:API compound document: the root as the
primary `data`, its child entities as `included` resources (each with `type`+`id`
but **no `self` link** and no endpoint of their own), linked from the root's
`relationships`. This is exactly the shape JSON:API read clients (e.g.
make-resource) normalize into their entity store — the children become cached
entities keyed by type/id, reachable through the root.

Two kinds of link come out of an aggregate, and they are not the same thing:

* `Child`  — an entity *inside* this aggregate. Serialized as a relationship
  **and** an `included` body, because the root owns it and loaded it.
* `Ref`    — a reference to a *different* aggregate, held by id (DDD's
  reference-by-identity rule). Serialized as a relationship **with no `included`
  body**: this document does not own that aggregate and must not speak for its
  contents. The client resolves it from its store or fetches it separately.
"""
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel


@dataclass
class Child:
    """An entity owned by this aggregate — relationship + `included` body."""

    attr: str                 # relationship attribute on the root (e.g. "members")
    type: str                 # JSON:API type for the child (e.g. "calendar_member")
    read: type[BaseModel]     # pydantic read schema for the child (must include `id`)
    many: bool = True


@dataclass
class Ref:
    """A by-id reference to another aggregate — relationship, no `included` body."""

    attr: str                      # id attribute on the root (e.g. "event_id")
    type: str                      # JSON:API type of the referenced root
    name: Optional[str] = None     # relationship name; defaults to `attr` sans "_id"

    @property
    def relationship_name(self) -> str:
        if self.name:
            return self.name
        return self.attr[:-3] if self.attr.endswith("_id") else self.attr


@dataclass
class AggregateSchema:
    """How to serialize an aggregate root + its children as a compound document."""

    type: str                 # JSON:API type for the root (e.g. "calendar")
    read: type[BaseModel]     # pydantic read schema for the root (must include `id`)
    children: list[Child] = field(default_factory=list)
    refs: list[Ref] = field(default_factory=list)


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

    for ref in schema.refs:
        ref_id = getattr(root, ref.attr, None)
        relationships[ref.relationship_name] = {
            "data": ({"type": ref.type, "id": str(ref_id)} if ref_id else None)
        }

    if relationships:
        data["relationships"] = relationships

    document: dict = {"data": data}
    if included:
        document["included"] = included
    if self_url:
        document["links"] = {"self": self_url}
    return document


def serialize_many(
    roots: list,
    schema: AggregateSchema,
    *,
    self_url: Optional[str] = None,
    next_cursor: Optional[str] = None,
) -> dict:
    """Serialize a list of aggregate roots into a JSON:API list compound document.

    `meta.count` is the size of *this page*. When `next_cursor` is given, a
    `links.next` is emitted — cursor clients treat its absence as "last page".

    **`links.next` is the bare cursor token, not a URL.** JSON:API specifies a URL
    there; this deliberately deviates. The client sends the value straight back as
    `page[cursor]`, so a URL would round-trip as a malformed cursor and 400. Treat
    it as opaque — do not parse or construct it.
    """
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

    links: dict = {}
    if self_url:
        links["self"] = self_url
    if next_cursor:
        links["next"] = next_cursor
    if links:
        document["links"] = links
    return document
