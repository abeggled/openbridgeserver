"""Mode-Switch-Rebuild haelt den RingBuffer DISABLED (#951 [P2] "Keep ringbuffer disabled during mode rebuild").

Codex-Finding: aendert ein Admin den ``segmented``-Modus eines LAUFENDEN Monitors,
stoppt der Config-Pfad den alten Buffer und ``reset``et den Singleton, laesst
``_enabled`` aber true, bis das awaited ``init_ringbuffer()`` den Ersatz fertig
aufgebaut hat. In diesem Rebuild-Fenster sehen nebenlaeufige query/export-Requests
``is_ringbuffer_enabled()`` → true und ``get_ringbuffer()`` wirft dann (Singleton ist
``None``) → transiente HTTP 500 beim Speichern der Config.

Erwartung: waehrend des Rebuild-Fensters liefern Read-Pfade deterministisch
"disabled" (leere Seite, HTTP 200), NIE eine unbehandelte 500. Nach erfolgreichem
Switch ist der Monitor wieder enabled und funktionsfaehig.
"""

from __future__ import annotations

import asyncio

import pytest

from obs.ringbuffer.persisted_config import DEFAULT_SEGMENT_MAX_AGE_SECONDS

pytestmark = pytest.mark.integration


async def _reset_to_segmented(client, auth_headers):
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


async def test_concurrent_query_during_mode_rebuild_gets_disabled_not_500(client, auth_headers, monkeypatch):
    """Nebenlaeufig zum Mode-Switch-Rebuild liefert /query deterministisch "disabled" (kein 500).

    Ablauf: ein Config-POST schaltet ``segmented=True`` → ``segmented=False``. Der Rebuild
    stoppt den alten Buffer, ``reset``et den Singleton und baut im Ziel-Modus neu auf.
    ``init_ringbuffer`` wird so gepatcht, dass es MITTEN im Rebuild-Fenster pausiert; dort
    feuern wir eine nebenlaeufige /query-Anfrage ab und pruefen: KEIN 500, sondern der
    deterministische Disabled-Pfad (HTTP 200, leere Seite).
    """
    import obs.api.v1.ringbuffer as rb_api
    from obs.ringbuffer.ringbuffer import get_optional_ringbuffer, is_ringbuffer_enabled

    await _reset_to_segmented(client, auth_headers)
    rb_before = get_optional_ringbuffer()
    assert rb_before is not None and rb_before.segmented and rb_before.store is not None

    real_init = rb_api.init_ringbuffer
    in_window = asyncio.Event()
    release = asyncio.Event()
    observed: dict = {}

    async def paused_init(*args, **kwargs):
        # Wir sind jetzt IM Rebuild-Fenster: der alte Buffer ist gestoppt+ge-``reset``,
        # der neue noch nicht da. Genau hier muss der Store deterministisch disabled sein.
        observed["enabled_in_window"] = is_ringbuffer_enabled()
        observed["buffer_in_window"] = get_optional_ringbuffer()
        in_window.set()
        await release.wait()
        return await real_init(*args, **kwargs)

    monkeypatch.setattr(rb_api, "init_ringbuffer", paused_init)

    config_task = asyncio.create_task(
        client.post(
            "/api/v1/ringbuffer/config",
            json={"segmented": False, "max_entries": 1000, "max_file_size_bytes": None, "max_age": None},
            headers=auth_headers,
        )
    )

    # Auf das Rebuild-Fenster warten, dann nebenlaeufig eine /query-Anfrage feuern.
    await asyncio.wait_for(in_window.wait(), timeout=5.0)

    # Im Fenster: deterministisch disabled, kein aktiver Buffer.
    assert observed["enabled_in_window"] is False
    assert observed["buffer_in_window"] is None

    query_resp = await client.post(
        "/api/v1/ringbuffer/query",
        json={"filters": {}, "pagination": {"limit": 50, "offset": 0}, "sort": {"field": "id", "order": "desc"}},
        headers=auth_headers,
    )

    # KEIN 500 waehrend des Rebuilds; deterministisch disabled (leere Liste).
    assert query_resp.status_code == 200, query_resp.text
    assert query_resp.json() == []

    # Rebuild abschliessen lassen.
    release.set()
    config_resp = await asyncio.wait_for(config_task, timeout=5.0)
    monkeypatch.undo()

    # Gegentest: nach erfolgreichem Switch ist der Monitor wieder enabled + funktionsfaehig.
    assert config_resp.status_code == 200, config_resp.text
    rb_after = get_optional_ringbuffer()
    assert rb_after is not None
    assert rb_after.segmented is False
    assert is_ringbuffer_enabled() is True

    ok_query = await client.post(
        "/api/v1/ringbuffer/query",
        json={"filters": {}, "pagination": {"limit": 50, "offset": 0}, "sort": {"field": "id", "order": "desc"}},
        headers=auth_headers,
    )
    assert ok_query.status_code == 200, ok_query.text

    await _reset_to_segmented(client, auth_headers)
