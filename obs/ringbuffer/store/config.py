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

# Technische Grenzen für EXPLIZITE Nutzereingaben (#919). Sie gelten NUR für
# vom Nutzer gesetzte Werte in ``POST /config`` (via
# ``validate_explicit_segment_bounds``), NIE für die automatische Ableitung beim
# Auto-Start (die läuft nicht durch diese Validierung). Verletzungen → HTTP 422.
_MIB = 1024 * 1024
_GIB = 1024 * 1024 * 1024
SEGMENT_MAX_BYTES_MIN = 4 * _MIB  # 4 MiB
SEGMENT_MAX_BYTES_MAX = 1 * _GIB  # 1 GiB
SEGMENT_MAX_AGE_MIN = 300  # 5 min
SEGMENT_MAX_AGE_MAX = 2_592_000  # 30 d
SEGMENT_MAX_ROWS_MIN = 1000


def _format_bytes_binary(value: int) -> str:
    """Formatiert Bytes in MiB/GiB (binär, 1024er) für Fehlermeldungen (#919)."""
    if value >= _GIB:
        return f"{value / _GIB:g} GiB"
    return f"{value / _MIB:g} MiB"


def validate_explicit_segment_bounds(
    *,
    segment_max_bytes: int | None = None,
    segment_max_age: int | None = None,
    segment_max_rows: int | None = None,
) -> None:
    """Prüft EXPLIZIT vom Nutzer gesetzte Segment-Rotations-Werte gegen technische Grenzen (#919).

    Bewusst getrennt von ``validate_store_config``: diese Grenzen gelten NUR für
    Werte, die der Nutzer in ``POST /config`` explizit gesetzt hat — NIE für die
    automatische ``derive_segment_max_bytes``-Ableitung beim Auto-Start (die darf
    auch unter 4 MiB liegen, damit winzige Budgets nie ein 422 im Auto-Start
    auslösen). Der Aufrufer übergibt daher nur die tatsächlich gesetzten Felder.

    Grenzen: ``segment_max_bytes`` 4 MiB…1 GiB, ``segment_max_age`` 300 s…2.592.000 s
    (5 min…30 d), ``segment_max_rows`` >= 1000. Verletzungen → ``ValueError`` mit
    klarer, verständlicher Meldung (welcher Wert, welche Grenze).
    """
    if segment_max_bytes is not None and not (SEGMENT_MAX_BYTES_MIN <= segment_max_bytes <= SEGMENT_MAX_BYTES_MAX):
        raise ValueError(
            f"segment_max_bytes ({_format_bytes_binary(segment_max_bytes)}) must be between "
            f"{_format_bytes_binary(SEGMENT_MAX_BYTES_MIN)} and {_format_bytes_binary(SEGMENT_MAX_BYTES_MAX)}"
        )
    if segment_max_age is not None and not (SEGMENT_MAX_AGE_MIN <= segment_max_age <= SEGMENT_MAX_AGE_MAX):
        raise ValueError(f"segment_max_age ({segment_max_age} s) must be between {SEGMENT_MAX_AGE_MIN} s (5 min) and {SEGMENT_MAX_AGE_MAX} s (30 d)")
    if segment_max_rows is not None and segment_max_rows < SEGMENT_MAX_ROWS_MIN:
        raise ValueError(f"segment_max_rows ({segment_max_rows}) must be >= {SEGMENT_MAX_ROWS_MIN}")


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
    """Lehnt zu grobe Segmentierung im Verhältnis zu aktiven Retention-Limits ab (#930).

    Prüft NUR die **3-Segment-Regel** (jeweils aktiv, wenn beide Seiten gesetzt):

    * ``max_file_size_bytes >= RETENTION_SEGMENT_RATIO * segment_max_bytes``
    * ``max_entries >= RETENTION_SEGMENT_RATIO * segment_max_rows``
    * ``max_age >= RETENTION_SEGMENT_RATIO * segment_max_age``

    Die zusätzlichen technischen Grenzen für EXPLIZITE Nutzereingaben werden
    bewusst NICHT hier geprüft (das würde auch die Auto-Ableitung beim Store-Open
    treffen), sondern separat über ``validate_explicit_segment_bounds`` im
    API-Layer. Verletzungen werden als ``ValueError`` gemeldet und nicht
    auto-korrigiert.
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
