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


def test_validate_accepts_config_at_exact_ratio_boundary():
    segments = SegmentConfig(segment_max_bytes=10, segment_max_rows=10, segment_max_age=10)
    retention = StoreRetentionConfig(
        max_file_size_bytes=RETENTION_SEGMENT_RATIO * 10,
        max_entries=RETENTION_SEGMENT_RATIO * 10,
        max_age=RETENTION_SEGMENT_RATIO * 10,
    )
    validate_store_config(segments, retention)


def test_validate_rejects_size_budget_smaller_than_three_segments():
    segments = SegmentConfig(segment_max_bytes=10)
    retention = StoreRetentionConfig(max_file_size_bytes=3 * 10 - 1)
    with pytest.raises(ValueError, match="max_file_size_bytes"):
        validate_store_config(segments, retention)


def test_validate_rejects_entries_budget_smaller_than_three_segments():
    segments = SegmentConfig(segment_max_rows=10)
    retention = StoreRetentionConfig(max_entries=3 * 10 - 1)
    with pytest.raises(ValueError, match="max_entries"):
        validate_store_config(segments, retention)


def test_validate_rejects_age_budget_smaller_than_three_segments():
    segments = SegmentConfig(segment_max_age=10)
    retention = StoreRetentionConfig(max_age=3 * 10 - 1)
    with pytest.raises(ValueError, match="max_age"):
        validate_store_config(segments, retention)


def test_validate_row_rule_only_active_when_both_limits_set():
    # segment_max_rows aktiv, aber max_entries nicht → keine Row-Regel.
    validate_store_config(
        SegmentConfig(segment_max_rows=1000),
        StoreRetentionConfig(max_entries=None),
    )
    # max_entries aktiv, aber segment_max_rows nicht → keine Row-Regel.
    validate_store_config(
        SegmentConfig(segment_max_rows=None),
        StoreRetentionConfig(max_entries=5),
    )


def test_validate_age_rule_only_active_when_both_limits_set():
    validate_store_config(
        SegmentConfig(segment_max_age=1000),
        StoreRetentionConfig(max_age=None),
    )
    validate_store_config(
        SegmentConfig(segment_max_age=None),
        StoreRetentionConfig(max_age=5),
    )
