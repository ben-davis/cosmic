# cosmic — Full Example

This file shows the complete picture of how the library is used, from models to running app. It uses a Galaxy / Star / Planet domain throughout.

---

## 1. ORM Models

Standard SQLAlchemy with `MappedAsDataclass`. No library-specific requirements. Aggregates that emit domain events carry a `domain_events` list (not mapped to the DB).

```python
# models.py
from dataclasses import field
from sqlalchemy.orm import MappedAsDataclass, DeclarativeBase, Mapped, mapped_column, relationship

class Base(MappedAsDataclass, DeclarativeBase):
    pass

class Galaxy(Base):
    __tablename__ = "galaxy"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str]
    stars: Mapped[list["Star"]] = relationship(back_populates="galaxy", default_factory=list)
    domain_events: list = field(default_factory=list, init=False, repr=False)

class Star(Base):
    __tablename__ = "star"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str]
    galaxy_id: Mapped[int | None] = mapped_column(ForeignKey("galaxy.id"), default=None)
    galaxy: Mapped["Galaxy"] = relationship(back_populates="stars", default=None)
    planets: Mapped[list["Planet"]] = relationship(back_populates="star", default_factory=list)
    domain_events: list = field(default_factory=list, init=False, repr=False)

class Planet(Base):
    __tablename__ = "planet"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str]
    star_id: Mapped[int | None] = mapped_column(ForeignKey("star.id"), default=None)
    star: Mapped["Star"] = relationship(back_populates="planets", default=None)
```

---

## 2. Schemas

Pydantic models for HTTP input and output. `__relationships__` declares which relationships are included in the JSON:API response.

```python
# schemas.py
from pydantic import BaseModel

class GalaxyRead(BaseModel):
    id: int
    name: str
    __relationships__ = ["stars"]

class GalaxyCreate(BaseModel):
    name: str

class GalaxyUpdate(BaseModel):
    name: str | None = None


class StarRead(BaseModel):
    id: int
    name: str
    __relationships__ = ["galaxy", "planets"]

class StarCreate(BaseModel):
    name: str
    __relationships__ = ["galaxy"]

class StarUpdate(BaseModel):
    name: str | None = None
    __relationships__ = ["galaxy", "planets"]
```

---

## 3. Message Bus

The library provides `MessageBus`. Create one instance for the application. All registration is explicit — nothing auto-registers.

```python
# bus.py
from cosmic import MessageBus

bus = MessageBus()
```

---

## 4. Repositories

`build_sqlalchemy_repo` generates a repository with `add`, `get`, `list`, and the full read infrastructure (filtering, pagination, eager loading). Subclass to add custom queries or override `get_where`.

```python
# repositories.py
from cosmic.repositories import build_sqlalchemy_repo

# Simple case — fully generated, no customisation needed
GalaxyRepo = build_sqlalchemy_repo(Galaxy)

# Custom case — override get_where for row-level filtering
class StarRepo(build_sqlalchemy_repo(Star)):
    def get_where(self, method):
        galaxy_id = self.context.get("galaxy_id")
        if galaxy_id:
            return [Star.galaxy_id == galaxy_id]
        return []
```

---

## 5. Unit of Work

Compose repositories into a UoW. Handlers receive it as a default parameter — the UoW never appears in resources.

```python
# unit_of_work.py
from sqlalchemy.orm import sessionmaker
from cosmic.unit_of_work import SqlAlchemyUnitOfWork
from repositories import GalaxyRepo, StarRepo

DEFAULT_SESSION_FACTORY = sessionmaker(bind=engine)

class AppUoW(SqlAlchemyUnitOfWork):
    def __enter__(self):
        self.session = self.session_factory()
        self.galaxies = GalaxyRepo(self.session)
        self.stars = StarRepo(self.session)
        return self

default_uow = AppUoW(session_factory=DEFAULT_SESSION_FACTORY)
```

---

## 6. Resources

### Simple case — three factories, wired manually

Commands, handlers, and the resource are defined separately. Commands can live in `domain/` since `build_commands` has no framework dependencies beyond the base `Command` class.

```python
# domain/commands/galaxy.py
from cosmic.domain import build_commands
from schemas import GalaxyCreate, GalaxyUpdate
from models import Galaxy

GalaxyCommands = build_commands(
    Db=Galaxy,
    Create=GalaxyCreate,
    Update=GalaxyUpdate,
)
# Commands:  GalaxyCommands.Create  →  CreateGalaxy(id, name)
#            GalaxyCommands.Update  →  UpdateGalaxy(id, name?)
#            GalaxyCommands.Delete  →  DeleteGalaxy(id)
# Events:    GalaxyCommands.Created →  GalaxyCreated(id)
#            GalaxyCommands.Updated →  GalaxyUpdated(id)
#            GalaxyCommands.Deleted →  GalaxyDeleted(id)
```

```python
# shell/handlers/galaxy.py
from cosmic.handlers import build_handlers
from domain.commands.galaxy import GalaxyCommands
from unit_of_work import default_uow
from models import Galaxy

GalaxyHandlers = build_handlers(
    Db=Galaxy,
    commands=GalaxyCommands,
    uow=default_uow,
)
# GalaxyHandlers.create  →  default create handler function
# GalaxyHandlers.update  →  default update handler function
# GalaxyHandlers.delete  →  default delete handler function
```

```python
# shell/resources/galaxy.py
from cosmic.resources import build_sqlalchemy_resource
from models import Galaxy
from schemas import GalaxyRead, GalaxyCreate, GalaxyUpdate
from domain.commands.galaxy import GalaxyCommands

GalaxyResource = build_sqlalchemy_resource(
    Db=Galaxy,
    Read=GalaxyRead,
    Create=GalaxyCreate,
    Update=GalaxyUpdate,
    commands=GalaxyCommands,
)
```

```python
# bus.py — explicit registration
from bus import bus
from domain.commands.galaxy import GalaxyCommands
from shell.handlers.galaxy import GalaxyHandlers

bus.register_resource(GalaxyCommands, GalaxyHandlers)
```

### Custom case — full resource class with hand-written commands

Use when the factory's defaults aren't enough: custom relationship handling in writes, non-standard write logic, or commands with fields that can't be inferred from the schema.

Commands live in `domain/` as plain frozen dataclasses:

```python
# domain/commands/star.py
from dataclasses import dataclass, field
from cosmic.domain import Command

@dataclass(frozen=True)
class CreateStar(Command):
    id: int
    name: str
    galaxy_id: int | None = None
    planet_ids: list[int] = field(default_factory=list)

@dataclass(frozen=True)
class UpdateStar(Command):
    id: int
    name: str | None = None
    galaxy_id: int | None = None
    planet_ids: list[int] | None = None

@dataclass(frozen=True)
class DeleteStar(Command):
    id: int
```

```python
# shell/resources/star.py
from cosmic.resources.sqlalchemy import SQLAlchemyResource
from cosmic.resources.sqlalchemy.paginators import LimitOffsetPaginator
from models import Star
from schemas import StarRead, StarCreate, StarUpdate
from repositories import StarRepo
from domain.commands.star import CreateStar, UpdateStar, DeleteStar

class StarResource(SQLAlchemyResource[Star]):
    Repo = StarRepo
    Read = StarRead
    Create = StarCreate
    Update = StarUpdate
    Paginator = LimitOffsetPaginator
    commands = type("commands", (), {
        "Create": CreateStar, "Update": UpdateStar, "Delete": DeleteStar
    })

    def create(self, attributes, relationships) -> Star:
        star_id = generate_id()
        self.messagebus_handle(CreateStar(
            id=star_id,
            name=attributes["name"],
            galaxy_id=relationships.get("galaxy"),
        ))
        return self.repo.get(star_id)

    def update(self, id, attributes, relationships) -> Star:
        self.messagebus_handle(UpdateStar(
            id=id,
            name=attributes.get("name"),
            galaxy_id=relationships.get("galaxy"),
            planet_ids=relationships.get("planets"),
        ))
        return self.repo.get(id)

    def delete(self, id) -> None:
        self.messagebus_handle(DeleteStar(id=id))
```

---

## 7. Custom Handlers and Bus Registration

All handlers — generated defaults and hand-written custom ones — are registered explicitly with the bus. Generated default handlers automatically emit the corresponding event from the commands namespace (`GalaxyCommands.Created`, etc.) by appending it to the entity's `domain_events` list before commit.

```python
# bus.py
from cosmic import MessageBus
from domain.commands.galaxy import GalaxyCommands
from domain.commands.star import CreateStar, UpdateStar, DeleteStar, StarCommands
from shell.handlers.galaxy import GalaxyHandlers
from unit_of_work import default_uow

bus = MessageBus()

# Galaxy — use generated defaults; default handlers emit GalaxyCommands.Created etc.
bus.register_resource(GalaxyCommands, GalaxyHandlers)

# Star — hand-written handler with business logic; emit the generated event explicitly
def create_star(cmd: CreateStar, uow=default_uow):
    with uow:
        star = Star(id=cmd.id, name=cmd.name)
        if cmd.galaxy_id is not None:
            galaxy = uow.galaxies.get(cmd.galaxy_id)
            if not galaxy:
                raise GalaxyNotFound(cmd.galaxy_id)
            star.galaxy = galaxy
        star.domain_events.append(StarCommands.Created(id=cmd.id))
        uow.stars.add(star)
        uow.commit()

bus.register(CreateStar, create_star)
bus.register(UpdateStar, update_star)
bus.register(DeleteStar, delete_star)
```

Subscribe to events — generated or custom — by registering event handlers:

```python
# The generated GalaxyCommands.Created carries only `id`.
# To enrich it, replace the attribute before any handlers are registered:
@dataclass(frozen=True)
class GalaxyCreated(Event):
    id: int
    name: str

GalaxyCommands.Created = GalaxyCreated
# Now the default create handler will emit GalaxyCreated(id=..., name=...) instead.
# (build_handlers must be called after this replacement.)

# Subscribe to the event:
def on_galaxy_created(event: GalaxyCreated, uow=default_uow):
    with uow:
        # e.g. seed a default star, send a welcome notification
        ...

bus.register(GalaxyCreated, on_galaxy_created)
```

---

## 8. FastAPI App

```python
# app.py
from fastapi import FastAPI
from cosmic.routers import JSONAPIResourceRouter
from resources import GalaxyResource, StarResource
from bus import bus

app = FastAPI()
app.state.messagebus_handle = bus.handle

app.include_router(JSONAPIResourceRouter(resource_class=GalaxyResource))
app.include_router(JSONAPIResourceRouter(resource_class=StarResource))
```

The router picks up `messagebus_handle` from app state and injects it into each resource instance. Session injection uses the existing mechanism — unchanged from the pre-factory design.

---

## 9. Using Commands Outside HTTP

Because commands are plain dataclasses that live in `domain/`, any part of the system can import and dispatch them directly via the bus — no HTTP involved.

```python
# A background job that creates a galaxy without going through HTTP
from bus import bus
from domain.commands.galaxy import GalaxyCommands

def nightly_import(galaxy_data: dict):
    bus.handle(GalaxyCommands.Create(
        id=generate_id(),
        name=galaxy_data["name"],
    ))
```

---

## 10. Tests

### Handler test — no HTTP, no session, FakeUnitOfWork

```python
def test_create_star_links_galaxy():
    uow = FakeUnitOfWork()
    galaxy = Galaxy(id=1, name="Milky Way")
    uow.galaxies._store[1] = galaxy

    create_star(CreateStar(id=42, name="Sirius", galaxy_id=1), uow=uow)

    star = uow.stars.get(42)
    assert star.name == "Sirius"
    assert star.galaxy == galaxy
    assert uow.committed


def test_create_star_raises_if_galaxy_missing():
    uow = FakeUnitOfWork()
    with pytest.raises(GalaxyNotFound):
        create_star(CreateStar(id=42, name="Sirius", galaxy_id=999), uow=uow)
```

### Resource test — no HTTP, spy on message bus handle

```python
def test_star_resource_create_dispatches_correct_command():
    dispatched = []

    resource = StarResource(
        session=FakeSession(),
        messagebus_handle=lambda cmd: dispatched.append(cmd),
        context={"galaxy_id": 1},
    )
    resource.create(
        attributes={"name": "Sirius"},
        relationships={"galaxy": 1},
    )

    assert isinstance(dispatched[0], CreateStar)
    assert dispatched[0].name == "Sirius"
    assert dispatched[0].galaxy_id == 1


def test_star_resource_list_filters_by_galaxy():
    repo = FakeStarRepository(context={"galaxy_id": 1})
    repo._store = {
        1: Star(id=1, name="Sirius", galaxy_id=1),
        2: Star(id=2, name="Vega", galaxy_id=2),
    }

    resource = StarResource(session=FakeSession(), context={"galaxy_id": 1})
    resource.repo = repo

    results, _, _ = resource.list()
    assert len(results) == 1
    assert results[0].name == "Sirius"
```

### Integration test — full stack

```python
async def test_create_star_via_http(client: AsyncClient):
    response = await client.post("/stars", json={
        "data": {
            "type": "star",
            "attributes": {"name": "Sirius"},
            "relationships": {
                "galaxy": {"data": {"type": "galaxy", "id": "1"}}
            }
        }
    })

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["attributes"]["name"] == "Sirius"
    assert data["relationships"]["galaxy"]["data"]["id"] == "1"
```
