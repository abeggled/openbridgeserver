"""Row-lazy EXPORT batch-scan (#951, Codex-Finding [P2] ringbuffer.py:1583).

Kern des Findings: kann ein Value-Filter NICHT als typisierter SQL-Pushdown laufen
(Scope-Verbreiterung über ``q``/adapter/name/metadata oder unbekannter Typ), wertet
der segmentierte Pfad ihn row-lazy über ``_apply_value_filters`` aus. Der bisherige
row-lazy Pfad holte dazu nur EINE gedeckelte Rohmenge (``effective_cap`` bzw. im
Export der mitwachsende ``candidate_cap_override``) am Store-``offset`` 0 und filterte
DANACH. Im CSV-/Export-Modus stoppt die Export-Schleife damit vorzeitig, sobald ein
Cap-Fenster aus den NEUESTEN Rohzeilen den Filter nicht matcht – ältere, sehr wohl
matchende Zeilen jenseits des Fensters werden nie gelesen (leere/abgeschnittene Seite
→ Export-Stopp). Der Legacy-Pfad (``segmented=False``) scannt dagegen batchweise
weiter, bis genug GEMATCHTE Zeilen vorliegen.

Diese Suite pinnt die Fix-Semantik:

* EXPORT row-lazy: NEUESTE Rohzeilen matchen NICHT, ÄLTERE (jenseits des ersten
  Cap-Fensters) schon → alle matchenden Zeilen werden geliefert, Parität zur
  ``segmented=False``-Export-Menge (kein vorzeitiger Stopp).
* MONITOR (nicht-Export): bleibt beim EINMALIGEN gedeckelten Fetch (kein Voll-Scan).
* Typkonflikt innerhalb des Scopes propagiert weiterhin als ``ValueError`` (422).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import obs.ringbuffer.ringbuffer as rbmod
from obs.ringbuffer.ringbuffer import RingBuffer


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


async def _seed_newest_nonmatching_oldest_matching(rb: RingBuffer) -> None:
    """10 Zeilen: die 6 NEUESTEN matchen NICHT (Wert 0), die 4 ÄLTESTEN matchen (Wert 100).

    Newest-first (``sort_order=desc``): der Store liefert zuerst die 6 Nuller. Ein
    row-lazy Export, der nur das erste Cap-Fenster aus den neuesten Rohzeilen filtert,
    sähe damit ausschließlich Nicht-Treffer und würde vorzeitig stoppen.
    """
    # Älteste zuerst schreiben → höhere ts = neuer.
    for i in range(4):
        await _record(rb, 100, f"2026-01-01T00:00:0{i}.000Z")
    for i in range(4, 10):
        await _record(rb, 0, f"2026-01-01T00:00:{i:02d}.000Z")


@pytest.mark.asyncio
async def test_rowlazy_export_batch_scans_past_first_cap_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """EXPORT row-lazy: alle matchenden Zeilen kommen, obwohl die neuesten Rohzeilen nicht matchen.

    Kleiner Batch-/Cap-Wert (3), damit die 4 Treffer garantiert JENSEITS des ersten
    Fensters liegen. Ohne den Batch-Scan lieferte der erste gedeckelte Fetch (3 neueste
    = Wert 0) eine leere gefilterte Seite → Export-Stopp. Mit dem Fix akkumuliert der
    Export batchweise, bis der Scope erschöpft ist, und liefert alle 4 Treffer.
    """
    monkeypatch.setattr(rbmod, "_SEGMENTED_CANDIDATE_CAP", 3)

    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        await _seed_newest_nonmatching_oldest_matching(rb)

        # Value-only Filter ohne datapoint_ids → NICHT pushbar → row-lazy.
        # Der Export-Cap ``candidate_cap_override`` = offset+limit ist bewusst KLEINER
        # als die 6 führenden Nicht-Treffer: ein roher Cap bei offset+limit läse nur
        # Nuller und stoppte vorzeitig. Der Batch-Scan muss trotzdem alle 4 Treffer
        # jenseits des Fensters einsammeln.
        entries = await rb.query_v2(
            value_filters=[{"operator": "gte", "value": 50}],
            limit=4,
            offset=0,
            candidate_cap_override=4,
            is_export=True,
        )
        assert [e.new_value for e in entries] == [100, 100, 100, 100]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_rowlazy_export_matches_legacy_segmented_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Parität: der segmentierte Export liefert exakt die ``segmented=False``-Menge."""
    monkeypatch.setattr(rbmod, "_SEGMENTED_CANDIDATE_CAP", 3)

    legacy = _rb(tmp_path / "legacy", segmented=False)
    await legacy.start()
    try:
        await _seed_newest_nonmatching_oldest_matching(legacy)
        legacy_entries = await legacy.query_v2(
            value_filters=[{"operator": "gte", "value": 50}],
            limit=100,
            offset=0,
            is_export=True,
        )
    finally:
        await legacy.stop()

    seg = _rb(tmp_path / "seg", segmented=True)
    await seg.start()
    try:
        await _seed_newest_nonmatching_oldest_matching(seg)
        seg_entries = await seg.query_v2(
            value_filters=[{"operator": "gte", "value": 50}],
            limit=4,
            offset=0,
            candidate_cap_override=4,
            is_export=True,
        )
    finally:
        await seg.stop()

    assert [e.new_value for e in seg_entries] == [e.new_value for e in legacy_entries]
    assert [e.ts for e in seg_entries] == [e.ts for e in legacy_entries]
    assert len(seg_entries) == 4


@pytest.mark.asyncio
async def test_rowlazy_export_paginates_across_batches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Der Export akkumuliert genug Treffer für ``offset+limit`` und paginiert danach korrekt."""
    monkeypatch.setattr(rbmod, "_SEGMENTED_CANDIDATE_CAP", 3)

    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        await _seed_newest_nonmatching_oldest_matching(rb)

        # Zweite Treffer-Seite (offset=2, limit=2) → die zwei ältesten Treffer.
        entries = await rb.query_v2(
            value_filters=[{"operator": "gte", "value": 50}],
            limit=2,
            offset=2,
            candidate_cap_override=4,
            is_export=True,
        )
        # newest-first: Treffer-Reihenfolge 100@ts3, 100@ts2, [100@ts1, 100@ts0].
        assert [e.new_value for e in entries] == [100, 100]
        assert [e.ts for e in entries] == [
            "2026-01-01T00:00:01.000Z",
            "2026-01-01T00:00:00.000Z",
        ]
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_monitor_rowlazy_stays_bounded_single_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """MONITOR (nicht-Export) bleibt beim EINMALIGEN gedeckelten Fetch – kein Voll-Scan.

    Gegentest zum Export: der Monitor-Live-View darf NICHT batchweise über den ganzen
    Scope scannen. Mit kleinem Cap (3) sieht er nur die 3 neuesten Rohzeilen; keine
    davon matcht → leere Seite (bounded), obwohl ältere Zeilen matchen würden.
    """
    monkeypatch.setattr(rbmod, "_SEGMENTED_CANDIDATE_CAP", 3)

    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        await _seed_newest_nonmatching_oldest_matching(rb)

        # Genau EIN Store-Read (bounded), kein Batch-Scan.
        real_query = rb.store.query
        calls = {"n": 0}

        async def counting_query(store_query):
            calls["n"] += 1
            return await real_query(store_query)

        rb.store.query = counting_query

        entries = await rb.query_v2(
            value_filters=[{"operator": "gte", "value": 50}],
            limit=100,
            offset=0,
            is_export=False,
        )
        # Bounded: der einmalige Fetch sah nur die 3 neuesten Nuller → keine Treffer.
        assert entries == []
        assert calls["n"] == 1
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_rowlazy_export_type_conflict_still_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ein Typkonflikt im Scope propagiert weiterhin als ``ValueError`` (422), auch im Export-Batch."""
    monkeypatch.setattr(rbmod, "_SEGMENTED_CANDIDATE_CAP", 3)

    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        await _record(rb, True, "2026-01-01T00:00:00.000Z", datapoint_id="dp-bool")
        with pytest.raises(ValueError, match="not supported for data_type 'BOOLEAN'"):
            await rb.query_v2(
                q="dp-bool",  # Scope-Verbreiterung → row-lazy
                value_filters=[{"operator": "gt", "value": 0}],
                datapoint_types={"dp-bool": "BOOLEAN"},
                limit=100,
                offset=0,
                candidate_cap_override=100,
                is_export=True,
            )
    finally:
        await rb.stop()
