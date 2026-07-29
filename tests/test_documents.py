"""Tests for the request half of the JSON:API contract."""
import dataclasses
from datetime import datetime
from typing import Optional

import pytest
from pydantic import ValidationError as PydanticValidationError

from cosmic import Command, Envelope, input_model


@dataclasses.dataclass(frozen=True)
class CreateThing(Command):
    name: str
    starts_at: Optional[datetime] = None
    count: int = 0


class TestInputModel:
    def test_required_and_defaulted_fields_survive(self):
        Model = input_model(CreateThing)
        parsed = Model(name="x")
        assert parsed.name == "x"
        assert parsed.count == 0

        with pytest.raises(PydanticValidationError):
            Model()  # `name` has no default on the command either

    def test_pydantic_coerces_to_the_command_types(self):
        Model = input_model(CreateThing)
        parsed = Model(name="x", starts_at="2026-01-01T12:00:00", count="3")
        assert parsed.starts_at == datetime(2026, 1, 1, 12, 0, 0)
        assert parsed.count == 3

    def test_exclude_drops_fields(self):
        Model = input_model(CreateThing, exclude=("count",))
        assert "count" not in Model.model_fields

    def test_only_narrows_to_the_named_fields(self):
        Model = input_model(CreateThing, only={"name"}, name="Narrow")
        assert set(Model.model_fields) == {"name"}
        assert Model.__name__ == "Narrow"

    def test_only_naming_an_absent_field_is_an_error(self):
        """Otherwise a renamed command field silently shrinks the endpoint."""
        with pytest.raises(ValueError, match="nope"):
            input_model(CreateThing, only={"name", "nope"})

    def test_optional_makes_every_field_unset_able(self):
        """The partial-update shape: absent means 'leave alone'."""
        Model = input_model(CreateThing, optional=True)
        parsed = Model(name="x")
        assert parsed.model_dump(exclude_unset=True) == {"name": "x"}

    def test_optional_still_distinguishes_explicit_null_from_absent(self):
        Model = input_model(CreateThing, optional=True)
        assert Model(starts_at=None).model_dump(exclude_unset=True) == {
            "starts_at": None
        }
        assert Model().model_dump(exclude_unset=True) == {}


class TestEnvelope:
    def test_parses_the_nested_write_body(self):
        body = Envelope[input_model(CreateThing)].model_validate(
            {"data": {"attributes": {"name": "party", "count": 2}}}
        )
        assert body.data.attributes.name == "party"
        assert body.data.attributes.count == 2

    def test_a_bare_attributes_body_is_rejected(self):
        """The envelope is the contract; accepting both shapes hides client bugs."""
        with pytest.raises(PydanticValidationError):
            Envelope[input_model(CreateThing)].model_validate({"name": "party"})
