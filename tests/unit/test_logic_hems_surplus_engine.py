"""Control behaviour of the ``hems_surplus`` engine (HEMS Lite, issue #1327).

The engine is pure: time is passed in, so every delay, hysteresis and lockout
below is driven by an explicit clock instead of sleeping.
"""

from __future__ import annotations

import math

import pytest

from obs.logic import hems_surplus as hs


class Sim:
    """One block instance: configuration, persistent state and a controllable clock."""

    def __init__(self, consumers=None, **config):
        self.config = {"on_delay_s": 60, "off_delay_s": 180, "interval_s": 30, "target_w": 0, "only_on_change": False, **config}
        self.config["consumers"] = consumers or []
        self.state: dict = {}
        self.now = 1000.0
        self.last: dict = {}

    def tick(self, advance=30, **inputs):
        """Advance the clock and evaluate; returns the emitted outputs."""
        self.now += advance
        self.last = hs.evaluate(self.config, inputs, self.state, self.now, "node-1")
        return self.last

    def cstate(self, cid):
        return self.state["consumers"][cid]


def pct(cid="a", **kw):
    return {"id": cid, "mode": "percent", "power_w": 10000, **kw}


def onoff(cid="a", **kw):
    return {"id": cid, "mode": "onoff", "power_w": 2000, **kw}


def trig(cid="a", **kw):
    return {"id": cid, "mode": "trigger", "min_surplus_w": 1500, **kw}


# ── Parsing helpers ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [(5, 5.0), ("7.5", 7.5), (None, None), (True, None), ("x", None), (float("nan"), None), (math.inf, None), (10**400, None)],
)
def test_num_accepts_only_finite_numbers(value, expected):
    assert hs._num(value) == expected


def test_cfg_num_and_opt_num_apply_defaults_and_bounds():
    assert hs._cfg_num("bad", 5.0) == 5.0
    assert hs._cfg_num(-4, 5.0, minimum=0) == 0
    assert hs._cfg_num(500, 5.0, maximum=100) == 100
    assert hs._opt_num("") is None
    assert hs._opt_num(None) is None
    assert hs._opt_num("bad") is None
    assert hs._opt_num(-3, minimum=0) == 0
    assert hs._opt_num(-3) == -3


@pytest.mark.parametrize(("value", "expected"), [("true", True), (" On ", True), ("0", False), ("nein", False), (1, True), (0, False), (None, False)])
def test_truthy(value, expected):
    assert hs._truthy(value) is expected


def test_load_consumers_accepts_json_string_and_list():
    raw = '[{"id": "x", "mode": "onoff", "name": "Boiler"}]'
    from_string = hs.load_consumers(raw)
    from_list = hs.load_consumers([{"id": "x", "mode": "onoff", "name": "Boiler"}])
    assert from_string == from_list
    assert from_string[0]["name"] == "Boiler"


@pytest.mark.parametrize("raw", ["not json", "{}", None, 5, {"id": "x"}])
def test_load_consumers_tolerates_garbage(raw):
    assert hs.load_consumers(raw) == []


def test_load_consumers_normalises_ids_modes_and_defaults():
    consumers = hs.load_consumers(
        [
            "skip me",
            {"id": "a b!", "mode": "bogus", "active": "false", "power_source": "input", "rearm": "weird", "below_min": "weird"},
            {"id": "ab"},
            {},
        ]
    )
    assert [c["id"] for c in consumers] == ["ab", "ab_", "n4"]
    first = consumers[0]
    assert first["mode"] == "percent"
    assert first["active"] is False
    assert first["power_source"] == "input"
    assert first["rearm"] == "after_drop"
    assert first["below_min"] == "off"
    assert consumers[1]["active"] is True
    assert consumers[2]["name"] == "#4"
    assert hs.load_consumers([{"id": "a", "below_min": "hold_min", "rearm": "reset_input"}])[0]["rearm"] == "reset_input"


def test_handle_helpers():
    data = {"consumers": [pct("a"), trig("b")]}
    assert hs.grid_input_names("split") == ("grid_import", "grid_export")
    assert hs.grid_input_names("feed_in_only") == ("feed_in",)
    assert hs.grid_input_names("anything") == ("grid",)
    assert {"c_a", "c_a_status", "c_b", "grid_power", "status", "warning"} <= hs.output_handles(data)
    assert hs.trigger_output_handles(data) == {"c_b"}


# ── Meter modes and safe state ──────────────────────────────────────────


@pytest.mark.parametrize("bad", [None, "abc", True, float("nan"), math.inf])
def test_invalid_bidirectional_meter_puts_everything_in_the_safe_state(bad):
    sim = Sim([pct("a"), onoff("b")], on_delay_s=0)
    sim.tick(grid=-12000)  # healthy: starts both
    out = sim.tick(grid=bad)
    assert out["c_a"] == 0
    assert out["c_b"] is False
    assert out["status"] == "invalid_input"
    assert out["warning"] is True
    assert out["c_a_status"] == "safe"


def test_missing_meter_input_is_invalid():
    sim = Sim([pct()])
    assert sim.tick()["status"] == "invalid_input"


def test_split_inputs_are_netted_and_both_must_be_valid():
    sim = Sim([], grid_mode="split")
    assert sim.tick(grid_import=800, grid_export=0)["grid_power"] == 800
    assert sim.tick(grid_import=0, grid_export=2500)["grid_power"] == -2500
    assert sim.tick(grid_import=100, grid_export=80)["grid_power"] == 20
    assert sim.tick(grid_import=100)["status"] == "invalid_input"
    assert sim.tick(grid_import=100, grid_export=None)["status"] == "invalid_input"
    assert sim.tick(grid_import=-1, grid_export=0)["status"] == "invalid_input"
    assert sim.tick(grid_import=0, grid_export=-1)["status"] == "invalid_input"


def test_feed_in_only_negative_value_is_invalid():
    sim = Sim([], grid_mode="feed_in_only")
    assert sim.tick(feed_in=-5)["status"] == "invalid_input"
    out = sim.tick(feed_in=1200)
    assert out["grid_power"] == -1200
    assert out["surplus"] == 1200


def test_unknown_grid_mode_falls_back_to_bidirectional():
    sim = Sim([], grid_mode="nonsense")
    assert sim.tick(grid=-300)["grid_power"] == -300


def test_disabled_and_plant_not_ok_force_safe_state_but_unconnected_or_none_do_not():
    sim = Sim([pct()], on_delay_s=0)
    assert sim.tick(grid=-12000)["c_a"] > 0
    out = sim.tick(grid=-12000, enable=False)
    assert (out["status"], out["c_a"], out["warning"]) == ("disabled", 0, False)
    out = sim.tick(grid=-12000, enable=True, plant_ok=0)
    assert out["status"] == "plant_not_ok"
    assert sim.tick(grid=-12000, enable=None, plant_ok=None)["status"] == "ok"


def test_recovery_after_safe_state_starts_a_cycle_immediately_but_still_honours_the_delay():
    sim = Sim([pct()], on_delay_s=60)
    sim.tick(grid=None)
    out = sim.tick(advance=1, grid=-12000)  # inside the interval, but the safe state reset the cycle
    assert out["c_a_status"] == "on_delay"
    assert out["status"] == "ok"
    sim.tick(advance=61, grid=-12000)
    assert sim.cstate("a")["on"] is True


def test_invalid_behavior_hold_keeps_outputs_and_does_not_fire_triggers():
    sim = Sim([pct("a"), trig("b", on_delay_s=0)], on_delay_s=0, invalid_behavior="hold", only_on_change=True)
    sim.tick(grid=-12000)
    out = sim.tick(grid="bad")
    assert "c_a" not in out
    assert out["c_a_status"] == "safe_hold"
    assert out["status"] == "invalid_input"
    assert sim.cstate("a")["on"] is True


def test_hold_does_not_apply_to_disabled():
    sim = Sim([pct()], on_delay_s=0, invalid_behavior="hold")
    sim.tick(grid=-12000)
    assert sim.tick(grid=-12000, enable=False)["c_a"] == 0


def test_stale_meter_detected_via_changed_flag():
    sim = Sim([pct()], on_delay_s=0, max_age_s=100)
    assert sim.tick(grid=-12000, grid_changed=True)["status"] == "ok"
    assert sim.tick(advance=60, grid=-12000, grid_changed=False)["status"] == "ok"
    out = sim.tick(advance=60, grid=-12000, grid_changed=False)
    assert (out["status"], out["warning"], out["c_a"]) == ("stale_input", True, 0)
    # a fresh telegram recovers
    assert sim.tick(advance=1, grid=-12000, grid_changed=True)["status"] == "ok"


def test_stale_check_needs_the_changed_signal_and_a_positive_max_age():
    sim = Sim([pct()], max_age_s=10)
    sim.tick(grid=-100)
    assert sim.tick(advance=500, grid=-100)["status"] == "ok"  # changed not wired → age cannot be judged
    sim = Sim([pct()], max_age_s=0)
    sim.tick(grid=-100, grid_changed=True)
    assert sim.tick(advance=500, grid=-100, grid_changed=False)["status"] == "ok"


def test_stale_check_in_split_mode_judges_each_input():
    sim = Sim([], grid_mode="split", max_age_s=50)
    sim.tick(grid_import=0, grid_export=100, grid_import_changed=True, grid_export_changed=True)
    out = sim.tick(advance=40, grid_import=0, grid_export=100, grid_import_changed=True, grid_export_changed=False)
    assert out["status"] == "ok"
    out = sim.tick(advance=40, grid_import=0, grid_export=100, grid_import_changed=True, grid_export_changed=False)
    assert out["status"] == "stale_input"


# ── Cycle gating and output filtering ───────────────────────────────────


def test_a_cycle_runs_at_most_once_per_interval():
    sim = Sim([pct()], on_delay_s=0)
    assert sim.tick(grid=-12000)
    assert sim.tick(advance=5, grid=-12000) == {}
    assert sim.tick(advance=20, grid=-12000) == {}  # 25 s, inside the 90 % tolerance window
    assert sim.tick(advance=5, grid=-12000)["status"] == "ok"  # 30 s elapsed


def test_interval_is_clamped_to_the_allowed_range():
    sim = Sim([], interval_s=1)
    sim.tick(grid=0)
    assert sim.tick(advance=3, grid=0) == {}  # clamped up to 5 s
    assert sim.tick(advance=2, grid=0)["status"] == "ok"
    sim = Sim([], interval_s=10**6)
    sim.tick(grid=0)
    assert sim.tick(advance=3000, grid=0) == {}  # clamped down to 3600 s
    assert sim.tick(advance=600, grid=0)["status"] == "ok"


def test_only_on_change_withholds_unchanged_outputs():
    sim = Sim([pct()], on_delay_s=0, only_on_change=True)
    first = sim.tick(grid=-12000)
    assert {"grid_power", "status", "c_a"} <= set(first)
    second = sim.tick(grid=-12000)
    assert "status" not in second
    assert "grid_power" not in second


def test_only_on_change_off_emits_everything_every_cycle():
    sim = Sim([pct()], on_delay_s=0, only_on_change=False)
    sim.tick(grid=-12000)
    second = sim.tick(grid=-12000)
    assert {"grid_power", "status", "c_a", "c_a_status"} <= set(second)


def test_only_on_change_distinguishes_false_from_zero():
    sim = Sim([], only_on_change=True)
    sim.tick(grid=0)
    sim.state["last_out"]["surplus"] = False  # equal to 0.0, but not the same value
    assert sim.tick(grid=0)["surplus"] == 0.0


def test_negative_zero_is_normalised():
    sim = Sim([], grid_mode="feed_in_only")
    out = sim.tick(feed_in=0)
    assert str(out["grid_power"]) == "0.0"


# ── Budget calculation ──────────────────────────────────────────────────


def test_budget_keeps_already_used_power_in_the_calculation():
    sim = Sim([pct(on_delay_s=0)], target_w=200)
    out = sim.tick(grid=-1000, c_a_power=2000)
    assert out["budget"] == 2800
    assert out["surplus"] == 800


def test_budget_example_from_the_issue_distributes_by_priority():
    consumers = [
        onoff("heater", power_w=2000),
        pct("wallbox", power_w=11000),
        trig("wash", min_surplus_w=1500),
    ]
    sim = Sim(consumers, on_delay_s=0, target_w=0)
    out = sim.tick(grid=-3800)
    assert out["c_heater"] is True
    assert out["c_wallbox"] == 16  # 1800 W of 11000 W, 1 % steps
    assert out["c_wash"] is False
    assert out["allocated"] == pytest.approx(2000 + 11000 * 16 / 100)
    assert out["remaining"] == pytest.approx(3800 - out["allocated"])


def test_budget_drop_reduces_from_the_bottom_up():
    consumers = [onoff("heater", power_w=2000), pct("wallbox", power_w=11000, min_power_w=1000)]
    sim = Sim(consumers, on_delay_s=0, off_delay_s=0)
    sim.tick(grid=-3800)
    assert sim.cstate("heater")["on"] is True
    assert sim.cstate("wallbox")["setpoint"] == 16
    # the export is used up: both loads draw what they were given, the grid sits at 0 W
    sim.tick(grid=0, c_heater_power=2000, c_wallbox_power=1760)
    assert sim.cstate("heater")["on"] is True  # untouched, highest priority
    assert sim.cstate("wallbox")["setpoint"] == 16
    # the PV output drops: the grid now delivers 1800 W → the lower priority gives way first
    sim.tick(grid=1500, c_heater_power=2000, c_wallbox_power=1760)
    assert sim.cstate("heater")["on"] is True
    assert sim.cstate("wallbox")["on"] is False
    # and only then the higher one
    out = sim.tick(grid=2000, c_heater_power=2000, c_wallbox_power=0)
    assert out["c_heater"] is False


def test_estimated_power_is_used_without_measurement():
    sim = Sim([pct(on_delay_s=0)], target_w=0, only_on_change=True)
    sim.tick(grid=-5000)
    assert sim.cstate("a")["setpoint"] == 50
    # without measurement the 5 kW stay in the budget: grid 0 W keeps 50 %
    out = sim.tick(grid=0)
    assert "c_a" not in out
    assert sim.cstate("a")["setpoint"] == 50


def test_negative_measured_power_counts_as_zero():
    sim = Sim([pct(on_delay_s=0)])
    assert sim.tick(grid=-1000, c_a_power=-50)["budget"] == 1000


def test_dynamic_target_input_overrides_the_configured_reserve():
    sim = Sim([], target_w=100)
    assert sim.tick(grid=-1000)["surplus"] == 900
    assert sim.tick(grid=-1000, target=400)["surplus"] == 600
    assert sim.tick(grid=-1000, target="bad")["surplus"] == 900  # falls back to the configured value


def test_surplus_is_never_negative_and_remaining_never_negative():
    sim = Sim([pct(on_delay_s=0)])
    out = sim.tick(grid=500)
    assert out["surplus"] == 0
    assert out["remaining"] == 0
    assert out["budget"] == -500


# ── Percent consumers ───────────────────────────────────────────────────


def test_percent_waits_for_the_on_delay_then_starts():
    sim = Sim([pct()])
    out = sim.tick(grid=-4000)
    assert out["c_a"] == 0
    assert out["c_a_status"] == "on_delay"
    out = sim.tick(advance=30, grid=-4000)
    assert out["c_a_status"] == "on_delay"
    out = sim.tick(advance=30, grid=-4000)
    assert out["c_a"] == 40
    assert out["c_a_status"] == "active"
    assert sim.cstate("a")["since"] == sim.now


def test_percent_on_delay_restarts_when_surplus_disappears():
    sim = Sim([pct()])
    sim.tick(grid=-4000)
    sim.tick(advance=30, grid=-4000)
    sim.tick(advance=30, grid=500)  # surplus gone → timer reset
    assert sim.cstate("a")["on_pending"] is None
    out = sim.tick(advance=30, grid=-4000)
    assert out["c_a_status"] == "on_delay"
    assert sim.cstate("a")["on"] is False


def test_percent_per_consumer_delay_overrides_global():
    sim = Sim([pct(on_delay_s=0)], on_delay_s=600)
    assert sim.tick(grid=-4000)["c_a"] == 40


def test_percent_step_quantises_downwards_and_respects_max_setpoint():
    sim = Sim([pct(step=10, max_setpoint=50, on_delay_s=0)])
    assert sim.tick(grid=-4400)["c_a"] == 40
    assert sim.tick(grid=-4400 - 4000, c_a_power=4000)["c_a"] == 50
    assert sim.cstate("a")["setpoint"] == 50


def test_percent_fractional_step_emits_floats():
    sim = Sim([pct(step=0.5, on_delay_s=0, power_w=1000)])
    assert sim.tick(grid=-125)["c_a"] == 12.5


def test_percent_below_the_minimum_power_stays_off():
    sim = Sim([pct(min_power_w=3000, on_delay_s=0)])
    out = sim.tick(grid=-2900)
    assert out["c_a"] == 0
    assert out["c_a_status"] == "off"
    assert sim.tick(grid=-3000)["c_a"] == 30


def test_percent_minimum_setpoint_is_a_floor():
    sim = Sim([pct(min_setpoint=20, on_delay_s=0)])
    assert sim.tick(grid=-1500)["c_a"] == 0
    assert sim.tick(grid=-2000)["c_a"] == 20


def test_percent_with_unreachable_minimum_never_starts():
    sim = Sim([pct(min_setpoint=80, max_setpoint=50, on_delay_s=0)])
    assert sim.tick(grid=-20000)["c_a"] == 0


def test_percent_below_min_off_switches_off_immediately():
    sim = Sim([pct(min_power_w=3000, on_delay_s=0)], only_on_change=True)
    sim.tick(grid=-5000)
    out = sim.tick(grid=0, c_a_power=5000)  # budget 5000 → still fine
    assert "c_a" not in out
    out = sim.tick(grid=2500, c_a_power=5000)  # budget 2500 < 3000
    assert out["c_a"] == 0
    assert sim.state["last_out"]["c_a_status"] == "reduced"
    assert sim.cstate("a")["on"] is False


def test_percent_below_min_hold_min_keeps_the_minimum_until_the_off_delay_expires():
    sim = Sim([pct(min_power_w=3000, below_min="hold_min", on_delay_s=0)], off_delay_s=100, only_on_change=True)
    sim.tick(grid=-5000)
    out = sim.tick(grid=2500, c_a_power=5000)
    assert out["c_a"] == 30
    assert out["c_a_status"] == "off_delay"
    out = sim.tick(advance=60, grid=2500, c_a_power=3000)
    assert "c_a" not in out
    out = sim.tick(advance=60, grid=2500, c_a_power=3000)
    assert out["c_a"] == 0


def test_percent_hold_min_pending_is_cleared_when_power_returns():
    sim = Sim([pct(min_power_w=3000, below_min="hold_min", on_delay_s=0)], off_delay_s=100)
    sim.tick(grid=-5000)
    sim.tick(grid=2500, c_a_power=5000)
    assert sim.cstate("a")["off_pending"] is not None
    sim.tick(grid=-3000, c_a_power=3000)
    assert sim.cstate("a")["off_pending"] is None


def test_percent_minimum_runtime_prevents_an_immediate_shed():
    sim = Sim([pct(min_power_w=3000, min_runtime_s=300, on_delay_s=0)])
    sim.tick(grid=-5000)
    out = sim.tick(grid=2500, c_a_power=5000)
    assert out["c_a"] == 30  # held at the minimum
    assert out["c_a_status"] == "min_runtime"
    out = sim.tick(advance=300, grid=2500, c_a_power=3000)
    assert out["c_a"] == 0


def test_percent_minimum_off_time_blocks_a_quick_restart():
    sim = Sim([pct(min_power_w=3000, min_off_s=120, on_delay_s=0)])
    sim.tick(grid=-5000)
    sim.tick(grid=3000, c_a_power=5000)  # budget 2000 < 3000 → off
    assert sim.cstate("a")["on"] is False
    out = sim.tick(advance=30, grid=-5000)
    assert out["c_a_status"] == "locked"
    assert sim.cstate("a")["on"] is False
    out = sim.tick(advance=100, grid=-5000)
    assert out["c_a"] == 50


def test_percent_reduced_status_when_the_budget_is_below_the_maximum():
    sim = Sim([pct(on_delay_s=0)])
    out = sim.tick(grid=-5000)
    assert out["c_a_status"] == "active"
    out = sim.tick(grid=0, c_a_power=5000)  # keeps 5 kW of 10 kW
    assert out["c_a_status"] == "reduced"
    out = sim.tick(grid=-15000, c_a_power=5000)
    assert out["c_a_status"] == "active"
    assert out["c_a"] == 100


def test_percent_dynamic_maximum_power_input():
    sim = Sim([pct(power_source="input", on_delay_s=0)])
    assert sim.tick(grid=-4000, c_a_max_power=8000)["c_a"] == 50
    out = sim.tick(grid=-4000, c_a_max_power=None)
    assert out["c_a"] == 0
    assert out["c_a_status"] == "invalid_input"
    out = sim.tick(grid=-4000, c_a_max_power=0)
    assert out["c_a_status"] == "invalid_input"


def test_percent_zero_power_consumer_is_never_started():
    sim = Sim([pct(power_w=0, on_delay_s=0)])
    out = sim.tick(grid=-5000)
    assert out["c_a"] == 0
    assert out["c_a_status"] == "off"


def test_inactive_consumer_is_skipped_and_its_budget_goes_down_the_list():
    sim = Sim([pct("a", active=False), pct("b", on_delay_s=0)])
    out = sim.tick(grid=-5000)
    assert out["c_a"] == 0
    assert out["c_a_status"] == "inactive"
    assert out["c_b"] == 50


def test_deactivating_a_running_consumer_switches_it_off():
    consumers = [onoff("a", on_delay_s=0)]
    sim = Sim(consumers)
    assert sim.tick(grid=-3000)["c_a"] is True
    consumers[0]["active"] = False
    assert sim.tick(grid=-3000)["c_a"] is False


def test_removed_consumers_lose_their_state():
    sim = Sim([pct("a"), pct("b")])
    sim.tick(grid=0)
    assert set(sim.state["consumers"]) == {"a", "b"}
    sim.config["consumers"] = [pct("b")]
    sim.tick(grid=0)
    assert set(sim.state["consumers"]) == {"b"}


def test_consumer_order_is_the_priority():
    sim = Sim([pct("a", power_w=4000, on_delay_s=0), pct("b", power_w=4000, on_delay_s=0)])
    out = sim.tick(grid=-5000)
    assert (out["c_a"], out["c_b"]) == (100, 25)
    sim = Sim([pct("b", power_w=4000, on_delay_s=0), pct("a", power_w=4000, on_delay_s=0)])
    out = sim.tick(grid=-5000)
    assert (out["c_b"], out["c_a"]) == (100, 25)


# ── On/off consumers ────────────────────────────────────────────────────


def test_onoff_switches_on_only_when_its_rated_power_is_available():
    sim = Sim([onoff(on_delay_s=0)])
    assert sim.tick(grid=-1900)["c_a"] is False
    assert sim.tick(grid=-2000)["c_a"] is True


def test_onoff_hysteresis_uses_separate_thresholds_and_delays():
    sim = Sim([onoff(on_threshold_w=2000, off_threshold_w=1500)], on_delay_s=60, off_delay_s=120, only_on_change=True)
    sim.tick(grid=-2000)
    out = sim.tick(advance=60, grid=-2000)
    assert out["c_a"] is True
    # the budget falls into the hysteresis band: stays on, no timer
    out = sim.tick(grid=0, c_a_power=1700)
    assert "c_a" not in out
    assert sim.cstate("a")["off_pending"] is None
    # below the off threshold: off-delay starts, then it switches off
    out = sim.tick(grid=500, c_a_power=1700)
    assert out["c_a_status"] == "off_delay"
    out = sim.tick(advance=120, grid=500, c_a_power=1700)
    assert out["c_a"] is False


def test_onoff_off_delay_resets_when_power_returns():
    sim = Sim([onoff(on_delay_s=0)], off_delay_s=120)
    sim.tick(grid=-2000)
    sim.tick(grid=1000, c_a_power=2000)
    assert sim.cstate("a")["off_pending"] is not None
    sim.tick(grid=0, c_a_power=2000)
    assert sim.cstate("a")["off_pending"] is None
    assert sim.cstate("a")["on"] is True


def test_onoff_off_threshold_above_on_threshold_is_clamped():
    sim = Sim([onoff(on_threshold_w=1000, off_threshold_w=5000, on_delay_s=0)], off_delay_s=0, only_on_change=True)
    sim.tick(grid=-1000)
    assert sim.cstate("a")["on"] is True
    out = sim.tick(grid=0, c_a_power=1000)
    assert "c_a" not in out  # budget 1000 ≥ clamped off threshold 1000


def test_onoff_minimum_runtime_and_minimum_off_time():
    sim = Sim([onoff(min_runtime_s=200, min_off_s=200, on_delay_s=0)], off_delay_s=0)
    sim.tick(grid=-2000)
    out = sim.tick(advance=30, grid=3000, c_a_power=2000)
    assert out["c_a_status"] == "min_runtime"
    assert sim.cstate("a")["on"] is True
    out = sim.tick(advance=200, grid=3000, c_a_power=2000)
    assert out["c_a"] is False
    out = sim.tick(advance=30, grid=-2000)
    assert out["c_a_status"] == "locked"
    out = sim.tick(advance=200, grid=-2000)
    assert out["c_a"] is True


def test_onoff_dynamic_rated_power_input():
    sim = Sim([onoff(power_source="input", on_delay_s=0)])
    assert sim.tick(grid=-3000, c_a_max_power=2500)["c_a"] is True
    out = sim.tick(grid=-3000, c_a_max_power="x")
    assert out["c_a"] is False
    assert out["c_a_status"] == "invalid_input"


def test_a_lower_priority_consumer_only_gets_what_the_higher_ones_leave():
    sim = Sim([onoff("a", on_delay_s=0), onoff("b", on_delay_s=0)])
    out = sim.tick(grid=-3000)
    assert (out["c_a"], out["c_b"]) == (True, False)
    out = sim.tick(grid=-4000, c_a_power=2000)
    assert out["c_b"] is True


# ── Trigger consumers ───────────────────────────────────────────────────


def test_trigger_fires_once_after_the_surplus_held_long_enough():
    sim = Sim([trig(on_delay_s=60, pulse_s=2, lockout_s=3600)])
    out = sim.tick(grid=-2000)
    assert out["c_a"] is False
    assert out["c_a_status"] == "on_delay"
    out = sim.tick(advance=60, grid=-2000)
    assert out["c_a"] is True
    assert out["c_a_status"] == "fired"
    assert sim.state["next_wake"] == sim.now + 2
    # the pulse ends on the very next tick after its duration, even inside the interval
    assert sim.tick(advance=1, grid=-2000) == {}
    out = sim.tick(advance=1, grid=-2000)
    assert out == {"c_a": False}
    assert sim.state["next_wake"] is None
    # never again in the following cycles
    for _ in range(5):
        out = sim.tick(advance=60, grid=-2000)
        assert out.get("c_a") is not True


def test_trigger_pending_resets_when_surplus_drops():
    sim = Sim([trig(on_delay_s=60)])
    sim.tick(grid=-2000)
    out = sim.tick(advance=30, grid=-1000)
    assert out["c_a_status"] == "off"
    assert sim.cstate("a")["trigger_pending"] is None


def test_trigger_rearm_after_drop():
    sim = Sim([trig(on_delay_s=0, lockout_s=100, rearm="after_drop", pulse_s=1)])
    assert sim.tick(grid=-2000)["c_a"] is True
    sim.tick(advance=2, grid=-2000)  # pulse over
    out = sim.tick(advance=200, grid=-2000)  # lockout over, but the threshold was never undershot
    assert out["c_a_status"] == "locked"
    sim.tick(advance=30, grid=0)  # undershot
    out = sim.tick(advance=30, grid=-2000)
    assert out["c_a"] is True


def test_trigger_rearm_after_lockout():
    sim = Sim([trig(on_delay_s=0, lockout_s=100, rearm="after_lockout", pulse_s=1)])
    assert sim.tick(grid=-2000)["c_a"] is True
    out = sim.tick(advance=30, grid=-2000)  # still in the lockout
    assert out["c_a_status"] == "locked"
    out = sim.tick(advance=100, grid=-2000)
    assert out["c_a"] is True


def test_trigger_rearm_via_reset_input_only():
    sim = Sim([trig(on_delay_s=0, lockout_s=10, rearm="reset_input", pulse_s=1)])
    assert sim.tick(grid=-2000)["c_a"] is True
    sim.tick(advance=2, grid=0)
    out = sim.tick(advance=100, grid=-2000)
    assert out["c_a_status"] == "locked"
    out = sim.tick(advance=30, grid=-2000, c_a_reset=True)  # rising edge → re-armed and fires
    assert out["c_a"] is True
    sim.tick(advance=2, grid=-2000, c_a_reset=True)  # pulse over; the held reset is no new edge
    assert sim.cstate("a")["reset_prev"] is True
    out = sim.tick(advance=100, grid=-2000, c_a_reset=True)
    assert out["c_a_status"] == "locked"
    sim.tick(advance=30, grid=-2000, c_a_reset=False)
    assert sim.cstate("a")["reset_prev"] is False


def test_trigger_reset_edge_rearms_even_inside_the_lockout():
    sim = Sim([trig(on_delay_s=0, lockout_s=10000, rearm="reset_input", pulse_s=1)])
    assert sim.tick(grid=-2000)["c_a"] is True
    sim.tick(advance=2, grid=-2000)
    out = sim.tick(advance=30, grid=-2000, c_a_reset=True)
    assert out["c_a"] is True


def test_trigger_does_not_fire_in_the_safe_state():
    sim = Sim([trig(on_delay_s=0)])
    out = sim.tick(grid=None)
    assert out["c_a"] is False
    out = sim.tick(grid=-5000, enable=False)
    assert out["c_a"] is False
    assert sim.cstate("a")["armed"] is True


def test_safe_state_ends_a_running_pulse():
    sim = Sim([trig(on_delay_s=0, pulse_s=100)])
    sim.tick(grid=-2000)
    assert sim.cstate("a")["pulse_until"] is not None
    out = sim.tick(advance=1, grid="bad")
    assert out["c_a"] is False
    assert sim.cstate("a")["pulse_until"] is None
    assert sim.state["next_wake"] is None


def test_trigger_reserves_power_while_firing():
    consumers = [trig("t", on_delay_s=0, reserve_w=1500, min_surplus_w=1500), pct("p", power_w=3000, on_delay_s=0)]
    sim = Sim(consumers)
    out = sim.tick(grid=-3000)
    assert out["c_t"] is True
    assert out["c_p"] == 50  # 3000 − 1500 reserved
    out = sim.tick(grid=-3000)  # the reservation holds for one more cycle
    assert sim.cstate("t")["reserve_cycles"] == 0
    out = sim.tick(grid=-3000)
    assert sim.cstate("p")["setpoint"] == 100


def test_trigger_power_input_is_not_used_for_the_trigger_itself():
    sim = Sim([trig(on_delay_s=0, reserve_w=500)])
    sim.tick(grid=-2000)
    # the reservation counts as used power in the next cycle's budget
    assert sim.tick(grid=-1000)["budget"] == 1500


def test_inactive_trigger_never_fires_and_clears_a_pulse():
    consumers = [trig(on_delay_s=0, pulse_s=100)]
    sim = Sim(consumers)
    sim.tick(grid=-2000)
    consumers[0]["active"] = False
    out = sim.tick(grid=-2000)
    assert out["c_a"] is False
    assert sim.cstate("a")["pulse_until"] is None


# ── Feed-in-only mode ───────────────────────────────────────────────────


def test_feed_in_only_holds_at_zero_and_sheds_one_step_after_the_off_delay():
    sim = Sim([pct(step=10, on_delay_s=0)], grid_mode="feed_in_only", off_delay_s=100)
    sim.tick(feed_in=5000)
    assert sim.cstate("a")["setpoint"] == 50
    out = sim.tick(feed_in=0)  # first zero reading: hold
    assert out["budget"] == 5000
    assert sim.cstate("a")["setpoint"] == 50
    sim.tick(advance=60, feed_in=0)  # still inside the off-delay
    assert sim.cstate("a")["setpoint"] == 50
    sim.tick(advance=60, feed_in=0)  # off-delay over → one 10 % step (1000 W)
    assert sim.cstate("a")["setpoint"] == 40
    sim.tick(advance=60, feed_in=0)
    assert sim.cstate("a")["setpoint"] == 40  # the next step waits for another off-delay
    sim.tick(advance=60, feed_in=0)
    assert sim.cstate("a")["setpoint"] == 30


def test_feed_in_only_returning_feed_in_resets_the_zero_timer():
    sim = Sim([pct(on_delay_s=0)], grid_mode="feed_in_only", off_delay_s=100)
    sim.tick(feed_in=5000)
    sim.tick(feed_in=0)
    assert sim.state["zero_since"] is not None
    sim.tick(feed_in=200)
    assert sim.state["zero_since"] is None


def test_feed_in_only_sheds_an_onoff_consumer_by_its_rated_power():
    sim = Sim([onoff("a", on_delay_s=0), onoff("b", on_delay_s=0)], grid_mode="feed_in_only", off_delay_s=0)
    sim.tick(feed_in=4000)
    assert sim.cstate("b")["on"] is True
    sim.tick(feed_in=0)  # first zero: hold
    sim.tick(feed_in=0)
    assert sim.cstate("b")["on"] is False  # the lowest priority goes first
    assert sim.cstate("a")["on"] is True


def test_feed_in_only_with_nothing_running_has_nothing_to_shed():
    sim = Sim([trig("t")], grid_mode="feed_in_only", off_delay_s=0)
    sim.tick(feed_in=0)
    assert sim.tick(feed_in=0)["budget"] == 0


# ── Misc ────────────────────────────────────────────────────────────────


def test_a_fresh_instance_starts_safe_and_never_fires_on_its_first_look():
    sim = Sim([pct("a"), trig("t", on_delay_s=60)])
    out = sim.tick(grid=-12000)
    assert out["c_a"] == 0
    assert out["c_t"] is False
    assert out["c_a_status"] == "on_delay"
    assert out["c_t_status"] == "on_delay"


def test_status_changes_are_logged(caplog):
    sim = Sim([pct()])
    with caplog.at_level("INFO", logger="obs.logic.hems_surplus"):
        sim.tick(grid=None)
        sim.tick(grid=0)
        sim.tick(grid=0, enable=False)
        sim.tick(grid=0, enable=True)
    messages = [r.getMessage() for r in caplog.records]
    assert any("invalid grid measurement (mode=bidirectional, grid=None)" in m for m in messages)
    assert any("resumed control" in m for m in messages)
    assert any("disabled" in m for m in messages)


def test_stale_status_logs_a_warning(caplog):
    sim = Sim([], max_age_s=10)
    sim.tick(grid=0, grid_changed=True)
    with caplog.at_level("WARNING", logger="obs.logic.hems_surplus"):
        sim.tick(advance=60, grid=0, grid_changed=False)
    assert any("stale grid measurement" in r.getMessage() for r in caplog.records)


def test_first_healthy_status_is_not_logged_as_a_resume(caplog):
    sim = Sim([])
    with caplog.at_level("INFO", logger="obs.logic.hems_surplus"):
        sim.tick(grid=0)
    assert not [r for r in caplog.records if "resumed" in r.getMessage()]


def test_evaluate_without_node_id_still_logs():
    state: dict = {}
    hs.evaluate({"consumers": []}, {"grid": None}, state, 1000.0)
    assert state["status"] == "invalid_input"


def test_a_pulse_longer_than_the_interval_stays_active_across_cycles_without_refiring():
    sim = Sim([trig(on_delay_s=0, pulse_s=100, lockout_s=0, rearm="after_lockout")])
    assert sim.tick(grid=-2000)["c_a"] is True
    out = sim.tick(advance=30, grid=-2000)
    assert out["c_a"] is True
    assert out["c_a_status"] == "fired"
    assert sim.cstate("a")["pulse_until"] == 1030.0 + 100


def test_full_snapshot_adds_the_last_emitted_outputs_without_recomputing():
    sim = Sim([onoff(on_delay_s=0)], only_on_change=True)
    first = sim.tick(grid=-3000)
    assert sim.tick(advance=1, grid=-3000) == {}  # inside the interval
    snap = hs.evaluate(sim.config, {"grid": -3000}, sim.state, sim.now + 1, "n", full_snapshot=True)
    assert snap == sim.state["last_out"]
    assert snap["c_a"] is True
    assert set(first) == set(snap)
    # a fresh instance has nothing to add and simply runs
    assert hs.evaluate(sim.config, {"grid": -3000}, {}, 1000.0, "n", full_snapshot=True)["status"] == "ok"


def test_diagnostics_are_reported_in_the_safe_state_too():
    sim = Sim([pct()])
    out = sim.tick(grid=800, enable=False)  # disabled: the measured grid power is still shown
    assert out["status"] == "disabled"
    assert out["grid_power"] == 800
    assert (out["surplus"], out["budget"], out["allocated"], out["remaining"]) == (0.0, 0.0, 0.0, 0.0)
    out = sim.tick(grid=None)  # invalid meter: nothing to report for the grid power
    assert out["status"] == "invalid_input"
    assert "grid_power" not in out
    assert out["budget"] == 0.0
