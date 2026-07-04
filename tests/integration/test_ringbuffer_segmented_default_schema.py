"""Integration tests: ``segmented`` als optionales Partial-Update-Feld (Codex #951 [P2]).

Der persistierte/Runtime-Default fuer neue Installs ist segmentiert, das GUI zeigt
keinen Legacy-Toggle mehr. Das Request-Schema darf ``segmented`` daher NICHT mit
Default ``false`` bewerben: sonst serialisieren generierte Clients / Admin-Skripte
beim Aendern UNRELATED Config ein ``segmented:false`` mit und bauen den laufenden
Monitor in den Legacy-Single-File-Pfad zurueck (v2-Segment-Historie nicht mehr
lesbar).

Vertrag:
    - ``segmented`` fehlt (bzw. ``None``) waehrend der persistierte Zustand
      segmentiert ist → bleibt segmentiert (KEIN Legacy-Rebuild).
    - explizit ``segmented=true`` → segmentiert (GUI-Pfad seit Runde 23).
    - explizit ``segmented=false`` → Legacy (bewusster Opt-out bleibt moeglich).
"""

from __future__ import annotations

import json

import pytest

from obs.db.database import get_db
from obs.ringbuffer.persisted_config import (
    DEFAULT_SEGMENT_MAX_AGE_SECONDS,
    PERSISTED_CONFIG_KEY,
)

pytestmark = pytest.mark.integration


async def _read_persisted_row():
    row = await get_db().fetchone("SELECT value FROM app_settings WHERE key=?", (PERSISTED_CONFIG_KEY,))
    return json.loads(row["value"]) if row and row["value"] else None


async def _reset_to_defaults(client, auth_headers):
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


async def _ensure_segmented(client, auth_headers):
    """Etabliert einen laufenden segmentierten Store und liefert ihn zurueck.

    Isolationsrobust: ein vorher laufender Test kann den globalen RingBuffer auf
    den Legacy-Pfad gestellt haben. Statt den Session-Default anzunehmen, wird der
    segmentierte Zustand hier explizit ueber einen ``segmented=true``-POST gesetzt.
    """
    from obs.ringbuffer.ringbuffer import get_optional_ringbuffer

    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={
            "storage": "file",
            "segmented": True,
            "max_entries": 1000,
            "max_file_size_bytes": None,
            "max_age": None,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    rb = get_optional_ringbuffer()
    assert rb is not None and rb.segmented and rb.store is not None
    return rb


async def test_schema_does_not_advertise_segmented_false_default():
    """Das OpenAPI-Request-Schema darf keinen ``false``-Default fuer ``segmented``
    publizieren – sonst kippt ein unrelated Config-Change generierter Clients das
    Feld auf false.
    """
    from obs.api.v1.ringbuffer import RingBufferConfig

    field = RingBufferConfig.model_fields["segmented"]
    # Kein serialisierbarer False-Default mehr: nicht gesetzt bleibt = unveraendert.
    assert field.default is None
    assert "segmented" not in RingBufferConfig().model_fields_set


async def test_partial_update_without_segmented_keeps_segmented(client, auth_headers):
    """PUT/POST der Config OHNE ``segmented`` waehrend segmentiert persistiert ist →
    bleibt segmentiert (kein Rebuild in den Legacy-Single-File-Pfad).
    """
    from obs.ringbuffer.ringbuffer import get_optional_ringbuffer

    # Ausgangszustand explizit als segmentiert etablieren (isolationsrobust).
    await _ensure_segmented(client, auth_headers)

    # Unrelated Config-Change OHNE segmented.
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={"max_entries": 2000, "max_file_size_bytes": None, "max_age": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    rb_after = get_optional_ringbuffer()
    assert rb_after is not None
    assert rb_after.segmented is True
    assert rb_after.store is not None
    assert (await _read_persisted_row())["segmented"] is True

    await _reset_to_defaults(client, auth_headers)


async def test_partial_update_explicit_none_keeps_segmented(client, auth_headers):
    """Explizit ``segmented=null`` verhaelt sich wie fehlend: persistierter Wert
    bleibt erhalten (NICHT als false interpretiert).
    """
    from obs.ringbuffer.ringbuffer import get_optional_ringbuffer

    await _ensure_segmented(client, auth_headers)

    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={"segmented": None, "max_entries": 3000, "max_file_size_bytes": None, "max_age": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    rb_after = get_optional_ringbuffer()
    assert rb_after is not None
    assert rb_after.segmented is True
    assert rb_after.store is not None
    assert (await _read_persisted_row())["segmented"] is True

    await _reset_to_defaults(client, auth_headers)


async def test_explicit_segmented_true_stays_segmented(client, auth_headers):
    """Gegentest: explizit ``segmented=true`` → segmentiert (GUI-Pfad)."""
    from obs.ringbuffer.ringbuffer import get_optional_ringbuffer

    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={"segmented": True, "max_entries": 1000, "max_file_size_bytes": None, "max_age": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    rb_after = get_optional_ringbuffer()
    assert rb_after is not None
    assert rb_after.segmented is True
    assert rb_after.store is not None
    assert (await _read_persisted_row())["segmented"] is True

    await _reset_to_defaults(client, auth_headers)


async def test_explicit_segmented_false_opts_out_to_legacy(client, auth_headers):
    """Gegentest: expliziter Opt-out ``segmented=false`` → Legacy-Single-File-Pfad
    bleibt moeglich.
    """
    from obs.ringbuffer.ringbuffer import get_optional_ringbuffer

    rb = get_optional_ringbuffer()
    assert rb is not None and rb.segmented and rb.store is not None

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

    # Zuruecksetzen auf segmentiert fuer Folge-Tests.
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={"segmented": True, "max_entries": 1000, "max_file_size_bytes": None, "max_age": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    await _reset_to_defaults(client, auth_headers)


async def test_none_after_explicit_false_keeps_legacy(client, auth_headers):
    """``None`` bewahrt den DEPLOYTEN/persistierten Wert – auch wenn dieser Legacy ist.

    Nach einem expliziten Opt-out (``segmented=false``) darf ein spaeterer
    unrelated Change ohne ``segmented`` den Store NICHT ungewollt zurueck auf
    segmentiert kippen. ``None`` = "unveraendert lassen", in BEIDE Richtungen.
    """
    from obs.ringbuffer.ringbuffer import get_optional_ringbuffer

    # Zunaechst bewusst Legacy.
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={"segmented": False, "max_entries": 1000, "max_file_size_bytes": None, "max_age": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert (await _read_persisted_row())["segmented"] is False

    # Unrelated Change ohne segmented → Legacy bleibt Legacy.
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={"max_entries": 5000, "max_file_size_bytes": None, "max_age": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    rb_after = get_optional_ringbuffer()
    assert rb_after is not None
    assert rb_after.segmented is False
    assert rb_after.store is None
    assert (await _read_persisted_row())["segmented"] is False

    # Aufraeumen: zurueck auf segmentierten Session-Default.
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={"segmented": True, "max_entries": 1000, "max_file_size_bytes": None, "max_age": None},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    await _reset_to_defaults(client, auth_headers)
