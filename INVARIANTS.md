# fastapi-resources Architecture Invariants

These are the rules of this library as it exists now. It is a small
**cosmicpython-style aggregate framework**: it provides the message-bus / unit-of-work /
repository / domain-event machinery for building aggregate-oriented apps, plus a
JSON:API **compound-document serializer** for reads and command responses.

> Note: the name is historical. This library no longer generates CRUD, resources,
> or JSON:API routing. HTTP routing lives in the consuming app (thin FastAPI
> adapters that call `serialize()`), not here.

---

## What This Library Is

A toolkit of independent pieces that work together:

- **`AggregateRoot`** — mixin that records domain events on aggregate roots
- **`Command` / `Event`** — frozen-dataclass base classes for messages
- **`MessageBus`** — routes commands to one handler and events to many
- **`AbstractUnitOfWork` / `SqlAlchemyUnitOfWork`** — the transactional boundary; collects domain events at commit
- **`AggregateRepository` / `build_aggregate_repo`** — loads a root + its children as a unit, scopes the root, tracks touched roots
- **`serialize` / `serialize_many` / `AggregateSchema` / `Child`** — JSON:API compound-document serialization
- **`Repository` / `UnitOfWork` ports** — Protocols the service layer types against
- **`NotFound`** — the one shared exception (→ HTTP 404 in the app)
- **`BaseSqlAlchemyRepo` / `build_sqlalchemy_repo`** — a plain non-aggregate repo, kept for generic use

Everything is exported from `fastapi_resources/__init__.py`. There is no HTTP,
FastAPI, or JSON:API *routing* code in this library.

---

## Domain messages (`domain.py`)

```python
@dataclass(frozen=True)
class Command: ...      # imperative intent, one handler, may fail loudly

@dataclass(frozen=True)
class Event: ...        # a fact that happened, zero-or-more handlers
```

Concrete commands/events are app-defined frozen dataclasses inheriting these.

### `AggregateRoot`

```python
class AggregateRoot:
    def record(self, event: Event) -> None      # append to lazy self._events
    def pull_events(self) -> list[Event]         # drain and return
```

Rules:
- `_events` is a plain instance attribute created lazily — **never an ORM column**.
- Aggregate roots mix this in: `class Calendar(AggregateRoot, BaseModel)`.
- Roots emit events from their behavior methods via `self.record(...)`.
- The UoW drains events with `pull_events()` at commit, from the roots its repos touched.

---

## Repositories (`repositories/__init__.py`)

### `AggregateRepository` + `build_aggregate_repo`

One repository per aggregate **root**. Loads the whole aggregate and scopes the root.

```python
CalendarRepo = build_aggregate_repo(
    Calendar,
    load=["members"],                                   # child collections to eager-load
    scope=lambda ctx: [Calendar.id.in_(...ctx["principal"]...)],  # row-level predicates on the root
)
```

Behavior:
- `__init__(session, context=None, id_field=None)` — creates `self.seen: dict` (keyed by `id()`).
- `add(root)` — `session.add(root)` **without flush** (PKs are app-assigned uuid7), records `seen[id(root)] = root`.
- `remove(root)` — `session.delete(root)`, records in `seen`.
- `get(pk)` — `select(Root).where(pk_col == pk)`, `selectinload` each `load` relationship, apply `scope()` predicates, `.one_or_none()`; raises `NotFound` if absent/out-of-scope; records in `seen`.
- `scope()` returns `[]` by default; `build_aggregate_repo`'s `scope=` becomes `scope(self.context)`.

Rules:
- **There is no repository for child entities.** Children are reached through the loaded root.
- `seen` is a **dict keyed by `id()`** because aggregate roots are `MappedAsDataclass` (unhashable) — never a `set`.
- The parameter is `pk`, not `id` (must not shadow the builtin `id()`).

### `BaseSqlAlchemyRepo` / `build_sqlalchemy_repo`

A generic, non-aggregate repo (`add` with flush, `get`, `list`, `get_where`). Retained
for occasional generic/read use; the aggregate repo is the primary path.

---

## Unit of Work (`unit_of_work.py`)

```python
class AbstractUnitOfWork(ABC):
    def __enter__(self) -> "AbstractUnitOfWork"
    def __exit__(self, *args)                 # calls self.rollback()
    def commit(self); def rollback(self)
    def collect_new_events(self) -> Iterator[Event]
    def repo_for(self, db_class)              # finds a repo attr whose .Db is db_class
```

`SqlAlchemyUnitOfWork(session_factory)`:
- `__enter__` opens a fresh `self.session = session_factory()` and resets `collected_events`.
- `__exit__` rolls back and closes the session.
- `commit()` does, in order:
  1. **Re-add** every seen root (`session.add(root)`), **skipping roots in `session.deleted`** — this cascades children appended *after* the initial `add()` (e.g. an account issuing a token) without resurrecting deleted roots.
  2. `session.flush()`.
  3. Collect events: for each repo with a `dict` `seen`, drain `root.pull_events()` from its roots. Fallback (no seen-tracking repos): scan `session.identity_map` for a `domain_events` attribute.
  4. `session.commit()`.
- `collect_new_events()` drains and clears `collected_events`.

Rules:
- A UoW is **per unit of work** (per command dispatch / per request). Instantiate freely; it's cheap.
- Concrete UoWs subclass `SqlAlchemyUnitOfWork` and assign one repo per aggregate in `__enter__`.
- The session is owned by the UoW and never handed out.

---

## Message Bus (`message_bus.py`)

The bus is an app-level singleton: a dispatch table. The **UoW is passed at dispatch time**.

```python
bus = MessageBus()
bus.register(SomeCommand, handle_some_command)      # command → exactly one handler
bus.register_projector(SomeEvent, project_some)     # event → append a handler (alias of register)
bus.handle(command, uow)                            # dispatch
```

Behavior of `handle(message, uow=None)`:
- **Command** → look up its single handler (missing → `ValueError`). Call `handler(cmd, uow)` when a uow is passed, else `handler(cmd)`. After it returns, drain `uow.collect_new_events()` and queue them.
- **Event** → call every registered handler `handler(event)`; exceptions are logged, not raised; remaining handlers still run.
- Returns the command handler's return value.

Rules:
- **Command handlers fail loudly**; **event handlers fail silently**.
- Event handlers / projectors get their non-uow dependencies bound at bootstrap (e.g. `partial(on_event_created, schedule=...)`); they do **not** receive the command's uow.
- The bus knows nothing about HTTP, sessions, or FastAPI.

---

## Serialization (`serialization.py`)

Turns an aggregate into a **JSON:API compound document**.

```python
CALENDAR = AggregateSchema(
    type="calendar",
    read=CalendarRead,                                   # pydantic; must include `id`
    children=[Child(attr="members", type="calendar_member", read=CalendarMemberRead)],
)
serialize(root, CALENDAR, self_url="/calendars/1")   # single
serialize_many(roots, CALENDAR, self_url="/calendars")  # list (+ meta.count)
```

Output shape:
- Root is the primary `data` (`{type, id, attributes, relationships}`); `id` is lifted out of attributes.
- Children are `included` resource objects — each has `type` + `id` + `attributes` but **no `self` link** and no endpoint of their own — linked from the root's `relationships`.
- `serialize_many` dedupes `included` by `(type, id)` and adds `meta.count`.

Rules:
- Children are addressable only *through* the root. This is exactly what a JSON:API read client (make-resource) normalizes into its entity store.
- Value objects (no identity) do **not** become `included` resources; embed them in attributes.

---

## Ports (`ports.py`)

Runtime-checkable Protocols the service layer types against, so handlers can be
driven by in-memory fakes:

```python
class Repository(Protocol):    def add(self, obj); def get(self, pk, ...)
class UnitOfWork(Protocol):    __enter__/__exit__; def repo_for(...); def commit(self)
```

---

## What This Library Does NOT Do

- No `build_commands` / `build_handlers` (generated CRUD).
- No `Resource` classes, mixins, or `build_sqlalchemy_resource`.
- No JSON:API **routing** / route generation. The app writes thin FastAPI routers
  that dispatch commands and call `serialize()`.

---

## Testing

- `tests/test_aggregate.py` — end-to-end spine: `AggregateRoot` + `build_aggregate_repo`
  (scope + `seen`) + UoW event collection + `bus.handle(cmd, uow)` + projector.
- `tests/test_serialization.py` — compound documents (root + `included`, no child self-links).
- `tests/test_message_bus.py`, `tests/test_unit_of_work.py`, `tests/test_repositories.py`, `tests/test_ports.py`.
- Run: `PYTHONPATH=. python -m pytest -q` (uses the parent app's venv).

---

## Module Reference

```
fastapi_resources/
├── __init__.py         # public exports (see "What This Library Is")
├── domain.py           # Command, Event, AggregateRoot
├── message_bus.py      # MessageBus (handle(message, uow))
├── unit_of_work.py     # AbstractUnitOfWork, SqlAlchemyUnitOfWork
├── repositories/       # BaseSqlAlchemyRepo, AggregateRepository, build_* factories
├── serialization.py    # AggregateSchema, Child, serialize, serialize_many
├── ports.py            # Repository, UnitOfWork Protocols
├── exceptions.py       # NotFound
└── types.py
```
