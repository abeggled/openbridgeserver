"""Portabler ``RingBufferStore``-Contract + Capability-Deskriptor (#920/#931).

Diese Grenze ist bewusst **engine-neutral**. Sie beschreibt, was OBS (und
mittelfristig ein separater ``ringbufferd``) vom Store sieht, ohne SQLite-
spezifische Konzepte durchsickern zu lassen:

* Keine ``rotate``/``segment_id``/``manifest``/WAL-Begriffe im Contract — ein
  relationales/TSDB-Backend hat keine Segmente. Die portable Query-Semantik ist
  „Events für Zeitfenster X, Filter Y, bounded auf N", nicht „wähle überlappende
  Segmente".
* ``stats()`` liefert ``common {...} + backend_extra {...}``. Backend-Interna
  (WAL/Checkpoint/SHM, Segmentzahl) gehören ausschließlich in ``backend_extra``.

Die generische Monitor-/Query-Schicht degradiert kontrolliert, wenn ein Backend
etwas nicht nativ kann — dafür deklariert jedes Backend seine ``capabilities``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OrderingGuarantee(str, Enum):
    """Welche Sortiergarantie ein Backend über die Store-Grenze zusichert."""

    # Stabile, monoton wachsende globale Event-ID über alle Partitionen/Segmente.
    GLOBAL_MONOTONIC = "global_monotonic"
    # Nur innerhalb einer Partition/eines Segments monoton.
    PER_PARTITION = "per_partition"
    # Keine Sortiergarantie.
    NONE = "none"


@dataclass(frozen=True)
class StoreCapabilities:
    """Was ein Backend nativ kann — die Query-Schicht degradiert sonst kontrolliert."""

    supports_native_retention: bool
    supports_typed_pushdown: bool
    ordering_guarantee: OrderingGuarantee
    supports_streaming_export: bool


@dataclass
class StoreEvent:
    """Ein aufzuzeichnendes Event — engine-neutrales Wertobjekt für ``append``."""

    ts: str
    datapoint_id: str
    topic: str
    old_value: Any
    new_value: Any
    source_adapter: str
    quality: str
    metadata_version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StoreQuery:
    """Portables Query-Objekt: Zeitfenster + Filter + Bound.

    Bewusst KEINE Segment-Auswahl — das Backend entscheidet intern, welche
    Partitionen/Segmente es liest.

    ``value_filters`` ist eine engine-neutrale Liste von Prädikaten auf den
    Wert eines Events. Jedes Prädikat ist ein ``dict`` mit den Feldern:

    * ``field`` (optional, Default ``"new_value"``): ``"new_value"`` oder
      ``"old_value"`` — auf welchen Wert das Prädikat wirkt.
    * ``operator``: einer von ``eq``, ``ne``, ``gt``, ``gte``, ``lt``, ``lte``,
      ``between``, ``contains``, ``regex``.
    * ``value``: Vergleichswert für ``eq``/``ne``/``gt``/``gte``/``lt``/``lte``
      und (als Nadel) für ``contains``.
    * ``lower`` / ``upper``: inklusive Grenzen für ``between``.
    * ``pattern``: Regex-Muster für ``regex``.
    * ``ignore_case`` (optional, Default ``False``): für ``contains``/``regex``.

    Backends mit ``supports_typed_pushdown`` schieben die einfachen Operatoren
    (``eq``..``between``) als typisierte WHERE-Prädikate in die Engine, damit
    ``limit`` nicht durch einen Post-Filter ausgehebelt wird. ``contains`` und
    ``regex`` sind Sonderfälle: nur mit einem eng gebundenen Query (Zeitfenster
    ``from_ts``+``to_ts`` oder ``candidate_cap``) erlaubt, sonst wird ein
    ``ValueError`` geworfen (422-tauglich), um unbounded Full-Scans zu verhindern.
    """

    from_ts: str | None = None
    to_ts: str | None = None
    datapoint_id: str | None = None
    source_adapter: str | None = None
    quality: str | None = None
    limit: int = 100
    offset: int = 0
    value_filters: list[dict[str, Any]] = field(default_factory=list)
    # Obergrenze für Kandidatenzeilen bei unbounded contains/regex ohne
    # Zeitfenster. ``None`` = kein Cap → contains/regex erfordern ein Zeitfenster.
    candidate_cap: int | None = None


@dataclass
class StoreStats:
    """Zweigeteilte Stats: portabel (``common``) + backend-spezifisch (``backend_extra``)."""

    common: dict[str, Any] = field(default_factory=dict)
    backend_extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"common": dict(self.common), "backend_extra": dict(self.backend_extra)}


class RingBufferStore(ABC):
    """Portabler Contract, den jedes Store-Backend erfüllen muss.

    Alle Methoden sind async, weil der SQLite-Backend I/O über ``aiosqlite``
    macht und ein späterer Netzwerk-Backend ohnehin async wäre.
    """

    @abstractmethod
    def capabilities(self) -> StoreCapabilities:
        """Statischer Capability-Deskriptor dieses Backends."""

    @abstractmethod
    async def append(self, events: list[StoreEvent]) -> None:
        """Hängt Events append-only an. Reihenfolge = Eingabereihenfolge."""

    @abstractmethod
    async def query(self, query: StoreQuery) -> list[dict[str, Any]]:
        """Liefert Events für Zeitfenster + Filter, bounded auf ``query.limit``."""

    @abstractmethod
    async def stats(self) -> StoreStats:
        """Portable ``common``-Kennzahlen + backend-spezifische ``backend_extra``."""

    @abstractmethod
    async def enforce_retention(self) -> int:
        """Wendet Retention an und liefert die Anzahl freigegebener Einheiten."""
