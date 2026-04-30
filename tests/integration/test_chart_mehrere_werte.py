"""Integration Tests — Chart-Widget mit mehreren Reihen (issue #234)

Deckt ab:
  - Verlaufsabfragen für mehrere Datenpunkte gleichzeitig liefern korrekte Daten
  - Werte verschiedener Datenpunkte bleiben unabhängig voneinander
  - Parallele Abfragen zu unterschiedlichen Zeitfenstern funktionieren korrekt
"""

from __future__ import annotations

import asyncio
import datetime
import uuid

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_float_dp(client, auth_headers, name: str) -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": name, "data_type": "FLOAT", "unit": "°C"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, f"create failed: {resp.text}"
    return resp.json()


async def _write_value(client, auth_headers, dp_id: str, value: float) -> None:
    resp = await client.post(
        f"/api/v1/datapoints/{dp_id}/value",
        json={"value": value},
        headers=auth_headers,
    )
    assert resp.status_code == 204, f"write failed: {resp.text}"


async def _query_history(client, auth_headers, dp_id: str, limit: int = 100) -> list:
    past = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)).isoformat()
    resp = await client.get(
        f"/api/v1/history/{dp_id}",
        params={"from": past, "limit": limit},
        headers=auth_headers,
    )
    assert resp.status_code == 200, f"history query failed: {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_mehrere_datenpunkte_liefern_unabhaengige_historien(client, auth_headers):
    """Verlaufsabfragen zweier Datenpunkte liefern jeweils nur eigene Werte."""
    suffix = uuid.uuid4().hex[:6]
    dp1 = await _create_float_dp(client, auth_headers, f"ChartMulti-A-{suffix}")
    dp2 = await _create_float_dp(client, auth_headers, f"ChartMulti-B-{suffix}")

    await _write_value(client, auth_headers, dp1["id"], 21.5)
    await _write_value(client, auth_headers, dp2["id"], 55.0)

    hist1 = await _query_history(client, auth_headers, dp1["id"])
    hist2 = await _query_history(client, auth_headers, dp2["id"])

    assert len(hist1) >= 1
    assert len(hist2) >= 1

    values1 = [float(p["v"]) for p in hist1]
    values2 = [float(p["v"]) for p in hist2]

    assert 21.5 in values1, "DP1-Wert muss in dessen Verlauf vorhanden sein"
    assert 55.0 in values2, "DP2-Wert muss in dessen Verlauf vorhanden sein"
    assert 55.0 not in values1, "DP2-Wert darf nicht in DP1-Verlauf erscheinen"
    assert 21.5 not in values2, "DP1-Wert darf nicht in DP2-Verlauf erscheinen"


async def test_parallele_verlaufsabfragen_korrekt(client, auth_headers):
    """Gleichzeitige Verlaufsabfragen (wie das Multi-Reihen-Widget sie stellt) sind korrekt."""
    suffix = uuid.uuid4().hex[:6]
    dp1 = await _create_float_dp(client, auth_headers, f"ChartParallel-A-{suffix}")
    dp2 = await _create_float_dp(client, auth_headers, f"ChartParallel-B-{suffix}")
    dp3 = await _create_float_dp(client, auth_headers, f"ChartParallel-C-{suffix}")

    await _write_value(client, auth_headers, dp1["id"], 10.0)
    await _write_value(client, auth_headers, dp2["id"], 20.0)
    await _write_value(client, auth_headers, dp3["id"], 30.0)

    # Alle drei parallel abfragen — genau wie das Chart-Widget es tut
    hist1, hist2, hist3 = await asyncio.gather(
        _query_history(client, auth_headers, dp1["id"]),
        _query_history(client, auth_headers, dp2["id"]),
        _query_history(client, auth_headers, dp3["id"]),
    )

    assert any(float(p["v"]) == 10.0 for p in hist1)
    assert any(float(p["v"]) == 20.0 for p in hist2)
    assert any(float(p["v"]) == 30.0 for p in hist3)


async def test_verlauf_ohne_werte_liefert_leere_liste(client, auth_headers):
    """Ein neuer Datenpunkt ohne Werte liefert eine leere Verlaufsliste."""
    suffix = uuid.uuid4().hex[:6]
    dp = await _create_float_dp(client, auth_headers, f"ChartEmpty-{suffix}")

    hist = await _query_history(client, auth_headers, dp["id"])
    assert hist == [], "Neu erstellter DP ohne Werte muss leere Verlaufsliste liefern"


async def test_mehrere_werte_pro_datenpunkt_werden_gespeichert(client, auth_headers):
    """Mehrere aufeinanderfolgende Werte werden alle im Verlauf gespeichert."""
    suffix = uuid.uuid4().hex[:6]
    dp = await _create_float_dp(client, auth_headers, f"ChartMultiVal-{suffix}")

    for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
        await _write_value(client, auth_headers, dp["id"], v)

    hist = await _query_history(client, auth_headers, dp["id"])
    recorded = [float(p["v"]) for p in hist]

    for expected in [1.0, 2.0, 3.0, 4.0, 5.0]:
        assert expected in recorded, f"Wert {expected} fehlt im Verlauf"
