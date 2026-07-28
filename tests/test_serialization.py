"""Compound JSON:API serialization: root + children as `included`, no child self-links."""
from dataclasses import dataclass

from pydantic import BaseModel

from fastapi_resources import AggregateSchema, Child, serialize, serialize_many


@dataclass
class FakeItem:
    id: str
    name: str


@dataclass
class FakeCart:
    id: str
    owner: str
    items: list


class ItemRead(BaseModel):
    id: str
    name: str


class CartRead(BaseModel):
    id: str
    owner: str


SCHEMA = AggregateSchema(
    type="cart",
    read=CartRead,
    children=[Child(attr="items", type="item", read=ItemRead)],
)


def _cart():
    return FakeCart(id="c1", owner="alice", items=[FakeItem(id="i1", name="apple")])


def test_root_is_primary_data_with_attributes_only():
    doc = serialize(_cart(), SCHEMA, self_url="/carts/c1")
    assert doc["data"]["type"] == "cart"
    assert doc["data"]["id"] == "c1"
    assert doc["data"]["attributes"] == {"owner": "alice"}  # id lifted out
    assert doc["links"] == {"self": "/carts/c1"}


def test_children_are_linked_and_included_without_self_links():
    doc = serialize(_cart(), SCHEMA)
    assert doc["data"]["relationships"]["items"]["data"] == [{"type": "item", "id": "i1"}]
    [included] = doc["included"]
    assert included == {"type": "item", "id": "i1", "attributes": {"name": "apple"}}
    assert "links" not in included  # children are not independently addressable


def test_serialize_many_dedupes_included_and_counts():
    doc = serialize_many([_cart(), _cart()], SCHEMA)
    assert doc["meta"]["count"] == 2
    assert len(doc["data"]) == 2
    assert len(doc["included"]) == 1  # same item id deduped across roots
