"""
Unit tests for the avg_multi node (Mittelwert / Gleitender Mittelwert)
and the persist_state toggle in the manager's persistence logic.

Covers:
  - avg_multi: aktueller Mittelwert aus 2–20 Eingängen
  - avg_multi: gleitende Mittelwerte (alle Zeitfenster)
  - avg_multi: Zustand akkumuliert sich über mehrere Ausführungen
  - avg_multi: None-Eingänge werden korrekt ignoriert
  - avg_multi: Puffer wird auf 365 Tage begrenzt
  - persist_state: False → Node-Zustand wird aus dem zu persistierenden Snapshot ausgeschlossen
  - persist_state: True (Standard) → Node-Zustand wird persistiert
"""
from __future__ import annotations

import datetime
import pytest

from obs.logic.executor import GraphExecutor
from tests.unit.conftest import make_executor, node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_avg(data: dict, inputs: dict, state: dict | None = None) -> dict:
    """Execute a single avg_multi node and return its outputs."""
    if state is None:
        state = {}
    n = node("a", "avg_multi", data)
    exc = make_executor([n], hysteresis_state=state)
    return exc.execute({"a": inputs}).get("a", {}), state


# ===========================================================================
# avg_multi — aktueller Mittelwert
# ===========================================================================

class TestAvgMultiCurrentAverage:
    def test_two_equal_inputs(self):
        out, _ = _run_avg({"input_count": 2}, {"in_1": 10.0, "in_2": 10.0})
        assert out["avg"] == pytest.approx(10.0)

    def test_two_different_inputs(self):
        out, _ = _run_avg({"input_count": 2}, {"in_1": 10.0, "in_2": 20.0})
        assert out["avg"] == pytest.approx(15.0)

    def test_three_inputs(self):
        out, _ = _run_avg({"input_count": 3}, {"in_1": 10.0, "in_2": 20.0, "in_3": 30.0})
        assert out["avg"] == pytest.approx(20.0)

    def test_none_inputs_ignored(self):
        # Only in_1 is connected; in_2 is None → avg of [10.0] = 10.0
        out, _ = _run_avg({"input_count": 2}, {"in_1": 10.0, "in_2": None})
        assert out["avg"] == pytest.approx(10.0)

    def test_all_none_inputs_returns_none(self):
        out, _ = _run_avg({"input_count": 2}, {})
        assert out["avg"] is None

    def test_input_count_defaults_to_2(self):
        out, _ = _run_avg({}, {"in_1": 4.0, "in_2": 8.0})
        assert out["avg"] == pytest.approx(6.0)

    def test_input_count_clamped_to_20_max(self):
        # Even with input_count=99, executor must not raise
        inputs = {f"in_{i}": float(i) for i in range(1, 21)}
        out, _ = _run_avg({"input_count": 99}, inputs)
        assert out["avg"] is not None

    def test_negative_values(self):
        out, _ = _run_avg({"input_count": 2}, {"in_1": -10.0, "in_2": 10.0})
        assert out["avg"] == pytest.approx(0.0)

    def test_avg_rounded_to_6_decimals(self):
        out, _ = _run_avg({"input_count": 3}, {"in_1": 1.0, "in_2": 1.0, "in_3": 2.0})
        assert out["avg"] == pytest.approx(4.0 / 3.0, rel=1e-5)


# ===========================================================================
# avg_multi — Zeitfenster-Ausgänge
# ===========================================================================

class TestAvgMultiWindowOutputs:
    def test_all_window_outputs_present(self):
        out, _ = _run_avg({"input_count": 2}, {"in_1": 5.0, "in_2": 5.0})
        for key in ("avg", "avg_1m", "avg_1h", "avg_1d", "avg_7d",
                    "avg_14d", "avg_30d", "avg_180d", "avg_365d"):
            assert key in out, f"Output '{key}' missing"

    def test_first_value_appears_in_all_windows(self):
        out, _ = _run_avg({"input_count": 2}, {"in_1": 20.0, "in_2": 20.0})
        for key in ("avg_1m", "avg_1h", "avg_1d", "avg_7d",
                    "avg_14d", "avg_30d", "avg_180d", "avg_365d"):
            assert out[key] == pytest.approx(20.0), f"Window '{key}' should be 20.0"

    def test_no_inputs_all_windows_none(self):
        out, _ = _run_avg({"input_count": 2}, {})
        for key in ("avg_1m", "avg_1h", "avg_1d", "avg_7d",
                    "avg_14d", "avg_30d", "avg_180d", "avg_365d"):
            assert out[key] is None, f"Window '{key}' should be None when no inputs"

    def test_window_avg_is_mean_of_stored_samples(self):
        """After feeding 3 values, the 1-min window avg equals their mean."""
        state: dict = {}
        for v in [10.0, 20.0, 30.0]:
            out, _ = _run_avg({"input_count": 1}, {"in_1": v}, state)
        assert out["avg_1m"] == pytest.approx(20.0)  # (10+20+30)/3

    def test_old_samples_excluded_from_short_window(self):
        """Samples outside the window must not influence the result.

        We inject a sample with an old timestamp directly into the state
        buffer and verify the 1-minute window skips it.
        """
        state: dict = {}
        n = node("a", "avg_multi", {"input_count": 1})
        exc = make_executor([n], hysteresis_state=state)
        exc.execute({"a": {"in_1": 100.0}})

        # Backdate the one existing sample to 2 minutes ago
        old_ts = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=2)
        ).isoformat()
        state["a"]["samples"][0][0] = old_ts

        # New value = 10.0; the 1-min window must contain ONLY this sample
        exc2 = make_executor([n], hysteresis_state=state)
        out = exc2.execute({"a": {"in_1": 10.0}})["a"]
        assert out["avg_1m"] == pytest.approx(10.0)

    def test_old_samples_still_in_long_window(self):
        """A 2-min-old sample is outside avg_1m but inside avg_1h."""
        state: dict = {}
        n = node("a", "avg_multi", {"input_count": 1})
        exc = make_executor([n], hysteresis_state=state)
        exc.execute({"a": {"in_1": 100.0}})

        # Backdate to 2 minutes ago
        old_ts = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=2)
        ).isoformat()
        state["a"]["samples"][0][0] = old_ts

        exc2 = make_executor([n], hysteresis_state=state)
        out = exc2.execute({"a": {"in_1": 10.0}})["a"]
        # avg_1h covers 60 min → both samples included: (100+10)/2 = 55
        assert out["avg_1h"] == pytest.approx(55.0)
        # avg_1m only the recent one
        assert out["avg_1m"] == pytest.approx(10.0)


# ===========================================================================
# avg_multi — Puffer-Verwaltung
# ===========================================================================

class TestAvgMultiBuffer:
    def test_samples_accumulate_across_runs(self):
        state: dict = {}
        for _ in range(5):
            _run_avg({"input_count": 1}, {"in_1": 1.0}, state)
        assert len(state["a"]["samples"]) == 5

    def test_buffer_trimmed_to_365_days(self):
        """Very old samples (>365 days) must be removed."""
        state: dict = {"a": {"samples": []}}
        old_ts = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=400)
        ).isoformat()
        # Inject a sample that is 400 days old
        state["a"]["samples"].append([old_ts, 999.0])

        _run_avg({"input_count": 1}, {"in_1": 1.0}, state)
        # Old sample should be gone; only the new one remains
        assert len(state["a"]["samples"]) == 1
        assert state["a"]["samples"][0][1] == pytest.approx(1.0)

    def test_empty_state_initialised_correctly(self):
        state: dict = {}
        _run_avg({"input_count": 2}, {"in_1": 5.0, "in_2": 5.0}, state)
        assert "a" in state
        assert "samples" in state["a"]
        assert len(state["a"]["samples"]) == 1


# ===========================================================================
# persist_state toggle — manager-level filtering
# ===========================================================================

class TestPersistStateFiltering:
    """
    Tests for the manager's persist_state filtering logic.

    We test the filtering logic in isolation without running the full
    LogicManager stack by replicating the exact filter the manager applies:
        no_persist = {n.id for n in flow.nodes if n.data.get("persist_state") is False}
        state_to_save = {nid: s for nid, s in hyst.items() if nid not in no_persist}
    """

    def _apply_filter(self, flow_nodes: list[dict], hyst: dict) -> dict:
        """Simulate the manager's persist_state filter (same logic as manager.py)."""
        from obs.logic.models import FlowData
        flow = FlowData.model_validate({"nodes": flow_nodes, "edges": []})
        no_persist = {n.id for n in flow.nodes if n.data.get("persist_state") is False}
        return {nid: s for nid, s in hyst.items() if nid not in no_persist}

    def test_persist_state_true_included(self):
        n = node("n1", "statistics", {"persist_state": True})
        hyst = {"n1": {"s_count": 5}}
        result = self._apply_filter([n], hyst)
        assert "n1" in result

    def test_persist_state_false_excluded(self):
        n = node("n1", "statistics", {"persist_state": False})
        hyst = {"n1": {"s_count": 5}}
        result = self._apply_filter([n], hyst)
        assert "n1" not in result

    def test_persist_state_not_set_defaults_to_persist(self):
        # No persist_state key → defaults to persist (None is not False)
        n = node("n1", "statistics", {})
        hyst = {"n1": {"s_count": 3}}
        result = self._apply_filter([n], hyst)
        assert "n1" in result

    def test_mixed_nodes_selectively_excluded(self):
        n_keep = node("keep", "statistics", {"persist_state": True})
        n_skip = node("skip", "hysteresis", {"persist_state": False})
        n_dflt = node("dflt", "min_max_tracker", {})
        hyst = {
            "keep": {"s_count": 1},
            "skip": True,
            "dflt": {"abs_min": 5.0},
        }
        result = self._apply_filter([n_keep, n_skip, n_dflt], hyst)
        assert "keep" in result
        assert "skip" not in result
        assert "dflt" in result

    def test_avg_multi_persist_state_false_excluded(self):
        n = node("m", "avg_multi", {"persist_state": False})
        hyst = {"m": {"samples": [[datetime.datetime.now(datetime.timezone.utc).isoformat(), 42.0]]}}
        result = self._apply_filter([n], hyst)
        assert "m" not in result

    def test_avg_multi_persist_state_true_included(self):
        n = node("m", "avg_multi", {"persist_state": True})
        hyst = {"m": {"samples": [[datetime.datetime.now(datetime.timezone.utc).isoformat(), 42.0]]}}
        result = self._apply_filter([n], hyst)
        assert "m" in result

    def test_persist_state_false_string_does_not_exclude(self):
        """Only Python False (boolean) triggers exclusion, not the string 'false'."""
        n = node("n1", "statistics", {"persist_state": "false"})
        hyst = {"n1": {"s_count": 1}}
        result = self._apply_filter([n], hyst)
        # "false" is not False → should be included
        assert "n1" in result
