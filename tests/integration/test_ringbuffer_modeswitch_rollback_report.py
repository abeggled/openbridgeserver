"""Regression: gescheitertes Mode-Switch-Rollback muss SICHTBAR werden (#951 [P2]).

Codex-Finding "Report failed mode-switch rollbacks": Scheitert beim
Segmentierungs-Wechsel der Neuaufbau NACHDEM der alte RingBuffer gestoppt wurde,
UND scheitert auch das Restore des vorherigen Buffers, verschluckte der Rollback-
Pfad den Restore-Fehler (``suppress(Exception)``) und lieferte nur den
ursprünglichen Config-Fehler zurück. Der Singleton blieb dabei ge-``reset`` und
evtl. ohne initialisierten Buffer → Recording steht, Query-Endpunkte liefern
disabled/500, ohne dass der Betreiber vom degradierten Zustand erfährt.

Erwartung: der Rollback-Fehler wird dem Aufrufer klar signalisiert (HTTP 500 mit
BEIDEN Fehlern in der Detail-Message) und der resultierende Zustand ist
deterministisch (deaktiviert, kein Buffer).
"""

from __future__ import annotations

import pytest

from obs.ringbuffer.persisted_config import DEFAULT_SEGMENT_MAX_AGE_SECONDS

pytestmark = pytest.mark.integration


async def _reset_to_defaults(client, auth_headers):
    """Session-scoped App für nachfolgende Tests stabil halten.

    Der degradierte Zustand (kein Buffer, deaktiviert) würde sonst in andere
    Tests lecken. Ein sauberer ``segmented=True``-Config-Call baut den Store
    wieder auf.
    """
    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={
            "enabled": True,
            "storage": "file",
            "max_entries": 1000,
            "max_file_size_bytes": None,
            "max_age": None,
            "segmented": True,
            "segment_max_age": DEFAULT_SEGMENT_MAX_AGE_SECONDS,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


async def test_config_post_switch_rollback_restore_failure_is_reported(client, auth_headers, monkeypatch):
    """Scheitert Rebuild UND Restore, wird der Rollback-Fehler SICHTBAR gemeldet.

    Kern des Codex-Findings: der Restore-Fehler darf nicht verschluckt werden.
    Erwartung:
    - HTTP 500 (kein stiller Erfolg, keine bloße 422-Config-Antwort),
    - die Detail-Message enthält SOWOHL den ursprünglichen Config-/Rebuild-Fehler
      ALS AUCH den Rollback-Restore-Fehler,
    - der resultierende Zustand ist deterministisch: deaktiviert, kein Buffer.
    """
    import obs.api.v1.ringbuffer as rb_api
    from obs.ringbuffer.ringbuffer import get_optional_ringbuffer, is_ringbuffer_enabled

    # Vorbedingung unabhängig von der Testreihenfolge herstellen: segmentierter,
    # laufender Buffer (andere Tests hinterlassen evtl. den Legacy-Modus).
    await _reset_to_defaults(client, auth_headers)
    rb_before = get_optional_ringbuffer()
    assert rb_before is not None and rb_before.segmented and rb_before.store is not None

    # BEIDE ``init_ringbuffer``-Aufrufe scheitern lassen: der Rebuild im Ziel-Modus
    # (Legacy, ``segmented=False``) UND das Restore des alten Modus (``segmented=True``).
    async def boom(*args, **kwargs):
        if kwargs.get("segmented") is False:
            raise RuntimeError("simulated rebuild failure")
        raise RuntimeError("simulated restore failure")

    monkeypatch.setattr(rb_api, "init_ringbuffer", boom)

    resp = await client.post(
        "/api/v1/ringbuffer/config",
        json={"segmented": False, "max_entries": 1000, "max_file_size_bytes": None, "max_age": None},
        headers=auth_headers,
    )

    monkeypatch.undo()

    # Der Rollback-Fehler ist SICHTBAR: 500, und BEIDE Fehler stehen in der Antwort.
    assert resp.status_code == 500, resp.text
    detail = resp.json()["detail"]
    assert "simulated rebuild failure" in detail
    assert "simulated restore failure" in detail

    # Deterministischer Zustand: deaktiviert, kein Buffer.
    assert is_ringbuffer_enabled() is False
    assert get_optional_ringbuffer() is None

    await _reset_to_defaults(client, auth_headers)


async def test_config_post_switch_rollback_restore_success_unchanged(client, auth_headers, monkeypatch):
    """Gegentest: gelingt das Restore, bleibt das bisherige Verhalten unverändert.

    Nur der Rebuild im Ziel-Modus scheitert, das Restore des alten Modus gelingt.
    Erwartung wie zuvor: der ursprüngliche Config-/Rebuild-Fehler propagiert
    (raw Exception via ASGITransport), der alte segmentierte Buffer läuft weiter
    und der Rollback-Fehler-Pfad wird NICHT betreten.
    """
    from obs.core.event_bus import DataValueEvent, get_event_bus
    from obs.ringbuffer.ringbuffer import get_optional_ringbuffer, is_ringbuffer_enabled

    # Vorbedingung unabhängig von der Testreihenfolge herstellen.
    await _reset_to_defaults(client, auth_headers)
    rb_before = get_optional_ringbuffer()
    assert rb_before is not None and rb_before.segmented and rb_before.store is not None

    import obs.api.v1.ringbuffer as rb_api

    real_init = rb_api.init_ringbuffer

    async def boom(*args, **kwargs):
        if kwargs.get("segmented") is False:
            raise RuntimeError("simulated rebuild failure")
        return await real_init(*args, **kwargs)

    monkeypatch.setattr(rb_api, "init_ringbuffer", boom)

    # Restore gelingt → der ursprüngliche Fehler propagiert als raw Exception,
    # NICHT als gemeldete 500-Rollback-Antwort.
    with pytest.raises(RuntimeError, match="simulated rebuild failure"):
        await client.post(
            "/api/v1/ringbuffer/config",
            json={"segmented": False, "max_entries": 1000, "max_file_size_bytes": None, "max_age": None},
            headers=auth_headers,
        )

    monkeypatch.undo()

    # Der alte, segmentierte Buffer läuft weiter (aktiviert, Subscription intakt).
    rb_after = get_optional_ringbuffer()
    assert rb_after is not None
    assert rb_after.segmented is True
    assert rb_after.store is not None
    assert is_ringbuffer_enabled() is True
    handlers = get_event_bus()._handlers.get(DataValueEvent, [])
    assert rb_after.handle_value_event in handlers

    await _reset_to_defaults(client, auth_headers)
