"""Row-lazy EXPORT scannt bis zur echten Scope-Erschoepfung (#951, Codex :1647).

Follow-up auf den row-lazy Export-Batch-Scan (Runde 39/40, siehe
``test_ringbuffer_rowlazy_export_batch.py``). Der fruehere ``max_batches``-Backstop
in der Export-Batch-Schleife brach bei einem scoped row-lazy Export mit SEHR
SPAERLICHEN Matches vorzeitig ab: liegt der erste Match erst nach mehr VOLLEN
Batches als der Backstop erlaubte, verliess die Schleife die Iteration, obwohl JEDER
Batch die volle ``batch_size`` lieferte (Store also NICHT erschoepft). Die Methode
gab dann eine leere/kurze Seite zurueck, und ``/export/csv`` behandelte das als
End-of-Results → der Export trunkierte still (fehlende Zeilen).

Fix-Semantik, die diese Suite pinnt:

* Ein EXPORT muss VOLLSTAENDIG sein. Terminierung NUR bei (a) Scope erschoepft (ein
  Store-Batch liefert WENIGER als ``batch_size`` Rohzeilen) ODER (b) genug gematchte
  Zeilen (``len(matched) >= needed``). Solange die Batches VOLL sind (Store liefert
  weiter ``batch_size`` → nicht erschoepft), darf KEIN Deckel abbrechen – auch dann
  nicht, wenn der erste Treffer erst nach sehr vielen vollen Batches kommt.
* Der wachsende Store-``offset`` laesst die Schleife bei einem endlichen Store
  natuerlich ueber (a) enden (der letzte Batch ist kurz/leer). Ein tatsaechlich
  erschoepfter Store terminiert damit sauber (kein Endlosloop).
* Gegentest: der Monitor-Live-View (nicht-Export) bleibt beim EINMALIGEN gedeckelten
  Fetch – unveraendert bounded.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


def _synthetic_row(*, gid: int, ts: str, value: object) -> dict[str, Any]:
    """Eine Store-Rohzeile im vom Ringpuffer erwarteten Schluesselschema."""
    return {
        "global_event_id": gid,
        "ts": ts,
        "datapoint_id": "dp-num",
        "topic": "dp/dp-num/value",
        "old_value": None,
        "new_value": value,
        "source_adapter": "api",
        "quality": "good",
        "metadata_version": 1,
        "metadata": {},
    }


@pytest.mark.asyncio
async def test_export_scans_past_old_max_batches_backstop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Sparse-match EXPORT: erster Treffer erst NACH mehr vollen Batches als der alte Backstop.

    Der Store wird durch einen synthetischen Stream ersetzt, der eine feste, kleine
    ``batch_size`` (1) und einen sehr spaeten ersten Treffer simuliert: die ersten
    ``sparse_full_batches`` Batches liefern je genau ``batch_size`` NICHT-matchende
    Rohzeilen (Wert 0), erst DANACH kommt genau ein Treffer (Wert 100), gefolgt von
    einem kurzen (leeren) Batch als echtes Scope-Ende.

    ``sparse_full_batches`` liegt bewusst OBERHALB des alten
    ``max_batches = (needed // batch_size + 1) * 1000``-Deckels (needed=1, batch_size=1
    → 2000). Mit dem alten Backstop haette die Schleife nach 2000 vollen Batches
    abgebrochen und eine LEERE Seite geliefert (stille Trunkierung). Mit dem Fix
    scannt sie bis zum kurzen Batch weiter und liefert den Treffer.
    """
    monkeypatch.setattr(rbmod, "_SEGMENTED_CANDIDATE_CAP", 1)

    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        batch_size = 1
        # Alter Backstop-Wert fuer needed=1, batch_size=1: (1 // 1 + 1) * 1000 = 2000.
        old_max_batches = (1 // batch_size + 1) * 1000
        sparse_full_batches = old_max_batches + 5  # klar jenseits des alten Deckels

        calls = {"n": 0}

        async def sparse_store_query(store_query: Any) -> list[dict[str, Any]]:
            calls["n"] += 1
            store_offset = store_query.offset
            # Erste ``sparse_full_batches`` Batches: je genau ``batch_size`` Nuller (VOLL).
            if store_offset < sparse_full_batches * batch_size:
                return [_synthetic_row(gid=store_offset, ts=f"ts-{store_offset}", value=0)]
            # Genau EIN Treffer im naechsten (immer noch vollen) Batch.
            if store_offset == sparse_full_batches * batch_size:
                return [_synthetic_row(gid=store_offset, ts="ts-match", value=100)]
            # Danach echtes Scope-Ende: kurzer (leerer) Batch < batch_size.
            return []

        rb.store.query = sparse_store_query

        entries = await rb.query_v2(
            value_filters=[{"operator": "gte", "value": 50}],
            limit=1,
            offset=0,
            candidate_cap_override=1,
            is_export=True,
        )

        # Der eine Treffer wird geliefert – keine stille Trunkierung.
        assert [e.new_value for e in entries] == [100]
        assert [e.ts for e in entries] == ["ts-match"]
        # Beweis, dass ueber den alten Backstop hinaus gescannt wurde.
        assert calls["n"] > old_max_batches
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_export_parity_with_segmented_false_on_sparse_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Parität: sparse-match EXPORT liefert exakt die ``segmented=False``-Menge.

    Reale (endliche) DB mit vielen fuehrenden Nicht-Treffern und wenigen sehr alten
    Treffern. Bei winzigem Cap (1) sind viele volle Batches noetig; der segmentierte
    Export muss trotzdem exakt dieselbe Trefferliste wie der Legacy-Pfad liefern.
    """
    monkeypatch.setattr(rbmod, "_SEGMENTED_CANDIDATE_CAP", 1)

    async def _seed(rb: RingBuffer) -> None:
        # 3 aelteste Treffer (Wert 100), danach 12 neuere Nicht-Treffer (Wert 0).
        for i in range(3):
            await _record(rb, 100, f"2026-01-01T00:00:{i:02d}.000Z")
        for i in range(3, 15):
            await _record(rb, 0, f"2026-01-01T00:00:{i:02d}.000Z")

    legacy = _rb(tmp_path / "legacy", segmented=False)
    await legacy.start()
    try:
        await _seed(legacy)
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
        await _seed(seg)
        seg_entries = await seg.query_v2(
            value_filters=[{"operator": "gte", "value": 50}],
            limit=3,
            offset=0,
            candidate_cap_override=3,
            is_export=True,
        )
    finally:
        await seg.stop()

    assert [e.new_value for e in seg_entries] == [e.new_value for e in legacy_entries]
    assert [e.ts for e in seg_entries] == [e.ts for e in legacy_entries]
    assert len(seg_entries) == 3


@pytest.mark.asyncio
async def test_export_exhausted_store_terminates_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Ein tatsaechlich erschoepfter Store terminiert sauber (kein Endlosloop).

    Kein einziger Treffer im ganzen (endlichen) Scope: die Schleife muss ueber den
    kurzen Endbatch terminieren und eine leere Seite liefern, NICHT haengen.
    """
    monkeypatch.setattr(rbmod, "_SEGMENTED_CANDIDATE_CAP", 1)

    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        for i in range(5):
            await _record(rb, 0, f"2026-01-01T00:00:0{i}.000Z")

        entries = await rb.query_v2(
            value_filters=[{"operator": "gte", "value": 50}],
            limit=10,
            offset=0,
            candidate_cap_override=10,
            is_export=True,
        )
        assert entries == []
    finally:
        await rb.stop()


@pytest.mark.asyncio
async def test_monitor_nonexport_stays_single_bounded_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Gegentest: der Monitor (nicht-Export) bleibt beim EINMALIGEN gedeckelten Fetch.

    Der Voll-Scan gilt ausschliesslich fuer den Export. Der Monitor-Live-View darf
    NICHT batchweise ueber den ganzen Scope scannen: mit kleinem Cap (1) sieht er nur
    die neueste Rohzeile.
    """
    monkeypatch.setattr(rbmod, "_SEGMENTED_CANDIDATE_CAP", 1)

    rb = _rb(tmp_path, segmented=True)
    await rb.start()
    try:
        for i in range(3):
            await _record(rb, 100, f"2026-01-01T00:00:0{i}.000Z")
        for i in range(3, 6):
            await _record(rb, 0, f"2026-01-01T00:00:0{i}.000Z")

        real_query = rb.store.query
        calls = {"n": 0}

        async def counting_query(store_query: Any) -> list[dict[str, Any]]:
            calls["n"] += 1
            return await real_query(store_query)

        rb.store.query = counting_query

        await rb.query_v2(
            value_filters=[{"operator": "gte", "value": 50}],
            limit=100,
            offset=0,
            is_export=False,
        )
        # Bounded: genau EIN Store-Read, kein Batch-Scan.
        assert calls["n"] == 1
    finally:
        await rb.stop()
