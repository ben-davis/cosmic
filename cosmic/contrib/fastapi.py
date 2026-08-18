"""FastAPI read adapters — the client half of this library's own cursor contract.

Optional: import it only if you want it, and only `cosmic[fastapi]` installs
FastAPI. Nothing else in the library imports a web framework, and that stays true.

These live here rather than in each app because `serialize_many` emits
``links.next`` as a **bare cursor token, not a URL** — a deliberate deviation
from JSON:API, since a URL would round-trip back as a malformed cursor and 400.
That means the reader has obligations: send the token back as ``page[cursor]``,
and preserve every *other* query parameter into the next page. Leaving those to
the caller left the two halves of one contract in two repositories, held together
by a comment in each.
"""
from typing import Any, Optional
from urllib.parse import urlencode

from fastapi import Query
from starlette.requests import Request

from cosmic.repositories import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from cosmic.serialization import AggregateSchema, serialize, serialize_many


class Page:
    """Cursor pagination parameters, as JSON:API spells them.

    Use as a dependency: ``page: Page = Depends()``.
    """

    def __init__(
        self,
        cursor: Optional[str] = Query(default=None, alias="page[cursor]"),
        size: int = Query(
            default=DEFAULT_PAGE_SIZE, alias="page[size]", ge=1, le=MAX_PAGE_SIZE
        ),
    ):
        self.cursor = cursor
        self.size = size


def request_self_url(request: Request) -> str:
    """This request's path and query, minus the cursor.

    The `next` link is this plus a fresh cursor, so everything else the caller
    sent — `page[size]`, and any future filters or sorts — has to survive into
    the following page. Hardcoding the bare path silently reset the page size
    after page one.
    """
    params = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key != "page[cursor]"
    ]
    query = urlencode(params)
    return f"{request.url.path}?{query}" if query else request.url.path


def read_root(
    uow,
    repo: str,
    schema: AggregateSchema,
    root_id: str,
    *,
    self_url: Optional[str] = None,
) -> dict:
    """Load one root through `uow.<repo>` and serialize it.

    Reads go through the UnitOfWork like writes do, so they get the same
    repositories built from the same `context`. Constructing a repository and its
    context separately on the read path is how a read ends up scoped differently
    from the write beside it.
    """
    with uow:
        root = getattr(uow, repo).get(root_id)
        return serialize(root, schema, self_url=self_url)


def read_list(
    request: Request,
    uow,
    repo: str,
    schema: AggregateSchema,
    page: Page,
    **filters: Any,
) -> dict:
    """List one page of roots through `uow.<repo>` and serialize it.

    Any keyword beyond the positional args is forwarded to the repo's
    `list()` as a named filter — see `AggregateRepository.list`.
    """
    with uow:
        roots, next_cursor = getattr(uow, repo).list(
            limit=page.size, cursor=page.cursor, **filters
        )
        return serialize_many(
            roots, schema, self_url=request_self_url(request), next_cursor=next_cursor
        )


def error_response(exc, status: Optional[int] = None) -> dict:
    """A `CosmicError` as a JSON:API errors document."""
    status = status if status is not None else getattr(exc, "status", 500)
    return {"errors": [{"status": status, "title": str(exc)}]}
