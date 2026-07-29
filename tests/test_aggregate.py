"""Spine test for the aggregate profile: AggregateRoot + build_aggregate_repo
(scope + .seen) + UnitOfWork event collection + MessageBus.handle(cmd, uow) + projectors."""
import dataclasses

import pytest
from sqlalchemy import ForeignKey, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

from cosmic import (
    AggregateRoot,
    Command,
    Event,
    MessageBus,
    NotFound,
    SqlAlchemyUnitOfWork,
    build_aggregate_repo,
)


class Base(DeclarativeBase):
    pass


@dataclasses.dataclass(frozen=True)
class ItemAdded(Event):
    cart_id: int
    name: str


class Item(Base):
    __tablename__ = "agg_item"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column()
    cart_id: Mapped[int] = mapped_column(ForeignKey("agg_cart.id"))


class Cart(AggregateRoot, Base):
    __tablename__ = "agg_cart"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    owner: Mapped[str] = mapped_column()
    items: Mapped[list[Item]] = relationship(cascade="all, delete-orphan")

    def add_item(self, item_id: int, name: str) -> None:
        self.items.append(Item(id=item_id, name=name))
        self.record(ItemAdded(cart_id=self.id, name=name))


CartRepo = build_aggregate_repo(
    Cart, load=["items"],
    scope=lambda ctx: [Cart.owner == ctx["owner"]] if ctx.get("owner") else [],
)


class CartUnitOfWork(SqlAlchemyUnitOfWork):
    def __init__(self, session_factory, owner=None):
        super().__init__(session_factory)
        self.owner = owner

    def __enter__(self):
        super().__enter__()
        self.carts = CartRepo(self.session, context={"owner": self.owner})
        return self


# --- command + handler + projector ---

@dataclasses.dataclass(frozen=True)
class AddItem(Command):
    cart_id: int
    item_id: int
    name: str


def handle_add_item(cmd: AddItem, uow: CartUnitOfWork) -> int:
    with uow:
        cart = uow.carts.get(cmd.cart_id)
        cart.add_item(cmd.item_id, cmd.name)
        uow.commit()
        return cart.id


@pytest.fixture
def sf():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as s:
        s.add(Cart(id=1, owner="alice"))
        s.commit()
    return factory


@pytest.fixture
def bus():
    b = MessageBus()
    b.register(AddItem, handle_add_item)
    return b


def test_command_loads_root_mutates_and_commits(sf, bus):
    cart_id = bus.handle(AddItem(cart_id=1, item_id=10, name="apples"), uow=CartUnitOfWork(sf, owner="alice"))
    assert cart_id == 1
    with sf() as s:
        assert [i.name for i in s.scalars(select(Item)).all()] == ["apples"]


def test_domain_event_collected_from_seen_and_dispatched_to_projector(sf, bus):
    projected: list = []
    bus.register(ItemAdded, lambda e: projected.append((e.cart_id, e.name)))

    bus.handle(AddItem(cart_id=1, item_id=11, name="pears"), uow=CartUnitOfWork(sf, owner="alice"))

    assert projected == [(1, "pears")]


def test_root_scope_hides_other_owners(sf, bus):
    with pytest.raises(NotFound):
        bus.handle(AddItem(cart_id=1, item_id=12, name="x"), uow=CartUnitOfWork(sf, owner="bob"))
