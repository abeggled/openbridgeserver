from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

pytestmark = pytest.mark.integration


class _InfluxV3Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/query":
            body = json.dumps({"results": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/v3/write_lp":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(length)
        query = parse_qs(parsed.query, keep_blank_values=True)
        request = {
            "path": parsed.path,
            "query": query,
            "authorization": self.headers.get("Authorization", ""),
        }
        self.server.captured_writes.append(request)  # type: ignore[attr-defined]

        if "db" not in query:
            body = b"serde error: missing field `db`"
            self.send_response(400)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(204)
        self.end_headers()

    def log_message(self, _format: str, *_args) -> None:
        # Keep integration test output quiet.
        return


@pytest.fixture
def fake_influx_v3_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _InfluxV3Handler)
    server.captured_writes = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


async def _update_history_settings(client, auth_headers, payload: dict):
    resp = await client.put("/api/v1/system/history/settings", json=payload, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_influxdb_v3_write_uses_db_query_param(client, auth_headers, fake_influx_v3_server):
    base_url = f"http://127.0.0.1:{fake_influx_v3_server.server_port}"
    influx_payload = {
        "plugin": "influxdb",
        "influx_url": base_url,
        "influx_version": 3,
        "influx_token": "test-token",
        "influx_org": "",
        "influx_bucket": "obs",
        "influx_database": "obs",
        "influx_username": "",
        "influx_password": "",
        "timescale_dsn": "",
    }
    sqlite_payload = {
        "plugin": "sqlite",
        "influx_url": "http://localhost:8086",
        "influx_version": 2,
        "influx_token": "",
        "influx_org": "",
        "influx_bucket": "obs",
        "influx_database": "obs",
        "influx_username": "",
        "influx_password": "",
        "timescale_dsn": "",
    }

    try:
        await _update_history_settings(client, auth_headers, influx_payload)
        fake_influx_v3_server.captured_writes.clear()  # type: ignore[attr-defined]

        create_resp = await client.post(
            "/api/v1/datapoints/",
            json={
                "name": f"InfluxV3-{uuid.uuid4().hex[:8]}",
                "data_type": "FLOAT",
                "unit": "C",
                "record_history": True,
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        dp_id = create_resp.json()["id"]

        write_resp = await client.post(
            f"/api/v1/datapoints/{dp_id}/value",
            json={"value": 23.5},
            headers=auth_headers,
        )
        assert write_resp.status_code == 204, write_resp.text

        deadline = time.time() + 2.0
        while time.time() < deadline and not fake_influx_v3_server.captured_writes:  # type: ignore[attr-defined]
            time.sleep(0.05)

        assert fake_influx_v3_server.captured_writes, "expected at least one v3 write request"  # type: ignore[attr-defined]
        req = fake_influx_v3_server.captured_writes[0]  # type: ignore[attr-defined]
        assert req["path"] == "/api/v3/write_lp"
        assert req["query"].get("db") == ["obs"]
        assert req["query"].get("precision") == ["nanosecond"]
        assert req["authorization"] == "Bearer test-token"
    finally:
        await _update_history_settings(client, auth_headers, sqlite_payload)
