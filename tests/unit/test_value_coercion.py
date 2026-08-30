"""Unit tests for the shared value coercion helpers (issue #1008).

`coerce_value_for_type` was extracted from `obs/api/v1/datapoints.py`;
`coerce_text_value_for_type` is the new text-parsing variant used by the
Zeitschaltuhr adapter and the binding save validation.
"""

from __future__ import annotations

import datetime

import pytest

from obs.models.types import (
    coerce_text_value_for_type,
    coerce_value_for_type,
    parse_text_value_heuristic,
)

# ---------------------------------------------------------------------------
# coerce_value_for_type — typed input
# ---------------------------------------------------------------------------


class TestCoerceValueForType:
    def test_unknown_passes_through_unchanged(self):
        sentinel = object()
        assert coerce_value_for_type(sentinel, "UNKNOWN") is sentinel

    def test_unregistered_type_falls_back_to_unknown(self):
        assert coerce_value_for_type("anything", "NOT_A_TYPE") == "anything"

    def test_matching_type_returned_as_is(self):
        assert coerce_value_for_type(3.5, "FLOAT") == 3.5
        assert coerce_value_for_type("x", "STRING") == "x"
        assert coerce_value_for_type(True, "BOOLEAN") is True

    def test_bool_to_integer(self):
        assert coerce_value_for_type(True, "INTEGER") == 1
        assert not isinstance(coerce_value_for_type(True, "INTEGER"), bool)

    def test_int_to_float(self):
        assert coerce_value_for_type(5, "FLOAT") == 5.0

    def test_bool_is_not_silently_accepted_as_float(self):
        with pytest.raises(ValueError):
            coerce_value_for_type(True, "FLOAT")

    def test_integral_float_to_integer(self):
        assert coerce_value_for_type(7.0, "INTEGER") == 7

    def test_fractional_float_rejected_for_integer(self):
        with pytest.raises(ValueError):
            coerce_value_for_type(7.5, "INTEGER")

    def test_int_to_boolean(self):
        assert coerce_value_for_type(1, "BOOLEAN") is True
        assert coerce_value_for_type(0, "BOOLEAN") is False

    def test_iso_strings_to_temporal_types(self):
        assert coerce_value_for_type("2026-12-24", "DATE") == datetime.date(2026, 12, 24)
        assert coerce_value_for_type("08:00:00", "TIME") == datetime.time(8, 0)
        assert coerce_value_for_type("2026-12-24T08:00:00", "DATETIME") == datetime.datetime.fromisoformat("2026-12-24T08:00:00")

    def test_invalid_iso_strings_rejected(self):
        for data_type in ("DATE", "TIME", "DATETIME"):
            with pytest.raises(ValueError):
                coerce_value_for_type("nope", data_type)

    def test_incompatible_value_rejected(self):
        with pytest.raises(ValueError):
            coerce_value_for_type("abc", "INTEGER")


# ---------------------------------------------------------------------------
# parse_text_value_heuristic — UNKNOWN fallback
# ---------------------------------------------------------------------------


class TestParseTextValueHeuristic:
    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "on", "ein", " 1 "])
    def test_true_literals(self, raw):
        assert parse_text_value_heuristic(raw) is True

    @pytest.mark.parametrize("raw", ["0", "false", "off", "aus"])
    def test_false_literals(self, raw):
        assert parse_text_value_heuristic(raw) is False

    @pytest.mark.parametrize("raw", ["yes", "no", "ja", "nein", "YES", "Nein"])
    def test_boolean_aliases_added_for_typed_targets_stay_strings(self, raw):
        """Codex review on PR #1155.

        ``yes``/``ja``/``no``/``nein`` are accepted for an explicitly typed BOOLEAN
        target, but must not reinterpret an UNKNOWN datapoint: a timer that has always
        emitted the command text stays a string, so downstream MQTT/protocol consumers
        see the same value after the upgrade as before it.
        """
        assert parse_text_value_heuristic(raw) == raw.strip()

    def test_integer(self):
        assert parse_text_value_heuristic("50") == 50

    def test_float(self):
        assert parse_text_value_heuristic("21.5") == 21.5

    def test_plain_string(self):
        assert parse_text_value_heuristic(" hello ") == "hello"


# ---------------------------------------------------------------------------
# coerce_text_value_for_type — the issue #1008 core
# ---------------------------------------------------------------------------


class TestCoerceTextValueBoolean:
    @pytest.mark.parametrize("raw", ["1", "true", "True", "on", "ein", "yes", "ja"])
    def test_true_literals(self, raw):
        assert coerce_text_value_for_type(raw, "BOOLEAN") is True

    @pytest.mark.parametrize("raw", ["0", "false", "off", "aus", "no", "nein"])
    def test_false_literals(self, raw):
        assert coerce_text_value_for_type(raw, "BOOLEAN") is False

    def test_whitespace_is_tolerated(self):
        assert coerce_text_value_for_type("  on  ", "BOOLEAN") is True

    @pytest.mark.parametrize("raw", ["", "50", "vielleicht"])
    def test_invalid_literal_rejected(self, raw):
        with pytest.raises(ValueError, match="BOOLEAN"):
            coerce_text_value_for_type(raw, "BOOLEAN")


class TestCoerceTextValueNumeric:
    def test_integer_zero_and_one_stay_numeric(self):
        for raw, expected in (("0", 0), ("1", 1)):
            result = coerce_text_value_for_type(raw, "INTEGER")
            assert result == expected
            assert not isinstance(result, bool)

    def test_integer_value(self):
        assert coerce_text_value_for_type("50", "INTEGER") == 50

    def test_integer_negative(self):
        assert coerce_text_value_for_type("-3", "INTEGER") == -3

    def test_integer_accepts_integral_float_literal(self):
        assert coerce_text_value_for_type("50.0", "INTEGER") == 50

    def test_integer_rejects_fractional_literal(self):
        with pytest.raises(ValueError, match="fractional"):
            coerce_text_value_for_type("50.5", "INTEGER")

    def test_integer_accepts_boolean_literal_as_one_zero(self):
        assert coerce_text_value_for_type("on", "INTEGER") == 1
        assert coerce_text_value_for_type("aus", "INTEGER") == 0

    def test_integer_rejects_garbage(self):
        with pytest.raises(ValueError, match="INTEGER"):
            coerce_text_value_for_type("abc", "INTEGER")

    def test_float_zero_and_one_stay_numeric(self):
        for raw, expected in (("0", 0.0), ("1", 1.0)):
            result = coerce_text_value_for_type(raw, "FLOAT")
            assert result == expected
            assert isinstance(result, float)

    def test_float_fractional(self):
        assert coerce_text_value_for_type("50.5", "FLOAT") == 50.5

    def test_float_accepts_boolean_literal(self):
        assert coerce_text_value_for_type("ein", "FLOAT") == 1.0
        assert coerce_text_value_for_type("off", "FLOAT") == 0.0

    def test_float_rejects_garbage(self):
        with pytest.raises(ValueError, match="FLOAT"):
            coerce_text_value_for_type("abc", "FLOAT")

    @pytest.mark.parametrize("raw", ["inf", "-inf", "Infinity", "nan", "NaN", "1e999"])
    def test_float_rejects_non_finite_values(self, raw):
        """nan/inf serialize to the invalid JSON literals NaN/Infinity on the MQTT topic."""
        with pytest.raises(ValueError, match="FLOAT"):
            coerce_text_value_for_type(raw, "FLOAT")

    @pytest.mark.parametrize("raw", ["inf", "-inf", "Infinity", "nan", "NaN", "1e999"])
    def test_integer_rejects_non_finite_values(self, raw):
        """int(inf) raises OverflowError, which callers do not catch — reject as ValueError."""
        with pytest.raises(ValueError, match="INTEGER"):
            coerce_text_value_for_type(raw, "INTEGER")

    def test_scientific_notation_is_accepted(self):
        assert coerce_text_value_for_type("1e3", "INTEGER") == 1000
        assert coerce_text_value_for_type("1.5e2", "FLOAT") == 150.0

    @pytest.mark.parametrize("raw", ["1.0000000000000001", "1.55e1", "0.1", "1e-3"])
    def test_integer_rejects_decimals_binary_float_would_round_to_integral(self, raw):
        """Codex review on PR #1155 — integrality is judged on the typed text.

        ``float('1.0000000000000001')`` is exactly ``1.0``, so a binary-float check
        would call the value integral and publish ``1`` for a lossy conversion.
        """
        with pytest.raises(ValueError, match="fractional part"):
            coerce_text_value_for_type(raw, "INTEGER")

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("9007199254740993.0", 9007199254740993), ("1000e-3", 1), ("1.5e1", 15), ("5.", 5)],
    )
    def test_integer_keeps_full_precision_of_integral_decimals(self, raw, expected):
        """``int(float('9007199254740993.0'))`` loses the last digit; Decimal does not."""
        assert coerce_text_value_for_type(raw, "INTEGER") == expected


class TestCoerceTextValueString:
    @pytest.mark.parametrize("raw", ["on", "off", "1", "0", "true", "ein", "50", ""])
    def test_string_is_taken_verbatim(self, raw):
        assert coerce_text_value_for_type(raw, "STRING") == raw

    def test_string_preserves_whitespace(self):
        assert coerce_text_value_for_type("  Hallo  ", "STRING") == "  Hallo  "


class TestCoerceTextValueTemporal:
    def test_date(self):
        assert coerce_text_value_for_type("2026-12-24", "DATE") == datetime.date(2026, 12, 24)

    def test_time(self):
        assert coerce_text_value_for_type("08:00:00", "TIME") == datetime.time(8, 0)

    def test_time_without_seconds(self):
        assert coerce_text_value_for_type("08:00", "TIME") == datetime.time(8, 0)

    def test_datetime(self):
        assert coerce_text_value_for_type("2026-12-24T08:00:00", "DATETIME") == datetime.datetime.fromisoformat("2026-12-24T08:00:00")

    @pytest.mark.parametrize("data_type", ["DATE", "TIME", "DATETIME"])
    def test_invalid_iso_rejected(self, data_type):
        with pytest.raises(ValueError, match="ISO 8601"):
            coerce_text_value_for_type("1", data_type)


class TestCoerceTextValueUnknown:
    def test_unknown_uses_heuristic(self):
        assert coerce_text_value_for_type("on", "UNKNOWN") is True
        assert coerce_text_value_for_type("50", "UNKNOWN") == 50
        assert coerce_text_value_for_type("hi", "UNKNOWN") == "hi"

    def test_unregistered_type_uses_heuristic(self):
        assert coerce_text_value_for_type("50", "NOT_A_TYPE") == 50


class TestCoerceTextValueCustomType:
    def test_custom_registered_type_falls_back_to_generic_coercion(self):
        from obs.models.types import DataTypeDefinition, DataTypeRegistry

        DataTypeRegistry.register(
            DataTypeDefinition(
                name="TEST_CUSTOM_1008",
                python_type=str,
                mqtt_serializer=str,
                mqtt_deserializer=str,
            ),
        )
        try:
            assert coerce_text_value_for_type("  abc  ", "TEST_CUSTOM_1008") == "abc"
        finally:
            DataTypeRegistry._types.pop("TEST_CUSTOM_1008", None)

    def test_custom_type_rejects_incompatible_value(self):
        from obs.models.types import DataTypeDefinition, DataTypeRegistry

        DataTypeRegistry.register(
            DataTypeDefinition(
                name="TEST_CUSTOM_1008_BYTES",
                python_type=bytearray,
                mqtt_serializer=str,
                mqtt_deserializer=str,
            ),
        )
        try:
            with pytest.raises(ValueError, match="not compatible"):
                coerce_text_value_for_type("abc", "TEST_CUSTOM_1008_BYTES")
        finally:
            DataTypeRegistry._types.pop("TEST_CUSTOM_1008_BYTES", None)


# ---------------------------------------------------------------------------
# Cross-implementation parity fixture (issue #1008)
# ---------------------------------------------------------------------------


class TestParityFixture:
    """Pin the backend against `tests/fixtures/timer_value_parity.json`.

    The same fixture is asserted by `gui/tests/utils/timerValue.spec.js` and
    `frontend/src/utils/timerValue.test.ts`, so a change to any one of the three
    implementations that breaks the shared contract fails a test somewhere.
    Regenerate with `tools/gen-timer-value-parity-fixture.py`.
    """

    @staticmethod
    def _fixture():
        import json
        import pathlib

        path = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "timer_value_parity.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_fixture_matches_current_behaviour(self):
        fx = self._fixture()
        mismatches = []
        for data_type in fx["types"]:
            for value, expected in zip(fx["values"], fx["backendValid"][data_type], strict=True):
                try:
                    coerce_text_value_for_type(value, data_type)
                    actual = True
                except ValueError:
                    actual = False
                if actual != expected:
                    mismatches.append((data_type, value, expected, actual))
        assert not mismatches, f"fixture is stale — regenerate it: {mismatches}"

    def test_only_value_error_escapes(self):
        """Callers catch ValueError only; anything else is a 500 / unhandled adapter error."""
        fx = self._fixture()
        for data_type in fx["types"]:
            for value in fx["values"]:
                try:
                    coerce_text_value_for_type(value, data_type)
                except ValueError:
                    pass
                except Exception as exc:  # pragma: no cover - fails the test
                    raise AssertionError(f"{type(exc).__name__} for {value!r} as {data_type}") from exc

    def test_fixture_covers_every_type(self):
        fx = self._fixture()
        assert set(fx["backendValid"]) == set(fx["types"])
        assert len(fx["values"]) > 50
