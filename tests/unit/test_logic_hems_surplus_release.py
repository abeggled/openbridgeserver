"""Per-consumer release (Freigabe) of the ``hems_surplus`` block (issue #1359).

Every consumer has a boolean input ``c_<id>_enable``. A locked consumer is left
out of the distribution as if it were not listed — its share goes to the
released consumers below it — and keeps its position for when it is released
again.
"""

from __future__ import annotations

import logging
import math
from decimal import Decimal

import pytest

from obs.logic import hems_surplus as hs
from obs.logic.executor import GraphExecutor
from obs.logic.models import FlowData
from tests.unit.conftest import edge, node
from tests.unit.test_logic_hems_surplus_engine import Sim, onoff, pct, trig

# ── Configuration ───────────────────────────────────────────────────────


def test_existing_configurations_are_released_by_default():
    consumer = hs.load_consumers([{"id": "a"}])[0]
    assert consumer["enable_default"] is True
    assert consumer["enable_fault"] == "lock"


@pytest.mark.parametrize(("raw", "expected"), [(None, True), (True, True), ("false", False), (False, False), (0, False)])
def test_the_default_release_is_read_as_a_yes_no_setting(raw, expected):
    assert hs.load_consumers([{"id": "a", "enable_default": raw}])[0]["enable_default"] is expected


@pytest.mark.parametrize(("raw", "expected"), [("default", "default"), (" Default ", "default"), ("lock", "lock"), ("weird", "lock"), (None, "lock")])
def test_the_fault_behaviour_falls_back_to_the_safe_lock(raw, expected):
    assert hs.load_consumers([{"id": "a", "enable_fault": raw}])[0]["enable_fault"] == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        (0.0, False),
        (2.5, True),
        (Decimal(1), True),
        (Decimal(0), False),
        (math.nan, None),
        (math.inf, None),
        ("true", True),
        (" ON ", True),
        ("yes", True),
        ("1", True),
        ("1.0", True),
        (" 0.0 ", False),
        ("2", True),
        ("false", False),
        ("Off", False),
        ("no", False),
        ("0", False),
        ("", None),
        ("  ", None),
        ("maybe", None),
        (None, None),
        ([True], None),
        ({"v": 1}, None),
    ],
)
def test_release_value_accepts_only_yes_no_values(value, expected):
    assert hs._release_value(value) is expected


# ── Locking ─────────────────────────────────────────────────────────────


def test_a_locked_consumer_is_skipped_and_its_share_goes_to_the_next_released_one():
    # The example of the issue: water heater locked, charger first, pool pump gets the rest.
    sim = Sim([pct("heater", power_w=3000, on_delay_s=0), pct("charger", power_w=4000, on_delay_s=0), pct("pool", power_w=2000, on_delay_s=0)])
    out = sim.tick(grid=-5000, c_heater_enable=False)
    assert (out["c_heater"], out["c_heater_status"]) == (0, "blocked")
    assert (out["c_charger"], out["c_charger_status"]) == (100, "active")
    assert (out["c_pool"], out["c_pool_status"]) == (50, "reduced")
    assert sim.cstate("heater")["blocked"] is True


def test_an_unwired_release_uses_its_configured_default():
    sim = Sim([pct("a", on_delay_s=0, enable_default=False), pct("b", on_delay_s=0)])
    out = sim.tick(grid=-5000)
    assert out["c_a_status"] == "blocked"
    assert out["c_b"] == 50


def test_a_wired_release_overrides_the_default():
    sim = Sim([pct("a", on_delay_s=0, enable_default=False)])
    assert sim.tick(grid=-5000, c_a_enable=True)["c_a"] == 50


def test_locking_between_cycles_switches_off_at_once_and_ignores_minimum_runtime():
    sim = Sim([onoff("a", on_delay_s=0, min_runtime_s=3600), pct("b", on_delay_s=0)], only_on_change=True)
    out = sim.tick(grid=-3000, c_a_enable=True)
    assert out["c_a"] is True
    out = sim.tick(advance=1, grid=-3000, c_a_enable=False)  # inside the control interval
    assert out == {"c_a": False, "c_a_status": "blocked"}
    assert sim.cstate("a")["on"] is False
    # still locked on the next tick inside the interval: nothing is repeated
    assert sim.tick(advance=1, grid=-1000, c_a_enable=False) == {}


def test_locking_on_a_cycle_hands_out_its_draw_only_once_the_meter_shows_it_freed():
    # Like a lock between cycles: a slowly ramping-down load must not overlap
    # with the lower priority that takes over its power.
    sim = Sim([onoff("a", on_delay_s=0), pct("b", power_w=4000, on_delay_s=0)])
    sim.tick(grid=-2000)  # a on (2 kW), b nothing left
    out = sim.tick(grid=0, c_a_enable=False)  # a still draws its 2 kW
    assert (out["c_a"], out["c_a_status"]) == (False, "blocked")
    assert (out["budget"], out["c_b"]) == (0, 0)
    out = sim.tick(grid=-2000, c_a_enable=False)  # a has gone off
    assert out["c_b"] == 50


def test_a_lock_ends_a_percent_consumer_inside_its_switch_off_delay():
    sim = Sim([pct("a", on_delay_s=0, below_min="hold_min", min_setpoint=50, off_delay_s=600)])
    sim.tick(grid=-8000)
    assert sim.tick(grid=6000)["c_a_status"] == "off_delay"
    out = sim.tick(grid=6000, c_a_enable=False)
    assert (out["c_a"], out["c_a_status"]) == (0, "blocked")


def test_a_lock_cancels_a_running_on_delay():
    sim = Sim([onoff("a", on_delay_s=60)])
    assert sim.tick(grid=-3000)["c_a_status"] == "on_delay"
    sim.tick(grid=-3000, c_a_enable=False)
    assert sim.cstate("a")["on_pending"] is None


def test_a_locked_trigger_ends_its_pulse_and_does_not_fire():
    sim = Sim([trig("t", on_delay_s=0, pulse_s=100, reserve_w=1500, rearm="after_lockout", lockout_s=0), pct("p", power_w=3000, on_delay_s=0)])
    out = sim.tick(grid=-3000)
    assert (out["c_t"], out["c_p"]) == (True, 50)
    out = sim.tick(advance=1, grid=-3000, c_t_enable=False)
    assert out["c_t"] is False
    assert out["c_t_status"] == "blocked"
    assert sim.cstate("t")["pulse_until"] is None
    assert sim.cstate("t")["reserve_cycles"] == 0
    # locked: never fires, and its reservation no longer cuts the lower priority
    for _ in range(3):
        out = sim.tick(grid=-1500, c_t_enable=False)
        assert out.get("c_t") is not True
        assert out["c_t_status"] == "blocked"
    assert sim.cstate("p")["setpoint"] == 100


def test_an_inactive_consumer_stays_inactive_when_also_locked():
    sim = Sim([pct("a", active=False), pct("b", on_delay_s=0)], only_on_change=True)
    out = sim.tick(grid=-5000, c_a_enable=False)
    assert out["c_a_status"] == "inactive"
    assert sim.tick(advance=1, grid=-5000, c_a_enable=False) == {}


# ── Releasing again ─────────────────────────────────────────────────────


def test_a_released_consumer_returns_to_its_position_at_the_next_cycle_with_its_delays():
    sim = Sim([onoff("a", on_delay_s=60, min_off_s=0), pct("b", power_w=4000, on_delay_s=0)], only_on_change=True)
    out = sim.tick(grid=-2000, c_a_enable=False)
    assert out["c_b"] == 50
    # released inside the interval: plainly off until the next cycle
    assert sim.tick(advance=1, grid=0, c_a_enable=True) == {"c_a_status": "off"}
    out = sim.tick(advance=29, grid=0, c_a_enable=True)
    assert out["c_a_status"] == "on_delay"  # not switched on with an old setpoint
    out = sim.tick(advance=60, grid=0, c_a_enable=True)
    assert (out["c_a"], out["c_a_status"]) == (True, "active")  # first in line again
    assert out["c_b"] == 0


def test_a_released_consumer_waits_out_its_minimum_off_time():
    sim = Sim([onoff("a", on_delay_s=0, min_off_s=300)])
    sim.tick(grid=-3000)
    sim.tick(grid=-1000, c_a_enable=False)
    out = sim.tick(grid=-3000, c_a_enable=True)
    assert out["c_a_status"] == "locked"  # the minimum off time, not the release
    sim.tick(advance=300, grid=-3000, c_a_enable=True)
    assert sim.cstate("a")["on"] is True


def test_a_released_trigger_waits_for_its_surplus_delay():
    sim = Sim([trig("t", on_delay_s=60)])
    sim.tick(grid=-3000, c_t_enable=False)
    assert sim.tick(grid=-3000, c_t_enable=True)["c_t_status"] == "on_delay"


# ── Missing, invalid and stale release values ───────────────────────────


def test_a_wired_release_without_a_value_locks_by_default(caplog):
    sim = Sim([pct("a", on_delay_s=0)])
    with caplog.at_level(logging.WARNING, logger="obs.logic.hems_surplus"):
        out = sim.tick(grid=-5000, c_a_enable=None)
        sim.tick(grid=-5000, c_a_enable="garbage")
    assert out["c_a_status"] == "blocked"
    assert sim.cstate("a")["release_fault"] is True
    assert sum("release of consumer" in r.message for r in caplog.records) == 1  # logged once
    sim.tick(grid=-5000, c_a_enable=True)
    assert sim.cstate("a")["release_fault"] is False


@pytest.mark.parametrize(("default", "expected"), [(True, "active"), (False, "blocked")])
def test_the_default_fault_behaviour_uses_the_configured_default(caplog, default, expected):
    sim = Sim([pct("a", on_delay_s=0, enable_fault="default", enable_default=default)])
    with caplog.at_level(logging.WARNING, logger="obs.logic.hems_surplus"):
        out = sim.tick(grid=-12000, c_a_enable="maybe")
    assert out["c_a_status"] == expected
    assert "using its default release" in caplog.text


def test_a_release_that_stops_arriving_is_held_then_stale():
    sim = Sim([pct("a", on_delay_s=0)], interval_s=30)
    assert sim.tick(grid=-5000, c_a_enable=True)["c_a"] == 50
    # nothing arrives: the last value counts for two control intervals ...
    assert sim.tick(grid=0, c_a_enable=None)["c_a"] == 50
    assert sim.tick(grid=0, c_a_enable=None)["c_a"] == 50
    # ... then the value is stale and the consumer is locked
    assert sim.tick(grid=-5000, c_a_enable=None)["c_a_status"] == "blocked"


def test_a_removed_consumer_forgets_its_held_release():
    sim = Sim([pct("a", on_delay_s=0)])
    sim.tick(grid=-5000, c_a_enable=False)
    assert "c_a_enable" in sim.state["held"]
    sim.config["consumers"] = []
    sim.tick(grid=-5000)
    assert "c_a_enable" not in sim.state["held"]


# ── Safe state ──────────────────────────────────────────────────────────


def test_the_safe_state_reports_a_locked_consumer_as_locked():
    sim = Sim([pct("a", on_delay_s=0), pct("b", on_delay_s=0), pct("c", active=False)])
    out = sim.tick(grid=None, c_a_enable=False, c_c_enable=False)
    assert (out["c_a"], out["c_a_status"]) == (0, "blocked")
    assert (out["c_b"], out["c_b_status"]) == (0, "safe")
    assert out["c_c_status"] == "safe"  # inactive: not a release question


def test_a_locked_consumer_is_not_held_on_a_meter_fault():
    sim = Sim([onoff("a", on_delay_s=0), onoff("b", on_delay_s=0)], invalid_behavior="hold", only_on_change=True)
    out = sim.tick(grid=-4000)
    assert (out["c_a"], out["c_b"]) == (True, True)
    out = sim.tick(advance=1, grid="bad", c_a_enable=False)
    assert (out["c_a"], out["c_a_status"]) == (False, "blocked")
    assert out["c_b_status"] == "safe_hold"
    assert "c_b" not in out


# ── Feed-in-only hold at 0 W ────────────────────────────────────────────


def test_a_consumer_locked_at_zero_feed_in_is_not_chosen_as_the_shed_step():
    sim = Sim([onoff("a", on_delay_s=0), onoff("b", on_delay_s=0)], grid_mode="feed_in_only", off_delay_s=0)
    out = sim.tick(feed_in=4000)
    assert (out["c_a"], out["c_b"]) == (True, True)
    sim.tick(feed_in=0)  # entering 0 W: hold
    out = sim.tick(feed_in=0, c_b_enable=False)
    # b's lock is this cycle's reduction; the next step falls on a, never on locked b
    assert out["c_b_status"] == "blocked"
    assert not sim.state["zero_shed_w"]
    sim.tick(feed_in=0, c_b_enable=False)
    assert sim.state["zero_shed_id"] == "a"


# ── Through the executor ────────────────────────────────────────────────


def test_a_wired_release_that_delivers_nothing_locks_only_that_consumer():
    consumers = [
        {"id": "boiler", "mode": "onoff", "power_w": 2000, "on_delay_s": 0},
        {"id": "pool", "mode": "onoff", "power_w": 1000, "on_delay_s": 0},
    ]
    flow = FlowData.model_validate(
        {
            "nodes": [node("ed", "edge_detect", {}), node("h", "hems_surplus", {"consumers": consumers})],
            "edges": [edge("ed", "h", "out", "c_boiler_enable")],
        }
    )
    out = GraphExecutor(flow, {}).execute(input_overrides={"h": {"grid": -5000}})["h"]
    assert out["status"] == "ok"
    assert (out["c_boiler"], out["c_boiler_status"]) == (False, "blocked")
    assert (out["c_pool"], out["c_pool_status"]) == (True, "active")


# ── Review follow-ups ───────────────────────────────────────────────────


def test_a_consumer_released_between_cycles_reports_off_until_the_next_cycle():
    sim = Sim([onoff("a", on_delay_s=0)], only_on_change=True)
    assert sim.tick(grid=-1000, c_a_enable=False)["c_a_status"] == "blocked"
    assert sim.tick(advance=1, grid=-1000, c_a_enable=True) == {"c_a_status": "off"}
    assert sim.tick(advance=1, grid=-1000, c_a_enable=True) == {}


def test_the_block_wakes_when_a_held_release_expires():
    sim = Sim([pct("a", on_delay_s=0)], interval_s=600, max_age_s=30)
    sim.tick(grid=-5000, grid_changed=True, c_a_enable=True)
    # the release expires before the meter (same moment) and long before the next cycle
    assert sim.state["next_wake"] == pytest.approx(sim.now + 30 + hs._STALE_WAKE_MARGIN_S)
    out = sim.tick(advance=30 + hs._STALE_WAKE_MARGIN_S, grid=-5000, grid_changed=True, c_a_enable=None)
    assert out["c_a_status"] == "blocked"
    # locked already: its expired release no longer schedules a wake
    assert sim.state["next_wake"] == pytest.approx(sim.now + 30 + hs._STALE_WAKE_MARGIN_S)


def test_an_expired_release_of_a_released_consumer_schedules_no_wake_in_the_past():
    sim = Sim([pct("a", on_delay_s=0, enable_fault="default")], interval_s=30)
    sim.tick(grid=-5000, c_a_enable=True)
    sim.tick(advance=100, grid=-5000, c_a_enable=None)  # held value expired: falls back to the default
    assert sim.cstate("a")["blocked"] is False
    assert sim.state["next_wake"] == sim.now + 30


def test_a_mode_change_does_not_log_an_ongoing_release_fault_again(caplog):
    consumers = [onoff("a", on_delay_s=0)]
    sim = Sim(consumers)
    with caplog.at_level(logging.WARNING, logger="obs.logic.hems_surplus"):
        sim.tick(grid=-5000, c_a_enable="garbage")
        consumers[0]["mode"] = "percent"
        sim.tick(grid=-5000, c_a_enable="garbage")
    assert sum("release of consumer" in r.message for r in caplog.records) == 1


@pytest.mark.parametrize(
    ("consumer", "wakes"),
    [
        (pct("a", enable_fault="default", enable_default=False), True),  # expiry locks: wake
        (pct("a", enable_fault="default", enable_default=True), False),  # expiry changes nothing
        (pct("a", active=False), False),  # inactive: off anyway
    ],
)
def test_a_held_release_wakes_the_block_only_when_its_expiry_changes_something(consumer, wakes):
    sim = Sim([consumer], interval_s=600, max_age_s=30)
    sim.tick(grid=-5000, c_a_enable=True)
    expiry = sim.now + 30 + hs._STALE_WAKE_MARGIN_S
    assert (sim.state["next_wake"] == pytest.approx(expiry)) is wakes


@pytest.mark.parametrize(
    ("consumer", "wakes"),
    [
        (pct("a", enable_fault="default", enable_default=True), True),  # expiry releases: wake
        (pct("a", enable_fault="default", enable_default=False), False),  # stays locked
        (pct("a"), False),  # the fault locks: stays locked
    ],
)
def test_a_held_lock_wakes_the_block_only_when_its_expiry_releases(consumer, wakes):
    sim = Sim([consumer], interval_s=600, max_age_s=30)
    sim.tick(grid=-5000, c_a_enable=False)
    expiry = sim.now + 30 + hs._STALE_WAKE_MARGIN_S
    assert (sim.state["next_wake"] == pytest.approx(expiry)) is wakes


def test_an_inactive_consumer_does_not_log_a_release_fault(caplog):
    sim = Sim([pct("a", active=False)])
    with caplog.at_level(logging.WARNING, logger="obs.logic.hems_surplus"):
        sim.tick(grid=-5000, c_a_enable="garbage")
    assert "release of consumer" not in caplog.text


def test_a_lock_on_a_cycle_at_zero_feed_in_voids_its_shed_step_instead_of_cutting_a_higher_priority():
    consumers = [onoff("hi", power_w=1000, on_delay_s=0), onoff("lo", power_w=500, on_delay_s=0, min_runtime_s=3600)]
    sim = Sim(consumers, grid_mode="feed_in_only", off_delay_s=0)
    out = sim.tick(feed_in=2000)
    assert (out["c_hi"], out["c_lo"]) == (True, True)
    sim.tick(feed_in=0)  # entering 0 W: hold
    sim.tick(feed_in=0)  # a step of "lo" is requested; its minimum runtime keeps it pending
    assert (sim.state["zero_shed_id"], sim.state["zero_shed_w"]) == ("lo", 500)
    out = sim.tick(feed_in=0, c_lo_enable=False)  # the lock lands on a cycle tick
    assert out["c_lo_status"] == "blocked"
    assert sim.cstate("hi")["on"] is True  # the step was lo's, never cut from hi
    assert sim.state["zero_shed_w"] == 0.0


def test_a_consumer_activated_with_a_faulty_release_logs_the_fault(caplog):
    consumers = [pct("a", active=False)]
    sim = Sim(consumers)
    with caplog.at_level(logging.WARNING, logger="obs.logic.hems_surplus"):
        sim.tick(grid=-5000, c_a_enable="garbage")
        assert "release of consumer" not in caplog.text
        consumers[0]["active"] = True
        out = sim.tick(grid=-5000, c_a_enable="garbage")
    assert out["c_a_status"] == "blocked"
    assert "release of consumer" in caplog.text


def test_a_mode_change_keeps_an_ongoing_lock():
    consumers = [onoff("a", on_delay_s=0)]
    sim = Sim(consumers)
    sim.tick(grid=-5000, c_a_enable=False)
    consumers[0]["mode"] = "percent"
    out = sim.tick(grid=-5000, c_a_enable=False)
    assert (out["c_a"], out["c_a_status"]) == (0, "blocked")
    assert sim.cstate("a")["blocked"] is True


def test_an_expired_lock_falls_back_to_a_releasing_default_at_its_expiry():
    sim = Sim([pct("a", on_delay_s=0, enable_fault="default")], interval_s=600, max_age_s=30)
    assert sim.tick(grid=-5000, c_a_enable=False)["c_a_status"] == "blocked"
    assert sim.state["next_wake"] == pytest.approx(sim.now + 30 + hs._STALE_WAKE_MARGIN_S)
    out = sim.tick(advance=30 + hs._STALE_WAKE_MARGIN_S, grid=-5000, c_a_enable=None)
    assert out["c_a_status"] == "off"  # released; it joins at the next cycle
    assert sim.cstate("a")["blocked"] is False


def test_a_release_held_from_a_removed_wire_is_not_used_by_a_wire_added_later():
    sim = Sim([pct("a", on_delay_s=0, enable_default=False)])
    sim.tick(grid=-5000, c_a_enable=True)
    assert sim.cstate("a")["blocked"] is False
    assert sim.tick(advance=1, grid=-5000)["c_a_status"] == "blocked"  # unwired: the default locks
    assert "c_a_enable" not in sim.state["held"]
    sim.tick(advance=1, grid=-5000, c_a_enable=None)  # rewired to a source without a value yet
    assert sim.cstate("a")["blocked"] is True


def test_a_stored_id_ending_in_enable_keeps_its_ports_beside_the_consumer_it_names():
    flow = FlowData.model_validate(
        {
            "nodes": [
                {
                    "id": "h",
                    "type": "hems_surplus",
                    "position": {"x": 0, "y": 0},
                    "data": {"consumers": [onoff("boiler", power_w=1000, on_delay_s=0), onoff("boiler_enable", power_w=1000, on_delay_s=0)]},
                }
            ]
        }
    )
    out = GraphExecutor(flow, {}).execute(input_overrides={"h": {"grid": -5000, "c_boiler_enable": False}})["h"]
    assert (out["c_boiler"], out["c_boiler_status"]) == (False, "blocked")
    assert (out["c_boiler_enable"], out["c_boiler_enable_status"]) == (True, "active")


def test_a_lock_at_zero_feed_in_postpones_a_step_pending_for_another_consumer():
    consumers = [onoff("hi", power_w=1000, on_delay_s=0), onoff("lo", power_w=500, on_delay_s=0, min_runtime_s=90)]
    sim = Sim(consumers, grid_mode="feed_in_only", off_delay_s=0)
    sim.tick(feed_in=2000)
    sim.tick(feed_in=0)  # entering 0 W: hold
    sim.tick(feed_in=0)  # a step of "lo" is requested; its minimum runtime keeps it pending
    assert (sim.state["zero_shed_id"], sim.state["zero_shed_w"]) == ("lo", 500)
    # lo's minimum runtime ends on the cycle "hi" is locked: the lock is that cycle's only reduction
    out = sim.tick(feed_in=0, c_hi_enable=False)
    assert (out["c_hi"], out["c_hi_status"]) == (False, "blocked")
    assert sim.cstate("lo")["on"] is True
    assert sim.state["zero_shed_w"] == 0.0
    out = sim.tick(feed_in=0, c_hi_enable=False)  # the next step follows in the next cycle
    assert out["c_lo"] is False


def test_a_lock_at_zero_feed_in_keeps_the_feed_in_reserve_requested():
    consumers = [onoff("hi", power_w=1000, on_delay_s=0), onoff("lo", power_w=500, on_delay_s=0, off_delay_s=600)]
    sim = Sim(consumers, grid_mode="feed_in_only", off_delay_s=0, target_w=300)
    sim.tick(feed_in=2000)
    sim.tick(feed_in=0)  # entering 0 W: the reserve stands as a reduction request
    assert (sim.state["zero_shed_id"], sim.state["zero_shed_w"]) == (None, 300)
    sim.tick(feed_in=0, c_hi_enable=False)
    assert sim.cstate("hi")["on"] is False
    assert (sim.state["zero_shed_id"], sim.state["zero_shed_w"]) == (None, 300)
