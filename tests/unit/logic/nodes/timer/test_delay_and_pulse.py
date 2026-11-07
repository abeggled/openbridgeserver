from __future__ import annotations

from obs.logic.nodes.timer.delay import NODE_TYPE as TIMER_DELAY
from obs.logic.nodes.timer.pulse import NODE_TYPE as TIMER_PULSE


def test_delay_duration_is_non_negative():
    assert TIMER_DELAY.config_schema["delay_s"]["min"] == 0
    assert TIMER_DELAY.config_schema["delay_s"]["default"] == 1.0


def test_pulse_duration_is_non_negative():
    assert TIMER_PULSE.config_schema["duration_s"]["min"] == 0
    assert TIMER_PULSE.config_schema["duration_s"]["default"] == 1.0


def test_delay_passes_a_trigger_through_while_pulse_emits_a_value():
    assert [port.type for port in TIMER_DELAY.outputs] == ["trigger"]
    assert [port.type for port in TIMER_PULSE.outputs] == ["value"]
