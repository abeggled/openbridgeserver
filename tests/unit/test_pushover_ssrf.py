from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from obs.logic.manager import LogicManager, _resolve_safe_image_url
from obs.logic.models import FlowData
from tests.unit.conftest import node


def _flow(nodes: list[dict], edges: list[dict] | None = None) -> FlowData:
    return FlowData.model_validate({"nodes": nodes, "edges": edges or []})


def _make_manager() -> LogicManager:
    db = AsyncMock()
    db.fetchall = AsyncMock(return_value=[])
    db.execute_and_commit = AsyncMock()
    event_bus = AsyncMock()
    registry = MagicMock()
    registry.get_value.return_value = None
    return LogicManager(db, event_bus, registry)


class TestResolveSafeImageUrl:
    @patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock)
    async def test_uses_async_dns_resolution(self, mock_to_thread):
        mock_to_thread.return_value = [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ]
        resolved = await _resolve_safe_image_url("https://example.com/path.png")
        assert resolved is not None
        mock_to_thread.assert_awaited_once()

    @patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock)
    async def test_rejects_non_global_ip_ranges(self, mock_to_thread):
        mock_to_thread.return_value = [
            (2, 1, 6, "", ("100.64.0.1", 443)),
        ]
        resolved = await _resolve_safe_image_url("https://example.com/path.png")
        assert resolved is None


class TestNotifyPushoverPinnedFetch:
    @patch("obs.logic.manager._resolve_safe_image_url", new_callable=AsyncMock)
    @patch("obs.logic.manager.httpx.AsyncClient")
    def test_fetch_uses_pinned_ip_and_sni(self, mock_client_cls, mock_resolve):
        mock_resolve.return_value = (
            "https://93.184.216.34/path.png",
            "example.com",
            "93.184.216.34",
        )

        img_resp = MagicMock()
        img_resp.status_code = 200
        img_resp.raise_for_status = MagicMock()
        img_resp.headers = {"content-type": "image/png", "content-length": "128"}
        img_resp.extensions = {}

        async def _ok_chunks():
            yield b"\x89PNG\r\n"

        img_resp.aiter_bytes = MagicMock(return_value=_ok_chunks())

        post_resp = MagicMock()
        post_resp.status_code = 200
        post_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        stream_ctx = MagicMock()
        stream_ctx.__aenter__ = AsyncMock(return_value=img_resp)
        stream_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock(return_value=stream_ctx)
        mock_client.post = AsyncMock(return_value=post_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        manager = _make_manager()
        flow = _flow(
            [
                node(
                    "push",
                    "notify_pushover",
                    {"app_token": "token", "user_key": "user", "title": "t"},
                ),
            ],
        )
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(
                manager._execute_graph(
                    graph_id,
                    "test",
                    flow,
                    {"push": {"trigger": True, "message": "msg", "image_url": "https://example.com/path.png"}},
                ),
            )

        mock_client.stream.assert_called_once_with(
            "GET",
            "https://93.184.216.34/path.png",
            timeout=10.0,
            follow_redirects=False,
            headers={"Host": "example.com"},
            extensions={"sni_hostname": "example.com"},
        )
        assert outputs["push"]["sent"] is True

    @patch("obs.logic.manager._PUSHOVER_ATTACHMENT_MAX_BYTES", 8)
    @patch("obs.logic.manager._resolve_safe_image_url", new_callable=AsyncMock)
    @patch("obs.logic.manager.httpx.AsyncClient")
    def test_streaming_aborts_when_attachment_exceeds_limit(self, mock_client_cls, mock_resolve):
        mock_resolve.return_value = (
            "https://93.184.216.34/path.png",
            "example.com",
            "93.184.216.34",
        )

        img_resp = MagicMock()
        img_resp.status_code = 200
        img_resp.raise_for_status = MagicMock()
        img_resp.headers = {"content-type": "image/png"}
        img_resp.extensions = {}

        async def _oversize_chunks():
            yield b"1234"
            yield b"5678"
            yield b"X"  # exceeds patched max size (8 bytes)

        img_resp.aiter_bytes = MagicMock(return_value=_oversize_chunks())

        mock_client = AsyncMock()
        stream_ctx = MagicMock()
        stream_ctx.__aenter__ = AsyncMock(return_value=img_resp)
        stream_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_client.stream = MagicMock(return_value=stream_ctx)
        mock_client.post = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        manager = _make_manager()
        flow = _flow(
            [
                node(
                    "push",
                    "notify_pushover",
                    {"app_token": "token", "user_key": "user", "title": "t"},
                ),
            ],
        )
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(
                manager._execute_graph(
                    graph_id,
                    "test",
                    flow,
                    {"push": {"trigger": True, "message": "msg", "image_url": "https://example.com/path.png"}},
                ),
            )

        mock_client.post.assert_not_awaited()
        assert outputs["push"]["sent"] is False
