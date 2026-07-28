"""Minimal ORM models + repos for the repository/uow/ports tests."""
from typing import Optional

from sqlalchemy import ForeignKey, create_engine
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fastapi_resources import build_sqlalchemy_repo
from tests.resources.sqlalchemy_base import Base

engine = create_engine(
    "sqlite+pysqlite://", connect_args={"check_same_thread": False}, future=True
)


class Galaxy(Base):
    __tablename__ = "galaxy"

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str]

    stars: Mapped[list["Star"]] = relationship(
        back_populates="galaxy", default_factory=list
    )


class Star(Base):
    __tablename__ = "star"

    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str]
    color: Mapped[str] = mapped_column(default="")

    galaxy_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("galaxy.id"), default=None
    )
    galaxy: Mapped[Optional[Galaxy]] = relationship(back_populates="stars", default=None)


GalaxyRepo = build_sqlalchemy_repo(Galaxy)

_StarBaseRepo = build_sqlalchemy_repo(Star)


class StarFilteredRepo(_StarBaseRepo):
    """Star repo with optional name filtering via context."""

    def get_where(self, method):
        if name := self.context.get("only_name"):
            return [Star.name == name]
        return []
