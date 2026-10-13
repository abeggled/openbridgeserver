"""Seam S2 (#1296): telegram in → datapoint value out, for every ETS address style.

A binding may carry its group address in any of the three ETS notations
(``1/0/234``, ``1/234``, ``2282``). The adapter must deliver an incoming
telegram to the bound datapoint regardless of the notation.

The adapter is connected through its real ``connect()`` with only the network
start of xknx stubbed out; telegrams enter through xknx's own incoming path
(``telegram_queue.process_telegram_incoming``), and the result is observed on
the event bus — never on the adapter's internal tables.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from xknx import XKNX
from xknx.cemi import CEMIHandler
from xknx.dpt import DPTArray, DPTBinary
from xknx.telegram import Telegram, TelegramDirection
from xknx.telegram.address import GroupAddress
from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite

from obs.adapters.knx.adapter import KnxAdapter
from obs.adapters.knx.dpt_registry import DPTRegistry
from obs.core.event_bus import DataValueEvent
from tests.adapters.conftest import make_binding

# The same two raw addresses written in each ETS notation.
COMMAND_RAW = 2282  # 1/0/234
STATE_RAW = 2283  # 1/0/235
NOTATIONS = {
    "ThreeLevel": {COMMAND_RAW: "1/0/234", STATE_RAW: "1/0/235"},
    "TwoLevel": {COMMAND_RAW: "1/234", STATE_RAW: "1/235"},
    "Free": {COMMAND_RAW: "2282", STATE_RAW: "2283"},
}
STYLES = list(NOTATIONS)


@pytest.fixture
async def connected_adapter(monkeypatch, mock_bus):
    """A KnxAdapter connected through connect(), without touching the network."""
    monkeypatch.setattr(XKNX, "start", AsyncMock())
    monkeypatch.setattr(XKNX, "stop", AsyncMock())
    adapter = KnxAdapter(event_bus=mock_bus, config={"connection_type": "routing", "local_ip": "127.0.0.1"})
    # Outgoing telegrams leave through the cEMI handler; stub the wire, keep the queue logic.
    monkeypatch.setattr(CEMIHandler, "send_telegram", AsyncMock())
    await adapter.connect()
    try:
        yield adapter
    finally:
        await adapter.disconnect()


async def _settle() -> None:
    """Let the sniffer's ensure_future(adapter._on_telegram(...)) run."""
    for _ in range(5):
        await asyncio.sleep(0)


def _value_events(mock_bus) -> list[DataValueEvent]:
    return [call.args[0] for call in mock_bus.publish.call_args_list if isinstance(call.args[0], DataValueEvent)]


def _incoming(raw_address: int, payload) -> Telegram:
    # The destination is built from the raw number, so the test does not depend on
    # how xknx happens to format addresses.
    return Telegram(destination_address=GroupAddress(raw_address), direction=TelegramDirection.INCOMING, payload=payload)


@pytest.mark.parametrize("style", STYLES)
async def test_incoming_write_reaches_the_bound_datapoint(style, connected_adapter, mock_bus):
    dpt = DPTRegistry.get("DPT9.001")
    binding = make_binding({"group_address": NOTATIONS[style][COMMAND_RAW], "dpt_id": "DPT9.001"})
    await connected_adapter.reload_bindings([binding])

    await connected_adapter._xknx.telegram_queue.process_telegram_incoming(
        _incoming(COMMAND_RAW, GroupValueWrite(DPTArray(list(dpt.encoder(21.5))))),
    )
    await _settle()

    events = _value_events(mock_bus)
    assert [event.datapoint_id for event in events] == [binding.datapoint_id]
    assert events[0].value == pytest.approx(21.5, abs=0.1)
    assert events[0].quality == "good"


@pytest.mark.parametrize("style", STYLES)
async def test_incoming_state_feedback_reaches_the_bound_datapoint(style, connected_adapter, mock_bus):
    binding = make_binding(
        {
            "group_address": NOTATIONS[style][COMMAND_RAW],
            "state_group_address": NOTATIONS[style][STATE_RAW],
            "dpt_id": "DPT1.001",
        },
        direction="BOTH",
    )
    await connected_adapter.reload_bindings([binding])

    await connected_adapter._xknx.telegram_queue.process_telegram_incoming(_incoming(STATE_RAW, GroupValueWrite(DPTBinary(1))))
    await _settle()

    events = _value_events(mock_bus)
    assert [(event.datapoint_id, event.value) for event in events] == [(binding.datapoint_id, True)]


@pytest.mark.parametrize("style", STYLES)
async def test_read_request_on_a_styled_binding_is_answered(style, connected_adapter, mock_bus):
    binding = make_binding({"group_address": NOTATIONS[style][COMMAND_RAW], "dpt_id": "DPT1.001", "respond_to_read": True})
    await connected_adapter.reload_bindings([binding])
    connected_adapter.set_value_getter(lambda _dp_id: type("State", (), {"quality": "good", "value": True})())

    await connected_adapter._xknx.telegram_queue.process_telegram_incoming(_incoming(COMMAND_RAW, GroupValueRead()))
    await _settle()

    queued = connected_adapter._xknx.telegrams
    assert queued.qsize() == 1
    response = queued.get_nowait()
    assert response.destination_address == GroupAddress(COMMAND_RAW)
    assert isinstance(response.payload, GroupValueResponse)


@pytest.mark.parametrize("style", STYLES)
async def test_state_feedback_after_own_write_is_recognized_as_confirmation(style, connected_adapter, mock_bus):
    """The confirmation bookkeeping compares the binding's state GA with the telegram's GA."""
    binding = make_binding(
        {
            "group_address": NOTATIONS[style][COMMAND_RAW],
            "state_group_address": NOTATIONS[style][STATE_RAW],
            "dpt_id": "DPT1.001",
        },
        direction="BOTH",
    )
    binding.enabled = True
    await connected_adapter.reload_bindings([binding])

    assert await connected_adapter.write_with_context(binding, True, logical_value=True) is True
    outgoing = connected_adapter._xknx.telegrams.get_nowait()
    await connected_adapter._xknx.telegram_queue.process_telegram_outgoing(outgoing)
    await _settle()
    mock_bus.publish.reset_mock()

    await connected_adapter._xknx.telegram_queue.process_telegram_incoming(_incoming(STATE_RAW, GroupValueWrite(DPTBinary(1))))
    await _settle()

    events = _value_events(mock_bus)
    assert [(event.datapoint_id, event.value) for event in events] == [(binding.datapoint_id, True)]
    assert events[0].suppress_write_propagation is True, "state feedback of the own write must count as confirmation"


async def test_broken_state_address_does_not_disable_writes(connected_adapter, mock_bus):
    """A broken feedback GA is treated as absent: the valid command GA keeps writing."""
    binding = make_binding({"group_address": "1/234", "state_group_address": "1/2/x", "dpt_id": "DPT1.001"}, direction="BOTH")
    binding.enabled = True
    await connected_adapter.reload_bindings([binding])

    assert await connected_adapter.write_with_context(binding, True, logical_value=True) is True
    sent = connected_adapter._xknx.telegrams.get_nowait()
    assert sent.destination_address == GroupAddress(COMMAND_RAW)

    await connected_adapter._xknx.telegram_queue.process_telegram_incoming(_incoming(COMMAND_RAW, GroupValueWrite(DPTBinary(0))))
    await _settle()
    assert [(event.datapoint_id, event.value) for event in _value_events(mock_bus)] == [(binding.datapoint_id, False)]


async def test_broken_group_addresses_are_reported_on_the_adapter_status(connected_adapter, mock_bus):
    """Not only in the log: the adapter card shows a warning naming the bindings (#1296)."""
    broken_state = make_binding({"group_address": "1/234", "state_group_address": "1/2/x", "dpt_id": "DPT1.001"}, direction="BOTH")
    broken_command = make_binding({"group_address": "32/0/0", "dpt_id": "DPT1.001"})
    healthy = make_binding({"group_address": "1/235", "dpt_id": "DPT1.001"})

    await connected_adapter.reload_bindings([broken_state, broken_command, healthy])
    assert connected_adapter.last_severity == "warning"
    assert connected_adapter.last_detail_code == "knxInvalidGroupAddresses"
    assert connected_adapter.last_detail_params["count"] == 2
    assert str(broken_state.id) in connected_adapter.last_detail_params["examples"]
    assert str(broken_command.id) in connected_adapter.last_detail_params["examples"]

    await connected_adapter.reload_bindings([healthy])
    assert connected_adapter.last_severity == "ok"
    assert connected_adapter.last_detail_code != "knxInvalidGroupAddresses"


async def test_invalid_group_address_warning_survives_later_connection_status(connected_adapter, mock_bus):
    """A later "ok" status (reconnect, tunnel pool stable) must not hide the warning while the problem persists."""
    broken = make_binding({"group_address": "32/0/0", "dpt_id": "DPT1.001"})
    await connected_adapter.reload_bindings([broken])
    assert connected_adapter.last_detail_code == "knxInvalidGroupAddresses"

    connected_adapter._warning_active = True  # a tunnel-overload warning was raised meanwhile …
    await connected_adapter._record_reconnect()  # … and clears with an "ok" status once the pool is quiet
    assert connected_adapter.last_severity == "warning"
    assert connected_adapter.last_detail_code == "knxInvalidGroupAddresses"

    await connected_adapter.reload_bindings([])
    assert connected_adapter.last_severity == "ok"


def _status_events(mock_bus):
    from obs.core.event_bus import AdapterStatusEvent

    return [call.args[0] for call in mock_bus.publish.call_args_list if isinstance(call.args[0], AdapterStatusEvent)]


async def test_invalid_group_address_hint_never_hides_a_connection_error(monkeypatch, mock_bus):
    """The GA hint is in addition to the connection status: an error stays an error (#1296, round 4).

    The connection fails, then the bindings arrive (one with a broken feedback GA):
    the card keeps the error. Once the connection works, the hint shows.
    """
    monkeypatch.setattr(XKNX, "stop", AsyncMock())
    monkeypatch.setattr(XKNX, "start", AsyncMock(side_effect=OSError("Tunnel connection could not be established")))
    adapter = KnxAdapter(event_bus=mock_bus, config={"connection_type": "routing", "local_ip": "127.0.0.1"})
    broken = make_binding({"group_address": "1/234", "state_group_address": "1/2/x", "dpt_id": "DPT1.001"}, direction="BOTH")
    try:
        await adapter.connect()
        await adapter.reload_bindings([broken])
        assert (adapter.last_severity, adapter.connected) == ("error", False)
        assert adapter.last_detail_code != "knxInvalidGroupAddresses"
        assert [event.severity for event in _status_events(mock_bus)][-1] == "error"

        monkeypatch.setattr(XKNX, "start", AsyncMock())
        await adapter.connect()  # the reconnect path
        assert (adapter.last_severity, adapter.last_detail_code, adapter.connected) == ("warning", "knxInvalidGroupAddresses", True)
        assert _status_events(mock_bus)[-1].connected is True
    finally:
        await adapter.disconnect()


async def test_disconnect_reports_disconnected_also_with_the_hint(monkeypatch, mock_bus):
    """After disconnect() the status event says connected=False, also while the GA hint is shown."""
    monkeypatch.setattr(XKNX, "start", AsyncMock())
    monkeypatch.setattr(XKNX, "stop", AsyncMock())
    adapter = KnxAdapter(event_bus=mock_bus, config={"connection_type": "routing", "local_ip": "127.0.0.1"})
    await adapter.connect()
    await adapter.reload_bindings([make_binding({"group_address": "32/0/0", "dpt_id": "DPT1.001"})])
    assert adapter.connected is True

    await adapter.disconnect()

    last = _status_events(mock_bus)[-1]
    assert (last.connected, adapter.connected) == (False, False)


async def test_disconnect_shows_disconnected_instead_of_the_hint(monkeypatch, mock_bus):
    """A hint never hides a more important state: after disconnect() the card says "disconnected" (#1296, round 5)."""
    monkeypatch.setattr(XKNX, "start", AsyncMock())
    monkeypatch.setattr(XKNX, "stop", AsyncMock())
    adapter = KnxAdapter(event_bus=mock_bus, config={"connection_type": "routing", "local_ip": "127.0.0.1"})
    await adapter.connect()
    await adapter.reload_bindings([make_binding({"group_address": "32/0/0", "dpt_id": "DPT1.001"})])
    assert adapter.last_detail_code == "knxInvalidGroupAddresses"

    await adapter.disconnect()

    last = _status_events(mock_bus)[-1]
    assert (last.connected, adapter.last_detail_code) == (False, "disconnected")


async def test_tunnel_overload_warning_is_not_hidden_by_the_hint(connected_adapter, mock_bus):
    """Two warnings: the connection's own one wins while it lasts, the hint returns after it clears."""
    await connected_adapter.reload_bindings([make_binding({"group_address": "32/0/0", "dpt_id": "DPT1.001"})])
    for _ in range(3):
        await connected_adapter._record_disconnect()  # xknx reports tunnel disconnects through this path
    assert connected_adapter.last_detail_code == "knxTunnelOverload"

    connected_adapter._disconnect_times.clear()
    await connected_adapter._record_reconnect()
    assert connected_adapter.last_detail_code == "knxInvalidGroupAddresses"
