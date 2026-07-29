"""Run the repository contract against both implementations.

This is the suite the library ships for its consumers; running it here on the
library's own `FakeRepo` and `AggregateRepository` is what keeps the shipped
double honest.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from cosmic import Context
from cosmic.testing import FakeRepo
from cosmic.testing.contract import RepositoryContractTests
from tests.resources.sqlalchemy_base import Base
from tests.resources.sqlalchemy_models import Comet, CometRepo, engine


@pytest.fixture(autouse=True)
def _db():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def session():
    with Session(engine) as s:
        yield s
        s.rollback()


class TestCometRepositoryContract(RepositoryContractTests):
    title_field = "name"

    @pytest.fixture(params=["fake", "real"])
    def repo(self, request, session):
        if request.param == "fake":
            return FakeRepo(sort=("seen_at", True))
        return CometRepo(session, context=Context(principal="alice"))

    @pytest.fixture
    def flush(self, repo, session):
        return session.flush if isinstance(repo, CometRepo) else (lambda: None)

    @pytest.fixture
    def make_root(self):
        counter = iter(range(1000))

        def _make(**kwargs):
            # Distinct sort values: the pagination cases need a total order, and
            # the PK tiebreaker is exercised separately in test_repositories.py.
            n = next(counter)
            kwargs.setdefault("name", f"comet-{n}")
            return Comet(
                owner="alice",
                seen_at=datetime(2026, 1, 1) + timedelta(minutes=n),
                **kwargs,
            )

        return _make
