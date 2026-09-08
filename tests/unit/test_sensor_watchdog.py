"""Unit tests for the sensor_watchdog node (staleness monitor for N inputs).

Covers:
  - fresh passthrough while values arrive within each input's timeout
  - transition to fault_value + fault_text + fault_trigger once a timeout elapses
    with no new event (simulated by back-dating the persisted "last_seen" state,
    not by sleeping)
  - no re-pulse of fault_trigger while an input stays stale across multiple ticks
  - recovery when a value arrives again after staleness
  - independent per-input timeouts within the same node instance
  - malformed/edge-case config (missing baseline, degenerate timeout, JSON-string
    config) is handled without raising
"""

from __future__ import annotations

import datetime

from tests.unit.conftest import make_executor, node

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(data: dict, inputs: dict, state: dict | None = None) -> tuple[dict, dict]:
    """Execute a single sensor_watchdog node and return (outputs, state)."""
    if state is None:
        state = {}
    n = node("w", "sensor_watchdog", data)
    exc = make_executor([n], hysteresis_state=state)
    out = exc.execute({"w": inputs}).get("w", {})
    return out, state


def _backdate(state: dict, input_index: int, seconds_ago: float) -> None:
    """Simulate elapsed wall-clock time by rewriting the persisted last_seen timestamp."""
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=seconds_ago)
    state["w"]["last_seen"][str(input_index)] = past.isoformat()


_ONE_INPUT = [{"label": "Sensor A", "timeout_s": 10, "fault_value": "OFFLINE"}]
_TWO_INPUTS = [
    {"label": "Sensor A", "timeout_s": 10, "fault_value": "OFFLINE_A"},
    {"label": "Sensor B", "timeout_s": 20, "fault_value": "OFFLINE_B"},
]


# ===========================================================================
# Fresh passthrough
# ===========================================================================


class TestFreshPassthrough:
    def test_fresh_value_is_passed_through(self):
        out, _ = _run({"inputs": _ONE_INPUT}, {"in_1": 42})
        assert out["out_1"] == 42

    def test_no_fault_outputs_on_first_fresh_tick(self):
        out, _ = _run({"inputs": _ONE_INPUT}, {"in_1": 42})
        assert "fault_text" not in out
        assert "fault_trigger" not in out

    def test_none_input_this_tick_omits_the_output_key(self):
        # Baseline just seeded (within timeout), no fresh value this tick →
        # nothing to report for out_1 (the "omit = nothing sent" contract).
        out, _ = _run({"inputs": _ONE_INPUT}, {})
        assert "out_1" not in out

    def test_missing_baseline_does_not_raise_and_is_not_immediately_stale(self):
        # No prior state at all (e.g. init-pass excluded, first-ever tick) —
        # must seed "now" defensively instead of treating it as infinitely stale.
        out, state = _run({"inputs": _ONE_INPUT}, {})
        assert "fault_trigger" not in out
        assert state["w"]["last_seen"]["1"]


# ===========================================================================
# Staleness onset
# ===========================================================================


class TestStalenessOnset:
    def test_elapsed_timeout_with_no_new_event_switches_to_fault_value(self):
        _, state = _run({"inputs": _ONE_INPUT}, {"in_1": 1})
        _backdate(state, 1, seconds_ago=11)  # timeout_s=10
        out, _ = _run({"inputs": _ONE_INPUT}, {}, state=state)
        assert out["out_1"] == "OFFLINE"

    def test_onset_fires_fault_text_and_trigger_exactly_once(self):
        _, state = _run({"inputs": _ONE_INPUT}, {"in_1": 1})
        _backdate(state, 1, seconds_ago=11)
        out, _ = _run({"inputs": _ONE_INPUT}, {}, state=state)
        assert out["fault_trigger"] is True
        assert out["fault_text"] == "Keine Daten von Sensor A"

    def test_still_within_timeout_does_not_fault(self):
        _, state = _run({"inputs": _ONE_INPUT}, {"in_1": 1})
        _backdate(state, 1, seconds_ago=5)  # timeout_s=10
        out, _ = _run({"inputs": _ONE_INPUT}, {}, state=state)
        assert "out_1" not in out
        assert "fault_trigger" not in out

    def test_unnamed_input_falls_back_to_generic_label(self):
        cfg = [{"timeout_s": 10, "fault_value": "X"}]
        _, state = _run({"inputs": cfg}, {"in_1": 1})
        _backdate(state, 1, seconds_ago=11)
        out, _ = _run({"inputs": cfg}, {}, state=state)
        assert out["fault_text"] == "Keine Daten von Eingang 1"


# ===========================================================================
# No re-pulse while sustained stale
# ===========================================================================


class TestSustainedStaleness:
    def test_fault_trigger_does_not_repeat_on_the_following_stale_tick(self):
        _, state = _run({"inputs": _ONE_INPUT}, {"in_1": 1})
        _backdate(state, 1, seconds_ago=11)
        out1, _ = _run({"inputs": _ONE_INPUT}, {}, state=state)
        assert out1["fault_trigger"] is True

        out2, _ = _run({"inputs": _ONE_INPUT}, {}, state=state)
        assert "fault_trigger" not in out2
        assert "fault_text" not in out2

    def test_fault_value_keeps_being_reasserted_every_stale_tick(self):
        _, state = _run({"inputs": _ONE_INPUT}, {"in_1": 1})
        _backdate(state, 1, seconds_ago=11)
        _run({"inputs": _ONE_INPUT}, {}, state=state)

        out2, _ = _run({"inputs": _ONE_INPUT}, {}, state=state)
        assert out2["out_1"] == "OFFLINE"


# ===========================================================================
# Recovery
# ===========================================================================


class TestRecovery:
    def test_recovery_passes_through_new_value_and_clears_fault(self):
        _, state = _run({"inputs": _ONE_INPUT}, {"in_1": 1})
        _backdate(state, 1, seconds_ago=11)
        _run({"inputs": _ONE_INPUT}, {}, state=state)

        out, _ = _run({"inputs": _ONE_INPUT}, {"in_1": 99}, state=state)
        assert out["out_1"] == 99
        assert "fault_trigger" not in out
        assert "fault_text" not in out

    def test_renewed_staleness_after_recovery_fires_a_new_trigger(self):
        _, state = _run({"inputs": _ONE_INPUT}, {"in_1": 1})
        _backdate(state, 1, seconds_ago=11)
        _run({"inputs": _ONE_INPUT}, {}, state=state)  # onset
        _run({"inputs": _ONE_INPUT}, {"in_1": 2}, state=state)  # recovery

        _backdate(state, 1, seconds_ago=11)
        out, _ = _run({"inputs": _ONE_INPUT}, {}, state=state)  # onset again
        assert out["fault_trigger"] is True


# ===========================================================================
# Independent per-input timeouts
# ===========================================================================


class TestIndependentTimeouts:
    def test_one_stale_one_fresh_in_the_same_node(self):
        _, state = _run({"inputs": _TWO_INPUTS}, {"in_1": 1, "in_2": 2})
        _backdate(state, 1, seconds_ago=11)  # A's timeout_s=10 → stale
        # B's timeout_s=20, only 11s elapsed → still fresh
        out, _ = _run({"inputs": _TWO_INPUTS}, {}, state=state)
        assert out["out_1"] == "OFFLINE_A"
        assert "out_2" not in out
        assert out["fault_text"] == "Keine Daten von Sensor A"

    def test_second_input_faulting_later_fires_its_own_trigger(self):
        _, state = _run({"inputs": _TWO_INPUTS}, {"in_1": 1, "in_2": 2})
        _backdate(state, 1, seconds_ago=11)
        _run({"inputs": _TWO_INPUTS}, {}, state=state)  # A onset, B still fresh

        _backdate(state, 2, seconds_ago=21)  # B's timeout_s=20
        out, _ = _run({"inputs": _TWO_INPUTS}, {}, state=state)
        assert out["out_2"] == "OFFLINE_B"
        assert out["fault_text"] == "Keine Daten von Sensor B"
        assert out["fault_trigger"] is True

    def test_lowest_index_wins_when_two_inputs_go_stale_the_same_tick(self):
        _, state = _run({"inputs": _TWO_INPUTS}, {"in_1": 1, "in_2": 2})
        _backdate(state, 1, seconds_ago=11)
        _backdate(state, 2, seconds_ago=21)
        out, _ = _run({"inputs": _TWO_INPUTS}, {}, state=state)
        assert out["fault_text"] == "Keine Daten von Sensor A"
        assert out["out_1"] == "OFFLINE_A"
        assert out["out_2"] == "OFFLINE_B"


# ===========================================================================
# Config edge cases
# ===========================================================================


class TestConfigEdgeCases:
    def test_degenerate_zero_timeout_is_clamped_to_at_least_one_second(self):
        cfg = [{"timeout_s": 0, "fault_value": "X"}]
        out, _ = _run({"inputs": cfg}, {"in_1": 1})
        # A fresh arrival this exact tick must never immediately read as stale.
        assert out["out_1"] == 1
        assert "fault_trigger" not in out

    def test_missing_inputs_config_defaults_to_a_single_input(self):
        out, _ = _run({}, {"in_1": 5})
        assert out["out_1"] == 5

    def test_inputs_config_as_json_string_is_parsed(self):
        import json

        cfg_json = json.dumps(_ONE_INPUT)
        out, _ = _run({"inputs": cfg_json}, {"in_1": 7})
        assert out["out_1"] == 7

    def test_more_than_ten_inputs_are_clamped(self):
        cfg = [{"timeout_s": 10, "fault_value": i} for i in range(15)]
        inputs = {f"in_{i}": i for i in range(1, 16)}
        out, _ = _run({"inputs": cfg}, inputs)
        assert "out_11" not in out
        assert out["out_10"] == 10
