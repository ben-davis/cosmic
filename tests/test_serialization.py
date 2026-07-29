"""Compound JSON:API serialization: root + children as `included`, no child self-links."""
from dataclasses import dataclass

from pydantic import BaseModel

from fastapi_resources import AggregateSchema, Child, Ref, serialize, serialize_many


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


# --- cross-aggregate references (Ref) ---


@dataclass
class FakeOrder:
    id: str
    cart_id: str
    coupon_id: str | None


class OrderRead(BaseModel):
    id: str


ORDER = AggregateSchema(
    type="order",
    read=OrderRead,
    refs=[Ref(attr="cart_id", type="cart"), Ref(attr="coupon_id", type="coupon")],
)


def test_ref_becomes_a_relationship_named_without_the_id_suffix():
    doc = serialize(FakeOrder(id="o1", cart_id="c1", coupon_id=None), ORDER)
    assert doc["data"]["relationships"]["cart"]["data"] == {"type": "cart", "id": "c1"}


def test_ref_emits_no_included_body():
    """A referenced aggregate is not owned here, so this document must not
    speak for its contents — only point at it."""
    doc = serialize(FakeOrder(id="o1", cart_id="c1", coupon_id=None), ORDER)
    assert "included" not in doc


def test_optional_ref_serializes_as_null_relationship():
    doc = serialize(FakeOrder(id="o1", cart_id="c1", coupon_id=None), ORDER)
    assert doc["data"]["relationships"]["coupon"] == {"data": None}


def test_ref_name_can_be_overridden():
    schema = AggregateSchema(
        type="order",
        read=OrderRead,
        refs=[Ref(attr="cart_id", type="cart", name="basket")],
    )
    doc = serialize(FakeOrder(id="o1", cart_id="c1", coupon_id=None), schema)
    assert "basket" in doc["data"]["relationships"]


# --- pagination links ---


def test_no_next_link_without_a_cursor():
    doc = serialize_many([_cart()], SCHEMA, self_url="/carts")
    assert doc["links"] == {"self": "/carts"}


def test_next_link_carries_the_cursor():
    doc = serialize_many([_cart()], SCHEMA, self_url="/carts", next_cursor="abc")
    assert doc["links"]["next"] == "/carts?page[cursor]=abc"


def test_next_link_appends_to_an_existing_query_string():
    doc = serialize_many(
        [_cart()], SCHEMA, self_url="/carts?sort=name", next_cursor="abc"
    )
    assert doc["links"]["next"] == "/carts?sort=name&page[cursor]=abc"
