"""Protocol tests for the multiplexed HA WebSocket connection (#1392)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from obs.adapters.homeassistant.ws_client import HaAuthError, HaCommandError, HaConnection, HaProtocolError
from tests.adapters.ha_fake import FakeHaSocket, FakeHomeAssistant, settle

TOKEN = "secret-token"


async def _open(fake: FakeHomeAssistant) -> HaConnection:
    return await HaConnection.open("ws://ha/api/websocket", TOKEN, connect=fake.connect)


class _ScriptedSocket(FakeHaSocket):
    """Socket whose server replies are scripted instead of computed."""

    def __init__(self, replies: list) -> None:
        super().__init__(FakeHomeAssistant(token=TOKEN))
        self._queue = asyncio.Queue()
        for reply in replies:
            self._queue.put_nowait(reply)

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))


async def test_authenticates_and_reports_version():
    fake = FakeHomeAssistant(token=TOKEN, ha_version="2026.1.0")
    conn = await _open(fake)
    assert conn.ha_version == "2026.1.0"
    assert fake.socket.sent[0] == {"type": "auth", "access_token": TOKEN}
    await conn.close()
    assert conn.closed


async def test_auth_invalid_raises_without_token_in_message():
    fake = FakeHomeAssistant(token="other")
    with pytest.raises(HaAuthError) as exc_info:
        await _open(fake)
    assert TOKEN not in str(exc_info.value)
    assert fake.socket.closed


@pytest.mark.parametrize(
    "replies",
    [
        [{"type": "hello"}],
        [{"type": "auth_required"}, {"type": "something"}],
    ],
)
async def test_protocol_violations_raise(replies):
    sock = _ScriptedSocket(replies)
    with pytest.raises(HaProtocolError):
        await HaConnection.open("ws://ha", TOKEN, connect=AsyncMock(return_value=sock))


async def test_command_success_error_and_closed():
    fake = FakeHomeAssistant({"sensor.a": {"state": "1"}}, token=TOKEN)
    conn = await _open(fake)
    states = await conn.command({"type": "get_states"})
    assert states[0]["entity_id"] == "sensor.a"
    with pytest.raises(HaCommandError) as exc_info:
        await conn.command({"type": "nope"})
    assert exc_info.value.code == "unknown_command"
    fake.socket.drop()
    await settle()
    with pytest.raises(ConnectionError):
        await conn.command({"type": "get_states"})
    with pytest.raises(ConnectionError):
        await conn.subscribe({"type": "subscribe_entities"}, AsyncMock())


async def test_error_result_without_details():
    sock = _ScriptedSocket([{"type": "auth_required"}, {"type": "auth_ok"}])
    conn = await HaConnection.open("ws://ha", TOKEN, connect=AsyncMock(return_value=sock))
    task = asyncio.create_task(conn.command({"type": "x"}))
    await settle()
    sock.push({"id": 1, "type": "result", "success": False})
    with pytest.raises(HaCommandError) as exc_info:
        await task
    assert exc_info.value.code == "unknown_error"
    task = asyncio.create_task(conn.subscribe({"type": "y"}, AsyncMock()))
    await settle()
    sock.push({"id": 2, "type": "result", "success": False})
    with pytest.raises(HaCommandError):
        await task
    await conn.close()


async def test_pending_command_fails_when_connection_drops():
    fake = FakeHomeAssistant(token=TOKEN)
    fake.answer_pings = False
    conn = await _open(fake)
    task = asyncio.create_task(conn.ping(timeout=5))
    await settle()
    fake.socket.drop()
    with pytest.raises(ConnectionError):
        await task


async def test_command_timeout_cleans_up():
    fake = FakeHomeAssistant(token=TOKEN)
    fake.answer_pings = False
    conn = await _open(fake)
    with pytest.raises(TimeoutError):
        await conn.ping(timeout=0.01)
    assert conn._pending == {}
    await conn.close()


async def test_subscribe_timeout_unregisters_callback():
    sock = _ScriptedSocket([{"type": "auth_required"}, {"type": "auth_ok"}])
    conn = await HaConnection.open("ws://ha", TOKEN, connect=AsyncMock(return_value=sock))
    with pytest.raises(TimeoutError):
        await conn.subscribe({"type": "subscribe_entities"}, AsyncMock(), timeout=0.01)
    assert conn._subscriptions == {}
    await conn.close()


async def test_events_routed_and_handler_errors_isolated(caplog):
    fake = FakeHomeAssistant({"sensor.a": {"state": "1"}}, token=TOKEN)
    conn = await _open(fake)
    seen: list = []

    async def handler(event):
        seen.append(event)
        if len(seen) == 1:
            raise RuntimeError("handler bug")

    sub_id = await conn.subscribe({"type": "subscribe_entities", "entity_ids": ["sensor.a"]}, handler)
    fake.set_state("sensor.a", "2")
    await settle()
    assert len(seen) == 2
    assert "event handler failed" in caplog.text
    # unknown result ids and stray pongs are ignored
    fake.socket.push({"id": 4711, "type": "result", "success": True})
    await settle()
    await conn.unsubscribe(sub_id)
    await conn.unsubscribe(sub_id)  # already gone: no command
    assert [m["type"] for m in fake.socket.sent].count("unsubscribe_events") == 1
    await conn.close()


async def test_unsubscribe_on_closed_connection_sends_nothing():
    fake = FakeHomeAssistant({"sensor.a": {"state": "1"}}, token=TOKEN)
    conn = await _open(fake)
    sub_id = await conn.subscribe({"type": "subscribe_entities"}, AsyncMock())
    conn._closed.set()
    await conn.unsubscribe(sub_id)
    assert "unsubscribe_events" not in [m["type"] for m in fake.socket.sent]


async def test_reader_error_closes_connection():
    class Broken(_ScriptedSocket):
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise OSError("reset")

    sock = Broken([{"type": "auth_required"}, {"type": "auth_ok"}])
    conn = await HaConnection.open("ws://ha", TOKEN, connect=AsyncMock(return_value=sock))
    await asyncio.wait_for(conn.wait_closed(), 1)
    assert conn.closed


class _Boom(BaseException):
    pass


async def test_a_dead_event_worker_closes_the_connection(caplog):
    # pings would still succeed: without this nothing reconnects and every value freezes
    conn = await _open(FakeHomeAssistant(token=TOKEN))

    async def fatal(_event):
        raise _Boom

    conn._subscriptions[99] = fatal
    conn._events.put_nowait((99, {}))
    await asyncio.wait_for(conn.wait_closed(), 1)
    assert "event worker stopped" in caplog.text
    await conn.close()

    cancelled = await _open(FakeHomeAssistant(token=TOKEN))
    cancelled._worker.cancel()
    await asyncio.wait_for(cancelled.wait_closed(), 1)
    await cancelled.close()


async def test_a_regular_close_is_no_worker_failure(caplog):
    conn = await _open(FakeHomeAssistant(token=TOKEN))
    await conn.close()
    await asyncio.sleep(0)
    assert "event worker stopped" not in caplog.text


async def test_close_propagates_callers_cancellation():
    fake = FakeHomeAssistant(token=TOKEN)
    conn = await _open(fake)
    started = asyncio.Event()

    async def closer():
        started.set()
        await conn.close()

    original_reader = conn._reader

    async def slow_reader():
        try:
            await asyncio.sleep(10)
        finally:
            await asyncio.sleep(0.05)

    original_reader.cancel()
    conn._reader = asyncio.create_task(slow_reader())
    task = asyncio.create_task(closer())
    await started.wait()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # the interrupted close still stops the worker and closes the connection
    await asyncio.sleep(0)
    assert conn._worker.done()
    assert conn.closed


async def test_unknown_message_types_and_done_futures_are_harmless():
    fake = FakeHomeAssistant(token=TOKEN)
    conn = await _open(fake)
    fake.socket.push({"type": "something_new"})
    await settle()
    done = asyncio.get_running_loop().create_future()
    done.set_result(None)
    conn._pending[99] = done
    await conn.close()
    assert conn._pending == {}


async def test_slow_event_handler_does_not_block_pings_or_results():
    fake = FakeHomeAssistant({"sensor.a": {"state": "1"}}, token=TOKEN)
    conn = await _open(fake)
    release = asyncio.Event()
    seen: list = []

    async def slow(event):
        seen.append(event)
        await release.wait()

    await conn.subscribe({"type": "subscribe_entities", "entity_ids": ["sensor.a"]}, slow)
    await settle()
    assert len(seen) == 1  # snapshot handler is blocked
    await asyncio.wait_for(conn.ping(timeout=1), 1)
    assert (await asyncio.wait_for(conn.command({"type": "get_states"}), 1))[0]["entity_id"] == "sensor.a"
    release.set()
    await conn.close()


async def test_queued_events_of_an_unsubscribed_subscription_are_dropped():
    fake = FakeHomeAssistant({"sensor.a": {"state": "1"}}, token=TOKEN)
    conn = await _open(fake)
    handler = AsyncMock()
    sub_id = await conn.subscribe({"type": "subscribe_entities", "entity_ids": ["sensor.a"]}, handler)
    conn._worker.cancel()  # hold events in the queue
    await settle()
    conn._worker = None
    before = handler.await_count
    fake.set_state("sensor.a", "2")
    await settle()
    await conn.unsubscribe(sub_id)
    conn._worker = asyncio.create_task(conn._event_loop())
    await settle()
    assert handler.await_count == before
    assert conn._events.empty()
    await conn.close()


@pytest.mark.parametrize("close_first", [True, False])
async def test_failed_send_leaves_no_unretrieved_future_error(close_first):
    fake = FakeHomeAssistant(token=TOKEN)
    conn = await _open(fake)
    futures: list[asyncio.Future] = []

    async def send(_data):
        futures.extend(conn._pending.values())
        if close_first:  # the reader saw the close first and failed the pending future
            conn._mark_closed(ConnectionError("closed"))
        raise ConnectionError("send failed")

    conn._ws.send = send
    with pytest.raises(ConnectionError, match="send failed"):
        await conn.command({"type": "get_states"})
    (fut,) = futures
    assert fut.done() is close_first
    if close_first:
        # retrieved by the client: asyncio's "never retrieved" check sees it as handled
        assert fut._log_traceback is False
    await conn.close()
