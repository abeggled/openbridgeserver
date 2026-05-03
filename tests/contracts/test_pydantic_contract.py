"""Contract tests for pydantic v2 — verifies the API surface used throughout obs.

OBS uses pydantic v2 exclusively. The key v2 APIs that would silently break if the
library is downgraded to v1 or upgraded to a hypothetical v3 with API changes:
  - BaseModel.model_dump() (v1: .dict())
  - BaseModel.model_validate() (v1: .parse_obj())
  - field_validator(mode="before"|"after") (v1: @validator)
  - model_validator(mode="after") (v1: @root_validator)
  - Field(default, default_factory, max_length, description)
  - model_config = {...} (v1: class Config:)
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field, field_validator, model_validator


class _SimpleModel(BaseModel):
    name: str
    value: float = 0.0


class _ValidatedModel(BaseModel):
    items: list[str] = Field(default_factory=list)

    @field_validator("items", mode="before")
    @classmethod
    def _coerce_items(cls, v):
        if isinstance(v, str):
            return [v]
        return v


class _AfterValidatorModel(BaseModel):
    first: str
    last: str
    full: str = ""

    @model_validator(mode="after")
    def _set_full(self) -> "_AfterValidatorModel":
        if not self.full:
            self.full = f"{self.first} {self.last}"
        return self


class TestBaseModel:
    def test_instantiation(self):
        m = _SimpleModel(name="test", value=1.5)
        assert m.name == "test"
        assert m.value == 1.5

    def test_model_dump_exists(self):
        m = _SimpleModel(name="test")
        assert hasattr(m, "model_dump"), (
            "pydantic.BaseModel no longer has model_dump(). "
            "This is the pydantic v2 API; v1 used .dict()."
        )

    def test_model_dump_returns_dict(self):
        m = _SimpleModel(name="test", value=2.5)
        d = m.model_dump()
        assert isinstance(d, dict)
        assert d["name"] == "test"
        assert d["value"] == 2.5

    def test_model_validate_exists(self):
        assert hasattr(_SimpleModel, "model_validate"), (
            "pydantic.BaseModel no longer has model_validate(). "
            "This is the pydantic v2 API; v1 used .parse_obj()."
        )

    def test_model_validate_from_dict(self):
        m = _SimpleModel.model_validate({"name": "validated", "value": 3.0})
        assert m.name == "validated"

    def test_validation_error_on_wrong_type(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _SimpleModel(name=123, value="not_a_float_dict_object")  # type: ignore[arg-type]


class TestField:
    def test_default_factory(self):
        m = _ValidatedModel()
        assert m.items == []

    def test_max_length_enforced(self):
        from pydantic import ValidationError

        class _MaxLen(BaseModel):
            name: str = Field(max_length=5)

        with pytest.raises(ValidationError):
            _MaxLen(name="toolongstring")

    def test_description_does_not_break(self):
        class _WithDesc(BaseModel):
            name: str = Field(description="The name of the item")

        m = _WithDesc(name="ok")
        assert m.name == "ok"


class TestFieldValidator:
    def test_before_validator_coerces(self):
        m = _ValidatedModel(items="single")
        assert m.items == ["single"]

    def test_before_validator_passes_list(self):
        m = _ValidatedModel(items=["a", "b"])
        assert m.items == ["a", "b"]


class TestModelValidator:
    def test_after_validator_sets_derived_field(self):
        m = _AfterValidatorModel(first="John", last="Doe")
        assert m.full == "John Doe"

    def test_after_validator_not_overridden_if_set(self):
        m = _AfterValidatorModel(first="John", last="Doe", full="Override")
        assert m.full == "Override"


class TestModelConfig:
    def test_from_attributes_config(self):
        class _OrmModel(BaseModel):
            id: int
            name: str

            model_config = {"from_attributes": True}

        class _FakeOrm:
            id = 1
            name = "orm_object"

        m = _OrmModel.model_validate(_FakeOrm())
        assert m.id == 1
        assert m.name == "orm_object"
