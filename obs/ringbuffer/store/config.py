"""Segment- und Retention-Konfigurationsvertrag für den RingBuffer-Store (#930).

Zwei getrennte Ebenen:

* **Segmentierung / Rotation** (``SegmentConfig``): ab wann ein aktives Segment
  geschlossen und ein neues geöffnet wird (``segment_max_bytes``,
  ``segment_max_rows``, ``segment_max_age``). Rein backend-intern.
* **Retention** (``StoreRetentionConfig``): wie viel Historie insgesamt
  aufbewahrt wird (``max_file_size_bytes``, ``max_entries``, ``max_age``).

Semantik im segmentierten Betrieb (verbindlich, #930):

* Retention löscht **nur ganze, geschlossene Segmente** — segmentgenau, nicht
  rowgenau. Es gibt keine row-weise Retention im segmentierten Normalbetrieb.
* ``max_file_size_bytes`` ist ein **hartes Budget** (Size-Grenze). ``max_age``
  und ``max_entries`` sind **Aufbewahrungsziele** mit Segmentgranularität.
* Rotation (Segment-Ebene) und Retention (Aufbewahrungs-Ebene) sind getrennt.

Damit segmentgenaue Retention überhaupt greifen kann, muss ein Retention-Budget
mindestens ``RETENTION_SEGMENT_RATIO`` Segmente umfassen — sonst wäre die
Segmentierung zu grob, um alte Daten kontrolliert freizugeben. Zu grobe
Kombinationen werden per ``validate_store_config`` **abgelehnt** (der API-Layer
übersetzt das in HTTP 422), nicht stillschweigend auto-korrigiert.
"""

from __future__ import annotations

from dataclasses import dataclass

# Ein Retention-Budget muss mindestens so viele Segmente fassen, damit
# segmentgenaue Retention Daten kontrolliert freigeben kann, statt am
# einzigen/aktiven Segment zu scheitern.
RETENTION_SEGMENT_RATIO = 3


def _require_positive(name: str, value: int | None) -> None:
    if value is not None and value < 1:
        raise ValueError(f"{name} must be >= 1 or null")


@dataclass(frozen=True)
class SegmentConfig:
    """Rotations-Schwellen für das SQLite-Segment-Backend (backend-intern)."""

    segment_max_bytes: int | None = None
    segment_max_rows: int | None = None
    segment_max_age: int | None = None

    def __post_init__(self) -> None:
        _require_positive("segment_max_bytes", self.segment_max_bytes)
        _require_positive("segment_max_rows", self.segment_max_rows)
        _require_positive("segment_max_age", self.segment_max_age)


@dataclass(frozen=True)
class StoreRetentionConfig:
    """Retention-Aufbewahrungsziele bzw. -Budgets (portabel gemeint)."""

    max_file_size_bytes: int | None = None
    max_entries: int | None = None
    max_age: int | None = None

    def __post_init__(self) -> None:
        _require_positive("max_file_size_bytes", self.max_file_size_bytes)
        _require_positive("max_entries", self.max_entries)
        _require_positive("max_age", self.max_age)


def validate_store_config(segments: SegmentConfig, retention: StoreRetentionConfig) -> None:
    """Lehnt zu grobe Segmentierung im Verhältnis zu aktiven Retention-Limits ab.

    Regeln (jeweils nur aktiv, wenn beide Seiten gesetzt sind):

    * ``max_file_size_bytes >= RETENTION_SEGMENT_RATIO * segment_max_bytes``
    * ``max_entries >= RETENTION_SEGMENT_RATIO * segment_max_rows``
    * ``max_age >= RETENTION_SEGMENT_RATIO * segment_max_age``

    Verletzungen werden als ``ValueError`` gemeldet und nicht auto-korrigiert.
    """
    _check_ratio(
        "max_file_size_bytes",
        retention.max_file_size_bytes,
        "segment_max_bytes",
        segments.segment_max_bytes,
    )
    _check_ratio(
        "max_entries",
        retention.max_entries,
        "segment_max_rows",
        segments.segment_max_rows,
    )
    _check_ratio(
        "max_age",
        retention.max_age,
        "segment_max_age",
        segments.segment_max_age,
    )


def _check_ratio(
    retention_name: str,
    retention_value: int | None,
    segment_name: str,
    segment_value: int | None,
) -> None:
    if retention_value is None or segment_value is None:
        return
    minimum = RETENTION_SEGMENT_RATIO * segment_value
    if retention_value < minimum:
        raise ValueError(
            f"{retention_name} ({retention_value}) must be >= "
            f"{RETENTION_SEGMENT_RATIO} * {segment_name} ({segment_value}) = {minimum}; "
            "segmentation is too coarse for segment-granular retention"
        )
