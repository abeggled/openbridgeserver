"""Discovery bounded UND exhaustiv (#919, Review #951 Runde 35).

Zwei Codex-[P2]-Findings zur Typ-Discovery aus Runde 33
(``distinct_datapoint_ids``). Sie stehen in SPANNUNG und werden GEMEINSAM
gelöst – die Lösung muss BEIDES erfüllen: bounded (Perf) UND exhaustiv (Parität).

**Finding 1 (bounded DISTINCT type discovery).**
Für eine value-gefilterte Query OHNE explizite ``datapoint_ids`` ruft der
segmentierte Read-Pfad die Discovery VOR der paged Query. Für unwindowed Scopes
(``q``, Metadaten-Filter, unscoped ``gt``) hatte das ``SELECT DISTINCT`` KEIN
LIMIT/candidate-cap und die leading-wildcard-``LIKE``/``EXISTS``-Prädikate zwangen
SQLite, die historischen Segment-Zeilen NUR zur Typvalidierung zu walken. Auf einem
20–30 GB-Store blockiert damit eine latest-page-Monitor-Query auf einem Full-
History-Discovery-Scan, BEVOR die bounded Query läuft.

Fix: Die reine ``datapoint_id``-DISTINCT-Discovery nutzt den Covering-Index
(``idx_rb_dp_ts_id``) und ist durch die DISTINCT-Anzahl gebunden. Der Freitext-``q``
wird NICHT mehr als ``LIKE``-Scan gepusht, sondern über eine per-Segment
``(datapoint_id, source_adapter)``-Summary (ebenfalls durch DISTINCT gebunden, für
geschlossene Segmente gecacht) in Python gefiltert. Der metadaten-bewusste Fall
läuft über die gedeckelte Kandidaten-Subquery (Runde 31), also bounded.

**Finding 2 (legacy type discovery exhaustive).**
Der Legacy-Discovery-Zweig delegierte an ``_query_legacy_segment`` mit
``StoreQuery``s Default ``limit=100``. Für eine attached Legacy-DB, deren neueste
100 in-scope-Zeilen numerisch sind, deren ältere in-scope-Zeile aber
STRING/BOOLEAN/gelöscht ist, verpasste die Validierung diesen Datapoint → der
Legacy-Fallback behandelt die numerische Range dann als Non-Match und droppt die
Zeilen still, statt das Legacy-422 zu wahren.

Fix: Legacy-Discovery läuft erschöpfend (kein ``limit=100``), erfasst also auch
ältere/gelöschte in-scope Datapoints → korrektes 422 (Parität).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from obs.ringbuffer.ringbuffer import RingBuffer
from obs.ringbuffer.store.interface import StoreEvent, StoreQuery
from obs.ringbuffer.store.sqlite_backend import SqliteSegmentStore

_LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS ringbuffer (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT    NOT NULL,
    datapoint_id   TEXT    NOT NULL,
    topic          TEXT    NOT NULL,
    old_value      TEXT,
    new_value      TEXT,
    source_adapter TEXT    NOT NULL,
    quality        TEXT    NOT NULL,
    metadata_version INTEGER NOT NULL DEFAULT 1,
    metadata       TEXT    NOT NULL DEFAULT '{}'
);
"""


def _rb(tmp_path: Path, **kwargs) -> RingBuffer:
    return RingBuffer(
        storage="file",
        disk_path=str(tmp_path / "obs_ringbuffer.db"),
        **kwargs,
    )


async def _record(rb: RingBuffer, value: object, ts: str, *, datapoint_id: str, adapter: str = "api") -> None:
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


# ===========================================================================
# Finding 1 (bounded): unwindowed ``q``-Discovery über einen Scope mit vielen
# historischen Zeilen weniger Datapoints darf KEINEN Full-Row-Scan (leading-
# wildcard ``LIKE`` ohne Cap) ausführen. Nachweis über die tatsächlich an SQLite
# abgesetzte Discovery-SQL: sie enthält kein unbounded ``datapoint_id LIKE`` /
# ``source_adapter LIKE`` mehr (der Freitext läuft summary-basiert in Python).
# ===========================================================================


async def _make_rb_q_hot_history(tmp_path: Path, *, segmented: bool) -> RingBuffer:
    """Viele historische Zeilen, wenige distinkte Datapoints; ``q='hot'`` matcht per id."""
    rb = _rb(tmp_path, segmented=segmented)
    await rb.start()
    # Ein „heißer" numerischer Datapoint mit vielen Zeilen (matcht q='hot' per id).
    for i in range(60):
        await _record(rb, i, f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}.000Z", datapoint_id="dp-hot", adapter="ada")
    return rb


@pytest.mark.asyncio
async def test_unwindowed_q_discovery_is_bounded_no_like_scan(tmp_path: Path):
    """Unwindowed ``q``-Discovery setzt kein unbounded ``LIKE``-Prädikat mehr ab (bounded).

    Der frühere ``_distinct_ids_v2_segment`` baute für ``q`` ein
    ``SELECT DISTINCT datapoint_id ... WHERE (datapoint_id LIKE ? OR
    source_adapter LIKE ?)`` OHNE Cap → SQLite walkt jede Zeile. Wir fangen die
    tatsächlich abgesetzten Discovery-SQLs ab und prüfen: KEINE davon trägt einen
    unbounded Leading-Wildcard-``LIKE`` (der Freitext läuft nun summary-basiert in
    Python bzw. über eine gedeckelte Subquery).
    """
    seg = await _make_rb_q_hot_history(tmp_path / "seg", segmented=True)
    store = seg._store
    captured: list[str] = []
    orig = store._distinct_ids_v2_segment

    async def _capturing(conn, segment, query):
        # Alle in dieser Methode abgesetzten SQLs mitschneiden.
        real_execute = conn.execute
        seen: list[str] = []

        def _wrap(sql, *a, **kw):
            seen.append(sql)
            return real_execute(sql, *a, **kw)

        conn.execute = _wrap  # type: ignore[method-assign]
        try:
            return await orig(conn, segment, query)
        finally:
            conn.execute = real_execute  # type: ignore[method-assign]
            captured.extend(seen)

    store._distinct_ids_v2_segment = _capturing  # type: ignore[method-assign]
    try:
        types = {"dp-hot": "FLOAT"}
        rows = await seg.query_v2(q="hot", value_filters=[{"operator": "gt", "value": 57}], datapoint_types=types, limit=10)
        assert sorted(e.new_value for e in rows) == [58, 59]
    finally:
        store._distinct_ids_v2_segment = orig  # type: ignore[method-assign]
        await seg.stop()

    # Discovery-SQL wurde abgesetzt …
    assert captured, "Discovery hat keine SQL abgesetzt"
    # … und KEINE davon ist ein unbounded Leading-Wildcard-``LIKE``-Scan.
    for sql in captured:
        norm = " ".join(sql.split()).lower()
        if "like ?" in norm and "limit" not in norm:
            pytest.fail(f"Unbounded LIKE-Scan in Discovery-SQL: {norm!r}")


@pytest.mark.asyncio
async def test_q_discovery_parity_deleted_string_dp_rejects_like_legacy(tmp_path: Path):
    """``q``-Discovery bleibt exhaustiv: ein gelöschter STRING-``q``-Treffer → 422 wie Legacy.

    Parität-Anker für Finding 1: die summary-basierte ``q``-Discovery darf den
    älteren STRING-Datapoint NICHT verlieren.
    """
    types = {"dp-num-hot": "FLOAT"}

    async def _make(tmp: Path, *, segmented: bool) -> RingBuffer:
        rb = _rb(tmp, segmented=segmented)
        await rb.start()
        # Älterer STRING-Datapoint, matcht q='hot' per id.
        await _record(rb, "warm", "2026-01-01T00:00:00.000Z", datapoint_id="dp-str-hot", adapter="ada")
        # Neuere numerische Zeilen eines anderen q-Treffers.
        await _record(rb, 5, "2026-01-01T00:00:05.000Z", datapoint_id="dp-num-hot", adapter="ada")
        await _record(rb, 9, "2026-01-01T00:00:09.000Z", datapoint_id="dp-num-hot", adapter="ada")
        return rb

    legacy = await _make(tmp_path / "legacy", segmented=False)
    seg = await _make(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 1}]
        with pytest.raises(ValueError):
            await legacy.query_v2(q="hot", value_filters=vf, datapoint_types=types, limit=10, is_export=True)
        with pytest.raises(ValueError):
            await seg.query_v2(q="hot", value_filters=vf, datapoint_types=types, limit=10, is_export=True)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_q_matches_source_adapter_discovery(tmp_path: Path):
    """``q`` matcht per ``source_adapter`` (nicht id): die Summary erfasst den Adapter → 422 wie Legacy.

    Ein datapoint-id-only-Summary würde diesen Fall verpassen; die
    ``(datapoint_id, source_adapter)``-Summary deckt ihn ab.
    """
    types = {"dp-num": "FLOAT"}

    async def _make(tmp: Path, *, segmented: bool) -> RingBuffer:
        rb = _rb(tmp, segmented=segmented)
        await rb.start()
        # STRING-Zeile, deren ADAPTER q='knx' matcht (die id NICHT).
        await _record(rb, "state", "2026-01-01T00:00:00.000Z", datapoint_id="dp-str", adapter="knx-bus")
        await _record(rb, 5, "2026-01-01T00:00:05.000Z", datapoint_id="dp-num", adapter="knx-bus")
        return rb

    legacy = await _make(tmp_path / "legacy", segmented=False)
    seg = await _make(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 1}]
        with pytest.raises(ValueError):
            await legacy.query_v2(q="knx", value_filters=vf, datapoint_types={**types, "dp-str": "STRING"}, limit=10, is_export=True)
        with pytest.raises(ValueError):
            await seg.query_v2(q="knx", value_filters=vf, datapoint_types={**types, "dp-str": "STRING"}, limit=10, is_export=True)
    finally:
        await legacy.stop()
        await seg.stop()


# ===========================================================================
# Finding 1 (metadaten-Scope, unwindowed): der Metadaten-Value-Filter läuft über die
# gedeckelte Kandidaten-Subquery (kein unbounded inline-``EXISTS``). Innerhalb des
# Caps bleibt die Parität zum Legacy-Pfad erhalten: ein getaggter STRING-Datapoint
# erzwingt 422; rein numerische Tag-Treffer nicht.
# ===========================================================================


async def _record_tagged(rb: RingBuffer, value: object, ts: str, *, datapoint_id: str, tags: list[str]) -> None:
    await rb.record(
        ts=ts,
        datapoint_id=datapoint_id,
        topic=f"dp/{datapoint_id}/value",
        old_value=None,
        new_value=value,
        source_adapter="ada",
        quality="good",
        metadata_version=1,
        metadata={"datapoint": {"tags": tags}},
    )


@pytest.mark.asyncio
async def test_metadata_scope_unwindowed_string_rejects_like_legacy(tmp_path: Path):
    """Metadaten-Tag-Scope + ``gt`` über einen getaggten STRING-Datapoint → 422 wie Legacy."""
    types = {"dp-num": "FLOAT", "dp-str": "STRING"}

    async def _make(tmp: Path, *, segmented: bool) -> RingBuffer:
        rb = _rb(tmp, segmented=segmented)
        await rb.start()
        await _record_tagged(rb, "on", "2026-01-01T00:00:00.000Z", datapoint_id="dp-str", tags=["klima"])
        await _record_tagged(rb, 5, "2026-01-01T00:00:05.000Z", datapoint_id="dp-num", tags=["klima"])
        return rb

    legacy = await _make(tmp_path / "legacy", segmented=False)
    seg = await _make(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 1}]
        with pytest.raises(ValueError):
            await legacy.query_v2(metadata_tags_any_of=["klima"], value_filters=vf, datapoint_types=types, limit=10, is_export=True)
        with pytest.raises(ValueError):
            await seg.query_v2(metadata_tags_any_of=["klima"], value_filters=vf, datapoint_types=types, limit=10, is_export=True)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_metadata_scope_unwindowed_numeric_only_no_422(tmp_path: Path):
    """Gegentest: Metadaten-Tag-Scope matcht nur numerische Datapoints → kein 422."""
    types = {"dp-num": "FLOAT", "dp-str": "STRING"}

    async def _make(tmp: Path, *, segmented: bool) -> RingBuffer:
        rb = _rb(tmp, segmented=segmented)
        await rb.start()
        # STRING-Datapoint trägt einen ANDEREN Tag, ist also NICHT im Scope.
        await _record_tagged(rb, "on", "2026-01-01T00:00:00.000Z", datapoint_id="dp-str", tags=["licht"])
        await _record_tagged(rb, 5, "2026-01-01T00:00:05.000Z", datapoint_id="dp-num", tags=["klima"])
        await _record_tagged(rb, 9, "2026-01-01T00:00:09.000Z", datapoint_id="dp-num", tags=["klima"])
        return rb

    legacy = await _make(tmp_path / "legacy", segmented=False)
    seg = await _make(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 6}]
        legacy_rows = await legacy.query_v2(metadata_tags_any_of=["klima"], value_filters=vf, datapoint_types=types, limit=20, is_export=True)
        seg_rows = await seg.query_v2(metadata_tags_any_of=["klima"], value_filters=vf, datapoint_types=types, limit=20, is_export=True)
        assert sorted(e.new_value for e in seg_rows) == [9]
        assert sorted(e.new_value for e in legacy_rows) == sorted(e.new_value for e in seg_rows)
    finally:
        await legacy.stop()
        await seg.stop()


# ===========================================================================
# Finding 2 (legacy exhaustiv): eine an einen SEGMENTIERTEN Store ATTACHED
# Legacy-DB, deren neueste 100 in-scope-Zeilen numerisch sind, deren ältere
# in-scope STRING-Zeile aber jenseits der neuesten 100 liegt → ``distinct_
# datapoint_ids`` MUSS sie erfassen (kein ``limit=100``-Miss), sonst droppt der
# Value-Filter-Export die Zeilen still statt 422 zu werfen.
# ===========================================================================


def _build_legacy_db(db: Path, *, old_string_rows: int, newer_numeric_rows: int) -> None:
    """Legacy-Single-DB: zuerst ``old_string_rows`` STRING-Zeilen, dann viele numerische.

    Die STRING-Zeilen bekommen die kleinsten rowids (id/desc → hinten); mit
    ``newer_numeric_rows > 100`` liegen sie jenseits der neuesten 100.
    """
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(_LEGACY_SCHEMA)
        for i in range(old_string_rows):
            conn.execute(
                "INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality) "
                "VALUES (?, 'dp-gone-str', 'dp/dp-gone-str/value', NULL, ?, 'ada', 'good')",
                (f"2025-01-01T00:00:{i:02d}.000Z", "hello"),
            )
        for j in range(newer_numeric_rows):
            conn.execute(
                "INSERT INTO ringbuffer (ts, datapoint_id, topic, old_value, new_value, source_adapter, quality) "
                "VALUES (?, 'dp-num', 'dp/dp-num/value', NULL, ?, 'ada', 'good')",
                (f"2026-01-01T00:{j // 60:02d}:{j % 60:02d}.000Z", str(j + 1)),
            )
        conn.commit()
    finally:
        conn.close()


async def _segmented_store_with_legacy(tmp_path: Path, db: Path) -> SqliteSegmentStore:
    """Segmentierter Store mit einer read-only ATTACHED Legacy-DB (kein Migrations-Lauf)."""
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    await store.manifest.register_legacy_segment(source_path=str(db), size_bytes=db.stat().st_size)
    return store


@pytest.mark.asyncio
async def test_distinct_ids_legacy_discovery_not_limited_to_100(tmp_path: Path):
    """``distinct_datapoint_ids`` liefert für die attached Legacy-DB auch den alten Datapoint.

    Der frühere ``replace(query, value_filters=[], is_export=True)`` erbte den
    ``StoreQuery``-Default ``limit=100`` → die STRING-Zeilen jenseits der neuesten
    100 fielen aus der Discovery. Erschöpfende Discovery erfasst sie.
    """
    db = tmp_path / "legacy.db"
    _build_legacy_db(db, old_string_rows=1, newer_numeric_rows=120)
    store = await _segmented_store_with_legacy(tmp_path, db)
    try:
        ids = await store.distinct_datapoint_ids(StoreQuery(value_filters=[]))
        assert "dp-gone-str" in ids
        assert "dp-num" in ids
    finally:
        await store.close()


# ===========================================================================
# Deckungs-/Parität-Fälle für die getrennten Discovery-Zweige: windowed ``q``
# (inline ``LIKE``), windowed Metadaten (inline ``EXISTS``), ``q`` mit sargable
# id/adapter-Scope (Summary-Scope-Filter) und ``q``+Metadaten (capped).
# ===========================================================================


@pytest.mark.asyncio
async def test_windowed_q_inline_string_rejects_like_legacy(tmp_path: Path):
    """WINDOWED ``q`` + ``gt`` über eine STRING-Zeile im Fenster → 422 (inline-``LIKE``-Pfad)."""
    types = {"dp-num-hot": "FLOAT", "dp-str-hot": "STRING"}

    async def _make(tmp: Path, *, segmented: bool) -> RingBuffer:
        rb = _rb(tmp, segmented=segmented)
        await rb.start()
        await _record(rb, "state", "2026-01-01T00:01:00.000Z", datapoint_id="dp-str-hot")
        await _record(rb, 5, "2026-01-01T00:01:05.000Z", datapoint_id="dp-num-hot")
        return rb

    legacy = await _make(tmp_path / "legacy", segmented=False)
    seg = await _make(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 1}]
        kwargs = dict(
            q="hot",
            value_filters=vf,
            datapoint_types=types,
            from_ts="2026-01-01T00:00:30.000Z",
            to_ts="2026-01-01T00:02:00.000Z",
            limit=10,
            is_export=True,
        )
        with pytest.raises(ValueError):
            await legacy.query_v2(**kwargs)
        with pytest.raises(ValueError):
            await seg.query_v2(**kwargs)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_windowed_metadata_inline_string_rejects_like_legacy(tmp_path: Path):
    """WINDOWED Metadaten-Tag + ``gt`` über eine getaggte STRING-Zeile im Fenster → 422 (inline-``EXISTS``)."""
    types = {"dp-num": "FLOAT", "dp-str": "STRING"}

    async def _make(tmp: Path, *, segmented: bool) -> RingBuffer:
        rb = _rb(tmp, segmented=segmented)
        await rb.start()
        await _record_tagged(rb, "on", "2026-01-01T00:01:00.000Z", datapoint_id="dp-str", tags=["klima"])
        await _record_tagged(rb, 5, "2026-01-01T00:01:05.000Z", datapoint_id="dp-num", tags=["klima"])
        return rb

    legacy = await _make(tmp_path / "legacy", segmented=False)
    seg = await _make(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 1}]
        kwargs = dict(
            metadata_tags_any_of=["klima"],
            value_filters=vf,
            datapoint_types=types,
            from_ts="2026-01-01T00:00:30.000Z",
            to_ts="2026-01-01T00:02:00.000Z",
            limit=10,
            is_export=True,
        )
        with pytest.raises(ValueError):
            await legacy.query_v2(**kwargs)
        with pytest.raises(ValueError):
            await seg.query_v2(**kwargs)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_q_with_adapter_scope_summary_filter(tmp_path: Path):
    """``q`` + ``adapter_any_of``-Scope: die Summary honoriert den Adapter-Scope (Parität).

    Ein STRING-``q``-Treffer AUSSERHALB des Adapter-Scopes darf KEIN 422 erzwingen;
    ein STRING-``q``-Treffer INNERHALB des Scopes schon.
    """
    types = {"dp-num-hot": "FLOAT", "dp-str-hot": "STRING"}

    async def _make(tmp: Path, *, segmented: bool) -> RingBuffer:
        rb = _rb(tmp, segmented=segmented)
        await rb.start()
        # STRING-q-Treffer, aber auf einem ANDEREN Adapter (out of scope).
        await _record(rb, "x", "2026-01-01T00:00:00.000Z", datapoint_id="dp-str-hot", adapter="other")
        await _record(rb, 5, "2026-01-01T00:00:05.000Z", datapoint_id="dp-num-hot", adapter="scope")
        return rb

    legacy = await _make(tmp_path / "legacy", segmented=False)
    seg = await _make(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 1}]
        # Scope = 'scope': der STRING-Treffer auf 'other' ist ausgeschlossen → kein 422.
        legacy_rows = await legacy.query_v2(q="hot", adapter_any_of=["scope"], value_filters=vf, datapoint_types=types, limit=10, is_export=True)
        seg_rows = await seg.query_v2(q="hot", adapter_any_of=["scope"], value_filters=vf, datapoint_types=types, limit=10, is_export=True)
        assert sorted(e.new_value for e in seg_rows) == [5]
        assert sorted(e.new_value for e in legacy_rows) == sorted(e.new_value for e in seg_rows)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_q_and_metadata_combined_capped_rejects_like_legacy(tmp_path: Path):
    """``q`` UND Metadaten-Tag (unwindowed) → capped Discovery mit beiden Prädikaten → 422 wie Legacy."""
    types = {"dp-num-hot": "FLOAT", "dp-str-hot": "STRING"}

    async def _make(tmp: Path, *, segmented: bool) -> RingBuffer:
        rb = _rb(tmp, segmented=segmented)
        await rb.start()
        await _record_tagged(rb, "on", "2026-01-01T00:00:00.000Z", datapoint_id="dp-str-hot", tags=["klima"])
        await _record_tagged(rb, 5, "2026-01-01T00:00:05.000Z", datapoint_id="dp-num-hot", tags=["klima"])
        return rb

    legacy = await _make(tmp_path / "legacy", segmented=False)
    seg = await _make(tmp_path / "seg", segmented=True)
    try:
        vf = [{"operator": "gt", "value": 1}]
        kwargs = dict(q="hot", metadata_tags_any_of=["klima"], value_filters=vf, datapoint_types=types, limit=10, is_export=True)
        with pytest.raises(ValueError):
            await legacy.query_v2(**kwargs)
        with pytest.raises(ValueError):
            await seg.query_v2(**kwargs)
    finally:
        await legacy.stop()
        await seg.stop()


@pytest.mark.asyncio
async def test_q_summary_dp_ids_by_name_and_singular_scope(tmp_path: Path):
    """Direkt am Store: ``q``-Summary honoriert ``dp_ids_by_name`` und den singulären id/adapter-Scope."""
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        await store.append(
            [
                _seg_event(1, "2026-01-01T00:00:00.000Z", datapoint_id="dp-name", adapter="a1"),
                _seg_event(2, "2026-01-01T00:00:01.000Z", datapoint_id="dp-hot", adapter="a1"),
                _seg_event(3, "2026-01-01T00:00:02.000Z", datapoint_id="dp-cold", adapter="a2"),
            ]
        )
        # ``dp_ids_by_name`` trifft dp-name (kein q-Substring); q='hot' trifft dp-hot.
        ids = await store.distinct_datapoint_ids(StoreQuery(value_filters=[], q="hot", dp_ids_by_name=["dp-name"]))
        assert ids == {"dp-name", "dp-hot"}
        # Singulärer datapoint_id-Scope schränkt die Summary ein.
        ids2 = await store.distinct_datapoint_ids(StoreQuery(value_filters=[], q="", dp_ids_by_name=["dp-name", "dp-hot"], datapoint_id="dp-hot"))
        assert ids2 == {"dp-hot"}
        # Singulärer source_adapter-Scope: q='cold' trifft dp-cold nur auf a2.
        ids3 = await store.distinct_datapoint_ids(StoreQuery(value_filters=[], q="cold", source_adapter="a2"))
        assert ids3 == {"dp-cold"}
        assert await store.distinct_datapoint_ids(StoreQuery(value_filters=[], q="cold", source_adapter="a1")) == set()
        # datapoint_ids-Liste + adapter-Liste als Summary-Scope.
        ids4 = await store.distinct_datapoint_ids(
            StoreQuery(value_filters=[], q="", dp_ids_by_name=["dp-hot", "dp-cold"], datapoint_ids=["dp-hot"], source_adapters=["a1"])
        )
        assert ids4 == {"dp-hot"}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_legacy_q_discovery_exhaustive(tmp_path: Path):
    """Legacy row-level ``q``-Discovery: ein alter STRING-``q``-Treffer jenseits der 100 wird erfasst.

    Deckt den row-level Legacy-Discovery-Zweig (``q``) mit erschöpfendem ``limit`` ab.
    """
    db = tmp_path / "legacy.db"
    _build_legacy_db(db, old_string_rows=1, newer_numeric_rows=120)
    store = await _segmented_store_with_legacy(tmp_path, db)
    try:
        # ``q='dp-'`` matcht beide Datapoint-IDs; die alte STRING-Zeile MUSS erscheinen.
        ids = await store.distinct_datapoint_ids(StoreQuery(value_filters=[], q="gone"))
        assert ids == {"dp-gone-str"}
    finally:
        await store.close()


def _seg_event(value: object, ts: str, *, datapoint_id: str, adapter: str = "ada") -> StoreEvent:
    return StoreEvent(
        ts=ts,
        datapoint_id=datapoint_id,
        topic=f"dp/{datapoint_id}/value",
        old_value=None,
        new_value=value,
        source_adapter=adapter,
        quality="good",
    )


@pytest.mark.asyncio
async def test_q_summary_cached_for_closed_segment(tmp_path: Path):
    """Die ``(datapoint_id, source_adapter)``-Summary eines GESCHLOSSENEN Segments wird gecacht.

    Zwei Segmente: ein geschlossenes (nach ``rotate``) mit dem ``q``-Treffer, ein
    aktives. Ein zweiter Discovery-Aufruf darf die Pair-Summary des geschlossenen
    Segments NICHT erneut aus SQLite berechnen (Cache-Hit) – nur das aktive Segment
    wird frisch gescannt. Nachweis: die Pair-Summary-SQL läuft beim zweiten Aufruf
    höchstens einmal (aktives Segment), nicht zweimal.
    """
    store = SqliteSegmentStore(tmp_path / "root")
    await store.open()
    try:
        # Geschlossenes Segment: der q='hot'-Treffer liegt hier.
        await store.append([_seg_event(1, "2026-01-01T00:00:00.000Z", datapoint_id="dp-hot")])
        await store.rotate()
        # Aktives Segment.
        await store.append([_seg_event(2, "2026-01-01T00:01:00.000Z", datapoint_id="dp-other")])

        pair_sql_calls = {"n": 0}
        orig = store._segment_dp_adapter_pairs

        async def _counting(conn, segment):
            # Zählt nur echte Neuberechnungen: der Cache-Hit ruft die SQL nicht.
            before = store._segment_dp_adapter_summary.get(segment.segment_id)
            result = await orig(conn, segment)
            if before is None:
                pair_sql_calls["n"] += 1
            return result

        store._segment_dp_adapter_pairs = _counting  # type: ignore[method-assign]
        q = StoreQuery(q="hot", value_filters=[])
        first = await store.distinct_datapoint_ids(q)
        second = await store.distinct_datapoint_ids(q)
        assert "dp-hot" in first and "dp-hot" in second
        # Erster Aufruf: geschlossenes + aktives Segment neu berechnet (2). Zweiter
        # Aufruf: geschlossenes aus Cache, nur das aktive neu (also insgesamt 3, NICHT 4).
        assert pair_sql_calls["n"] == 3
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_distinct_ids_legacy_numeric_only(tmp_path: Path):
    """Gegentest: rein numerische attached Legacy-DB → nur der numerische Datapoint."""
    db = tmp_path / "legacy.db"
    _build_legacy_db(db, old_string_rows=0, newer_numeric_rows=120)
    store = await _segmented_store_with_legacy(tmp_path, db)
    try:
        ids = await store.distinct_datapoint_ids(StoreQuery(value_filters=[]))
        assert ids == {"dp-num"}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_distinct_ids_legacy_sargable_scope(tmp_path: Path):
    """Legacy-Discovery mit sargable Scope (source_adapter/datapoint_ids/quality/Zeitfenster).

    Deckt den direkten ``SELECT DISTINCT datapoint_id``-Legacy-Zweig mit sargable
    WHERE ab: der alte STRING-Datapoint bleibt erfasst (exhaustiv), der Scope grenzt
    korrekt ein.
    """
    db = tmp_path / "legacy.db"
    _build_legacy_db(db, old_string_rows=1, newer_numeric_rows=120)
    store = await _segmented_store_with_legacy(tmp_path, db)
    try:
        # source_adapter = 'ada' (beide DPs) + quality good + Zeitfenster ab Anfang.
        q = StoreQuery(
            value_filters=[],
            source_adapters=["ada"],
            datapoint_ids=["dp-gone-str", "dp-num"],
            quality="good",
            from_ts="2025-01-01T00:00:00.000Z",
        )
        ids = await store.distinct_datapoint_ids(q)
        assert ids == {"dp-gone-str", "dp-num"}
        # Enger Adapter-Scope, den keine Zeile hat → leer.
        assert await store.distinct_datapoint_ids(StoreQuery(value_filters=[], source_adapter="nope")) == set()
    finally:
        await store.close()
