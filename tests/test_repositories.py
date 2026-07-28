"""Tests for BaseSqlAlchemyRepo and build_sqlalchemy_repo."""
import pytest
from sqlalchemy.orm import Session

from fastapi_resources import NotFound, build_sqlalchemy_repo
from tests.resources.sqlalchemy_base import Base
from tests.resources.sqlalchemy_models import (
    Galaxy,
    GalaxyRepo,
    Star,
    StarFilteredRepo,
    engine,
)


@pytest.fixture(scope="module", autouse=True)
def _db():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def session():
    with Session(engine) as s:
        yield s
        s.rollback()


class TestBuildSqlAlchemyRepo:
    def test_generates_repo_class(self):
        Repo = build_sqlalchemy_repo(Galaxy)
        assert issubclass(Repo, object)
        assert Repo.Db is Galaxy

    def test_repo_name_includes_model_name(self):
        Repo = build_sqlalchemy_repo(Galaxy)
        assert "Galaxy" in Repo.__name__


class TestRepoAdd:
    def test_add_assigns_pk(self, session: Session):
        galaxy = Galaxy(name="New Galaxy")
        repo = GalaxyRepo(session)
        repo.add(galaxy)
        assert galaxy.id is not None

    def test_add_makes_object_findable(self, session: Session):
        galaxy = Galaxy(name="Findable Galaxy")
        repo = GalaxyRepo(session)
        repo.add(galaxy)

        found = repo.get(galaxy.id)
        assert found.name == "Findable Galaxy"


class TestRepoGet:
    def test_get_returns_object(self, session: Session):
        galaxy = Galaxy(name="Pinwheel")
        session.add(galaxy)
        session.flush()

        repo = GalaxyRepo(session)
        result = repo.get(galaxy.id)
        assert result.name == "Pinwheel"

    def test_get_raises_not_found(self, session: Session):
        repo = GalaxyRepo(session)
        with pytest.raises(NotFound):
            repo.get(999999)


class TestRepoList:
    def test_list_returns_all(self, session: Session):
        session.add(Galaxy(name="Sombrero"))
        session.add(Galaxy(name="Triangulum"))
        session.flush()

        repo = GalaxyRepo(session)
        rows, next_cursor, count = repo.list()

        names = {r.name for r in rows}
        assert "Sombrero" in names
        assert "Triangulum" in names
        assert count >= 2
        assert next_cursor is None

    def test_list_count_matches_rows(self, session: Session):
        repo = GalaxyRepo(session)
        rows, _, count = repo.list()
        assert len(rows) == count


class TestRepoGetWhere:
    def test_custom_get_where_filters_get(self, session: Session):
        star = Star(name="Hidden Star")
        session.add(star)
        session.flush()

        _StarBaseRepo = build_sqlalchemy_repo(Star)

        class NameFilteredRepo(_StarBaseRepo):
            def get_where(self, method):
                return [Star.name == "Gazorbo"]

        repo = NameFilteredRepo(session)
        with pytest.raises(NotFound):
            repo.get(star.id)

    def test_custom_get_where_filters_list(self, session: Session):
        session.add(Star(name="GazorboStar"))
        session.add(Star(name="OtherStar"))
        session.flush()

        _StarBaseRepo = build_sqlalchemy_repo(Star)

        class NameFilteredRepo(_StarBaseRepo):
            def get_where(self, method):
                return [Star.name == "GazorboStar"]

        repo = NameFilteredRepo(session)
        rows, _, count = repo.list()
        assert all(r.name == "GazorboStar" for r in rows)
        assert count == 1


class TestRepoContextPropagation:
    def test_context_available_in_get_where(self, session: Session):
        _StarBaseRepo = build_sqlalchemy_repo(Star)

        class ContextAwareRepo(_StarBaseRepo):
            def get_where(self, method):
                if name := self.context.get("only_name"):
                    return [Star.name == name]
                return []

        star = Star(name="ContextStar")
        session.add(star)
        session.flush()

        repo = ContextAwareRepo(session, context={"only_name": "ContextStar"})
        rows, _, count = repo.list()
        assert count == 1
        assert rows[0].name == "ContextStar"

    def test_empty_context_returns_all(self, session: Session):
        _StarBaseRepo = build_sqlalchemy_repo(Star)

        class ContextAwareRepo(_StarBaseRepo):
            def get_where(self, method):
                if name := self.context.get("only_name"):
                    return [Star.name == name]
                return []

        session.add(Star(name="StarA"))
        session.add(Star(name="StarB"))
        session.flush()

        repo = ContextAwareRepo(session, context={})
        _, _, count = repo.list()
        assert count >= 2
