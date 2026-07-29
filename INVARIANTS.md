# cosmic — Architecture Invariants

These are the rules of this library as it exists now. It is a small
**cosmicpython-style aggregate framework**: it provides the message-bus / unit-of-work /
repository / domain-event machinery for building aggregate-oriented apps, plus
JSON:API **compound-document** serialization for reads and command responses, the
matching write-document shapes, and the test doubles for all of it.

> Renamed from `fastapi-resources`, which described what it used to be — a CRUD
> and JSON:API *routing* generator. None of that survives. `cosmic.contrib.fastapi`
> holds optional **read adapters**, not route generation; routing lives in the
> consuming app as thin adapters that call `serialize()`.

---

## What This Library Is

A toolkit of independent pieces that work together:

- **`AggregateRoot`** — mixin that records domain events on aggregate roots
- **`Command` / `PartialUpdate` / `Event`** — frozen-dataclass base classes for messages
- **`Context`** — who is asking; every repository's `scope()` reads it
- **`apply_changes`** — allowlisted partial update of a root
- **`MessageBus`** — routes commands to one handler and events to many; `on_error` escalates failed event handlers
- **`AbstractUnitOfWork` / `SqlAlchemyUnitOfWork`** — the transactional boundary; declares its repositories, collects domain events at commit
- **`AggregateRepository`** — loads a root + its children as a unit, scopes the root, tracks touched roots, and paginates (`list()`)
- **`serialize` / `serialize_many` / `AggregateSchema` / `Child` / `Ref`** — JSON:API compound-document output
- **`Envelope` / `Attributes` / `input_model`** — JSON:API write-document input
- **`Repository` / `UnitOfWork` ports** — Protocols the service layer types against
- **`cosmic.errors`** — the shared error hierarchy, each carrying the status it means
- **`cosmic.testing`** — `FakeRepo`, `InMemoryUnitOfWork`, and `RepositoryContractTests`
- **`cosmic.contrib.fastapi`** — optional read adapters (`cosmic[fastapi]`)

Everything except `contrib` and `testing` is exported from `cosmic/__init__.py`.
There is no JSON:API *routing* code in this library.

---

## What this library does and does not enforce

Worth being explicit, because the DDD vocabulary implies more than the code delivers.

**It does enforce** the service-layer boundary. Nothing outside
`cosmic.contrib.fastapi` imports a web framework, handlers take `(command, uow)`
and stay callable from a worker or a test, and a consuming app's `domain/` has no
route in it.

**It does not enforce persistence ignorance.** An aggregate root here *is* a
SQLAlchemy model (`class Event(AggregateRoot, BaseModel)`), not a plain object
mapped onto one. The cosmicpython book separates the two with imperative mapping;
this library takes the pragmatic trade instead, because `MappedAsDataclass` is
pleasant and `start_mappers()` is not. The costs are real and localised, so they
are named rather than hidden:

- `apply_changes` needs an **allowlist** because `setattr` on a root writes a
  column. A persistence-ignorant domain object would not need one.
- `SqlAlchemyUnitOfWork.commit()` re-adds seen roots — an ORM cascade workaround
  living inside the transaction boundary.
- The domain layer cannot be imported, or tested, without SQLAlchemy.

Those three are the places that would change if a future version wanted it.

---

## Errors (`errors.py`)

```
CosmicError            status = 500   # base; anything else is a bug, not a client error
├── ValidationError            400
│   ├── InvalidCursor
│   └── NotEditable
├── NotFound                   404
├── AuthenticationFailed       401
├── PermissionDenied           403
└── ConflictError              409
```

Rules:
- **The status lives on the error, not in an adapter's mapping table.** The
  meaning ("this isn't yours") is a domain decision; only the encoding is HTTP's.
  An adapter registers *one* handler for `CosmicError` and reads `exc.status`, so
  there is nothing to keep in sync when a new error is added.
- **`NotFound` covers "exists but is out of your scope".** Distinguishing it from
  "does not exist" confirms the existence of other people's records to anyone who
  asks. Prefer it for row-level failures; reserve `PermissionDenied` for
  operations the caller may not perform at all.
- Apps subclass these for their own rules (`class UsernameTaken(ConflictError)`),
  naming the rule rather than the status.

---

## Domain messages (`domain.py`)

```python
@dataclass(frozen=True)
class Command:                  # imperative intent, one handler, may fail loudly
    def data(self, *exclude) -> dict

@dataclass(frozen=True)
class PartialUpdate(Command):   # id + changes dict
    id: str
    changes: dict

@dataclass(frozen=True)
class Event: ...                # a fact that happened, zero-or-more handlers
```

- `Command.data(*exclude)` splats a command into an aggregate factory without
  restating its field list at the call site, where "forgot one" looks like a field
  that silently never gets set.
- **`PartialUpdate` carries a dict, not optional attributes.** An explicit `None`
  has to mean "clear this" while an absent key means "leave it alone". Optional
  attributes cannot express that: every unsent field arrives as `None`,
  indistinguishable from a null, and no field could ever be cleared.

### `Context`

```python
@dataclass(frozen=True)
class Context:
    principal: Optional[str] = None
    def require_principal(self) -> str   # raises PermissionDenied
```

A dataclass, not a dict: the whole row-level authorization scheme keys off it, and
a mistyped `ctx["principle"]` yields `None` — which scopes to "rows owned by
nobody" and returns 404 everywhere, a failure that reads as missing data rather
than as a bug. An attribute typo raises. Apps needing more subclass it.

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

### `apply_changes(root, changes, allowed) -> tuple[str, ...]`

Applies a `PartialUpdate`'s `changes`, raising `NotEditable` for anything outside
`allowed`, and returns the names actually applied so the caller can record an
event naming them — or skip recording when nothing changed.

The allowlist is load-bearing, not politeness: roots are ORM models, so a blind
`setattr` loop over client input writes columns. It would let a caller flip an
internal status field or reassign a row to another owner.

---

## Repositories (`repositories/__init__.py`)

One repository per aggregate **root**, as a plain subclass:

```python
class CalendarRepo(AggregateRepository):
    Db = Calendar
    load = ("members",)                       # child collections to eager-load
    sort = (Calendar.name, False)             # (column, descending) for list()

    def scope(self) -> list:                  # row-level predicates on the root
        return [Calendar.id.in_(...self.context.principal...)]
```

There is deliberately **no `build_aggregate_repo` factory**. It produced a class
via `type()`, so type checkers saw `type[AggregateRepository]` and the `scope`
lambda went unchecked — for the same line count as the subclass above. `scope()`
*is* the row-level authorization rule; it belongs where a reader and a type
checker can both see it.

Behaviour:
- `__init__(session, context=None)` — creates `self.seen: dict` (keyed by `id()`).
- `add(root)` — `session.add(root)` **without flush** (PKs are app-assigned uuid7), tracks it.
- `remove(root)` — `session.delete(root)`, tracks it.
- `get(pk)` — `_base_select()` + `pk_col == pk`; raises `NotFound` if absent/out-of-scope.
- `find_one(**equals)` — first root where every `column == value` holds, or `None`.
- `list(limit=…, cursor=…)` — returns `(roots, next_cursor)`.
- `track(root)` — record a root so its events are collected; **public**.
- `unscoped()` — a view with `scope()` switched off, sharing this repo's `seen`.
- `_base_select()` — the shared scoped+eager-loaded select behind `get`, `list`, and `find_one`.

Rules:
- **There is no repository for child entities.** Children are reached through the loaded root.
- **`get`, `list`, and `find_one` share `_base_select()`.** A root visible to one
  and hidden from another is a row-level authorization bug; deriving them all from
  one select makes that divergence impossible rather than merely unlikely.
- **`find_one` takes keyword equality, not arbitrary predicates.** That is the
  shape an in-memory fake can implement too, so hand-written lookups stay covered
  by the contract tests instead of each growing a bespoke, untested method.
- **`track` is public because bypassing it is silent.** A hand-written query that
  does not track its root loses that aggregate's domain events with no error;
  before this existed, such queries reached into `seen` and had to know it was a
  dict keyed by `id()`.
- **Unscoped access is spelled at the call site.** `uow.events.unscoped().get(id)`
  says what it does. A second, unscoped repository parked on the UoW under its own
  name (`uow.all_events`) makes the difference between authorized and not a matter
  of picking the right attribute, with nothing to catch the wrong one.
- `seen` is a **dict keyed by `id()`** because aggregate roots are `MappedAsDataclass`
  (unhashable) — never a `set`.
- The parameter is `pk`, not `id` (must not shadow the builtin `id()`).

### Pagination

Keyset ("seek"), never OFFSET:

- Ordering is `(sort_column, pk)`. The PK is a **tiebreaker that makes the order
  total** — without it, rows sharing a sort value can be duplicated or skipped
  across a page boundary.
- The cursor is a base64url `[sort_value, pk]` pair. `_decode_cursor` re-hydrates
  the sort value to the column's Python type, because SQLite compares a
  TEXT-bound datetime differently from a DATETIME-bound one.
- `list()` over-fetches by one row to detect a further page without a second
  `COUNT(*)`. `next_cursor` is `None` on the last page.
- `limit` is clamped to `[1, MAX_PAGE_SIZE]`; a malformed cursor raises `InvalidCursor`.
- Index every `sort` column, or pagination degrades to a sort of the whole table.

---

## Unit of Work (`unit_of_work.py`)

```python
class MyUnitOfWork(SqlAlchemyUnitOfWork):
    repos = {"events": EventRepo, "calendars": CalendarRepo}

uow = MyUnitOfWork(session_factory, Context(principal=account_id))
with uow:
    uow.events.get(id)          # built in __enter__ from this UoW's context
```

`SqlAlchemyUnitOfWork(session_factory, context=None)`:
- `__enter__` opens a fresh session, resets `collected_events`, and builds one
  repository per `repos` entry against that session and `context`.
- `__exit__` rolls back and closes the session.
- `commit()`:
  1. Refuse to proceed if a repository is attached outside `repos` (see below).
  2. **Re-add** every seen root, **skipping roots in `session.deleted`** — this
     cascades children appended *after* the initial `add()` without resurrecting
     deleted roots.
  3. `session.flush()`.
  4. Drain `root.pull_events()` from every tracked root.
  5. `session.commit()`.
- `track(root)` — for a root reached outside a repository.
- `require_principal()` — delegates to `context`.
- `collect_new_events()` drains and clears `collected_events`.

Rules:
- **Repositories are declared, not assigned.** `commit()` used to find them by
  scanning `vars(self)`; anything that scan missed lost its aggregates' domain
  events in silence, which is the worst possible failure for this mechanism. The
  scan is kept **inverted, as a tripwire**: a repository attached any other way
  raises rather than being quietly skipped. An `unscoped()` view may be assigned,
  since it shares its parent's `seen` (matched by identity of that dict).
- Events come **only** from roots a repository touched or `track` recorded. An
  object added straight to `uow.session` is not an aggregate root here.
- A UoW is **per unit of work** (per command dispatch / per request). Instantiate
  freely; it's cheap. **Reads should use it too** — building a repository and its
  context separately on the read path is how a read ends up scoped differently
  from the write it mirrors.
- The session is owned by the UoW and never handed out.
- **`session_factory` is required.** A default that reaches for a module global
  means the composition-root seam can be bypassed by omitting an argument, and
  then tests have to patch both to keep them in agreement.

---

## Message Bus (`message_bus.py`)

The bus is an app-level singleton: a dispatch table. The **UoW is passed at dispatch time**.

```python
bus = MessageBus(on_error=report)
bus.register(SomeCommand, handle_some_command)      # command → exactly one handler
bus.register(SomeEvent, project_some)               # event → append a handler
bus.handle(command, uow)                            # dispatch
```

Behavior of `handle(message, uow=None)`:
- **Command** → look up its single handler (missing → `ValueError`). Call
  `handler(cmd, uow)` when a uow is passed, else `handler(cmd)`. After it returns,
  drain `uow.collect_new_events()` and queue them.
- **Event** → call every registered handler `handler(event)`; exceptions are
  logged and passed to `on_error`, not raised; remaining handlers still run.
- Returns the result of the message it was **called with** — never that of a
  follow-on command drained from the queue. The caller asked for one thing and has
  no way to know what else the queue picked up on the way.

Rules:
- **Command handlers fail loudly**; **event handlers fail soft** — a broken
  subscriber must not undo a command that already committed.
- **Soft failure needs `on_error`.** Without it a permanently broken handler is
  indistinguishable from a working one; this exact gap once hid the fact that no
  background task in the consuming app could be scheduled *at all*. A failing
  `on_error` is itself caught, so it can never break dispatch.
- Event handlers get their non-uow dependencies bound at bootstrap (e.g.
  `partial(on_event_created, schedule=..., session_factory=...)`); they do **not**
  receive the command's uow.
- The bus knows nothing about HTTP, sessions, or FastAPI. It is **sync**: a
  handler that schedules async work owns the thread→loop handoff itself.

---

## Documents

### Output — compound documents (`serialization.py`)

```python
CALENDAR = AggregateSchema(
    type="calendar",
    read=CalendarRead,                                   # pydantic; must include `id`
    children=[Child(attr="members", type="calendar_member", read=CalendarMemberRead)],
)
SAVED_EVENT = AggregateSchema(
    type="saved_event",
    read=SavedEventRead,
    refs=[Ref(attr="event_id", type="event")],           # another aggregate, by id
)
serialize(root, CALENDAR, self_url="/calendars/1")
serialize_many(roots, CALENDAR, self_url="/calendars", next_cursor=cursor)
```

**`Child` vs `Ref`** — the distinction is ownership, and it is the point:

| | Relationship entry | `included` body |
|---|---|---|
| `Child` — an entity *inside* this aggregate | ✅ | ✅ (the root owns it and loaded it) |
| `Ref` — another aggregate, held by id | ✅ | ❌ (this document doesn't own it) |

Both take an optional `name` overriding the relationship name; `Ref` defaults to
`attr` with a trailing `_id` stripped, `Child` to `attr`.

Rules:
- Children are addressable only *through* the root. This is exactly what a JSON:API
  read client (make-resource) normalizes into its entity store.
- Value objects (no identity) do **not** become `included` resources; embed them in attributes.
- A `Ref` must **never** inline the referenced aggregate. Serializing it here would
  bypass that aggregate's own scope rules and let this endpoint leak rows the
  caller isn't allowed to read.
- A read schema without an `id` raises at **declaration** time. `_resource_object`
  pops `id` out of the dumped attributes, so the alternative is a bare `KeyError`
  on the first request that happens to serialize it.
- `serialize_many` dedupes `included` by `(type, id)`, sets `meta.count` to **this
  page's** size, and emits `links.next` — **as the bare cursor token, not a URL**.
  A deliberate deviation from JSON:API: the client sends the value straight back
  as `page[cursor]`, so a URL would round-trip as a malformed cursor and 400.

### Input — write documents (`documents.py`)

```python
class Attributes(BaseModel, Generic[T]):  attributes: T
class Envelope(BaseModel, Generic[T]):    data: Attributes[T]

EventCreateInput = input_model(CreateEvent)
EventUpdateInput = input_model(CreateEvent, only=Event.EDITABLE, optional=True)
```

The library owns both halves of the contract for the same reason: the envelope
shape is cross-boundary, and until it lived somewhere it was a sentence repeated
in the server's docs and the client's docs, agreeing only for as long as someone
remembered to update both. Now the client's `{attributes: {...}}` and the server's
`body.data.attributes` are one declaration.

`input_model(command, *, name, only, exclude, optional)` builds a pydantic model
from a `Command` dataclass:
- **From the command, never from the ORM model.** A model-driven generator exposes
  columns by default, so every new column is a new public field until someone opts
  out — the road back to the CRUD framework this library used to be.
- The command already *is* the write contract; a parallel hand-written model means
  the field list exists twice, and adding a field to one but not the other fails
  silently (the endpoint accepts the key and drops it).
- `optional=True` makes every field nullable with a `None` default, for partial
  updates dumped with `exclude_unset=True`.
- `only=` naming a field the command doesn't have raises, so a renamed command
  field cannot silently shrink an endpoint.

---

## Ports (`ports.py`)

Runtime-checkable Protocols the service layer types against, so handlers can be
driven by in-memory fakes.

Rule: **a port declares exactly what the machinery calls — no more, no less.**
`UnitOfWork` once declared `repo_for` (which nothing called) while omitting
`collect_new_events` (which `MessageBus._handle_command` calls after every
command). A fake could satisfy the port, silently swallow every domain event, and
the consuming app had to hand-roll its own Protocol to compensate. If you add a
call site in the bus or UoW, update the port in the same change.

Repositories are **not** declared on `UnitOfWork`: they are named per application
(`uow.events`), and a Protocol cannot express "whichever attributes the app listed
in `repos`". Those names are already written down once, on the app's UoW subclass.

---

## Testing support (`cosmic/testing/`)

- **`FakeRepo`** — an `AggregateRepository` over a list: same `seen` tracking, same
  `find_one`, and **real keyset pagination** using the same cursor codec.
- **`InMemoryUnitOfWork`** — declares `repos` the same way, drains events from the
  same place, same `context` / `require_principal` / `track`.
- **`RepositoryContractTests`** — a mixin an app runs against both implementations.

**Why these ship with the library.** A double whose semantics drift from the real
thing is worse than no double: it makes handler tests *confidently* wrong. A
hand-written copy of `FakeRepo` in a consuming app had already diverged three ways
— it drained events from every object it held rather than only from touched roots,
its `list()` ignored the cursor and always claimed "no more pages", and it never
cleared collected events on re-entry. Each of those makes a class of production
bug invisible in tests.

**What the fake cannot model: `scope()`.** Predicates are SQLAlchemy expressions;
there is no running them against plain objects. The fake models the *result* of
scoping — put in it exactly what the principal can see. Row-level authorization is
therefore asserted at the integration layer, against a real database, and the
contract suite says so rather than pretending otherwise.

---

## `cosmic.contrib.fastapi` (optional — `cosmic[fastapi]`)

`Page`, `request_self_url`, `read_root`, `read_list`, `error_response`.

Read adapters only; still no routing generation. They live here rather than in
each app because they are the client half of a contract this library defines: the
cursor token round-trips as `page[cursor]`, and the `self` URL must preserve every
*other* query parameter into the next page (hardcoding the bare path silently
reset `page[size]` after page one). Leaving those to the caller put the two halves
of one contract in two repositories, held together by a comment in each.

`read_root` / `read_list` take a **UnitOfWork**, not a session, so reads use the
same repositories — and therefore the same scope — as the writes beside them.

---

## What This Library Does NOT Do

- No `build_commands` / `build_handlers` (generated CRUD).
- No `Resource` classes, mixins, or `build_sqlalchemy_resource`.
- No `build_aggregate_repo` factory — repositories are plain subclasses.
- No generic non-aggregate repository. The aggregate repo is the only path, so
  nothing can quietly reach a child entity without going through its root.
- No JSON:API **routing** / route generation.
- No async dispatch. The bus is sync; scheduling async work is the app's job.

---

## Testing

- `tests/test_aggregate.py` — end-to-end spine: `AggregateRoot` + `AggregateRepository`
  (scope + `seen`) + UoW event collection + `bus.handle(cmd, uow)` + projector.
- `tests/test_repository_contract.py` — the shipped contract suite, run against
  `FakeRepo` and the real repository.
- `tests/test_in_memory_uow.py` — the fake UoW's event-draining semantics.
- `tests/test_documents.py` — `input_model` + `Envelope`.
- `tests/test_serialization.py` — compound documents (`Child` bodies vs `Ref` links) + pagination links.
- `tests/test_repositories.py` — scoping, `get`/`list` agreement, keyset pagination.
- `tests/test_message_bus.py`, `tests/test_unit_of_work.py`, `tests/test_ports.py`.
- Run: `PYTHONPATH=. python -m pytest -q` (uses the parent app's venv).

---

## Module Reference

```
cosmic/
├── __init__.py         # public exports (see "What This Library Is")
├── domain.py           # Command, PartialUpdate, Event, Context, AggregateRoot, apply_changes
├── errors.py           # CosmicError hierarchy, each with a `status`
├── message_bus.py      # MessageBus (handle(message, uow))
├── unit_of_work.py     # AbstractUnitOfWork, SqlAlchemyUnitOfWork (declarative `repos`)
├── repositories/       # AggregateRepository, cursor helpers
├── serialization.py    # AggregateSchema, Child, Ref, serialize, serialize_many
├── documents.py        # Envelope, Attributes, input_model
├── ports.py            # Repository, UnitOfWork Protocols
├── contrib/fastapi.py  # optional read adapters (FastAPI only)
└── testing/            # FakeRepo, InMemoryUnitOfWork, RepositoryContractTests
```
