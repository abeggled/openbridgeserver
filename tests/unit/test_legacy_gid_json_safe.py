"""Synthetische Legacy-global_event_ids JSON-/JS-sicher halten (#951, Runde 23).

Codex-Finding „Keep legacy entry IDs JSON-safe": die synthetischen Legacy-IDs lagen um
``-(1<<62)`` (Read-Pfad ``_legacy_row_to_dict``).
Über die JSON-API exponiert überschreiten sie den JS-sicheren Integer-Bereich
(``±(2**53-1)``); Browser parsen JSON-Zahlen als IEEE-754-Doubles, sodass benachbarte
Legacy-rowids auf denselben Double kollabieren und jeder JS-Consumer, der per ``id``
keyed/dedupliziert, bricht.

Das gid-Schema wird so umskaliert, dass alle exponierten synthetischen IDs innerhalb
``[-(2**53-1), 0)`` liegen und die Invarianten erhalten bleiben: (a) strikt negativ und
strikt kleiner als jede positive v2-gid; (b) pro Quelle/Segment disjunkte, kollisions-
freie Buckets; (c) eindeutig pro Zeile; (d) mehr-negativ = älter bleibt monoton.

TDD-first: diese Tests binden die JS-Sicherheit direkt an das Konstanten-Schema und
schlagen mit den alten ``1<<62``/``1<<40``-Werten fehl (Werte außerhalb ``±(2**53-1)``,
zwei benachbarte rowids kollabieren als float auf denselben Wert).
"""

from __future__ import annotations

from obs.ringbuffer.store.sqlite_backend import (
    _LEGACY_GID_OFFSET,
    _LEGACY_GID_STRIDE,
    _LEGACY_SOURCE_BUCKETS,
)

JS_MAX = 2**53 - 1  # größter exakt als IEEE-754-Double darstellbarer Integer


def _read_path_gid(rowid: int, segment_id: int) -> int:
    """Repliziert die Read-Pfad-Formel aus ``_legacy_row_to_dict`` (sqlite_backend.py).

    Cross-Source-Ordnung (#951, Codex :1558): ``segment_id`` wird an der Bucket-Schranke
    gespiegelt, damit die neuere Quelle (höhere segment_id) weniger negativ = ``id desc``
    zuerst liegt.
    """
    source_factor = _LEGACY_SOURCE_BUCKETS - 1 - (segment_id % _LEGACY_SOURCE_BUCKETS)
    return rowid - _LEGACY_GID_OFFSET - source_factor * _LEGACY_GID_STRIDE


def test_worst_case_read_path_gid_is_js_safe():
    """Auch der Worst-Case-Read-Pfad-gid bleibt in ``[-(2**53-1), 0)``.

    Worst Case = kleinste rowid (1) im höchsten dokumentierten Segment-/Bucket-Index.
    Die dokumentierte Kapazitätsgrenze ist ``segment_id < _LEGACY_SOURCE_BUCKETS``
    (= ``1<<20``); an dieser Grenze muss der Betrag noch ``<= JS_MAX`` sein.
    """
    # Cross-Source-Spiegelung (#951, Codex :1558): der Betrag ist maximal beim NIEDRIGSTEN
    # segment_id (Faktor ``B-1``), nicht mehr beim höchsten. Worst Case = rowid 1, seg 0.
    worst = _read_path_gid(rowid=1, segment_id=0)
    assert -JS_MAX <= worst < 0
    assert abs(worst) <= JS_MAX
    # Auch der höchste dokumentierte Index (strikt < cap) bleibt sicher (weniger negativ).
    top = _read_path_gid(rowid=1, segment_id=_LEGACY_SOURCE_BUCKETS - 1)
    assert -JS_MAX <= top < 0


def test_read_path_gids_strictly_negative_and_below_positive():
    """Alle synthetischen Legacy-gids sind strikt negativ (unter jeder positiven v2-gid)."""
    for rowid in (1, 2, 1000, _LEGACY_GID_STRIDE - 1):
        for segment_id in (0, 1, 5, 100):
            assert _read_path_gid(rowid, segment_id) < 0


def test_read_path_rowid_monotone_within_segment():
    """Innerhalb eines Segments: höhere rowid (neuer) ⇒ höhere (weniger negative) gid."""
    seg = 3
    prev = None
    for rowid in range(1, 50):
        gid = _read_path_gid(rowid, seg)
        if prev is not None:
            assert gid > prev
        prev = gid


def test_read_path_higher_segment_id_sorts_newer():
    """Höhere segment_id ⇒ weniger negativer Block – neuere Legacy-Quelle zuerst (id desc).

    Cross-Source-Ordnung (#951, Codex :1558): eine später registrierte Legacy-Quelle
    (höhere segment_id) ist die NEUERE und muss im ``id desc`` VOR der älteren liegen,
    also weniger negativ. Der gesamte Block der höheren segment_id liegt ÜBER dem der
    niedrigeren (jeder rowid der neueren Quelle > jeder rowid der älteren).
    """
    older_max = _read_path_gid(rowid=_LEGACY_GID_STRIDE - 1, segment_id=1)
    newer_min = _read_path_gid(rowid=1, segment_id=2)
    assert newer_min > older_max


def test_read_path_buckets_disjoint_no_collision():
    """Zwei Segmente kollidieren nie auf denselben synthetischen gid.

    rowid r der einen und rowid r+1 der nächsten dürfen NICHT dieselbe ID erzeugen.
    Da rowids strikt < STRIDE bleiben, sind die Blöcke disjunkt.
    """
    gids: set[int] = set()
    for segment_id in range(0, 6):
        for rowid in range(1, 200):
            gid = _read_path_gid(rowid, segment_id)
            assert gid not in gids, f"Kollision bei seg={segment_id} rowid={rowid}"
            gids.add(gid)


def test_adjacent_rowids_distinct_as_float64():
    """Benachbarte Legacy-rowids kollabieren als IEEE-754-Double NICHT auf denselben Wert.

    Genau der Bug: bei ``|gid| > 2**53`` rundet ``float(gid)`` benachbarte gids auf
    denselben Double, sodass ein JS-Consumer sie nicht mehr unterscheiden kann. Im
    JS-sicheren Band bleibt ``float(gid_a) != float(gid_b)``.
    """
    for segment_id in (0, 7, _LEGACY_SOURCE_BUCKETS - 1):
        a = _read_path_gid(rowid=1, segment_id=segment_id)
        b = _read_path_gid(rowid=2, segment_id=segment_id)
        assert a != b
        assert float(a) != float(b), f"float-Kollision bei seg={segment_id}"


def test_offset_and_stride_documented_capacity():
    """Die gewählten Konstanten halten die dokumentierte Kapazitätsgrenze unter 2**53."""
    assert _LEGACY_GID_OFFSET + (_LEGACY_SOURCE_BUCKETS - 1) * _LEGACY_GID_STRIDE < 2**53
    assert _LEGACY_GID_OFFSET == 1 << 52
    assert _LEGACY_GID_STRIDE == 1 << 32
