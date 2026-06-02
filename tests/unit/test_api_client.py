"""Unit tests for the api_client logic node.

Covers:
  - Executor: placeholder outputs (trigger, body, response, status, success)
  - Manager: HTTP GET/POST success (200) and error (4xx/5xx) handling
  - Manager: Basic Auth, Digest Auth, Bearer Auth configuration
  - Manager: WS broadcast happens AFTER HTTP call (success=True visible)
  - Manager: Downstream nodes receive real api_client outputs (second-pass fix)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from obs.logic.manager import LogicManager, _is_private_host
from obs.logic.models import FlowData
from tests.unit.conftest import edge, make_executor, node

# ===========================================================================
# Helpers
# ===========================================================================


def _flow(nodes: list[dict], edges: list[dict] | None = None) -> FlowData:
    return FlowData.model_validate({"nodes": nodes, "edges": edges or []})


def _make_manager() -> LogicManager:
    """Build a minimal LogicManager with all external dependencies mocked."""
    db = AsyncMock()
    db.fetchall = AsyncMock(return_value=[])
    db.execute_and_commit = AsyncMock()
    event_bus = AsyncMock()
    registry = MagicMock()
    registry.get_value.return_value = None
    return LogicManager(db, event_bus, registry)


def _mock_response(status_code: int, json_data: object | None = None, text: str = "") -> MagicMock:
    """Build a fake httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or (str(json_data) if json_data is not None else "")
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no JSON")
    return resp


class TestApiClientSsrfHostGuard:
    """Unit tests for low-level host classification in SSRF guard."""

    def test_empty_host_is_blocked(self):
        assert _is_private_host("") is True

    def test_localhost_localdomain_is_allowed(self):
        assert _is_private_host("localhost.localdomain") is False

    def test_direct_loopback_ip_is_allowed(self):
        assert _is_private_host("127.0.0.1") is False

    @patch("obs.logic.manager.socket.getaddrinfo", side_effect=OSError("dns fail"))
    def test_dns_failure_is_blocked(self, _mock_getaddrinfo):
        assert _is_private_host("example.com") is True

    @patch("obs.logic.manager.socket.getaddrinfo", return_value=[(None, None, None, None, ("not-an-ip", 0))])
    def test_invalid_dns_answer_is_blocked(self, _mock_getaddrinfo):
        assert _is_private_host("example.com") is True

    @patch("obs.logic.manager.socket.getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 0))])
    def test_loopback_dns_answer_is_allowed(self, _mock_getaddrinfo):
        assert _is_private_host("example.com") is False


# ===========================================================================
# Executor: placeholder behaviour
# ===========================================================================


class TestApiClientExecutorPlaceholder:
    """The executor returns a placeholder dict; real HTTP happens in manager."""

    def test_placeholder_structure_no_trigger(self):
        n = node("ac", "api_client", {"url": "http://example.com", "method": "GET"})
        exc = make_executor([n])
        out = exc.execute().get("ac", {})
        assert out["response"] is None
        assert out["status"] is None
        assert out["success"] is False

    def test_placeholder_with_trigger_true(self):
        n = node("ac", "api_client", {"url": "http://example.com"})
        exc = make_executor([n])
        out = exc.execute({"ac": {"trigger": True}}).get("ac", {})
        assert out["_trigger"] is True
        assert out["success"] is False  # still placeholder

    def test_placeholder_body_forwarded(self):
        n = node("ac", "api_client", {"url": "http://example.com", "method": "POST"})
        exc = make_executor([n])
        out = exc.execute({"ac": {"trigger": True, "body": {"key": "val"}}}).get("ac", {})
        assert out["_body"] == {"key": "val"}

    def test_downstream_receives_false_before_manager(self):
        """Without the second-pass fix, downstream sees success=False from placeholder."""
        ac = node("ac", "api_client", {"url": "http://x.com"})
        cv = node("cv", "const_value", {"value": "true", "data_type": "bool"})
        exc = make_executor(
            [cv, ac],
            [edge("cv", "ac", "value", "trigger")],
        )
        out = exc.execute()
        # Executor placeholder must be False — manager second-pass fixes this later
        assert out["ac"]["success"] is False


# ===========================================================================
# Manager: HTTP execution
# ===========================================================================


class TestApiClientManagerHttp:
    """Tests that verify the real HTTP call path in LogicManager._execute_graph."""

    def _build_graph(self, method: str = "GET", extra_data: dict | None = None) -> tuple[str, FlowData]:
        """Return (node_id, flow) for a single api_client node with trigger=True."""
        data = {
            "url": "http://example.com/api",
            "method": method,
            **(extra_data or {}),
        }
        n = node("ac", "api_client", data)
        flow = _flow([n])
        return "ac", flow

    def _run(self, manager: LogicManager, flow: FlowData, overrides: dict | None = None) -> dict:
        """Run the graph synchronously via asyncio."""
        graph_id = "test-graph"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        return asyncio.run(
            manager._execute_graph(
                graph_id,
                "test",
                flow,
                overrides if overrides is not None else {"ac": {"trigger": True}},
            ),
        )

    @patch("obs.logic.manager.httpx.AsyncClient")
    def test_get_200_sets_success_true(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=_mock_response(200, {"ok": True}))

        manager = _make_manager()
        _, flow = self._build_graph()
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = self._run(manager, flow)

        assert outputs["ac"]["success"] is True
        assert outputs["ac"]["status"] == 200
        assert outputs["ac"]["response"] == {"ok": True}

    @patch("obs.logic.manager.httpx.AsyncClient")
    def test_get_404_sets_success_false(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=_mock_response(404, text="Not Found"))

        manager = _make_manager()
        _, flow = self._build_graph()
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = self._run(manager, flow)

        assert outputs["ac"]["success"] is False
        assert outputs["ac"]["status"] == 404

    @patch("obs.logic.manager.httpx.AsyncClient")
    def test_network_error_sets_success_false(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(side_effect=Exception("Connection refused"))

        manager = _make_manager()
        _, flow = self._build_graph()
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = self._run(manager, flow)

        assert outputs["ac"]["success"] is False
        assert outputs["ac"]["status"] is None
        assert "Connection refused" in outputs["ac"]["response"]

    @patch("obs.logic.manager.httpx.AsyncClient")
    def test_no_trigger_skips_http_call(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock()

        manager = _make_manager()
        _, flow = self._build_graph()
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            # No trigger override → trigger=None → skip
            outputs = self._run(manager, flow, overrides={})

        mock_client.request.assert_not_called()
        assert outputs["ac"]["success"] is False  # placeholder stays

    @patch("obs.logic.manager.httpx.AsyncClient")
    def test_get_request_does_not_send_body(self, mock_client_cls):
        """For GET requests req_kwargs must not contain 'content' or 'data'."""
        captured: dict = {}
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        async def _capture(method, url, **kwargs):
            captured.update(kwargs)
            return _mock_response(200, {})

        mock_client.request = _capture

        manager = _make_manager()
        _, flow = self._build_graph("GET")
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            self._run(manager, flow)

        assert "content" not in captured
        assert "data" not in captured

    @patch("obs.logic.manager.httpx.AsyncClient")
    @patch("obs.logic.manager._is_private_host", return_value=True)
    def test_blocked_target_sets_blocked_output(self, _mock_private_host, mock_client_cls):
        """Blocked target must set explicit error output and skip HTTP call."""
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock()

        manager = _make_manager()
        _, flow = self._build_graph()
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = self._run(manager, flow)

        mock_client.request.assert_not_called()
        assert outputs["ac"]["response"] == "Blocked URL target"
        assert outputs["ac"]["status"] is None
        assert outputs["ac"]["success"] is False

    @patch("obs.logic.manager.httpx.AsyncClient")
    def test_text_response_is_truncated_to_1mb(self, mock_client_cls):
        """Large text responses must be truncated before storing node output."""
        big = "x" * 1_500_000
        resp = _mock_response(200, text=big)
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=resp)

        manager = _make_manager()
        _, flow = self._build_graph(extra_data={"response_type": "text/plain"})
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = self._run(manager, flow)

        assert outputs["ac"]["success"] is True
        assert isinstance(outputs["ac"]["response"], str)
        assert len(outputs["ac"]["response"]) == 1_000_000

    @patch("obs.logic.manager.httpx.AsyncClient")
    def test_localhost_target_is_allowed(self, mock_client_cls):
        """localhost targets stay allowed for local/self-hosted use-cases."""
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=_mock_response(200, {"ok": True}))

        manager = _make_manager()
        _, flow = self._build_graph(extra_data={"url": "http://localhost:8080/api/v1/system/health"})
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = self._run(manager, flow)

        mock_client.request.assert_called_once()
        assert outputs["ac"]["success"] is True
        assert outputs["ac"]["status"] == 200


# ===========================================================================
# Manager: Authentication
# ===========================================================================


class TestApiClientAuthentication:
    """Tests for Basic, Digest and Bearer auth configuration."""

    def _run_with_auth(self, auth_data: dict) -> tuple[dict, list]:
        """Run a graph with auth config; return (outputs, captured_httpx_auth_args)."""
        captured_auth: list = []

        class _FakeClient:
            def __init__(self, auth=None, verify=True):
                captured_auth.append(auth)
                self._resp = _mock_response(200, {"ok": True})

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            async def request(self, method, url, **kwargs):
                return self._resp

        data = {"url": "http://example.com/", "method": "GET", **auth_data}
        n = node("ac", "api_client", data)
        flow = _flow([n])

        manager = _make_manager()
        graph_id = "test-graph"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.logic.manager.httpx.AsyncClient", _FakeClient):
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"ac": {"trigger": True}}))
        return outputs, captured_auth

    def test_basic_auth_passes_httpx_basic_auth(self):
        import httpx as _httpx

        outputs, captured = self._run_with_auth(
            {
                "auth_type": "basic",
                "auth_username": "alice",
                "auth_password": "secret",
            },
        )
        assert outputs["ac"]["success"] is True
        assert len(captured) == 1
        assert isinstance(captured[0], _httpx.BasicAuth)

    def test_digest_auth_passes_httpx_digest_auth(self):
        import httpx as _httpx

        outputs, captured = self._run_with_auth(
            {
                "auth_type": "digest",
                "auth_username": "bob",
                "auth_password": "pass",
            },
        )
        assert outputs["ac"]["success"] is True
        assert isinstance(captured[0], _httpx.DigestAuth)

    def test_basic_auth_empty_username_skipped(self):
        """If username is empty, no auth object must be passed."""
        outputs, captured = self._run_with_auth(
            {
                "auth_type": "basic",
                "auth_username": "",
                "auth_password": "ignored",
            },
        )
        assert captured[0] is None

    @patch("obs.logic.manager.httpx.AsyncClient")
    def test_bearer_auth_sets_authorization_header(self, mock_client_cls):
        captured_headers: list[dict] = []
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        async def _capture(method, url, **kwargs):
            captured_headers.append(kwargs.get("headers", {}))
            return _mock_response(200, {})

        mock_client.request = _capture

        manager = _make_manager()
        data = {
            "url": "http://example.com/",
            "method": "GET",
            "auth_type": "bearer",
            "auth_token": "my-token-xyz",
        }
        n = node("ac", "api_client", data)
        flow = _flow([n])
        graph_id = "g"
        manager._graphs[graph_id] = ("t", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            asyncio.run(manager._execute_graph(graph_id, "t", flow, {"ac": {"trigger": True}}))

        assert len(captured_headers) == 1
        assert captured_headers[0].get("Authorization") == "Bearer my-token-xyz"

    @patch("obs.logic.manager.httpx.AsyncClient")
    def test_bearer_empty_token_no_header(self, mock_client_cls):
        captured_headers: list[dict] = []
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        async def _capture(method, url, **kwargs):
            captured_headers.append(kwargs.get("headers", {}))
            return _mock_response(200, {})

        mock_client.request = _capture

        manager = _make_manager()
        data = {
            "url": "http://example.com/",
            "method": "GET",
            "auth_type": "bearer",
            "auth_token": "",
        }
        n = node("ac", "api_client", data)
        flow = _flow([n])
        graph_id = "g2"
        manager._graphs[graph_id] = ("t", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            asyncio.run(manager._execute_graph(graph_id, "t", flow, {"ac": {"trigger": True}}))

        assert "Authorization" not in captured_headers[0]

    @patch("obs.logic.manager.httpx.AsyncClient")
    def test_none_auth_no_auth_object(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=_mock_response(200, {}))

        manager = _make_manager()
        data = {"url": "http://example.com/", "method": "GET", "auth_type": "none"}
        n = node("ac", "api_client", data)
        flow = _flow([n])
        graph_id = "g3"
        manager._graphs[graph_id] = ("t", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            asyncio.run(manager._execute_graph(graph_id, "t", flow, {"ac": {"trigger": True}}))

        # Called with auth=None
        _, kwargs = mock_client_cls.call_args
        assert kwargs.get("auth") is None


# ===========================================================================
# Manager: downstream re-propagation (second-pass fix)
# ===========================================================================


class TestApiClientDownstreamPropagation:
    """The core bug fix: downstream nodes must see the real api_client outputs
    (success=True for 200 OK), not the executor's placeholder (success=False).
    """

    @patch("obs.logic.manager.httpx.AsyncClient")
    def test_downstream_node_receives_real_success(self, mock_client_cls):
        """Graph: const_value(True) → api_client.trigger
               api_client.success → const_value_gate (and gate as proxy)
        After the second-pass fix, the downstream node must see success=True.
        """
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=_mock_response(200, {"data": 42}))

        # Graph: cv_trig → ac.trigger,  ac.success → gate.in1, cv_true → gate.in2
        nodes = [
            node("cv_trig", "const_value", {"value": "true", "data_type": "bool"}),
            node("cv_true", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://x.com", "method": "GET"}),
            node("gate", "and", {"input_count": 2}),
        ]
        edges = [
            edge("cv_trig", "ac", "value", "trigger"),
            edge("ac", "gate", "success", "in1"),
            edge("cv_true", "gate", "value", "in2"),
        ]
        flow = _flow(nodes, edges)

        manager = _make_manager()
        graph_id = "g"
        manager._graphs[graph_id] = ("t", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "t", flow, {}))

        # api_client must show real success
        assert outputs["ac"]["success"] is True
        assert outputs["ac"]["status"] == 200
        # gate must receive success=True from second-pass and evaluate to True
        assert outputs["gate"]["out"] is True

    @patch("obs.logic.manager.httpx.AsyncClient")
    def test_downstream_not_triggered_on_4xx(self, mock_client_cls):
        """On 404, success=False, downstream AND gate must remain False."""
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=_mock_response(404))

        nodes = [
            node("cv_trig", "const_value", {"value": "true", "data_type": "bool"}),
            node("cv_true", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://x.com"}),
            node("gate", "and", {"input_count": 2}),
        ]
        edges = [
            edge("cv_trig", "ac", "value", "trigger"),
            edge("ac", "gate", "success", "in1"),
            edge("cv_true", "gate", "value", "in2"),
        ]
        flow = _flow(nodes, edges)

        manager = _make_manager()
        graph_id = "g2"
        manager._graphs[graph_id] = ("t", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "t", flow, {}))

        assert outputs["ac"]["success"] is False
        assert outputs["gate"]["out"] is False


# ===========================================================================
# Manager: WS broadcast receives final (post-HTTP) outputs
# ===========================================================================


class TestApiClientWsBroadcast:
    """WS broadcast must show real api_client results, not initial placeholders."""

    @patch("obs.logic.manager.httpx.AsyncClient")
    def test_ws_broadcast_shows_real_success(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=_mock_response(200, {"result": 1}))

        ws_payloads: list[dict] = []

        mock_ws_manager = MagicMock()
        mock_ws_manager.broadcast = AsyncMock(side_effect=lambda p: ws_payloads.append(p))

        manager = _make_manager()
        n = node("ac", "api_client", {"url": "http://x.com", "method": "GET"})
        flow = _flow([n])
        graph_id = "g"
        manager._graphs[graph_id] = ("t", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", return_value=mock_ws_manager):
            asyncio.run(manager._execute_graph(graph_id, "t", flow, {"ac": {"trigger": True}}))

        assert len(ws_payloads) == 1
        ac_out = ws_payloads[0]["outputs"]["ac"]
        # success must be the real value (True), not the placeholder (False)
        assert ac_out["success"] is True
        assert ac_out["status"] == 200


# ===========================================================================
# Manager: response_type values (issue #208)
# ===========================================================================


class TestApiClientResponseType:
    """response_type 'application/json' and 'text/plain' (new MIME-style values)
    must behave identically to the legacy 'json' / 'text' values.
    """

    def _run_with_response_type(self, response_type: str, mock_resp) -> dict:
        manager = _make_manager()
        n = node(
            "ac",
            "api_client",
            {
                "url": "http://example.com/api",
                "method": "GET",
                "response_type": response_type,
            },
        )
        flow = _flow([n])
        graph_id = "g"
        manager._graphs[graph_id] = ("t", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.logic.manager.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.request = AsyncMock(return_value=mock_resp)
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                return asyncio.run(manager._execute_graph(graph_id, "t", flow, {"ac": {"trigger": True}}))

    def test_application_json_parses_json(self):
        resp = _mock_response(200, {"key": "value"})
        outputs = self._run_with_response_type("application/json", resp)
        assert outputs["ac"]["response"] == {"key": "value"}

    def test_text_plain_returns_text(self):
        resp = _mock_response(200, text="hello world")
        outputs = self._run_with_response_type("text/plain", resp)
        assert outputs["ac"]["response"] == "hello world"

    def test_legacy_json_still_works(self):
        """Backward compat: old nodes with response_type='json' must still parse JSON."""
        resp = _mock_response(200, {"legacy": True})
        outputs = self._run_with_response_type("json", resp)
        assert outputs["ac"]["response"] == {"legacy": True}

    def test_legacy_text_still_works(self):
        """Backward compat: old nodes with response_type='text' must return plain text."""
        resp = _mock_response(200, text="plain text")
        outputs = self._run_with_response_type("text", resp)
        assert outputs["ac"]["response"] == "plain text"


# ===========================================================================
# Manager: notify_pushover image_url security hardening
# ===========================================================================


class TestNotifyPushoverImageUrlSecurity:
    def _run_notify_pushover(self, image_url: str) -> dict:
        manager = _make_manager()
        n = node(
            "po",
            "notify_pushover",
            {
                "app_token": "app-token",
                "user_key": "user-key",
                "message": "hello",
                "image_url": image_url,
            },
        )
        flow = _flow([n])
        graph_id = "g-po"
        manager._graphs[graph_id] = ("notify", True, flow)
        manager._node_state[graph_id] = {}
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            return asyncio.run(manager._execute_graph(graph_id, "notify", flow, {"po": {"trigger": True}}))

    @patch("obs.logic.manager.httpx.AsyncClient")
    def test_rejects_non_global_resolved_ip(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock()
        mock_client.post = AsyncMock()

        with patch("obs.logic.manager.socket.getaddrinfo", return_value=[(0, 0, 0, "", ("100.64.0.1", 443))]):
            outputs = self._run_notify_pushover("https://example.com/image.png")

        mock_client.get.assert_not_called()
        mock_client.post.assert_not_called()
        assert outputs["po"]["sent"] is False

    @patch("obs.logic.manager.httpx.AsyncClient")
    def test_binds_fetch_to_validated_ip_with_host_and_sni(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        img_resp = AsyncMock()
        img_resp.raise_for_status = MagicMock()
        img_resp.headers = {"content-type": "image/png", "content-length": "4"}
        img_resp.extensions = {"network_stream": MagicMock(get_extra_info=MagicMock(return_value=("93.184.216.34", 443)))}

        async def _iter_bytes():
            yield b"\x89PNG"

        img_resp.aiter_bytes = _iter_bytes

        stream_cm = AsyncMock()
        stream_cm.__aenter__.return_value = img_resp
        stream_cm.__aexit__.return_value = False
        mock_client.stream = MagicMock(return_value=stream_cm)

        post_resp = MagicMock()
        post_resp.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=post_resp)

        with patch("obs.logic.manager.socket.getaddrinfo", return_value=[(0, 0, 0, "", ("93.184.216.34", 443))]):
            outputs = self._run_notify_pushover("https://example.com/path/image.png?size=1")

        assert outputs["po"]["sent"] is True
        assert mock_client.stream.call_count == 1
        get_call = mock_client.stream.call_args
        assert get_call.args[0] == "GET"
        assert get_call.args[1] == "https://93.184.216.34/path/image.png?size=1"
        assert get_call.kwargs["headers"] == {"Host": "example.com"}
        assert get_call.kwargs["extensions"] == {"sni_hostname": "example.com"}
        assert get_call.kwargs["follow_redirects"] is False
