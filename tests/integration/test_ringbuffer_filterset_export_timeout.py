"""Integration tests for the multi-filterset export/count timeout guard (Codex #951, P2).

``POST /filtersets/export/csv`` und ``POST /filtersets/export/count`` sammeln ihre
Zeilen ueber ``_collect_multi_entries`` und rufen dort pro aktivem Set
``_query_v2_entries(is_export=True)`` auf. Ohne Guard koennte ein pathologischer
q-/metadata-/contains-/regex-/value-Filter ueber eine grosse Legacy-Datei oder viele
v2-Segmente bis zur Erschoepfung scannen und den API-Worker blockieren, BEVOR eine
Response gesendet wird.

Diese Tests spiegeln den bestehenden ``/export/csv``-Timeout-Test: ein langsamer/
erschoepfender Scan muss deterministisch mit 504 abbrechen (per-Query-Timeout), und
ein gutmuetiger Set-Export/-Count muss unveraendert vollstaendig durchlaufen.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytestmark = pytest.mark.integration


_DP_BASE = {
    "name": "Ringbuffer Filterset Timeout DP",
    "data_type": "FLOAT",
    "unit": "W",
    "tags": ["ringbuffer-filterset-timeout-test"],
    "persist_value": False,
}


async def _create_dp(client, auth_headers, name: str, *, tags: list[str]) -> dict:
    payload = {**_DP_BASE, "name": name, "tags": tags}
    resp = await client.post("/api/v1/datapoints/", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _write_value(client, auth_headers, dp_id: str, value: object) -> None:
    resp = await client.post(
        f"/api/v1/datapoints/{dp_id}/value",
        json={"value": value},
        headers=auth_headers,
    )
    assert resp.status_code == 204, resp.text


async def _create_filterset(client, auth_headers, payload: dict) -> dict:
    resp = await client.post("/api/v1/ringbuffer/filtersets", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _delete_filterset(client, auth_headers, filterset_id: str) -> None:
    await client.delete(f"/api/v1/ringbuffer/filtersets/{filterset_id}", headers=auth_headers)


async def _post_count(client, auth_headers, body: dict):
    return await client.post(
        "/api/v1/ringbuffer/filtersets/export/count",
        json=body,
        headers=auth_headers,
    )


async def _post_export(client, auth_headers, body: dict):
    return await client.post(
        "/api/v1/ringbuffer/filtersets/export/csv",
        json=body,
        headers=auth_headers,
    )


async def test_filterset_export_returns_504_when_set_query_times_out(client, auth_headers, monkeypatch):
    """Ein Set-Scan, der das Per-Query-Budget sprengt, bricht deterministisch mit 504 ab."""
    import obs.api.v1.ringbuffer as ringbuffer_api

    tag = f"rbfsto-{uuid.uuid4().hex[:8]}"
    dp = await _create_dp(client, auth_headers, f"RBFSTO A {uuid.uuid4()}", tags=[tag])
    await _write_value(client, auth_headers, dp["id"], 1.0)

    set_id = (
        await _create_filterset(
            client,
            auth_headers,
            {"name": f"RBFSTO set {uuid.uuid4()}", "filter": {"tags": [tag]}},
        )
    )["id"]

    async def _slow_query(*_args, **_kwargs):
        await asyncio.sleep(0.05)
        return []

    monkeypatch.setattr(ringbuffer_api, "_CSV_EXPORT_QUERY_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(ringbuffer_api, "_query_v2_entries", _slow_query)

    try:
        resp = await _post_export(client, auth_headers, {"set_ids": [set_id]})
        assert resp.status_code == 504, resp.text
        assert "ringbuffer CSV export timed out" in resp.text
    finally:
        await _delete_filterset(client, auth_headers, set_id)


async def test_filterset_export_count_returns_504_when_set_query_times_out(client, auth_headers, monkeypatch):
    """Auch die Count-Preflight teilt sich den Guard und bricht mit 504 ab."""
    import obs.api.v1.ringbuffer as ringbuffer_api

    tag = f"rbfsto-{uuid.uuid4().hex[:8]}"
    dp = await _create_dp(client, auth_headers, f"RBFSTO C {uuid.uuid4()}", tags=[tag])
    await _write_value(client, auth_headers, dp["id"], 2.0)

    set_id = (
        await _create_filterset(
            client,
            auth_headers,
            {"name": f"RBFSTO count set {uuid.uuid4()}", "filter": {"tags": [tag]}},
        )
    )["id"]

    async def _slow_query(*_args, **_kwargs):
        await asyncio.sleep(0.05)
        return []

    monkeypatch.setattr(ringbuffer_api, "_CSV_EXPORT_QUERY_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(ringbuffer_api, "_query_v2_entries", _slow_query)

    try:
        resp = await _post_count(client, auth_headers, {"set_ids": [set_id]})
        assert resp.status_code == 504, resp.text
        assert "ringbuffer CSV export timed out" in resp.text
    finally:
        await _delete_filterset(client, auth_headers, set_id)


async def test_filterset_export_returns_504_when_total_budget_exhausted(client, auth_headers, monkeypatch):
    """Viele Sets, jeder knapp unter dem Per-Query-Budget, sprengen zusammen das Gesamt-Budget."""
    import obs.api.v1.ringbuffer as ringbuffer_api

    tag = f"rbfsto-{uuid.uuid4().hex[:8]}"
    dp = await _create_dp(client, auth_headers, f"RBFSTO T {uuid.uuid4()}", tags=[tag])
    await _write_value(client, auth_headers, dp["id"], 3.0)

    set_ids: list[str] = []
    for _ in range(3):
        set_ids.append(
            (
                await _create_filterset(
                    client,
                    auth_headers,
                    {"name": f"RBFSTO total {uuid.uuid4()}", "filter": {"tags": [tag]}},
                )
            )["id"]
        )

    async def _slow_query(*_args, **_kwargs):
        await asyncio.sleep(0.02)
        return []

    # Per-Query-Budget grosszuegig, Gesamt-Budget so knapp, dass die Summe der Sets es sprengt.
    monkeypatch.setattr(ringbuffer_api, "_CSV_EXPORT_QUERY_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(ringbuffer_api, "_CSV_EXPORT_TOTAL_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(ringbuffer_api, "_query_v2_entries", _slow_query)

    try:
        resp = await _post_export(client, auth_headers, {"set_ids": set_ids})
        assert resp.status_code == 504, resp.text
        assert "ringbuffer CSV export timed out" in resp.text
    finally:
        for set_id in set_ids:
            await _delete_filterset(client, auth_headers, set_id)


async def test_filterset_export_benign_filter_runs_to_completion(client, auth_headers):
    """Gegentest: gutmuetiger Set-Export laeuft unveraendert vollstaendig durch (kein 504)."""
    tag = f"rbfsto-{uuid.uuid4().hex[:8]}"
    dp = await _create_dp(client, auth_headers, f"RBFSTO OK {uuid.uuid4()}", tags=[tag])
    await _write_value(client, auth_headers, dp["id"], 1.0)
    await _write_value(client, auth_headers, dp["id"], 2.0)
    await _write_value(client, auth_headers, dp["id"], 3.0)

    set_id = (
        await _create_filterset(
            client,
            auth_headers,
            {"name": f"RBFSTO ok set {uuid.uuid4()}", "filter": {"tags": [tag]}},
        )
    )["id"]

    try:
        count_resp = await _post_count(client, auth_headers, {"set_ids": [set_id]})
        assert count_resp.status_code == 200, count_resp.text
        assert count_resp.json()["row_count"] >= 3

        export_resp = await _post_export(client, auth_headers, {"set_ids": [set_id]})
        assert export_resp.status_code == 200, export_resp.text
        assert int(export_resp.headers["x-ringbuffer-export-rows"]) >= 3
    finally:
        await _delete_filterset(client, auth_headers, set_id)
