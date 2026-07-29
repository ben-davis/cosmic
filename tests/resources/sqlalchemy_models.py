"""Minimal aggregate models + repos for the repository / uow / ports tests."""
from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, create_engine
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cosmic import AggregateRoot, build_aggregate_repo
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


GalaxyRepo = build_aggregate_repo(Galaxy, load=["stars"])

# Scoped by owner, and paginated newest-first by discovery date.
OwnedGalaxyRepo = build_aggregate_repo(
    Galaxy,
    load=["stars"],
    scope=lambda ctx: [Galaxy.owner == ctx["owner"]] if ctx.get("owner") else [],
    sort=(Galaxy.discovered_at, True),
)
