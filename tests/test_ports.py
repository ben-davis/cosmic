"""The concrete implementations must satisfy the ports they claim to.

The ports exist so an app can drive handlers with in-memory fakes; if a port
drifts from what the machinery actually calls, those fakes stop being evidence.
"""
from sqlalchemy.orm import sessionmaker

from cosmic import Repository, SqlAlchemyUnitOfWork, UnitOfWork
from tests.resources.sqlalchemy_models import GalaxyRepo, engine


def test_aggregate_repo_satisfies_repository_port():
    # Port conformance only — the session is never touched.
    repo = GalaxyRepo(session=None)  # ty: ignore[invalid-argument-type]
    assert isinstance(repo, Repository)


def test_sqlalchemy_uow_satisfies_unit_of_work_port():
    uow = SqlAlchemyUnitOfWork(sessionmaker(engine))
    assert isinstance(uow, UnitOfWork)


def test_unit_of_work_port_covers_what_the_bus_calls():
    """Regression guard: the bus calls collect_new_events after every command.

    It used to be absent from the port while `repo_for` — which nothing calls —
    was present, so a conforming fake could silently swallow every domain event.
    """
    assert hasattr(UnitOfWork, "collect_new_events")

    class UoWMissingEventCollection:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def commit(self): return None

    assert not isinstance(UoWMissingEventCollection(), UnitOfWork)
