"""
Integration Tests — QR-Code-Widget (Visu-Seiten-API)

Das QR-Code-Widget ist ein reines Frontend-Widget ohne eigenen Backend-Endpunkt.
Diese Tests prüfen, dass die Visu-Seiten-API die Widget-Konfiguration korrekt
speichert und zurückliefert (Round-Trip-Sicherheit).

Abgedeckt:
  1.  QrCode-Widget-Konfiguration wird vollständig gespeichert und zurückgegeben
  2.  Alle Felder (content, label, errorCorrection, darkColor, lightColor)
      überleben den Round-Trip verlustfrei
  3.  Widget-Typ ist 'QrCode'
  4.  Fehlende optionale Felder werden als leere Strings/Defaults toleriert
  5.  Mehrere QrCode-Widgets auf einer Seite sind unabhängig voneinander
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration

# ── Hilfsroutinen ─────────────────────────────────────────────────────────────

async def _create_page(client, auth_headers, name: str) -> str:
    resp = await client.post(
        "/api/v1/visu/nodes",
        json={"name": name, "type": "PAGE", "order": 999, "access": "public"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, f"Seite erstellen fehlgeschlagen: {resp.text}"
    return resp.json()["id"]


async def _save_page(client, auth_headers, page_id: str, widgets: list) -> None:
    resp = await client.put(
        f"/api/v1/visu/pages/{page_id}",
        json={
            "grid_cols": 12,
            "grid_row_height": 80,
            "grid_cell_width": 80,
            "background": None,
            "widgets": widgets,
        },
        headers=auth_headers,
    )
    assert resp.status_code in (200, 204), f"Seite speichern fehlgeschlagen: {resp.text}"


async def _load_page(client, auth_headers, page_id: str) -> dict:
    resp = await client.get(f"/api/v1/visu/pages/{page_id}", headers=auth_headers)
    assert resp.status_code == 200, f"Seite laden fehlgeschlagen: {resp.text}"
    return resp.json()


async def _delete_node(client, auth_headers, node_id: str) -> None:
    await client.delete(f"/api/v1/visu/nodes/{node_id}", headers=auth_headers)


def _qrcode_widget(widget_id: str, config: dict) -> dict:
    return {
        "id": widget_id,
        "name": "E2E QR-Code",
        "type": "QrCode",
        "datapoint_id": None,
        "status_datapoint_id": None,
        "x": 0, "y": 0, "w": 3, "h": 3,
        "config": config,
    }


# ── Test 1: Vollständige Konfiguration round-trip ─────────────────────────────

async def test_qrcode_full_config_round_trip(client, auth_headers):
    page_id   = await _create_page(client, auth_headers, f"E2E-QrCode-Full-{uuid.uuid4().hex[:8]}")
    widget_id = str(uuid.uuid4())
    cfg = {
        "content":         "https://example.com/willkommen",
        "label":           "Startseite",
        "errorCorrection": "H",
        "darkColor":       "#1a1a1a",
        "lightColor":      "#f5f5f5",
    }

    try:
        await _save_page(client, auth_headers, page_id, [_qrcode_widget(widget_id, cfg)])
        page = await _load_page(client, auth_headers, page_id)

        widgets = page["widgets"]
        assert len(widgets) == 1

        w = widgets[0]
        assert w["type"] == "QrCode"
        assert w["id"] == widget_id

        saved_cfg = w["config"]
        assert saved_cfg["content"]         == cfg["content"]
        assert saved_cfg["label"]           == cfg["label"]
        assert saved_cfg["errorCorrection"] == cfg["errorCorrection"]
        assert saved_cfg["darkColor"]       == cfg["darkColor"]
        assert saved_cfg["lightColor"]      == cfg["lightColor"]
    finally:
        await _delete_node(client, auth_headers, page_id)


# ── Test 2: Alle Felder einzeln prüfen ────────────────────────────────────────

@pytest.mark.parametrize("field,value", [
    ("content",         "WIFI:S:MeinNetz;T:WPA;P:geheim;;"),
    ("label",           "WiFi beitreten"),
    ("errorCorrection", "Q"),
    ("darkColor",       "#003366"),
    ("lightColor",      "#fffae6"),
])
async def test_qrcode_single_field_round_trip(client, auth_headers, field, value):
    page_id   = await _create_page(client, auth_headers, f"E2E-QrCode-Field-{uuid.uuid4().hex[:8]}")
    widget_id = str(uuid.uuid4())
    cfg = {
        "content": "https://example.com",
        "label": "",
        "errorCorrection": "M",
        "darkColor": "#000000",
        "lightColor": "#ffffff",
        field: value,
    }

    try:
        await _save_page(client, auth_headers, page_id, [_qrcode_widget(widget_id, cfg)])
        page = await _load_page(client, auth_headers, page_id)

        saved_cfg = page["widgets"][0]["config"]
        assert saved_cfg[field] == value
    finally:
        await _delete_node(client, auth_headers, page_id)


# ── Test 3: Leerer content (Platzhalter-Zustand) ──────────────────────────────

async def test_qrcode_empty_content_saved(client, auth_headers):
    page_id   = await _create_page(client, auth_headers, f"E2E-QrCode-Empty-{uuid.uuid4().hex[:8]}")
    widget_id = str(uuid.uuid4())
    cfg = {
        "content":         "",
        "label":           "",
        "errorCorrection": "M",
        "darkColor":       "#000000",
        "lightColor":      "#ffffff",
    }

    try:
        await _save_page(client, auth_headers, page_id, [_qrcode_widget(widget_id, cfg)])
        page = await _load_page(client, auth_headers, page_id)

        saved_cfg = page["widgets"][0]["config"]
        assert saved_cfg["content"] == ""
    finally:
        await _delete_node(client, auth_headers, page_id)


# ── Test 4: Mehrere QrCode-Widgets auf einer Seite ────────────────────────────

async def test_qrcode_multiple_widgets_independent(client, auth_headers):
    page_id = await _create_page(client, auth_headers, f"E2E-QrCode-Multi-{uuid.uuid4().hex[:8]}")
    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())
    cfg_a = {
        "content": "https://seite-a.example.com",
        "label": "Seite A",
        "errorCorrection": "L",
        "darkColor": "#000000",
        "lightColor": "#ffffff",
    }
    cfg_b = {
        "content": "https://seite-b.example.com",
        "label": "Seite B",
        "errorCorrection": "H",
        "darkColor": "#ff0000",
        "lightColor": "#0000ff",
    }
    w_a = _qrcode_widget(id_a, cfg_a)
    w_a["x"] = 0
    w_b = _qrcode_widget(id_b, cfg_b)
    w_b["x"] = 4

    try:
        await _save_page(client, auth_headers, page_id, [w_a, w_b])
        page = await _load_page(client, auth_headers, page_id)

        by_id = {w["id"]: w for w in page["widgets"]}
        assert by_id[id_a]["config"]["content"] == cfg_a["content"]
        assert by_id[id_a]["config"]["errorCorrection"] == cfg_a["errorCorrection"]
        assert by_id[id_b]["config"]["content"] == cfg_b["content"]
        assert by_id[id_b]["config"]["darkColor"] == cfg_b["darkColor"]
    finally:
        await _delete_node(client, auth_headers, page_id)


# ── Test 5: Widget-Typ wird korrekt gespeichert ────────────────────────────────

async def test_qrcode_widget_type_is_qrcode(client, auth_headers):
    page_id   = await _create_page(client, auth_headers, f"E2E-QrCode-Type-{uuid.uuid4().hex[:8]}")
    widget_id = str(uuid.uuid4())

    try:
        await _save_page(client, auth_headers, page_id, [
            _qrcode_widget(widget_id, {
                "content": "https://example.com",
                "label": "",
                "errorCorrection": "M",
                "darkColor": "#000000",
                "lightColor": "#ffffff",
            })
        ])
        page = await _load_page(client, auth_headers, page_id)
        assert page["widgets"][0]["type"] == "QrCode"
    finally:
        await _delete_node(client, auth_headers, page_id)
