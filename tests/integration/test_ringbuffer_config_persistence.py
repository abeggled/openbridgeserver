"""Integration tests for ringbuffer config persistence (issue: monitor-ringbuffer).

The ``POST /api/v1/ringbuffer/config`` endpoint must persist the runtime
config (max_entries, max_file_size_bytes, max_age) so values survive
container restarts. Defaults apply only when nothing is persisted yet.
"""

from __future__ import annotations

import json

import pytest

from obs.db.database import get_db
from obs.ringbuffer.persisted_config import (
    DEFAULT_SEGMENT_MAX_AGE_SECONDS,
    PERSISTED_CONFIG_KEY,
    load_persisted_ringbuffer_config,
)

pytestmark = pytest.mark.integration


async def _read_persisted_row():
    row = await get_db().fetchone("SELECT value FROM app_settings WHERE key=?", (PERSISTED_CONFIG_KEY,))
    return json.loads(row["value"]) if row and row["value"] else None


async def _reset_to_defaults(client, auth_headers):
    """Keep the session-scoped app stable for unrelated tests.

    Mirrors the reset pattern in ``test_ringbuffer_filters.py`` so tests can
    run in any order without leaking ringbuffer state between them.
    """
    # ``segment_max_age`` is reset to the deployed default too, so a prior test's
    # explicit value does not leak into the next test's persisted config.
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={
            "storage": "file",
            "max_entries": 1000,
            "max_file_size_bytes": None,
            "max_age": None,
            "segment_max_age": DEFAULT_SEGMENT_MAX_AGE_SECONDS,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


async def test_config_post_persists_full_payload_to_app_settings(client, auth_headers):
    # ``segmented`` is now the deployed default. ``segment_max_age`` is sent
    # explicitly so the 3-segment age rule (max_age >= 3*segment_max_age) holds:
    # 7200 >= 3*2400. The full payload must round-trip verbatim.
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={
            "storage": "file",
            "max_entries": 42_000,
            "max_file_size_bytes": 5 * 1024 * 1024,
            "max_age": 7200,
            "segment_max_age": 2400,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    persisted = await _read_persisted_row()
    assert persisted == {
        "enabled": True,
        "max_entries": 42_000,
        "max_file_size_bytes": 5 * 1024 * 1024,
        "max_age": 7200,
        "segmented": True,
        "segment_max_bytes": None,
        "segment_max_rows": None,
        "segment_max_age": 2400,
    }

    await _reset_to_defaults(client, auth_headers)


async def test_config_post_persists_null_max_entries(client, auth_headers):
    # max_age=None → the age ratio rule is inactive, so the default 6-h
    # segment_max_age passes through untouched and is persisted.
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={
            "storage": "file",
            "max_entries": None,
            "max_file_size_bytes": 3 * 1024 * 1024,
            "max_age": None,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    persisted = await _read_persisted_row()
    assert persisted == {
        "enabled": True,
        "max_entries": None,
        "max_file_size_bytes": 3 * 1024 * 1024,
        "max_age": None,
        "segmented": True,
        "segment_max_bytes": None,
        "segment_max_rows": None,
        "segment_max_age": DEFAULT_SEGMENT_MAX_AGE_SECONDS,
    }

    await _reset_to_defaults(client, auth_headers)


async def test_load_persisted_ringbuffer_config_after_post_matches_payload(client, auth_headers):
    """Round-trip via the public API loader used by main.py at startup."""
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={
            "storage": "file",
            "max_entries": 25_000,
            "max_file_size_bytes": 8 * 1024 * 1024,
            "max_age": 3600,
            "segment_max_age": 1200,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    cfg = await load_persisted_ringbuffer_config(get_db())
    assert cfg == {
        "enabled": True,
        "max_entries": 25_000,
        "max_file_size_bytes": 8 * 1024 * 1024,
        "max_age": 3600,
        "segmented": True,
        "segment_max_bytes": None,
        "segment_max_rows": None,
        "segment_max_age": 1200,
    }

    await _reset_to_defaults(client, auth_headers)


async def test_stats_returns_persisted_segment_config(client, auth_headers):
    """/stats gibt die persistierten Segment-Parameter zurück, damit der
    Config-Dialog die gespeicherten Werte hydratisiert (#919/#938)."""
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={"segment_max_age": 43200},  # 12 h; max_age=None → keine Ratio-Verletzung
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    # Die POST-/config-Response selbst muss den gespeicherten Wert zurueckgeben,
    # damit das Modal nach dem Speichern korrekt hydratisiert (nicht auf 6h faellt).
    assert resp.json()["segment_max_age"] == 43200

    stats = await client.get("/api/v1/ringbuffer/stats", headers=auth_headers)
    assert stats.status_code == 200, stats.text
    assert stats.json()["segment_max_age"] == 43200

    await _reset_to_defaults(client, auth_headers)


async def test_config_post_segment_max_age_applies_live_to_running_store(client, auth_headers):
    """POST /config mit segment_max_age wirkt LIVE auf den laufenden Store (#919/#938).

    Ohne Neustart müssen sich der RingBuffer-Wert UND die Store-``SegmentConfig``
    ändern — die Prognose (``_compute_prognosis``) nutzt ``segment_max_age`` sofort.
    """
    from obs.ringbuffer.ringbuffer import get_optional_ringbuffer

    rb = get_optional_ringbuffer()
    assert rb is not None and rb.segmented and rb.store is not None

    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={
            # Retention-Limits auf None → die 3-Segment-Ratio kann nicht verletzt
            # werden; explizite Bytes/Rows liegen innerhalb der technischen Grenzen.
            "max_entries": None,
            "max_file_size_bytes": None,
            "max_age": None,
            "segment_max_age": 7200,  # 2 h
            "segment_max_bytes": 8 * 1024 * 1024,  # 8 MiB (>= 4 MiB Untergrenze)
            "segment_max_rows": 5000,  # >= 1000 Untergrenze
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    # Live, ohne Neustart: RingBuffer-Felder und Store-SegmentConfig tragen die Werte.
    assert rb._segment_max_age == 7200
    assert rb._segment_max_bytes == 8 * 1024 * 1024
    assert rb._segment_max_rows == 5000
    assert rb.store._segment_config.segment_max_age == 7200
    assert rb.store._segment_config.segment_max_bytes == 8 * 1024 * 1024
    assert rb.store._segment_config.segment_max_rows == 5000

    # Explizite Bytes/Rows wieder auf Auto (None) zurücksetzen, damit sie nicht in
    # nachfolgende Tests leaken (``_reset_to_defaults`` fasst sie nicht an).
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={"segment_max_bytes": None, "segment_max_rows": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    await _reset_to_defaults(client, auth_headers)


async def test_config_post_segmentation_toggle_rebuilds_running_instance(client, auth_headers):
    """Ein Wechsel von ``segmented`` muss den laufenden RingBuffer neu aufbauen (#951).

    Regression: lief der Monitor bereits (unterstützt) im Legacy-Modus, fiel ein
    späterer ``segmented:true``-Request in den in-place-``reconfigure``-Pfad, der
    ``_segmented`` nicht ändert. Die API persistierte dann ``segmented=true``, die
    laufende Instanz blieb aber Legacy (kein Store) — persistierter und tatsächlicher
    Zustand divergierten. Der Roundtrip true→false→true prüft beide Richtungen.
    """
    from obs.ringbuffer.ringbuffer import get_optional_ringbuffer

    rb = get_optional_ringbuffer()
    assert rb is not None and rb.segmented and rb.store is not None

    # true → false: Legacy-Neuaufbau, kein Store mehr.
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={"segmented": False, "max_entries": 1000, "max_file_size_bytes": None, "max_age": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    rb_legacy = get_optional_ringbuffer()
    assert rb_legacy is not None
    assert rb_legacy.segmented is False
    assert rb_legacy.store is None
    assert (await _read_persisted_row())["segmented"] is False

    # false → true: Store-Neuaufbau, laufende Instanz ist wieder segmentiert.
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={"segmented": True, "max_entries": 1000, "max_file_size_bytes": None, "max_age": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    rb_seg = get_optional_ringbuffer()
    assert rb_seg is not None
    assert rb_seg.segmented is True
    assert rb_seg.store is not None
    assert (await _read_persisted_row())["segmented"] is True

    await _reset_to_defaults(client, auth_headers)


async def test_config_post_budget_change_rederives_auto_segment_max_bytes(client, auth_headers):
    """Ein reiner Budget-Wechsel muss die AUTO-Segmentgröße live neu ableiten (#919).

    Regression: ``segment_max_bytes=None`` (auto) wurde einmal aus ``budget/3``
    abgeleitet und dann in ``_segment_max_bytes`` eingefroren. Änderte man später
    NUR das Budget (ohne ``segment_max_bytes`` mitzusenden), blieb die effektive
    Segmentgröße auf dem alten ``budget/3`` stehen — die Prognose (Größen-Cap,
    Rotation) nahm das neue Budget nicht wahr.
    """
    from obs.ringbuffer.ringbuffer import derive_segment_max_bytes, get_optional_ringbuffer

    rb = get_optional_ringbuffer()
    assert rb is not None and rb.segmented and rb.store is not None

    # Budget 300 MiB, segment_max_bytes auto → effektiv = derive(300 MiB) = 100 MiB.
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={"max_file_size_bytes": 300 * 1024 * 1024, "max_age": None, "max_entries": None, "segment_max_bytes": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert rb.store._segment_config.segment_max_bytes == derive_segment_max_bytes(300 * 1024 * 1024)

    # NUR das Budget ändern (segment_max_bytes NICHT mitsenden → auto bleibt auto).
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={"max_file_size_bytes": 900 * 1024 * 1024},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    # Die effektive Auto-Segmentgröße muss dem neuen Budget folgen (nicht auf 300/3 einfrieren).
    assert rb.store._segment_config.segment_max_bytes == derive_segment_max_bytes(900 * 1024 * 1024)
    assert rb.store._segment_config.segment_max_bytes != derive_segment_max_bytes(300 * 1024 * 1024)
    # Auto-Absicht bleibt erhalten: die persistierte/gemeldete Config ist weiter None.
    assert resp.json()["segment_max_bytes"] is None

    await _reset_to_defaults(client, auth_headers)
