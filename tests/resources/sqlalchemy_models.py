"""Minimal aggregate models + repos for the repository / uow / ports tests."""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import ForeignKey, create_engine
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cosmic import AggregateRepository, AggregateRoot
from tests.resources.sqlalchemy_base import Base

engine = create_engine(
    "sqlite+pysqlite://", connect_args={"check_same_thread": False}, future=True
)


class Star(Base):
    """A child entity — reached only through its Galaxy."""

    __tablename__ = "star"

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str]
    color: Mapped[str] = mapped_column(default="")

    galaxy_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("galaxy.id"), default=None, init=False
    )
    galaxy: Mapped[Optional["Galaxy"]] = relationship(
        back_populates="stars", default=None
    )


class Galaxy(AggregateRoot, Base):
    """An aggregate root owning Stars."""

    __tablename__ = "galaxy"

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str]
    discovered_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    owner: Mapped[str] = mapped_column(default="")

    stars: Mapped[list[Star]] = relationship(
        back_populates="galaxy", cascade="all, delete-orphan", default_factory=list
    )


class GalaxyRepo(AggregateRepository):
    Db = Galaxy
    load = ("stars",)


class OwnedGalaxyRepo(AggregateRepository):
    """Scoped to the principal, paginated newest-first by discovery date."""

    Db = Galaxy
    load = ("stars",)
    sort = (Galaxy.discovered_at, True)

    def scope(self) -> list:
        if self.context.principal is None:
            return []
        return [Galaxy.owner == self.context.principal]


class Comet(AggregateRoot, Base):
    """A root with an **app-assigned** id, for the repository contract suite.

    `add()` deliberately does not flush, because the apps this library targets
    generate their own PKs (uuid7) — so a root has an identity before it is
    persisted, and an in-memory double can hold one that behaves the same.
    A database-assigned autoincrement id (as `Galaxy` uses) cannot: it is `None`
    until flush, so every unsaved root would collide with every other.
    """

    __tablename__ = "comet"

    id: Mapped[str] = mapped_column(
        primary_key=True, default_factory=lambda: str(uuid4())
    )
    name: Mapped[str] = mapped_column(default="")
    owner: Mapped[str] = mapped_column(default="")
    seen_at: Mapped[Optional[datetime]] = mapped_column(default=None)


class CometRepo(AggregateRepository):
    Db = Comet
    sort = (Comet.seen_at, True)

    def scope(self) -> list:
        if self.context.principal is None:
            return []
        return [Comet.owner == self.context.principal]
