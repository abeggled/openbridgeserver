"""Codex-Runde-36-Findings (#951, drei [P2] in ``ringbuffer.py``) – Parität gegen Legacy.

Diese Suite fixiert drei Feinschliff-Findings aus Runde 36:

**F1 (:1438) – „Validate only name hits that survive the scope".**
Der segmentierte Pfad addierte die Namens-Treffer (``dp_ids_by_name``/``q``-aufgelöste
IDs) BLIND zur Value-Filter-Typprüfmenge – auch wenn diese Namen unter dem effektiven
adapter/time/metadata-Scope KEINE Zeilen haben. Matcht ``q`` also einen STRING-Namen
OHNE Zeilen im angefragten Adapter-/Zeitfenster, während die tatsächlich gescopten
Zeilen numerisch sind, wirft der segmentierte Pfad fälschlich 422, der row-lazy Legacy-
Pfad dagegen nicht. Fix: die Namens-Treffer NICHT blind unionen, sondern der Discovery
(die den vollen Scope inkl. adapter/time/metadata berücksichtigt) die Kandidatenmenge
überlassen – ein Name-Treffer ohne in-scope-Zeilen darf kein 422 auslösen.

**F2 (:366) – „Reattach repaired quarantined legacy files".**
Wurde eine attached Legacy-DB nach einem Read-Fehler ``quarantined``, behandelte der
Startup-Attach-Guard die Manifest-Zeile weiter als existierende Legacy-Quelle (nur weil
ihr ``schema_version`` legacy ist) und übersprang ``classify()``/``attach_readonly()``.
``list_segments_for_query`` schließt quarantined-Zeilen aber aus → die reparierte
Historie blieb dauerhaft versteckt. Fix: ändert sich die physische Datei-Identität
(hier: reale Disk-Größe inkl. ``-wal``/``-shm``) gegenüber der bei Quarantäne
persistierten ``size_bytes``, wird die stale quarantined-Zeile entfernt und die Datei
neu klassifiziert/attached. Unveränderte Datei → bleibt quarantined (kein Flapping).

**F3 (:2186) – „Check stored value types before SQL pushdown".**
Prinzipielle Spannung: perfekte Parität bräuchte Row-Scanning der GESPEICHERTEN
Wert-Typen – genau das, was der bounded Pushdown vermeidet. Gewählter Weg:
dokumentierter bounded-best-effort-Kompromiss (analog Runde-35-Metadaten-Kompromiss).
Der segmentierte Pfad prüft die Registry-Typen der Kandidaten, NICHT die historischen
Row-Typen; hat ein aktuell numerischer Datapoint eine historische STRING-Zeile, liefert
segmentiert Teilergebnisse (SQL-Prädikat filtert die STRING-Zeile via ``new_value_num``
weg), während Legacy die Zeile row-lazy typ-checkt und 422 wirft. Dieser Test schreibt
den DOKUMENTIERTEN Unterschied fest, statt still zu divergieren.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer


def _rb(disk_path: Path, **kwargs) -> RingBuffer:
    return RingBuffer(storage="file", disk_path=str(disk_path), **kwargs)


async def _record(rb: RingBuffer, value: object, ts: str, *, datapoint_id: str, adapter: str) -> None:
    await rb.record(
        ts=ts,
        datapoint_id=datapoint_id,
        topic=f"dp/{datapoint_id}/value",
        old_value=None,
        new_value=value,
        source_adapter=adapter,
        quality="good",
        metadata_version=1,
        metadata={},
    )


# Registry-Typuniversum wie es der API-Layer aus ``registry_entries`` baut.
_TYPES = {
    "dp-num": "FLOAT",
    "dp-str": "STRING",
}


async def _make_rb(disk_path: Path, *, segmented: bool) -> RingBuffer:
    """Installation: numerischer Datapoint an ``numeric-adapter`` + STRING-Datapoint
    an ``string-adapter``. ``dp-str`` heißt so, dass ``q='dp-str'`` seinen NAMEN matcht,
    er aber im ``numeric-adapter``-Scope KEINE Zeilen hat."""
    rb = _rb(disk_path, segmented=segmented)
    await rb.start()
    await _record(rb, 5, "2026-01-01T00:00:00.000Z", datapoint_id="dp-num", adapter="numeric-adapter")
    await _record(rb, 9, "2026-01-01T00:00:01.000Z", datapoint_id="dp-num", adapter="numeric-adapter")
    await _record(rb, "hello", "2026-01-01T00:00:02.000Z", datapoint_id="dp-str", adapter="string-adapter")
    return rb


# ===========================================================================
# F1: Name-Treffer nur validieren, wenn sie im effektiven Scope Zeilen haben
# ===========================================================================


@pytest.mark.asyncio
async def test_f1_name_hit_without_in_scope_rows_does_not_reject(tmp_path: Path):
    """``q`` matcht STRING-Namen OHNE Zeilen im adapter-Scope → KEIN 422 (Parität Legacy).

    Scope: ``adapter_any_of=['numeric-adapter']`` (AND-verknüpft mit dem ``q``-Prädikat).
    ``q='dp-str'`` matcht per LIKE den NAMEN des STRING-Datapoints, der aber im
    numeric-adapter-Scope keine Zeilen hat → die effektive Kandidatenmenge ist LEER.
    Legacy typ-checkt nur zurückgegebene Zeilen und wirft daher KEIN 422 (liefert leer).
    Der segmentierte Pfad addierte den phantom-Namens-Treffer bisher blind zur
    Typprüfmenge und warf fälschlich 422 – hier fixiert: gleiche leere Rückgabe, kein 422.
    """
    legacy = await _make_rb(tmp_path / "legacy", segmented=False)
    seg = await _make_rb(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 6}]
        legacy_rows = await legacy.query_v2(q="dp-str", adapter_any_of=["numeric-adapter"], value_filters=vf, datapoint_types=_TYPES, limit=10)
        seg_rows = await seg.query_v2(q="dp-str", adapter_any_of=["numeric-adapter"], value_filters=vf, datapoint_types=_TYPES, limit=10)
        assert [e.new_value for e in legacy_rows] == []
        assert [e.new_value for e in seg_rows] == []
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_f1_dp_ids_by_name_without_in_scope_rows_does_not_reject(tmp_path: Path):
    """Gleiche Parität über ``dp_ids_by_name`` statt ``q``: aufgelöster STRING-Name ohne
    in-scope-Zeilen darf kein 422 auslösen (Legacy liefert leer, kein Typ-Check)."""
    legacy = await _make_rb(tmp_path / "legacy", segmented=False)
    seg = await _make_rb(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 6}]
        legacy_rows = await legacy.query_v2(
            dp_ids_by_name=["dp-str"], adapter_any_of=["numeric-adapter"], value_filters=vf, datapoint_types=_TYPES, limit=10
        )
        seg_rows = await seg.query_v2(
            dp_ids_by_name=["dp-str"], adapter_any_of=["numeric-adapter"], value_filters=vf, datapoint_types=_TYPES, limit=10
        )
        assert [e.new_value for e in legacy_rows] == []
        assert [e.new_value for e in seg_rows] == []
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_f1_name_hit_with_in_scope_rows_still_rejects(tmp_path: Path):
    """Gegentest: STRING-Name-Treffer MIT in-scope-Zeilen → weiterhin 422 (Parität Legacy).

    Ohne Adapter-Scope hat ``dp-str`` echte Zeilen. ``q='dp-str'`` + ``gt`` muss dann in
    beiden Pfaden 422 werfen (Legacy typ-checkt die zurückgegebene STRING-Zeile).
    """
    legacy = await _make_rb(tmp_path / "legacy", segmented=False)
    seg = await _make_rb(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 1}]
        with pytest.raises(ValueError):
            await legacy.query_v2(q="dp-str", value_filters=vf, datapoint_types=_TYPES, limit=10)
        with pytest.raises(ValueError):
            await seg.query_v2(q="dp-str", value_filters=vf, datapoint_types=_TYPES, limit=10)
    finally:
        await legacy.stop()
        await seg.stop()


# ===========================================================================
# F2: reparierte quarantined Legacy-Datei wird wieder eingehängt
# ===========================================================================


async def _seed_legacy(disk_path: Path, values: list[int]) -> None:
    """Legacy-Single-File-RingBuffer befüllen und schließen."""
    legacy = RingBuffer(storage="file", disk_path=str(disk_path))
    await legacy.start()
    try:
        for i, value in enumerate(values):
            await legacy.record(
                ts=f"2025-01-01T00:00:0{i}.000Z",
                datapoint_id="dp-leg",
                topic="dp/dp-leg/value",
                old_value=None,
                new_value=value,
                source_adapter="api",
                quality="good",
            )
    finally:
        await legacy.stop()


async def _quarantine_legacy_segment(rb: RingBuffer) -> None:
    """Markiert die eingehängte Legacy-Segment-Zeile als ``quarantined`` (simuliert einen
    Read-Fehler auf der attached Datei)."""
    from obs.ringbuffer.store.manifest import LEGACY_SCHEMA_VERSION

    store = rb._store
    assert store is not None
    segments = await store.manifest.list_segments()
    legacy_rows = [s for s in segments if s.schema_version == LEGACY_SCHEMA_VERSION]
    assert legacy_rows, "erwartete eine eingehängte Legacy-Segment-Zeile"
    for seg in legacy_rows:
        await store.manifest.mark_quarantined(seg.segment_id, "corrupt-read (Test)")


@pytest.mark.asyncio
async def test_f2_repaired_quarantined_legacy_is_reattached(tmp_path: Path):
    """Quarantined Legacy-DB, danach Datei-Identität geändert (Reparatur) → Startup re-attached.

    Ablauf: Legacy befüllen → segmentiert starten (attach) → Legacy-Segment quarantinieren
    → stoppen → Legacy-Datei ersetzen/erweitern (Reparatur, andere Disk-Größe) → neu
    starten. Die reparierte Historie muss wieder sichtbar sein.
    """
    disk_path = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(disk_path, [100, 101])

    rb = _rb(disk_path, segmented=True)
    await rb.start()
    try:
        # Vor Quarantäne ist die Historie sichtbar.
        entries = await rb.query_v2(limit=10)
        assert {e.new_value for e in entries} == {100, 101}
        await _quarantine_legacy_segment(rb)
        # Nach Quarantäne verschwindet sie aus dem Query-Set.
        entries = await rb.query_v2(limit=10)
        assert {e.new_value for e in entries} == set()
    finally:
        await rb.stop()

    # Operator repariert/ersetzt dieselbe obs_ringbuffer.db mit mehr Zeilen (Größe ändert sich).
    await _seed_legacy(disk_path, [100, 101, 102, 103, 104, 105, 106, 107])

    rb2 = _rb(disk_path, segmented=True)
    await rb2.start()
    try:
        entries = await rb2.query_v2(limit=20)
        assert {100, 101, 102, 103} <= {e.new_value for e in entries}, "reparierte Legacy-Historie muss wieder sichtbar sein"
    finally:
        await rb2.stop()


@pytest.mark.asyncio
async def test_f2_unchanged_quarantined_legacy_stays_hidden(tmp_path: Path):
    """Gegentest: unveränderte quarantined Datei → bleibt quarantined (kein Flapping)."""
    disk_path = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(disk_path, [100, 101])

    rb = _rb(disk_path, segmented=True)
    await rb.start()
    try:
        await _quarantine_legacy_segment(rb)
    finally:
        await rb.stop()

    # Datei NICHT anfassen → Identität unverändert → bleibt versteckt.
    rb2 = _rb(disk_path, segmented=True)
    await rb2.start()
    try:
        entries = await rb2.query_v2(limit=10)
        assert {e.new_value for e in entries} == set(), "unveränderte quarantined Datei darf nicht re-attachen"
        # Manifest-Zeile bleibt quarantined.
        from obs.ringbuffer.store.manifest import LEGACY_SCHEMA_VERSION, SEGMENT_STATUS_QUARANTINED

        store = rb2._store
        assert store is not None
        legacy_rows = [s for s in await store.manifest.list_segments() if s.schema_version == LEGACY_SCHEMA_VERSION]
        assert legacy_rows and all(s.status == SEGMENT_STATUS_QUARANTINED for s in legacy_rows)
    finally:
        await rb2.stop()


@pytest.mark.asyncio
async def test_f2_malformed_attach_identity_sidecar_is_conservative(tmp_path: Path):
    """Sidecar mit Nicht-Integer-Werten (Truncation/manueller Edit) → konservativ quarantined.

    Runde 47 (Codex): ein JSON-Objekt-Sidecar mit z. B. String-Wert ließ ``int(v)``
    mit ``ValueError`` durchschlagen und brach den gesamten Segment-Store-Startup ab,
    obwohl die Funktion korrupte Sidecars als konservatives ``False`` dokumentiert.
    Erwartung: Startup läuft durch, die unveränderte Datei bleibt quarantined.
    """
    disk_path = tmp_path / "obs_ringbuffer.db"
    await _seed_legacy(disk_path, [100, 101])

    rb = _rb(disk_path, segmented=True)
    await rb.start()
    try:
        await _quarantine_legacy_segment(rb)
    finally:
        await rb.stop()

    # Sidecar beschädigen: gültiges JSON-Objekt, aber Nicht-Integer-Wert.
    sidecar = disk_path.with_name(f"{disk_path.name}.attach_identity")
    sidecar.write_text('{"mtime_ns": "kaputt", "size": null}', encoding="utf-8")

    rb2 = _rb(disk_path, segmented=True)
    await rb2.start()  # darf NICHT mit ValueError/TypeError abbrechen
    try:
        entries = await rb2.query_v2(limit=10)
        assert {e.new_value for e in entries} == set(), "korrupter Sidecar darf kein Re-Attach ausloesen"
    finally:
        await rb2.stop()


# ===========================================================================
# F3: gespeicherte Row-Typen vor SQL-Pushdown – dokumentierter Kompromiss
# ===========================================================================


async def _make_num_dp_with_string_history(disk_path: Path, *, segmented: bool) -> RingBuffer:
    """Ein aktuell NUMERISCHER Datapoint (Registry FLOAT), der eine historische STRING-
    Zeile im Buffer hat (Typwechsel / fehlerhafter Adapter-Write)."""
    rb = _rb(disk_path, segmented=segmented)
    await rb.start()
    await _record(rb, 5, "2026-01-01T00:00:00.000Z", datapoint_id="dp-num", adapter="numeric-adapter")
    await _record(rb, "legacy-string", "2026-01-01T00:00:01.000Z", datapoint_id="dp-num", adapter="numeric-adapter")
    await _record(rb, 9, "2026-01-01T00:00:02.000Z", datapoint_id="dp-num", adapter="numeric-adapter")
    return rb


@pytest.mark.asyncio
async def test_f3_mixed_type_history_consistent_skip(tmp_path: Path):
    """Numerischer Datapoint mit historischer STRING-Zeile: Legacy UND segmentiert skippen.

    Auflösung der früher dokumentierten Divergenz (#951, Codex :2263): Bis Runde 36
    warf der Legacy/row-lazy-Pfad 422 auf der Mixed-Type-Historie, während der
    segmentierte SQL-Pushdown die STRING-Zeile still übersprang – derselbe
    datapoint-gescopte ``gt``-Filter lieferte je nach Storage-Modus mal 422, mal ein
    Teilergebnis. Jetzt behandeln BEIDE Pfade eine nicht-numerische HISTORIE-Zeile als
    kein-Match (skip); ein ungültiger FILTER-Wert bleibt in beiden ein 422. Damit
    liefern Legacy und segmentiert identisch die numerischen Treffer (``[9]`` für
    ``gt 6``). Konsistent zur v1-Referenz ``test_legacy_range_filter_excludes_cross_type_rows``.
    """
    legacy = await _make_num_dp_with_string_history(tmp_path / "legacy", segmented=False)
    seg = await _make_num_dp_with_string_history(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 6}]
        legacy_rows = await legacy.query_v2(datapoint_ids=["dp-num"], value_filters=vf, datapoint_types=_TYPES, limit=10)
        seg_rows = await seg.query_v2(datapoint_ids=["dp-num"], value_filters=vf, datapoint_types=_TYPES, limit=10)
        # Beide überspringen die STRING-Zeile und liefern denselben numerischen Treffer.
        assert [e.new_value for e in legacy_rows] == [9]
        assert [e.new_value for e in seg_rows] == [9]
        # Ein ungültiger FILTER-Wert wirft weiter – in beiden Modi konsistent.
        bad = [{"operator": "gt", "value": None}]
        for rb in (legacy, seg):
            with pytest.raises(ValueError):
                await rb.query_v2(datapoint_ids=["dp-num"], value_filters=bad, datapoint_types=_TYPES, limit=10)
    finally:
        await legacy.stop()
        await seg.stop()
