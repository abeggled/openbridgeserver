from __future__ import annotations

from obs.logic.nodes.timer.interval import NODE_TYPE as TIMER_INTERVAL
from obs.logic.registry import get_node_type


def test_interval_is_registered_in_the_timer_category():
    registered = get_node_type("timer_interval")
    assert registered is not None
    assert registered.has_external_side_effect is False
    assert TIMER_INTERVAL.category == "timer"
    assert TIMER_INTERVAL.help_id == "logic-block-timer-interval"


def test_interval_default_and_bound():
    assert TIMER_INTERVAL.config_schema["interval_s"]["min"] == 0
    assert TIMER_INTERVAL.config_schema["interval_s"]["default"] == 5.0


def test_interval_has_no_input_and_fires_a_trigger():
    assert TIMER_INTERVAL.inputs == []
    assert [port.type for port in TIMER_INTERVAL.outputs] == ["trigger"]
