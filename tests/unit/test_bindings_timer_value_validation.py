"""Unit tests for `_validate_timer_output_value` (issue #1008).

The reachable paths are covered end-to-end in `tests/integration/test_bindings_api.py`;
these tests pin the registry-lookup edge cases that an API request cannot produce.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from obs.api.v1.bindings import _validate_timer_output_value


def _registry(dp):
    registry = MagicMock()
    registry.get.return_value = dp
    return registry


def _validate(config, dp, adapter_type="ZEITSCHALTUHR"):
    with patch("obs.api.v1.bindings.get_registry", return_value=_registry(dp)):
        _validate_timer_output_value(adapter_type, config, uuid.uuid4())


class TestValidateTimerOutputValue:
    def test_unknown_datapoint_is_skipped(self):
        """A binding whose DataPoint is no longer in the registry must not 422."""
        _validate({"timer_type": "daily", "value": "abc"}, None)

    def test_other_adapter_type_is_skipped(self):
        _validate({"value": "abc"}, SimpleNamespace(data_type="FLOAT"), adapter_type="KNX")

    def test_omitted_value_is_validated_against_the_adapter_default(self):
        """Codex review on PR #1155 — an omitted value is not "no value".

        Both routes store the config as sent and the adapter fills in its own default
        ("1") when the point fires, so a DATE target has to be rejected here instead of
        having the event dropped at every firing.
        """
        with pytest.raises(HTTPException) as exc:
            _validate({"timer_type": "daily"}, SimpleNamespace(data_type="DATE"))
        assert exc.value.status_code == 422
        assert "DATE" in exc.value.detail

    def test_omitted_value_passes_when_the_default_fits_the_target(self):
        """The default is a valid BOOLEAN/numeric literal — only temporal types reject it."""
        _validate({"timer_type": "daily"}, SimpleNamespace(data_type="BOOLEAN"))
        _validate({"timer_type": "daily"}, SimpleNamespace(data_type="FLOAT"))

    def test_omitted_value_on_a_meta_binding_is_still_skipped(self):
        """A meta binding publishes its metadata, never the switching value."""
        _validate({"timer_type": "meta"}, SimpleNamespace(data_type="DATE"))

    def test_meta_binding_is_skipped(self):
        _validate({"timer_type": "meta", "value": "abc"}, SimpleNamespace(data_type="FLOAT"))

    def test_default_timer_type_is_validated(self):
        """An omitted timer_type defaults to 'daily' and is therefore checked."""
        with pytest.raises(HTTPException) as exc:
            _validate({"value": "abc"}, SimpleNamespace(data_type="FLOAT"))
        assert exc.value.status_code == 422

    def test_compatible_value_passes(self):
        _validate({"timer_type": "daily", "value": "50"}, SimpleNamespace(data_type="FLOAT"))

    def test_incompatible_value_raises_422_naming_the_type(self):
        with pytest.raises(HTTPException) as exc:
            _validate({"timer_type": "daily", "value": "50"}, SimpleNamespace(data_type="BOOLEAN"))
        assert exc.value.status_code == 422
        assert "BOOLEAN" in exc.value.detail

    def test_non_string_value_is_stringified_before_parsing(self):
        """A GUI that sends a JSON number must still be validated, not crash."""
        _validate({"timer_type": "daily", "value": 50}, SimpleNamespace(data_type="FLOAT"))
