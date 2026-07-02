"""Vertrag für die segmentierte RingBuffer-Konfiguration (#930).

Trennt Segment-Parameter (Rotation) von Retention-Zielen und validiert
zu grobe Segmentierung im Verhältnis zu aktivierten Retention-Limits.
Verletzungen werden abgelehnt (ValueError → HTTP 422), nie auto-korrigiert.
"""

from __future__ import annotations

import pytest

from obs.ringbuffer.store.config import (
    RETENTION_SEGMENT_RATIO,
    SegmentConfig,
    StoreRetentionConfig,
    validate_explicit_segment_bounds,
    validate_store_config,
)


def test_segment_config_defaults_are_disabled():
    cfg = SegmentConfig()
    assert cfg.segment_max_bytes is None
    assert cfg.segment_max_rows is None
    assert cfg.segment_max_age is None


def test_segment_config_rejects_non_positive_values():
    with pytest.raises(ValueError, match="segment_max_bytes"):
        SegmentConfig(segment_max_bytes=0)
    with pytest.raises(ValueError, match="segment_max_rows"):
        SegmentConfig(segment_max_rows=-1)
    with pytest.raises(ValueError, match="segment_max_age"):
        SegmentConfig(segment_max_age=0)


def test_retention_config_rejects_non_positive_values():
    with pytest.raises(ValueError, match="max_file_size_bytes"):
        StoreRetentionConfig(max_file_size_bytes=0)
    with pytest.raises(ValueError, match="max_entries"):
        StoreRetentionConfig(max_entries=0)
    with pytest.raises(ValueError, match="max_age"):
        StoreRetentionConfig(max_age=-5)


def test_validate_accepts_config_with_disabled_segmentation():
    # Ohne aktive Segmentierung gibt es nichts zu prüfen (Legacy-Pfad).
    retention = StoreRetentionConfig(max_file_size_bytes=100, max_entries=100, max_age=100)
    validate_store_config(SegmentConfig(), retention)


# In-Bounds-Basiswerte für explizite Segment-Eingaben (#919): technische Grenzen
# sind segment_max_bytes 4 MiB…1 GiB, segment_max_age 300 s…2.592.000 s,
# segment_max_rows >= 1000. Die 3-Segment-Regel wird mit diesen gültigen Werten
# getestet, damit nicht schon der Bounds-Check zuerst greift.
_SEG_BYTES = 4 * 1024 * 1024  # 4 MiB (Untergrenze)
_SEG_AGE = 300  # 5 min (Untergrenze)
_SEG_ROWS = 1000  # Untergrenze


def test_validate_accepts_config_at_exact_ratio_boundary():
    segments = SegmentConfig(segment_max_bytes=_SEG_BYTES, segment_max_rows=_SEG_ROWS, segment_max_age=_SEG_AGE)
    retention = StoreRetentionConfig(
        max_file_size_bytes=RETENTION_SEGMENT_RATIO * _SEG_BYTES,
        max_entries=RETENTION_SEGMENT_RATIO * _SEG_ROWS,
        max_age=RETENTION_SEGMENT_RATIO * _SEG_AGE,
    )
    validate_store_config(segments, retention)


def test_validate_rejects_size_budget_smaller_than_three_segments():
    segments = SegmentConfig(segment_max_bytes=_SEG_BYTES)
    retention = StoreRetentionConfig(max_file_size_bytes=3 * _SEG_BYTES - 1)
    with pytest.raises(ValueError, match="max_file_size_bytes"):
        validate_store_config(segments, retention)


def test_validate_rejects_entries_budget_smaller_than_three_segments():
    segments = SegmentConfig(segment_max_rows=_SEG_ROWS)
    retention = StoreRetentionConfig(max_entries=3 * _SEG_ROWS - 1)
    with pytest.raises(ValueError, match="max_entries"):
        validate_store_config(segments, retention)


def test_validate_rejects_age_budget_smaller_than_three_segments():
    segments = SegmentConfig(segment_max_age=_SEG_AGE)
    retention = StoreRetentionConfig(max_age=3 * _SEG_AGE - 1)
    with pytest.raises(ValueError, match="max_age"):
        validate_store_config(segments, retention)


def test_validate_row_rule_only_active_when_both_limits_set():
    # segment_max_rows aktiv, aber max_entries nicht → keine Row-Regel.
    validate_store_config(
        SegmentConfig(segment_max_rows=_SEG_ROWS),
        StoreRetentionConfig(max_entries=None),
    )
    # max_entries aktiv, aber segment_max_rows nicht → keine Row-Regel.
    validate_store_config(
        SegmentConfig(segment_max_rows=None),
        StoreRetentionConfig(max_entries=5),
    )


def test_validate_age_rule_only_active_when_both_limits_set():
    validate_store_config(
        SegmentConfig(segment_max_age=_SEG_AGE),
        StoreRetentionConfig(max_age=None),
    )
    validate_store_config(
        SegmentConfig(segment_max_age=None),
        StoreRetentionConfig(max_age=5),
    )


# ---------------------------------------------------------------------------
# (#919) Technische Grenzen NUR für EXPLIZITE Nutzereingaben → klare Fehlermeldung.
# Diese laufen über ``validate_explicit_segment_bounds`` (NICHT über
# validate_store_config, das würde auch die Auto-Ableitung treffen).
# ---------------------------------------------------------------------------


def test_bounds_reject_segment_max_bytes_below_minimum():
    with pytest.raises(ValueError, match=r"segment_max_bytes.*between.*4 MiB.*1 GiB"):
        validate_explicit_segment_bounds(segment_max_bytes=4 * 1024 * 1024 - 1)


def test_bounds_reject_segment_max_bytes_above_maximum():
    with pytest.raises(ValueError, match=r"segment_max_bytes.*between.*4 MiB.*1 GiB"):
        validate_explicit_segment_bounds(segment_max_bytes=1024 * 1024 * 1024 + 1)


def test_bounds_accept_segment_max_bytes_at_bounds():
    validate_explicit_segment_bounds(segment_max_bytes=4 * 1024 * 1024)
    validate_explicit_segment_bounds(segment_max_bytes=1024 * 1024 * 1024)


def test_bounds_reject_segment_max_age_below_minimum():
    with pytest.raises(ValueError, match=r"segment_max_age.*300 s.*30 d"):
        validate_explicit_segment_bounds(segment_max_age=299)


def test_bounds_reject_segment_max_age_above_maximum():
    with pytest.raises(ValueError, match=r"segment_max_age.*300 s.*30 d"):
        validate_explicit_segment_bounds(segment_max_age=2_592_000 + 1)


def test_bounds_accept_segment_max_age_at_bounds():
    validate_explicit_segment_bounds(segment_max_age=300)
    validate_explicit_segment_bounds(segment_max_age=2_592_000)


def test_bounds_reject_segment_max_rows_below_minimum():
    with pytest.raises(ValueError, match=r"segment_max_rows.*>= 1000"):
        validate_explicit_segment_bounds(segment_max_rows=999)


def test_bounds_accept_segment_max_rows_at_minimum():
    validate_explicit_segment_bounds(segment_max_rows=1000)


def test_bounds_ignore_unset_none_values():
    # Auto-Ableitung übergibt None → keine Prüfung, auch für Sub-4-MiB-Werte via Auto.
    validate_explicit_segment_bounds()


def test_validate_store_config_does_not_enforce_technical_bounds():
    # Auto-abgeleitete winzige Segmentgröße (< 4 MiB) muss validate_store_config
    # passieren, solange die 3-Segment-Regel hält — kein 422 im Auto-Startpfad.
    validate_store_config(SegmentConfig(segment_max_bytes=341), StoreRetentionConfig(max_file_size_bytes=1024))
