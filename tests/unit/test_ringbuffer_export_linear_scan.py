"""Row-lazy CSV-Export: LINEARER Roh-Scan statt O(n²) (#951, Codex-Finding [P2] ringbuffer.py:1654).

Follow-up auf den Runde-43-Export-Batch-Scan (``test_ringbuffer_rowlazy_export_batch.py``).
Dort scannte JEDER Export-Chunk seinen row-lazy Batch-Scan wieder ab Store-``offset`` 0
und verwarf ``matched[:offset]``. Fuer einen spaerlichen, nicht-pushbaren Value-Filter
ueber einen grossen Segment-Store re-las und re-filterte damit jeder spaetere Chunk ALLE
vorherigen Rohzeilen – der Gesamt-Export wurde quadratisch (100k-Zeilen-Export → wiederholte
Full-Scans, reisst die 3s-/20s-Timeouts).

Fix: der Export-Endpunkt haelt EINEN ``RowLazyExportCursor`` ueber alle Chunks; der
segmentierte Reader nimmt den Roh-Scan bei ``cursor.store_offset`` wieder auf und filtert
jede Rohzeile GENAU EINMAL ueber den gesamten Export → lineare Gesamtarbeit.

Diese Suite pinnt:

* LINEARITAET: der Cursor-Pfad liest jede Rohzeile genau einmal; die Gesamtzahl der
  Store-Reads waechst NICHT quadratisch mit der Chunk-Zahl (Zaehler-Nachweis).
* VOLLSTAENDIGKEIT/Paritaet: alle matchenden Zeilen werden geliefert, exakt die
  ``segmented=False``-Menge.
* Gegentest MONITOR (nicht-Export): unveraendert EIN gedeckelter Store-Read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import obs.ringbuffer.ringbuffer as rbmod
from obs.ringbuffer.ringbuffer import RingBuffer, RowLazyExportCursor


def _rb(tmp_path: Path, *, segmented: bool, **kwargs) -> RingBuffer:
    return RingBuffer(
        storage="file",
        disk_path=str(tmp_path / "obs_ringbuffer.db"),
        segmented=segmented,
        **kwargs,
    )


async def _record(rb: RingBuffer, value: object, ts: str, *, datapoint_id: str = "dp-num", adapter: str = "api") -> None:
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


async def _seed_sparse(rb: RingBuffer, *, total: int, match_every: int) -> int:
    """``total`` Zeilen; jede ``match_every``-te matcht (Wert 100), sonst Wert 0.

    Gibt die Anzahl der matchenden Zeilen zurueck.
    """
    matches = 0
    for i in range(total):
        value = 100 if i % match_every == 0 else 0
        if value == 100:
            matches += 1
        await _record(rb, value, f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}.000Z")
    return matches


class _CountingStore:
    """Delegiert an den echten Store und zaehlt Reads + gelesene Rohzeilen."""

    def __init__(self, real):
        self._real = real
        self.query_calls = 0
        self.rows_read = 0

    async def query(self, store_query):
        self.query_calls += 1
        rows = await self._real.query(store_query)
        self.rows_read += len(rows)
        return rows

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.mark.asyncio
async def test_rowlazy_export_cursor_reads_each_row_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """LINEARITAET: der Cursor-Pfad liest jede Rohzeile genau einmal ueber alle Chunks.

    Kleiner Batch (5), 40 Rohzeilen, jede 8. matcht (5 Treffer). Der Export paginiert
    chunk-weise (``limit`` = 2 pro Chunk). Ohne den Cursor scannte Chunk N erneut ab 0
    und die kumulierten ``rows_read`` waeren quadratisch (Summe der Prefixes). Mit dem
    Cursor bleibt ``rows_read`` gebunden durch die Gesamtzahl der Rohzeilen (+ ein
    kurzer Abschluss-Batch), also LINEAR.
    """
    monkeypatch.setattr(rbmod, "_SEGMENTED_CANDIDATE_CAP", 5)

    total = 40
    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        n_matches = await _seed_sparse(rb, total=total, match_every=8)
        assert n_matches == 5

        counting = _CountingStore(rb._store)
        rb._store = counting

        cursor = RowLazyExportCursor()
        collected: list = []
        # Endpunkt-artige Chunk-Schleife: wachsender ``offset``, EIN geteilter Cursor.
        offset = 0
        chunk_limit = 2
        for _ in range(100):  # harte Obergrenze gegen einen Test-Endlosloop
            chunk = await rb.query_v2(
                value_filters=[{"operator": "gte", "value": 50}],
                limit=chunk_limit,
                offset=offset,
                candidate_cap_override=offset + chunk_limit,
                is_export=True,
                export_store_cursor=cursor,
            )
            if not chunk:
                break
            collected.extend(chunk)
            offset += len(chunk)
            if len(chunk) < chunk_limit:
                break

        # Vollstaendigkeit: alle 5 Treffer.
        assert [e.new_value for e in collected] == [100, 100, 100, 100, 100]

        # Linearitaet: jede der 40 Rohzeilen genau einmal gelesen. Der Scan endet, sobald
        # der letzte (kurze) Batch das Scope-Ende meldet; das sind hoechstens
        # ``total + batch_size`` Rohzeilen (kein quadratischer Rescan). Ein quadratischer
        # Pfad laege deutlich darueber (Summe wachsender Prefix-Reads).
        assert counting.rows_read <= total + 5
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_rowlazy_export_cursor_no_quadratic_growth_with_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Die Store-Reads wachsen NICHT quadratisch mit der Chunk-Zahl.

    Vergleich zweier Laeufe mit gleicher Datenmenge, aber unterschiedlich vielen Chunks
    (kleines vs. grosses Chunk-``limit``). Bei linearem Scan sind die kumulierten
    Rohzeilen-Reads in beiden Laeufen praktisch gleich (jede Zeile einmal). Bei einem
    quadratischen Rescan waere der Lauf mit VIELEN kleinen Chunks drastisch teurer.
    """
    monkeypatch.setattr(rbmod, "_SEGMENTED_CANDIDATE_CAP", 5)
    total = 60

    async def _run(chunk_limit: int) -> int:
        sub = tmp_path / f"chunk{chunk_limit}"
        rb = _rb(sub, segmented=True)
        await rb.start()
        try:
            await _seed_sparse(rb, total=total, match_every=6)
            counting = _CountingStore(rb._store)
            rb._store = counting
            cursor = RowLazyExportCursor()
            offset = 0
            for _ in range(500):
                chunk = await rb.query_v2(
                    value_filters=[{"operator": "gte", "value": 50}],
                    limit=chunk_limit,
                    offset=offset,
                    candidate_cap_override=offset + chunk_limit,
                    is_export=True,
                    export_store_cursor=cursor,
                )
                if not chunk:
                    break
                offset += len(chunk)
                if len(chunk) < chunk_limit:
                    break
            return counting.rows_read
        finally:
            await rb.stop()

    few_chunks_reads = await _run(chunk_limit=10)
    many_chunks_reads = await _run(chunk_limit=1)

    # Linear: beide lesen jede Rohzeile ~einmal → praktisch identisch, unabhaengig von der
    # Chunk-Zahl. Quadratisch waere ``many_chunks_reads`` ein Vielfaches.
    assert many_chunks_reads <= few_chunks_reads + 5


@pytest.mark.asyncio
async def test_rowlazy_export_cursor_matches_legacy_full_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Paritaet: der gechunkte Cursor-Export liefert exakt die ``segmented=False``-Menge."""
    monkeypatch.setattr(rbmod, "_SEGMENTED_CANDIDATE_CAP", 5)
    total = 50

    legacy = _rb(tmp_path / "legacy", segmented=False)
    await legacy.start()
    try:
        await _seed_sparse(legacy, total=total, match_every=7)
        legacy_entries = await legacy.query_v2(
            value_filters=[{"operator": "gte", "value": 50}],
            limit=1000,
            offset=0,
            is_export=True,
        )
    finally:
        await legacy.stop()

    seg = _rb(tmp_path / "seg", segmented=True)
    await seg.start()
    try:
        await _seed_sparse(seg, total=total, match_every=7)
        cursor = RowLazyExportCursor()
        collected: list = []
        offset = 0
        chunk_limit = 2
        for _ in range(1000):
            chunk = await seg.query_v2(
                value_filters=[{"operator": "gte", "value": 50}],
                limit=chunk_limit,
                offset=offset,
                candidate_cap_override=offset + chunk_limit,
                is_export=True,
                export_store_cursor=cursor,
            )
            if not chunk:
                break
            collected.extend(chunk)
            offset += len(chunk)
            if len(chunk) < chunk_limit:
                break
    finally:
        await seg.stop()

    assert [e.ts for e in collected] == [e.ts for e in legacy_entries]
    assert [e.new_value for e in collected] == [e.new_value for e in legacy_entries]
    assert len(collected) == len(legacy_entries) > 0


@pytest.mark.asyncio
async def test_monitor_rowlazy_stays_single_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Gegentest: der Monitor-Live-View (nicht-Export) bleibt EIN gedeckelter Store-Read."""
    monkeypatch.setattr(rbmod, "_SEGMENTED_CANDIDATE_CAP", 5)

    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        await _seed_sparse(rb, total=40, match_every=8)
        counting = _CountingStore(rb._store)
        rb._store = counting

        await rb.query_v2(
            value_filters=[{"operator": "gte", "value": 50}],
            limit=100,
            offset=0,
            is_export=False,
        )
        assert counting.query_calls == 1
    finally:
        await rb.stop()
