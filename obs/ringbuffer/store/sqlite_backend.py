"""SQLite-Segment-Backend — implementiert den portablen ``RingBufferStore`` (#931).

Backend-intern (unter der portablen Grenze). Verwaltet:

* ein ``segments/``-Verzeichnis mit je einer SQLite-Datei pro Segment,
* das ``Manifest`` (Segment-Metadaten + globaler Event-ID-Zähler),
* eine root-weite ``WriterLease`` (fail-fast bei zweitem Writer),
* genau **ein** aktives writable Segment; geschlossene Segmente sind read-only.

Append hängt append-only an das aktive Segment an und vergibt je Event eine
**stabile globale Event-ID** aus dem Manifest, damit die Ordnung über
Segmentgrenzen hinweg stabil bleibt (Vorbedingung für #932).

``rotate()`` schließt das aktive Segment sauber und öffnet genau ein neues.
Beim Schließen wird ``wal_checkpoint(TRUNCATE)`` versucht; scheitert es (busy
durch aktive Reader), wird das Segment als ``checkpoint_pending`` markiert, statt
es stillschweigend als löschbar zu behandeln.

Reader-Modell (aus der #931-Plan-Validierung): OBS/ringbufferd lesen
**ausschließlich über diese Store-Grenze**, nie direkt auf Segment-Dateien.
Dadurch kontrolliert der Writer alle Connections und Checkpoint-busy bleibt
selten.

Segment-Retention (``enforce_retention``), die Betriebs-/Support-Stats
(``backend_extra``), der Checkpoint-Läufer für ``checkpoint_pending`` und die
Per-Segment-Recovery/Quarantäne sind hier umgesetzt (#936, Vertrag aus #930).
Retention löscht ausschließlich ganze, sauber geschlossene Segmente — nie
rowweise, nie das aktive Segment, nie ein noch nicht konsistentes (pending/
quarantäniertes) Segment. Integrity läuft on-demand pro Segment, NICHT als
globaler Startup-Scan über 20–30 GB.

Die segmentbewusste, bounded Query (#932) wählt Segmente zuerst über das
Manifest (Zeitfenster-Overlap bzw. neueste zuerst), mergt sie nach
``global_event_id`` DESC und terminiert früh, sobald ``offset+limit`` Zeilen
sicher zusammengeführt sind (kein Voll-Merge über alle Segmente). Legacy-
Migration inkl. Dirty-WAL-Handling großer Single-DBs (#934) bleibt außerhalb
dieses Kernels; die Nahtstelle ist mit ``# TODO(#…)`` markiert.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from obs.core.json import json_default, json_dumps
from obs.ringbuffer.ringbuffer import (
    _extract_metadata_binding_index_rows,
    _extract_metadata_tags,
    _is_sqlite_corruption,
)
from obs.ringbuffer.store.config import SegmentConfig, StoreRetentionConfig, validate_store_config
from obs.ringbuffer.store.interface import (
    OrderingGuarantee,
    RingBufferStore,
    StoreCapabilities,
    StoreEvent,
    StoreQuery,
    StoreStats,
)
from obs.ringbuffer.store.manifest import (
    LEGACY_SCHEMA_VERSION,
    SEGMENT_STATUS_ACTIVE,
    SEGMENT_STATUS_CHECKPOINT_PENDING,
    SEGMENT_STATUS_CLOSED,
    SEGMENT_STATUS_QUARANTINED,
    Manifest,
    SegmentRecord,
)
from obs.ringbuffer.store.writer_lock import WriterLease

_LOGGER = logging.getLogger(__name__)

SEGMENT_SCHEMA_VERSION = 2

# Netzlaufwerk-Erkennung (WAL/mmap-Warnung): Dateisystemtypen bzw. mount-Optionen,
# auf denen SQLite-WAL/shared-memory-mmap unzuverlässig ist. Rein diagnostisch —
# der Store degradiert nicht still, sondern meldet den Fall in den Stats.
_NETWORK_FS_TYPES = frozenset({"nfs", "nfs4", "smbfs", "cifs", "afpfs", "fuse.sshfs", "webdav"})

# Segment-lokales Schema. Identisch je Segment; die globale Ordnung liegt in
# der zusätzlichen Spalte ``global_event_id`` (aus dem Manifest-Zähler), nicht
# in der segment-lokalen rowid ``id``.
#
# Die JSON-Spalten ``old_value``/``new_value`` bleiben erhalten (API-Kompat).
# Zusätzlich (#933) tragen typisierte Spalten den Wert typgerecht, damit
# einfache Wertfilter als SQL-WHERE gepusht werden können und ``LIMIT`` greift:
# ``*_value_type`` ∈ {numeric, text, bool, null}; genau eine der Spalten
# ``*_value_num`` (REAL) / ``*_value_text`` (TEXT) / ``*_value_bool`` (0/1) ist
# je nach Typ befüllt.
_SEGMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS ringbuffer (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    global_event_id  INTEGER NOT NULL,
    ts               TEXT    NOT NULL,
    datapoint_id     TEXT    NOT NULL,
    topic            TEXT    NOT NULL,
    old_value        TEXT,
    new_value        TEXT,
    old_value_type   TEXT,
    old_value_num    REAL,
    old_value_text   TEXT,
    old_value_bool   INTEGER,
    new_value_type   TEXT,
    new_value_num    REAL,
    new_value_text   TEXT,
    new_value_bool   INTEGER,
    source_adapter   TEXT    NOT NULL,
    quality          TEXT    NOT NULL,
    metadata_version INTEGER NOT NULL DEFAULT 1,
    metadata         TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_rb_gid ON ringbuffer(global_event_id DESC);
CREATE INDEX IF NOT EXISTS idx_rb_ts_id_desc ON ringbuffer(ts DESC, global_event_id DESC);
CREATE INDEX IF NOT EXISTS idx_rb_dp_ts_id ON ringbuffer(datapoint_id, ts DESC, global_event_id DESC);
CREATE INDEX IF NOT EXISTS idx_rb_adp_ts_id ON ringbuffer(source_adapter, ts DESC, global_event_id DESC);
CREATE INDEX IF NOT EXISTS idx_rb_quality_ts_id ON ringbuffer(quality, ts DESC, global_event_id DESC);
CREATE INDEX IF NOT EXISTS idx_rb_new_num ON ringbuffer(new_value_num, global_event_id DESC);
CREATE INDEX IF NOT EXISTS idx_rb_new_text ON ringbuffer(new_value_text, global_event_id DESC);

CREATE TABLE IF NOT EXISTS ringbuffer_metadata_tags (
    entry_id INTEGER NOT NULL REFERENCES ringbuffer(id) ON DELETE CASCADE,
    tag      TEXT    NOT NULL,
    PRIMARY KEY (entry_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_rb_meta_tag_entry ON ringbuffer_metadata_tags(tag, entry_id);

CREATE TABLE IF NOT EXISTS ringbuffer_metadata_bindings (
    entry_id             INTEGER NOT NULL REFERENCES ringbuffer(id) ON DELETE CASCADE,
    adapter_type         TEXT    NOT NULL DEFAULT '',
    adapter_instance_id  TEXT    NOT NULL DEFAULT '',
    group_address        TEXT    NOT NULL DEFAULT '',
    topic                TEXT    NOT NULL DEFAULT '',
    entity_id            TEXT    NOT NULL DEFAULT '',
    register_type        TEXT    NOT NULL DEFAULT '',
    register_address     TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_rb_meta_bind_adapter_type_entry ON ringbuffer_metadata_bindings(adapter_type, entry_id);
CREATE INDEX IF NOT EXISTS idx_rb_meta_bind_adapter_instance_entry ON ringbuffer_metadata_bindings(adapter_instance_id, entry_id);
CREATE INDEX IF NOT EXISTS idx_rb_meta_bind_group_address_entry ON ringbuffer_metadata_bindings(group_address, entry_id);
CREATE INDEX IF NOT EXISTS idx_rb_meta_bind_topic_entry ON ringbuffer_metadata_bindings(topic, entry_id);
CREATE INDEX IF NOT EXISTS idx_rb_meta_bind_entity_id_entry ON ringbuffer_metadata_bindings(entity_id, entry_id);
CREATE INDEX IF NOT EXISTS idx_rb_meta_bind_register_entry ON ringbuffer_metadata_bindings(register_type, register_address, entry_id);
"""


def _utc_now_compact() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S-%f")


def _safe_getsize(path: Path) -> int:
    """Dateigröße in Bytes; 0 statt Exception, wenn die Datei fehlt/unlesbar ist (#919).

    ``stats()`` liest Segment-Größen ausschließlich per ``os.path.getsize`` (nie
    durch Öffnen der Segment-DB) und darf auch bei einem komplett kaputten oder
    verschwundenen File nie werfen.
    """
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _is_legacy_segment(segment: SegmentRecord) -> bool:
    """True für read-only eingehängte v1/Legacy-Single-DBs (#934).

    Erkennung über die Segment-Schema-Version — NICHT über den Status —, damit der
    Read-Pfad unabhängig von der Manifest-Statusmaschine korrekt degradiert.
    """
    return segment.schema_version <= LEGACY_SCHEMA_VERSION


# Legacy-Query darf ohne Zeitfenster nicht unbounded scannen: die JSON-basierte
# Value-Filter-Degradation liest höchstens so viele Kandidatenzeilen.
_LEGACY_DEFAULT_CANDIDATE_CAP = 10_000

# Synthetische global_event_id für Legacy-Zeilen: aus der chronologischen
# Legacy-rowid abgeleitet (NICHT aus der Fetch-Reihenfolge), damit die Ordnung
# unabhängig von der Sort-Richtung des Kandidaten-Fetches stabil bleibt. Der
# große Offset hält alle Legacy-IDs strikt negativ (unter allen positiven
# v2-IDs); höhere rowid (neuer) ⇒ höhere (weniger negative) ID.
_LEGACY_GID_OFFSET = 1 << 62


# Einfache Operatoren, die als typisiertes SQL-WHERE gepusht werden.
_PUSHDOWN_OPERATORS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "between"})
# contains/regex: nur mit gebundenem Query (Zeitfenster oder Kandidaten-Cap).
_GUARDED_OPERATORS = frozenset({"contains", "regex"})
_VALID_OPERATORS = _PUSHDOWN_OPERATORS | _GUARDED_OPERATORS
# Erlaubte Zielspalten eines Wertfilters (engine-neutrale field-Namen).
_FILTER_FIELDS = frozenset({"new_value", "old_value"})
# Reihenfolge der Binding-Index-Spalten — identisch zu
# ``_extract_metadata_binding_index_rows`` (positionell), für den Legacy-
# Python-Fallback der Metadaten-Binding-Filter.
_BINDING_INDEX_COLUMNS = (
    "adapter_type",
    "adapter_instance_id",
    "group_address",
    "topic",
    "entity_id",
    "register_type",
    "register_address",
)
# Regex-Härtung (Referenz: Legacy _match_regex in ringbuffer.py).
_REGEX_MAX_PATTERN_LEN = 256
# Ziel-String-Längenbegrenzung wie Legacy ``_match_regex`` (#951, Pkt 6): der
# SQLite-Callback läuft synchron je Kandidatenzeile; ohne diese Grenze könnte ein
# sehr langer Wert (kombiniert mit einem Muster) die Query/den Event-Loop lange
# blockieren. Der Vergleich wird daher auf die ersten ``_REGEX_MAX_TARGET_LEN``
# Zeichen begrenzt — gebounded statt blockierend.
_REGEX_MAX_TARGET_LEN = 4096
_RE_UNSAFE_NESTED_QUANTIFIERS = re.compile(r"\((?:[^()\\]|\\.)*[+*][^()]*\)[+*]")
# SQL-Vergleichsoperatoren je Pushdown-Operator (between separat behandelt).
_SQL_COMPARATORS = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def _derive_value_type(value: Any) -> str:
    """Leitet den typisierten Spaltentyp aus einem Python-Wert ab.

    Reihenfolge orientiert sich an den Legacy-Typ-Helfern (``_is_boolean_type``
    vor ``_is_numeric_type``), weil ``bool`` in Python eine ``int``-Subklasse
    ist und sonst fälschlich als numerisch klassifiziert würde.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "numeric"
    if isinstance(value, str):
        return "text"
    # Listen/Dicts o.ä. sind für typisierte Pushdown-Filter nicht adressierbar,
    # dürfen aber NICHT als ``null`` getaggt werden (#951, Pkt 1): sonst matchte
    # der Pushdown ``eq value:null`` diese komplexen Werte (und ``ne null`` schlösse
    # sie aus), obwohl der Referenz-Filter ``value == None`` sie nie als null wertet.
    # Ein eigener ``json``-Typ hält alle Nutzspalten NULL wie bei null, unterscheidet
    # sich aber im ``*_value_type`` – so trifft ``eq/ne null`` ausschließlich echtes
    # JSON-null.
    return "json"


def _canonical_json(value: Any) -> str:
    """Order-unabhängige kanonische JSON-Repräsentation (sortierte Objekt-Keys).

    Für den JSON-``eq/ne``-Vergleich (#951, Codex :1281): zwei inhaltsgleiche
    Objekte müssen matchen, auch wenn ihre Key-Reihenfolge differiert. ``sort_keys``
    normalisiert Objekte; Listen bleiben ordnungsempfindlich wie in der Referenz.
    """
    return json.dumps(value, default=json_default, sort_keys=True)


def _obs_json_eq_impl(raw: Any, canonical_expected: str) -> int:
    """SQLite-Callback: 1, wenn die gespeicherte JSON-Spalte kanonisch ``== expected`` ist.

    Spiegelt die Legacy-Referenz ``actual == expected`` für komplexe (list/dict)
    Werte (#951, Codex :1281): die gespeicherte ``new_value``/``old_value``-JSON-Spalte
    wird dekodiert und kanonisch (sortierte Keys) re-serialisiert, dann String-gleich
    mit dem ebenfalls kanonisierten Filterwert verglichen. So matchen gleiche Objekte
    unabhängig von der Key-Reihenfolge; malformed/non-JSON-Spalten matchen nie.
    """
    if not isinstance(raw, str):
        return 0
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return 0
    return 1 if _canonical_json(decoded) == canonical_expected else 0


def _obs_regexp_impl(pattern: str, flags: int, value: Any) -> int:
    """SQLite-Callback für gepushtes ``regex``. 1 bei Treffer, sonst 0.

    Das Muster ist beim Clause-Bau bereits gehärtet (Länge, nested quantifiers,
    Kompilierbarkeit); der Query-Kontext ist gebunden (Zeitfenster/Cap).
    """
    if not isinstance(value, str):  # pragma: no cover - SQL filtert bereits text_col IS NOT NULL
        return 0
    # Ziel-Länge begrenzen (Legacy-Parität, #951 Pkt 6): ein pathologisch langer
    # Wert darf den synchronen Callback nicht blockieren. re.search auf den ersten
    # ``_REGEX_MAX_TARGET_LEN`` Zeichen ist gebounded; ``re.match``-Anker (^) bleiben
    # korrekt, da vom Anfang gesucht wird.
    target = value if len(value) <= _REGEX_MAX_TARGET_LEN else value[:_REGEX_MAX_TARGET_LEN]
    try:
        return 1 if re.compile(pattern, flags).search(target) else 0
    except re.error:  # pragma: no cover - bereits beim Clause-Bau geprüft
        return 0


def _obs_icontains_impl(needle_lower: str, value: Any) -> int:
    """SQLite-Callback für case-insensitive ``contains``. 1 bei Treffer, sonst 0.

    Unicode-Folding-Parität (#951, Codex :1364): SQLite-``LOWER()`` foldet auf
    Standard-Builds nur ASCII, sodass Nicht-ASCII-Text (z. B. deutsche Umlaute)
    bei ``ignore_case`` NICHT matchte, obwohl der Legacy-Python-Pfad (``.lower()``)
    ihn trifft. Der Callback lowert Nadel wie Heuhaufen in Python und ist damit
    Unicode-fähig. ``needle_lower`` ist bereits gelowered (Clause-Bau).
    """
    if not isinstance(value, str):  # pragma: no cover - SQL filtert bereits text_col IS NOT NULL
        return 0
    return 1 if needle_lower in value.lower() else 0


def _typed_columns_for(value: Any) -> tuple[str, float | None, str | None, int | None]:
    """(type, num, text, bool) — genau eine Nutzspalte ist je nach Typ gesetzt."""
    value_type = _derive_value_type(value)
    if value_type == "bool":
        return ("bool", None, None, 1 if value else 0)
    if value_type == "numeric":
        return ("numeric", float(value), None, None)
    if value_type == "text":
        return ("text", None, value, None)
    # null UND json (list/dict) tragen keine typisierte Nutzspalte; nur der
    # ``*_value_type`` unterscheidet sie, damit ``eq/ne null`` nur echtes JSON-null
    # trifft (#951, Pkt 1).
    return (value_type, None, None, None)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_ts(value: str | None) -> float | None:
    """ISO-8601-Timestamp (mit ``Z``) → POSIX-Sekunden; None bei unparsebarem Wert."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _legacy_row_matches_filters(record: dict[str, Any], value_filters: list[dict[str, Any]]) -> bool:
    """Python-Fallback-Auswertung der Value-Filter für v1/Legacy-Zeilen (#934).

    Ohne typisierte Wertspalten kann Legacy keinen SQL-Pushdown machen; die Filter
    werden hier gegen die dekodierten JSON-Werte ausgewertet. Semantik spiegelt die
    v2-Pushdown/Guarded-Operatoren, damit gemischte Legacy+v2-Queries konsistent sind.
    Alle Prädikate müssen zutreffen (AND).
    """
    return all(_legacy_filter_matches(record, spec) for spec in value_filters)


def _legacy_filter_matches(record: dict[str, Any], spec: dict[str, Any]) -> bool:
    operator = str(spec.get("operator", "")).strip().lower()
    if operator not in _VALID_OPERATORS:
        raise ValueError(f"invalid value filter operator: {operator!r}")
    field_name = str(spec.get("field", "new_value")).strip().lower()
    if field_name not in _FILTER_FIELDS:
        raise ValueError(f"invalid value filter field: {field_name!r}")
    actual = record.get(field_name)

    if operator == "between":
        lower, upper = spec.get("lower"), spec.get("upper")
        if not _is_number(lower) or not _is_number(upper):
            raise ValueError("between requires numeric lower/upper bounds")
        if lower > upper:
            raise ValueError("value filter lower must be <= upper")
        return _is_number(actual) and lower <= actual <= upper

    if operator in _SQL_COMPARATORS:
        return _legacy_compare(operator, actual, spec.get("value"))

    ignore_case = bool(spec.get("ignore_case", False))
    if operator == "contains":
        needle = spec.get("value")
        if not isinstance(needle, str):
            raise ValueError("contains requires a string value")
        if not isinstance(actual, str):
            return False
        haystack = actual.lower() if ignore_case else actual
        return (needle.lower() if ignore_case else needle) in haystack

    # regex — dieselbe Härtung wie der v2-Guarded-Zweig.
    pattern = spec.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("regex requires a non-empty pattern")
    if len(pattern) > _REGEX_MAX_PATTERN_LEN:
        raise ValueError("unsafe regex pattern: pattern too long")
    if _RE_UNSAFE_NESTED_QUANTIFIERS.search(pattern):
        raise ValueError("unsafe regex pattern: nested quantifiers are not allowed")
    if not isinstance(actual, str):
        return False
    flags = re.IGNORECASE if ignore_case else 0
    # Ziel-Länge kappen wie der v2-``_obs_regexp_impl`` (#951, Codex :376): ein
    # pathologisch langer gespeicherter Wert darf den synchronen ``re.search`` nicht
    # den Event-Loop blockieren lassen. Vergleich auf die ersten
    # ``_REGEX_MAX_TARGET_LEN`` Zeichen — gebounded statt blockierend, ``^``-Anker
    # bleiben korrekt (vom Anfang gesucht).
    target = actual if len(actual) <= _REGEX_MAX_TARGET_LEN else actual[:_REGEX_MAX_TARGET_LEN]
    try:
        return re.compile(pattern, flags).search(target) is not None
    except re.error as exc:  # pragma: no cover - Muster wurde oben bereits kompiliert
        raise ValueError(f"invalid regex pattern: {exc}") from exc


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _safe_json_decode(raw: Any) -> Any:
    """Dekodiert einen Legacy-JSON-Wert; gibt bei Fehler den Rohwert zurück (#951, Pkt 3).

    ``old_value``/``new_value`` einer alten Single-DB können – durch fremde Schreiber
    oder frühere Formate – malformed/non-JSON sein. Ein direktes ``json.loads`` würfe
    dann eine ``JSONDecodeError``, die NICHT vom Korruptionspfad gefangen wird und die
    gesamte Query/den Export abbräche. Der pre-Segmentierungs-Reader dekodierte sicher
    und lieferte im Fehlerfall den Rohwert. ``None`` bleibt ``None``.
    """
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return raw


def _legacy_metadata_decode(raw: Any) -> dict[str, Any]:
    """Dekodiert die Legacy-``metadata``-Spalte zu einem dict; leeres dict bei Fehler.

    Nachgelagerte Metadaten-Filter erwarten ein ``dict``. Ein malformed/non-JSON-
    Wert (oder ein JSON-Skalar) darf die Query nicht brechen (#951, Pkt 3) und
    degradiert daher auf ``{}`` (kein Metadaten-Treffer) statt zu werfen.
    """
    if not raw:
        return {}
    decoded = _safe_json_decode(raw)
    return decoded if isinstance(decoded, dict) else {}


def _legacy_compare(operator: str, actual: Any, expected: Any) -> bool:
    """eq/ne/gt/gte/lt/lte für den v1/Legacy-Python-Fallback.

    eq/ne folgen der verbindlichen Legacy-Referenz ``_matches_value_filter``
    (``obs/ringbuffer/ringbuffer.py``): reine Python-Gleichheit ``actual == expected``
    — typübergreifend inkl. der Python-Äquivalenz ``True == 1`` — sodass ``ne`` Zeilen
    anderen Typs sowie null einschließt (#951, Pkt 1). Nur die Range-Operatoren
    bleiben typtreu und lehnen inkompatible Typen ab.
    """
    if operator == "eq":
        return actual == expected
    if operator == "ne":
        return actual != expected
    # Range-Operatoren sind wie der v2-Pushdown UND ``_matches_value_filter`` nur für
    # numerische Werte definiert (#951, Codex :439). Ein gt/gte/lt/lte gegen einen
    # STRING/BOOLEAN-Vergleichswert würde sonst zu einem lexikografischen Text- bzw.
    # 0/1-Bool-Vergleich degradieren — segment-abhängiges Verhalten. Daher hier mit
    # demselben 422-tauglichen ValueError ablehnen, sodass eine upgegradete Instanz,
    # die nur ihr Legacy-Segment bedient, identisch reagiert.
    # null als Range-Vergleichswert ist bedeutungslos und würde beim Roh-Vergleich
    # (``actual > None``) einen TypeError werfen, der NICHT in den 422-Pfad der API
    # konvertiert wird (#951, Codex :467). Wie der v2-Pushdown mit derselben Meldung
    # ablehnen, BEVOR verglichen wird.
    if expected is None:
        raise ValueError(f"operator '{operator}' is not supported for null value")
    if isinstance(expected, bool) or isinstance(expected, str):
        data_type = "BOOLEAN" if isinstance(expected, bool) else "STRING"
        raise ValueError(f"operator '{operator}' is not supported for data_type '{data_type}'")
    # Komplexe (list/dict) Vergleichswerte werfen bei ``actual > [..]`` ebenfalls einen
    # TypeError. Wie der v2-Pushdown (``value_type == 'json'`` → STRING-Ablehnung) und
    # die Referenz als ungültigen Range-Filter ablehnen (#951, Codex :467).
    if isinstance(expected, (list, dict)):
        raise ValueError(f"operator '{operator}' is not supported for data_type 'STRING'")
    # Cross-Typ (numerischer Vergleichswert, nicht-numerische Zeile) ist bedeutungslos
    # → wie v2/Legacy kein Treffer.
    if _is_number(expected) and not _is_number(actual):
        return False
    if operator == "gt":
        return actual > expected
    if operator == "gte":
        return actual >= expected
    if operator == "lt":
        return actual < expected
    return actual <= expected  # lte


class SqliteSegmentStore(RingBufferStore):
    """Segmentiertes SQLite-Backend hinter der portablen Store-Grenze."""

    def __init__(
        self,
        root: str | Path,
        *,
        segments: SegmentConfig | None = None,
        retention: StoreRetentionConfig | None = None,
    ) -> None:
        self._root = Path(root)
        self._segments_dir = self._root / "segments"
        self._segment_config = segments or SegmentConfig()
        self._retention_config = retention or StoreRetentionConfig()
        self._lease = WriterLease(self._root)
        self.manifest = Manifest(self._root / "manifest.sqlite")
        self._active_conn: aiosqlite.Connection | None = None
        self._active_segment: SegmentRecord | None = None
        # Checkpoint-Betriebsdetails (SQLite-Interna → nur backend_extra).
        self._last_checkpoint_at: str | None = None
        self._last_checkpoint_mode: str | None = None
        self._last_checkpoint_result: str | None = None
        self._wal_checkpoint_busy_count = 0

    def apply_config(
        self,
        *,
        segments: SegmentConfig | None = None,
        retention: StoreRetentionConfig | None = None,
    ) -> None:
        """Übernimmt eine neue Segment-/Retention-Config in den laufenden Store (#919/#938).

        Die Config-Dataclasses sind ``frozen`` — daher wird das jeweilige Attribut
        neu zugewiesen. Rotation (``rotate``/Threshold-Checks im RingBuffer),
        segmentgenaue Retention (``enforce_retention``) und die Prognose
        (``_compute_prognosis``) lesen ``self._segment_config`` bzw.
        ``self._retention_config`` bei jedem Aufruf live — die neuen Werte greifen
        also ab dem nächsten Aufruf ohne Store-Neustart. Nur gesetzte Argumente
        werden übernommen (``None`` lässt die jeweilige Ebene unverändert).
        """
        if segments is not None:
            self._segment_config = segments
        if retention is not None:
            self._retention_config = retention

    # ------------------------------------------------------------------
    # Contract: Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> StoreCapabilities:
        return StoreCapabilities(
            supports_native_retention=True,
            # #933: typisierte Wertspalten + SQL-Pushdown für einfache Operatoren.
            supports_typed_pushdown=True,
            ordering_guarantee=OrderingGuarantee.GLOBAL_MONOTONIC,
            # #932 liefert den bounded Monitor-/Debug-Query-Pfad (query() ist stets
            # auf offset+limit begrenzt). Ein streambarer Voll-Export über alle
            # Segmente ist bewusst ein GETRENNTER Pfad mit eigenem Timeout/Limit und
            # hier noch nicht implementiert → False.
            supports_streaming_export=False,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        # Config-Vertrag früh durchsetzen (zu grobe Segmentierung → ValueError).
        validate_store_config(self._segment_config, self._retention_config)
        # Root-weite Writer-Exklusivität zuerst (fail-fast bei zweitem Writer).
        await self._lease.acquire()
        try:
            self._segments_dir.mkdir(parents=True, exist_ok=True)
            await self.manifest.open()
            active = await self.manifest.get_active_segment()
            if active is None:
                active = await self._create_segment_locked()
            self._active_segment = active
            self._active_conn = await self._open_segment_conn(active.filename)
        except Exception:
            await self._lease.release()
            raise

    async def close(self) -> None:
        if self._active_conn is not None:
            await self._active_conn.close()
            self._active_conn = None
        await self.manifest.close()
        await self._lease.release()

    async def _create_segment_locked(self) -> SegmentRecord:
        filename = f"rb_{_utc_now_compact()}.sqlite"
        return await self.manifest.create_segment(filename=filename, schema_version=SEGMENT_SCHEMA_VERSION)

    async def _open_segment_conn(self, filename: str) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(str(self._segments_dir / filename))
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.executescript(_SEGMENT_SCHEMA)
        await conn.commit()
        return conn

    # ------------------------------------------------------------------
    # Contract: append
    # ------------------------------------------------------------------

    async def append(self, events: list[StoreEvent]) -> None:
        if not events or self._active_conn is None or self._active_segment is None:
            return
        # Zusammenhängenden Block globaler IDs reservieren → stabile Ordnung.
        start_id = await self.manifest.reserve_global_event_ids(len(events))
        try:
            for offset, event in enumerate(events):
                await self._insert_event(self._active_conn, start_id + offset, event)
        except BaseException:
            # Scheitert ein Insert mitten im Batch (z.B. nicht serialisierbare Metadaten
            # oder ein fehlgeschlagener Metadaten-Index-Insert), bleiben die früheren
            # Inserts sonst in der offenen Transaktion und würden vom nächsten
            # erfolgreichen append() auf derselben Connection MIT-committet, obwohl der
            # Aufrufer einen Fehler sah (#951, Codex :584). Aktive Transaktion daher
            # zurückrollen – kein partieller Batch committet später.
            await self._active_conn.rollback()
            raise
        await self._active_conn.commit()
        await self._refresh_active_segment_stats()
        # TODO(#932/#936): hier greift später Rotation nach segment_max_* und
        # anschließend enforce_retention() auf geschlossene Segmente.

    async def _insert_event(self, conn: aiosqlite.Connection, global_event_id: int, event: StoreEvent) -> None:
        # JSON-Spalten bleiben (API-Kompat); typisierte Spalten für Pushdown (#933).
        old_type, old_num, old_text, old_bool = _typed_columns_for(event.old_value)
        new_type, new_num, new_text, new_bool = _typed_columns_for(event.new_value)
        cursor = await conn.execute(
            """INSERT INTO ringbuffer
               (global_event_id, ts, datapoint_id, topic, old_value, new_value,
                old_value_type, old_value_num, old_value_text, old_value_bool,
                new_value_type, new_value_num, new_value_text, new_value_bool,
                source_adapter, quality, metadata_version, metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                global_event_id,
                event.ts,
                event.datapoint_id,
                event.topic,
                json_dumps(event.old_value),
                json_dumps(event.new_value),
                old_type,
                old_num,
                old_text,
                old_bool,
                new_type,
                new_num,
                new_text,
                new_bool,
                event.source_adapter,
                event.quality,
                event.metadata_version,
                json.dumps(event.metadata or {}),
            ),
        )
        await self._persist_metadata_indexes(conn, cursor.lastrowid, event.metadata or {})

    async def _persist_metadata_indexes(self, conn: aiosqlite.Connection, entry_id: int, metadata: dict[str, Any]) -> None:
        if entry_id is None or entry_id <= 0:
            return
        tags = _extract_metadata_tags(metadata)
        if tags:
            await conn.executemany(
                "INSERT OR IGNORE INTO ringbuffer_metadata_tags (entry_id, tag) VALUES (?, ?)",
                [(entry_id, tag) for tag in tags],
            )
        binding_rows = _extract_metadata_binding_index_rows(metadata)
        if binding_rows:
            await conn.executemany(
                """INSERT INTO ringbuffer_metadata_bindings
                   (entry_id, adapter_type, adapter_instance_id, group_address, topic, entity_id, register_type, register_address)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [(entry_id, *row) for row in binding_rows],
            )

    # ------------------------------------------------------------------
    # Contract: query
    # ------------------------------------------------------------------

    async def query(self, query: StoreQuery) -> list[dict[str, Any]]:
        """Segmentbewusste, bounded Read-Query (#932) — Monitor/Debug-Pfad.

        Dies ist bewusst der **gebundene** Monitor-/Debug-Pfad: die Ausgabe ist
        stets auf ``offset+limit`` Zeilen begrenzt. Voll-Export/Historie sind
        getrennte Pfade mit eigenem Streaming/Timeout/Limit (siehe
        ``capabilities().supports_streaming_export``), NICHT dieser Merge.
        """
        rows = await self._collect_rows_across_segments(query)
        # Finaler Sort auf der bereits gebundenen Kandidatenmenge in der
        # gewünschten Ordnung. Für ``id`` sortiert ``global_event_id`` global
        # stabil (v2 positiv, Legacy synthetisch negativ). Für ``ts`` bricht
        # ``global_event_id`` Gleichstände deterministisch. Der Sort ist billig
        # (nur offset+limit-nahe Kandidaten je Segment).
        self._sort_rows_in_place(rows, query)
        start = max(query.offset, 0)
        end = start + max(query.limit, 0)
        return rows[start:end]

    @staticmethod
    def _sort_rows_in_place(rows: list[dict[str, Any]], query: StoreQuery) -> None:
        reverse = query.sort_order != "asc"
        if query.sort_field == "ts":
            rows.sort(key=lambda r: (r["ts"], r["global_event_id"]), reverse=reverse)
        else:
            rows.sort(key=lambda r: r["global_event_id"], reverse=reverse)

    async def _collect_rows_across_segments(self, query: StoreQuery) -> list[dict[str, Any]]:
        """Merged Segmente **neueste zuerst** und terminiert früh (bounded).

        Segmentauswahl läuft zuerst über das Manifest: mit Zeitfilter nur
        überlappende Segmente, sonst neueste zuerst (``segment_id DESC``). Da
        ``global_event_id`` beim Append streng monoton vergeben wird, hält ein
        später angelegtes Segment ausschließlich höhere IDs als jedes ältere —
        die per Segment bereits ``ORDER BY global_event_id DESC LIMIT ?``
        begrenzten Kandidaten sind damit über die Segmentgrenze hinweg schon
        korrekt absteigend sortiert. Sobald ``offset+limit`` Zeilen zusammen sind,
        können die restlichen (älteren) Segmente NICHT mehr in das Ausgabefenster
        gelangen und werden gar nicht erst geöffnet.
        """
        needed = max(query.offset, 0) + max(query.limit, 0)
        collected: list[dict[str, Any]] = []
        segments = await self.manifest.list_segments_for_query(query.from_ts, query.to_ts)
        # Early-Termination über Segmentgrenzen ist nur für die Default-Ordnung
        # (``id``/``desc``) korrekt: dort entspricht die Manifest-Reihenfolge
        # (neueste zuerst, Legacy zuletzt) exakt der ``global_event_id``-DESC-
        # Ordnung. Bei abweichender Sortierung (``ts`` oder ``asc``) kann ein
        # älteres Segment noch in das Ausgabefenster fallen; dann werden ALLE
        # passenden Segmente je ``offset+limit``-bounded gelesen (kein Voll-Scan)
        # und der finale Sort in ``query()` begrenzt die Ausgabe.
        allow_early_termination = query.sort_field == "id" and query.sort_order == "desc"
        for segment in segments:
            if allow_early_termination and needed and len(collected) >= needed:
                break  # bounded: ältere Segmente können das Fenster nicht mehr treffen.
            rows = await self._read_segment_rows(segment, query)
            if rows is not None:
                collected.extend(rows)
        return collected

    async def _read_segment_rows(self, segment: SegmentRecord, query: StoreQuery) -> list[dict[str, Any]] | None:
        """Liest ein einzelnes Segment; quarantäniert es on-the-fly bei Korruption (#919).

        Ein wirklich defektes (nicht-quarantäniertes) Segment-File darf die
        gesamte Query nicht brechen: eine SQLite-Korruptions-Exception beim
        Öffnen/Lesen wird gefangen, das Segment wird mit Grund als
        ``quarantined``/``corrupt`` markiert und **übersprungen** (Rückgabe
        ``None``). Die übrigen Segmente liefern normal. Das aktive Segment wird
        nie quarantäniert. Legacy-Segmente werden ebenfalls nur übersprungen (nie
        die in-place liegende Original-Datei anfassen).
        """
        # Race (#951, Pkt 2): löscht die Retention ein geschlossenes Segment
        # zwischen ``list_segments_for_query()`` und diesem Open, ist die Datei weg.
        # Ein read-only-Open (``mode=ro``) auf einer fehlenden Datei wirft, statt —
        # wie ein schreibendes ``connect`` — still eine leere Ersatz-DB anzulegen
        # (die dann „no such table" → 500 liefern würde). Ein zwischenzeitlich
        # gelöschtes/fehlendes, nicht-aktives Segment wird daher übersprungen.
        if self._segment_read_file_missing(segment):
            return None
        try:
            conn = await self._connection_for_read(segment)
        except aiosqlite.Error as exc:
            return await self._skip_or_quarantine_read(segment, exc)
        close_after = conn is not self._active_conn
        try:
            if _is_legacy_segment(segment):
                # v1/Legacy-Single-DB: kein global_event_id, keine typisierten
                # Spalten → eigener degradierender Read-Zweig (#934).
                return await self._query_legacy_segment(conn, segment, query)
            return await self._query_segment(conn, query)
        except aiosqlite.Error as exc:
            return await self._skip_or_quarantine_read(segment, exc)
        finally:
            if close_after:
                await conn.close()

    def _segment_read_file_missing(self, segment: SegmentRecord) -> bool:
        """True, wenn die zu lesende Segment-Datei nicht (mehr) existiert (#951, Pkt 2).

        Das aktive Segment wird über die gehaltene Connection gelesen und nie als
        fehlend behandelt. Legacy-Segmente liegen als absoluter Pfad in ``filename``,
        v2-Segmente unter ``segments/``.
        """
        if self._active_segment is not None and segment.segment_id == self._active_segment.segment_id:
            return False
        path = Path(segment.filename) if _is_legacy_segment(segment) else self._segments_dir / segment.filename
        return not path.exists()

    async def _skip_or_quarantine_read(self, segment: SegmentRecord, exc: aiosqlite.Error) -> None:
        """Überspringt ein zwischenzeitlich verschwundenes Segment, quarantäniert echte Korruption.

        Race (#951, Pkt 2): ist die Datei zwischen Manifest-Auswahl und Open/Read
        weggeräumt worden (Retention-Delete), wird das Segment sauber übersprungen
        statt 500 zu werfen. Sonst greift der reguläre Korruptions-/Quarantäne-Pfad.
        """
        if self._segment_read_file_missing(segment):
            return None
        return await self._quarantine_corrupt_read(segment, exc)

    async def _quarantine_corrupt_read(self, segment: SegmentRecord, exc: aiosqlite.Error) -> None:
        """Quarantäniert ein beim Read als korrupt erkanntes Segment und überspringt es.

        Nur echte SQLite-Korruption (malformed/not a database/…) führt zur
        Quarantäne; andere ``aiosqlite.Error`` werden weitergereicht, damit echte
        Fehler (z. B. Programmierfehler im SQL) nicht als Korruption maskiert werden.
        Das aktive Segment wird nie quarantäniert.
        """
        if not _is_sqlite_corruption(exc):
            raise exc
        if self._active_segment is not None and segment.segment_id == self._active_segment.segment_id:
            raise exc
        await self.manifest.mark_quarantined(segment.segment_id, reason=str(exc))
        return None

    async def _connection_for_read(self, segment: SegmentRecord) -> aiosqlite.Connection:
        if self._active_segment is not None and segment.segment_id == self._active_segment.segment_id and self._active_conn is not None:
            return self._active_conn
        if _is_legacy_segment(segment):
            return await self._open_legacy_read_conn(segment)
        # v2-Segment (#951, Pkt 2): read-only-URI (``mode=ro``) statt schreibendem
        # ``connect``. Ein schreibendes Open auf eine zwischenzeitlich gelöschte
        # Datei legte still eine leere Ersatz-DB an → „no such table" → 500. ``mode=ro``
        # wirft in dem Fall (der Aufrufer überspringt das Segment). Zusätzlich werden
        # geschlossene Segmente so nie versehentlich schreibend geöffnet.
        uri = f"file:{(self._segments_dir / segment.filename).as_posix()}?mode=ro"
        conn = await aiosqlite.connect(uri, uri=True)
        conn.row_factory = aiosqlite.Row
        return conn

    async def _open_legacy_read_conn(self, segment: SegmentRecord) -> aiosqlite.Connection:
        """Öffnet eine Legacy-Single-DB read-only für den degradierenden Read-Zweig (#934).

        Legacy-Datei liegt in place (absoluter Pfad im ``filename``), NICHT unter
        ``segments/``.

        Dirty-WAL-Handling (#951, Pkt 4): ``immutable=1`` ignoriert committete
        WAL-Frames — jüngste Alt-Einträge einer dirty-WAL-Legacy-DB verschwänden
        sonst aus dem Read. Für **kleine** Legacy-DBs (unter ``SMALL_MAX_BYTES``)
        mit dirty WAL wird daher EINMAL sauber gecheckpointet (committete Frames in
        die Haupt-DB übernehmen), danach read-only gelesen. Für **große** Dateien
        bleibt es beim ``immutable=1``-Pfad (kein Startup-Checkpoint auf riesiger
        Datei), auch wenn dadurch die WAL-Frames ungelesen bleiben.
        """
        legacy_path = Path(segment.filename)
        if segment.recovery_status == "dirty_wal" and self._legacy_is_small(segment, legacy_path):
            if await self._checkpoint_small_legacy(legacy_path):
                # Checkpoint hat die committeten WAL-Frames in die Haupt-DB gefaltet/
                # getruncatet (#951, Codex :758). Die im Manifest hinterlegte
                # pre-checkpoint-``size_bytes`` überschätzt jetzt die reale Disk-Nutzung
                # (Phantom-WAL-Bytes) und ``dirty_wal`` würde bei jedem weiteren Read
                # erneut einen Checkpoint auslösen. Beides mit dem REALEN post-checkpoint-
                # Zustand nachziehen – analog zum v2-Rotations-Checkpoint-Größen-Refresh.
                await self.manifest.mark_legacy_wal_recovered(
                    segment.segment_id,
                    size_bytes=self._segment_file_size(segment.filename),
                )
        uri = f"file:{legacy_path.as_posix()}?mode=ro&immutable=1"
        conn = await aiosqlite.connect(uri, uri=True)
        conn.row_factory = aiosqlite.Row
        return conn

    @staticmethod
    def _legacy_is_small(segment: SegmentRecord, legacy_path: Path) -> bool:
        """True, wenn die Legacy-DB unter dem ``SMALL_MAX_BYTES``-Schwellwert liegt (#951, Pkt 4).

        Nutzt die im Manifest hinterlegte ``size_bytes``; fällt bei fehlender/0-Größe
        auf die tatsächliche Dateigröße zurück. Der Schwellwert wird lazy importiert,
        weil ``migration`` seinerseits ``sqlite_backend`` importiert (Zyklusvermeidung).
        """
        from obs.ringbuffer.store.migration import SMALL_MAX_BYTES

        size = segment.size_bytes if segment.size_bytes > 0 else _safe_getsize(legacy_path)
        return size < SMALL_MAX_BYTES

    @staticmethod
    async def _checkpoint_small_legacy(legacy_path: Path) -> bool:
        """Einmaliger sauberer WAL-Checkpoint einer kleinen Legacy-DB (#951, Pkt 4).

        Öffnet die Datei genau einmal schreibbar, checkpointet die committeten
        WAL-Frames per ``wal_checkpoint(TRUNCATE)`` in die Haupt-DB und schließt
        wieder. Danach liest der reguläre read-only-Pfad die vollständigen Daten.
        Fehler (z. B. read-only-Filesystem) werden geschluckt — der Read degradiert
        dann auf den ``immutable=1``-Pfad statt zu brechen.

        WICHTIG (#951, Codex :854): ``wal_checkpoint(TRUNCATE)`` wirft bei einem
        BUSY-Checkpoint NICHT, sondern meldet den Fall in der ERGEBNIS-ZEILE
        ``(busy, log, checkpointed)`` mit ``busy=1``. Ohne Auswertung dieser Zeile
        würde ``True`` zurückgegeben, obwohl die committeten WAL-Frames NICHT in die
        Haupt-DB übernommen wurden. Der Aufrufer markierte dann ``recovered`` und läse
        anschließend mit ``immutable=1`` — die nicht-gecheckpointeten Frames blieben
        dauerhaft unsichtbar. Daher gilt nur ``busy == 0`` als Erfolg; bei ``busy != 0``
        wird wie bei einem Checkpoint-Fehler degradiert (kein ``recovered``-Mark).

        Liefert ``True`` NUR bei vollständigem Checkpoint (der Aufrufer frischt dann
        Manifest-Größe + Recovery-Status auf, #951 Codex :758), ``False`` bei Fehler
        oder BUSY.
        """
        try:
            conn = await aiosqlite.connect(str(legacy_path))
            try:
                async with conn.execute("PRAGMA wal_checkpoint(TRUNCATE)") as cur:
                    row = await cur.fetchone()
                await conn.commit()
            finally:
                await conn.close()
        except aiosqlite.Error:
            return False
        # Ergebnis-Zeile (busy, log, checkpointed): busy != 0 → nicht vollständig
        # (gleiche Auswertung wie in ``_try_truncate_checkpoint``).
        return not (row is not None and row[0] != 0)

    async def _query_segment(self, conn: aiosqlite.Connection, query: StoreQuery) -> list[dict[str, Any]]:
        sql, params = self._build_segment_sql(query)
        if any(str(f.get("operator", "")).strip().lower() == "regex" for f in query.value_filters):
            # REGEXP-Callback nur registrieren, wenn ein Regex-Filter vorliegt.
            # Registrierung erfolgt lokal auf der übergebenen Read-Connection.
            await conn.create_function("obs_regexp", 3, _obs_regexp_impl, deterministic=True)
        if self._has_json_eq_filter(query.value_filters):
            # JSON-eq/ne-Callback nur registrieren, wenn ein komplexer (list/dict)
            # eq/ne-Filterwert vorliegt (#951, Codex :1281).
            await conn.create_function("obs_json_eq", 2, _obs_json_eq_impl, deterministic=True)
        if self._has_icontains_filter(query.value_filters):
            # Unicode-fähigen contains-Callback nur registrieren, wenn ein
            # case-insensitives ``contains`` vorliegt (#951, Codex :1364).
            await conn.create_function("obs_icontains", 2, _obs_icontains_impl, deterministic=True)
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [self._row_to_dict(row) for row in rows]

    async def _query_legacy_segment(
        self,
        conn: aiosqlite.Connection,
        segment: SegmentRecord,
        query: StoreQuery,
    ) -> list[dict[str, Any]]:
        """Degradierender Read-Zweig für eine v1/Legacy-Single-DB (#934).

        Legacy-Segmente haben weder ``global_event_id`` noch typisierte Wertspalten:

        * **Ordering** wird aus ``ts`` + segment-lokaler rowid ``id`` abgeleitet
          (neueste zuerst) und in einen synthetischen, streng **negativen**
          ``global_event_id`` übersetzt. Damit sortieren alle Legacy-Zeilen unter
          jeder v2-Zeile (positive IDs) — Legacy-Daten sind per Definition älter als
          jedes nach Aktivierung geschriebene v2-Segment — und behalten intern ihre
          ts/rowid-Ordnung.
        * **Value-Filter** werden NICHT typisiert in SQL gepusht (die Spalten fehlen),
          sondern kontrolliert **bounded** in Python auf den dekodierten JSON-Werten
          ausgewertet. Der Kandidatensatz ist auf ``candidate_cap`` bzw. einen Default-
          Cap begrenzt, damit ein Value-Filter über Legacy nicht in einen unbounded
          Full-Scan über 20–30 GB kippt.
        """
        clauses, params = self._time_where(query)
        if query.datapoint_id is not None:
            clauses.append("datapoint_id = ?")
            params.append(query.datapoint_id)
        if query.datapoint_ids:
            placeholders = ",".join("?" * len(query.datapoint_ids))
            clauses.append(f"datapoint_id IN ({placeholders})")
            params.extend(query.datapoint_ids)
        if query.source_adapter is not None:
            clauses.append("source_adapter = ?")
            params.append(query.source_adapter)
        if query.source_adapters:
            placeholders = ",".join("?" * len(query.source_adapters))
            clauses.append(f"source_adapter IN ({placeholders})")
            params.extend(query.source_adapters)
        if query.quality is not None:
            clauses.append("quality = ?")
            params.append(query.quality)
        q_clause, q_params = self._free_text_clause(query)
        if q_clause:
            clauses.append(q_clause)
            params.extend(q_params)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        # Metadaten-Tag/Binding-Filter kann eine v1/Legacy-DB nicht per Index-
        # Subquery bedienen (die Index-Tabellen fehlen dort). Sie werden bounded
        # in Python auf den dekodierten metadata-JSON ausgewertet — wie die
        # Value-Filter. Beides zusammen erzwingt den Kandidaten-Cap.
        has_python_post_filter = bool(query.value_filters) or self._has_metadata_filter(query)
        if has_python_post_filter:
            fetch_limit = self._legacy_candidate_cap(query)
        else:
            fetch_limit = max(query.offset, 0) + max(query.limit, 0)
        # Fetch-Richtung an die gewünschte Sortierung koppeln, damit die gebundene
        # Kandidatenmenge die RICHTIGEN Extremwerte enthält: bei ``asc`` die
        # ältesten, sonst die neuesten. Sonst liefert eine große Legacy-DB (mehr
        # Zeilen als der Cap) bei ``sort=ts asc`` fälschlich die neuesten statt der
        # ältesten Zeilen (die echte älteste Zeile liegt dann außerhalb des Caps).
        direction = "ASC" if query.sort_order == "asc" else "DESC"
        # Kandidaten-Ordnung muss zur FINALEN Sortierung passen (#951, Pkt 2): der
        # synthetische ``global_event_id`` einer Legacy-Zeile leitet sich aus der
        # rowid (``id``) ab. Bei ``sort_field='id'`` (Default) sortiert die Query
        # final nach id – also müssen die Kandidaten ebenfalls per id gedeckelt
        # werden. Würde hier immer nach ``ts`` limitiert, schlössen out-of-order-
        # Timestamps (eine hohe rowid mit frühem ts) genau die höchsten rowids aus,
        # die der finale id-Sort dann nie mehr einholt. Nur bei ``sort_field='ts'``
        # ist die ts-Ordnung die richtige Kandidatengrenze.
        candidate_order = "ts" if query.sort_field == "ts" else "id"
        # pre-#388 Legacy-Schema (#951, Pkt 1): eine sehr alte Single-DB hat noch
        # KEINE ``metadata_version``/``metadata``-Spalten. Ein bedingungsloses
        # SELECT dieser Spalten scheiterte mit „no such column" und machte die
        # gesamte Alt-Historie unlesbar. Die Spalten werden daher nur selektiert,
        # wenn sie existieren; fehlen sie, liefert ``_legacy_row_to_dict`` die
        # Defaults (``metadata_version=1``, ``metadata={}``).
        has_metadata_cols = await self._legacy_has_metadata_columns(conn)
        metadata_select = "metadata_version, metadata" if has_metadata_cols else "NULL AS metadata_version, NULL AS metadata"
        sql = (
            "SELECT id, ts, datapoint_id, topic, old_value, new_value, "
            f"source_adapter, quality, {metadata_select} "
            f"FROM ringbuffer{where} ORDER BY {candidate_order} {direction}, id {direction} LIMIT ?"
        )
        params.append(fetch_limit)
        async with conn.execute(sql, params) as cur:
            raw_rows = await cur.fetchall()

        results: list[dict[str, Any]] = []
        for row in raw_rows:
            record = self._legacy_row_to_dict(row, segment.segment_id)
            if query.value_filters and not _legacy_row_matches_filters(record, query.value_filters):
                continue
            if not self._legacy_metadata_matches(record, query):
                continue
            results.append(record)
        return results

    @staticmethod
    def _has_metadata_filter(query: StoreQuery) -> bool:
        return bool(query.metadata_tags_any_of) or any(query.metadata_binding_filters.values())

    def _legacy_metadata_matches(self, record: dict[str, Any], query: StoreQuery) -> bool:
        """Python-Auswertung der Metadaten-Tag/Binding-Filter für Legacy-Zeilen.

        Semantik wie die v2-EXISTS-Subquery: Tags OR-verknüpft, Binding-Spalten je
        als OR innerhalb der Spalte und verschiedene Spalten AND-verknüpft. Alle
        Vergleiche laufen normalisiert (getrimmt, lowercase) — identisch zu den
        beim Append befüllten Index-Zeilen.
        """
        metadata = record.get("metadata") or {}
        if query.metadata_tags_any_of:
            row_tags = set(_extract_metadata_tags(metadata))
            if not row_tags.intersection(query.metadata_tags_any_of):
                return False
        active_columns = {col: vals for col, vals in query.metadata_binding_filters.items() if vals}
        if active_columns:
            index = {col: idx for idx, col in enumerate(_BINDING_INDEX_COLUMNS)}
            positions = {col: index[col] for col in active_columns if col in index}
            if len(positions) != len(active_columns):
                # Eine angefragte Spalte existiert nicht im Binding-Index → nie erfüllbar.
                return False
            binding_rows = _extract_metadata_binding_index_rows(metadata)
            # Parität zur v2-EXISTS-Subquery (#951, Pkt 5): EINE einzelne Binding-
            # Zeile muss ALLE angefragten Spalten erfüllen. Zuvor wurde jede Spalte
            # unabhängig gegen IRGENDEINE Zeile geprüft, sodass mehrere verschiedene
            # Zeilen die Bedingungen zusammen erfüllen konnten.
            if not any(all(binding[pos] in active_columns[col] for col, pos in positions.items()) for binding in binding_rows):
                return False
        return True

    @staticmethod
    def _legacy_candidate_cap(query: StoreQuery) -> int:
        """Gebundener Kandidaten-Cap für Legacy-Value-Filter (kein Full-Scan)."""
        if query.candidate_cap is not None and query.candidate_cap > 0:
            return query.candidate_cap
        return _LEGACY_DEFAULT_CANDIDATE_CAP

    @staticmethod
    async def _legacy_has_metadata_columns(conn: aiosqlite.Connection) -> bool:
        """True, wenn die Legacy-``ringbuffer``-Tabelle ``metadata``-Spalten trägt (#951, Pkt 1).

        pre-#388 Single-DBs haben die ``metadata_version``/``metadata``-Spalten noch
        nicht. Ein bedingungsloses SELECT dieser Spalten würde mit „no such column"
        scheitern und die komplette Alt-Historie unlesbar machen. Erkennung über
        ``PRAGMA table_info`` (analog zum alten ``_ensure_compat_schema``), damit
        der Read-Zweig fehlende Spalten als Defaults liefern kann.
        """
        async with conn.execute("PRAGMA table_info(ringbuffer)") as cur:
            columns = {row["name"] for row in await cur.fetchall()}
        return {"metadata_version", "metadata"}.issubset(columns)

    @staticmethod
    def _legacy_row_to_dict(row: aiosqlite.Row, segment_id: int) -> dict[str, Any]:
        # Synthetischer global_event_id aus der chronologischen Legacy-rowid
        # (``id``): fetch-richtungsunabhängig, streng negativ (unter allen v2-IDs)
        # und rowid-monoton — höhere rowid (neuer) ⇒ höhere (weniger negative) ID.
        # ``segment_id`` bricht Gleichstände zwischen mehreren Legacy-Segmenten.
        synthetic_gid = int(row["id"]) - _LEGACY_GID_OFFSET - (segment_id & 0xFFFF)
        # pre-#388 Legacy-Schema (#951, Pkt 1): fehlt die metadata-Spalte, liefert
        # das SELECT NULL → Default ``metadata_version=1``/``metadata={}``.
        metadata_version = row["metadata_version"] if row["metadata_version"] is not None else 1
        return {
            "global_event_id": synthetic_gid,
            "ts": row["ts"],
            "datapoint_id": row["datapoint_id"],
            "topic": row["topic"],
            # Safe decode (#951, Pkt 3): malformed Legacy-JSON bricht die Query nicht,
            # sondern liefert den Rohwert. metadata bleibt best-effort JSON-Objekt.
            "old_value": _safe_json_decode(row["old_value"]),
            "new_value": _safe_json_decode(row["new_value"]),
            "source_adapter": row["source_adapter"],
            "quality": row["quality"],
            "metadata_version": metadata_version,
            "metadata": _legacy_metadata_decode(row["metadata"]),
        }

    def _build_segment_sql(self, query: StoreQuery) -> tuple[str, list[Any]]:
        """Baut das segment-lokale SELECT inkl. gepushter Wertfilter.

        Einfache Wertfilter (eq/ne/gt/gte/lt/lte/between) landen als typisiertes
        WHERE-Prädikat, damit ``LIMIT`` NICHT durch einen Python-Post-Filter
        ausgehebelt wird. ``contains``/``regex`` werden als SQL-``LIKE`` bzw.
        ``REGEXP``-taugliches Prädikat nur zugelassen, wenn der Query gebunden ist
        (Zeitfenster oder ``candidate_cap``); sonst ``ValueError`` (422-tauglich).

        Boundedness von contains/regex OHNE Zeitfenster (#951, Pkt 4): der teure
        Match (``instr``/``obs_regexp``-Callback) würde als inline-WHERE-Prädikat
        JEDE Zeile jedes Segments berühren, bis ``offset+limit`` Treffer gesammelt
        sind – bei seltenem/fehlendem Treffer also einen Full-Scan. Ist der Query
        nur per ``candidate_cap`` gebunden (kein Zeitfenster), wird der Match daher
        AUF EINE gedeckelte Kandidatenmenge angewandt: die neuesten ``candidate_cap``
        Zeilen (nach ``order_by``) bilden eine innere Subquery, der Match filtert nur
        diese. So bleibt der Scan hart auf ``candidate_cap`` Zeilen je Segment
        begrenzt. Preis der Deckelung: passt ein Treffer erst JENSEITS der neuesten
        ``candidate_cap`` Zeilen, wird er nicht mehr gefunden – das ist die
        dokumentierte, gewollte Begrenzung eines unwindowed contains/regex.
        """
        clauses, params = self._common_where(query)
        guarded_specs = [s for s in query.value_filters if str(s.get("operator", "")).strip().lower() in _GUARDED_OPERATORS]
        cheap_specs = [s for s in query.value_filters if s not in guarded_specs]
        # Nicht-Guarded Filter (typisierter Pushdown) sind billig und bleiben inline.
        for spec in cheap_specs:
            clause, filter_params = self._value_filter_clause(spec, query)
            clauses.append(clause)
            params.extend(filter_params)

        order_by = self._segment_order_by(query)
        final_limit = max(query.offset, 0) + max(query.limit, 0)

        # Guarded Filter validieren (wirft bei ungebundenem Query) und Klauseln bauen.
        guarded_clauses: list[str] = []
        guarded_params: list[Any] = []
        for spec in guarded_specs:
            clause, filter_params = self._value_filter_clause(spec, query)
            guarded_clauses.append(clause)
            guarded_params.extend(filter_params)

        select_cols = "global_event_id, ts, datapoint_id, topic, old_value, new_value, source_adapter, quality, metadata_version, metadata"

        # Der teure Match wird nur dann auf eine gedeckelte Kandidaten-Subquery
        # gelegt, wenn der Query ausschließlich per candidate_cap (ohne Zeitfenster)
        # gebunden ist. Mit Zeitfenster bindet bereits das WHERE den Scan.
        if guarded_clauses and not self._query_is_windowed(query):
            cap = self._effective_candidate_cap(query)
            inner_where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
            # Die innere Subquery muss auch die typisierten Text-Spalten
            # durchreichen, auf denen die Guarded-Prädikate (instr/obs_regexp)
            # arbeiten – sonst kennt die äußere Query ``*_value_text`` nicht.
            inner_cols = f"{select_cols}, old_value_text, new_value_text"
            inner_sql = f"SELECT {inner_cols} FROM ringbuffer{inner_where} ORDER BY {order_by} LIMIT ?"
            outer_where = " AND ".join(guarded_clauses)
            sql = f"SELECT {select_cols} FROM ({inner_sql}) AS capped WHERE {outer_where} ORDER BY {order_by} LIMIT ?"
            return sql, [*params, cap, *guarded_params, final_limit]

        # Mit Zeitfenster (oder ohne Guarded-Filter): alle Klauseln inline.
        clauses.extend(guarded_clauses)
        params.extend(guarded_params)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT {select_cols} FROM ringbuffer{where} ORDER BY {order_by} LIMIT ?"
        params.append(final_limit)
        return sql, params

    @staticmethod
    def _time_where(query: StoreQuery) -> tuple[list[str], list[Any]]:
        """Zeitfenster-Prädikate, inklusiv per Default, exklusiv wenn gefordert.

        Der Store ist per Default inklusiv (``>=``/``<=``). Der segmentierte Read-
        Pfad setzt ``from_exclusive``/``to_exclusive`` für die Legacy-``query_v2``-
        Semantik (``ts > from``, ``ts < to``).
        """
        clauses: list[str] = []
        params: list[Any] = []
        if query.from_ts is not None:
            clauses.append("ts > ?" if query.from_exclusive else "ts >= ?")
            params.append(query.from_ts)
        if query.to_ts is not None:
            clauses.append("ts < ?" if query.to_exclusive else "ts <= ?")
            params.append(query.to_ts)
        return clauses, params

    def _common_where(self, query: StoreQuery) -> tuple[list[str], list[Any]]:
        """Baut die von v2- und (soweit anwendbar) Legacy-Segment geteilten WHERE-Prädikate.

        Deckt Zeitfenster, Ein-Wert-Kern (datapoint_id/source_adapter/quality),
        additive ``IN (...)``-Listen (mehrere datapoint_ids/adapter), den
        Freitext-``q``/``dp_ids_by_name``-OR-Block sowie Metadaten-Tag/Binding-
        Filter als ``EXISTS``-Subquery ab (Semantik wie Legacy ``query_v2``).
        """
        clauses, params = self._time_where(query)
        if query.datapoint_id is not None:
            clauses.append("datapoint_id = ?")
            params.append(query.datapoint_id)
        if query.datapoint_ids:
            placeholders = ",".join("?" * len(query.datapoint_ids))
            clauses.append(f"datapoint_id IN ({placeholders})")
            params.extend(query.datapoint_ids)
        if query.source_adapter is not None:
            clauses.append("source_adapter = ?")
            params.append(query.source_adapter)
        if query.source_adapters:
            placeholders = ",".join("?" * len(query.source_adapters))
            clauses.append(f"source_adapter IN ({placeholders})")
            params.extend(query.source_adapters)
        if query.quality is not None:
            clauses.append("quality = ?")
            params.append(query.quality)
        q_clause, q_params = self._free_text_clause(query)
        if q_clause:
            clauses.append(q_clause)
            params.extend(q_params)
        meta_clause, meta_params = self._metadata_clause(query)
        if meta_clause:
            clauses.append(meta_clause)
            params.extend(meta_params)
        return clauses, params

    @staticmethod
    def _free_text_clause(query: StoreQuery) -> tuple[str | None, list[Any]]:
        """OR-Block für Freitext-``q`` (LIKE) + ``dp_ids_by_name`` (IN) — Legacy-Semantik."""
        parts: list[str] = []
        params: list[Any] = []
        q = (query.q or "").strip()
        if q:
            parts.append("datapoint_id LIKE ?")
            params.append(f"%{q}%")
            parts.append("source_adapter LIKE ?")
            params.append(f"%{q}%")
        if query.dp_ids_by_name:
            placeholders = ",".join("?" * len(query.dp_ids_by_name))
            parts.append(f"datapoint_id IN ({placeholders})")
            params.extend(query.dp_ids_by_name)
        if not parts:
            return None, []
        return f"({' OR '.join(parts)})", params

    @staticmethod
    def _metadata_clause(query: StoreQuery) -> tuple[str | None, list[Any]]:
        """EXISTS-Subqueries für Metadaten-Tags/Bindings (Semantik wie Legacy)."""
        clauses: list[str] = []
        params: list[Any] = []
        tags = query.metadata_tags_any_of
        if tags:
            placeholders = ",".join("?" * len(tags))
            clauses.append(f"EXISTS (SELECT 1 FROM ringbuffer_metadata_tags rmt WHERE rmt.entry_id = ringbuffer.id AND rmt.tag IN ({placeholders}))")
            params.extend(tags)
        binding_clauses: list[str] = []
        binding_params: list[Any] = []
        for column, values in query.metadata_binding_filters.items():
            if not values:
                continue
            placeholders = ",".join("?" * len(values))
            binding_clauses.append(f"rmb.{column} IN ({placeholders})")
            binding_params.extend(values)
        if binding_clauses:
            clauses.append(
                f"EXISTS (SELECT 1 FROM ringbuffer_metadata_bindings rmb WHERE rmb.entry_id = ringbuffer.id AND {' AND '.join(binding_clauses)})"
            )
            params.extend(binding_params)
        if not clauses:
            return None, []
        return " AND ".join(clauses), params

    @staticmethod
    def _segment_order_by(query: StoreQuery) -> str:
        """Per-Segment ``ORDER BY`` passend zur gewünschten Sortierung.

        Der Store liefert je Segment die ``offset+limit`` **relevanten** Zeilen in
        der Zielordnung, damit der finale Merge in ``query()`` bounded bleibt.
        """
        direction = "ASC" if query.sort_order == "asc" else "DESC"
        if query.sort_field == "ts":
            return f"ts {direction}, global_event_id {direction}"
        return f"global_event_id {direction}"

    @staticmethod
    def _query_is_windowed(query: StoreQuery) -> bool:
        """True, wenn ein beidseitiges Zeitfenster den Scan bereits bindet."""
        return query.from_ts is not None and query.to_ts is not None

    @staticmethod
    def _query_is_bounded(query: StoreQuery) -> bool:
        """contains/regex nur mit engem Zeitfenster oder Kandidaten-Cap zulassen."""
        has_cap = query.candidate_cap is not None and query.candidate_cap > 0
        return SqliteSegmentStore._query_is_windowed(query) or has_cap

    @staticmethod
    def _effective_candidate_cap(query: StoreQuery) -> int:
        """Harte Zeilenobergrenze für den unwindowed Guarded-Scan je Segment (#951, Pkt 4).

        Nutzt den vom Aufrufer gesetzten ``candidate_cap``; fällt auf den Legacy-
        Default zurück, falls (wider Erwartung) keiner gesetzt ist. So bleibt der
        teure Match auf höchstens diese Zeilenzahl je Segment begrenzt.
        """
        if query.candidate_cap is not None and query.candidate_cap > 0:
            return query.candidate_cap
        return _LEGACY_DEFAULT_CANDIDATE_CAP

    def _value_filter_clause(self, spec: dict[str, Any], query: StoreQuery) -> tuple[str, list[Any]]:
        """Übersetzt einen engine-neutralen Wertfilter in ein SQL-WHERE-Prädikat."""
        operator = str(spec.get("operator", "")).strip().lower()
        if operator not in _VALID_OPERATORS:
            raise ValueError(f"invalid value filter operator: {operator!r}")
        field_name = str(spec.get("field", "new_value")).strip().lower()
        if field_name not in _FILTER_FIELDS:
            raise ValueError(f"invalid value filter field: {field_name!r}")

        if operator in _GUARDED_OPERATORS:
            if not self._query_is_bounded(query):
                raise ValueError(f"operator '{operator}' requires a bounded query (from_ts+to_ts or candidate_cap)")
            return self._guarded_clause(operator, field_name, spec)
        return self._pushdown_clause(operator, field_name, spec)

    @staticmethod
    def _pushdown_clause(operator: str, field_name: str, spec: dict[str, Any]) -> tuple[str, list[Any]]:
        num_col = f"{field_name}_num"

        if operator == "between":
            lower = spec.get("lower")
            upper = spec.get("upper")
            lo = _typed_columns_for(lower)
            up = _typed_columns_for(upper)
            if lo[0] != "numeric" or up[0] != "numeric":
                raise ValueError("between requires numeric lower/upper bounds")
            if lo[1] > up[1]:
                raise ValueError("value filter lower must be <= upper")
            return (f"({num_col} IS NOT NULL AND {num_col} BETWEEN ? AND ?)", [lo[1], up[1]])

        value = spec.get("value")
        comparator = _SQL_COMPARATORS[operator]

        # ``value is None`` (JSON-null) ist für eq/ne KEIN Fehler mehr (#951, Pkt 4):
        # Legacy vergleicht ``value == None`` direkt, ``eq null`` liefert die Zeilen
        # mit JSON-null, ``ne null`` deren Inverses. Range-Operatoren auf null bleiben
        # sinnlos → wie Legacy (unsupported) abgelehnt.
        if value is None:
            if operator == "eq":
                return (f"{field_name}_type = 'null'", [])
            if operator == "ne":
                return (f"{field_name}_type != 'null'", [])
            raise ValueError(f"operator '{operator}' is not supported for null value")

        value_type, num, text, bool_val = _typed_columns_for(value)

        # Range-Operatoren sind wie Legacy nur für numerische Werte definiert. Ein
        # gt/gte/lt/lte gegen einen text-/bool-Vergleichswert würde sonst zu einem
        # lexikografischen Text- bzw. 0/1-Bool-Vergleich degradieren (#951, Pkt 3);
        # Legacy lehnt Range auf STRING/BOOLEAN ab (422-tauglicher ValueError).
        if operator in {"gt", "gte", "lt", "lte"} and value_type != "numeric":
            data_type = "BOOLEAN" if value_type == "bool" else "STRING"
            raise ValueError(f"operator '{operator}' is not supported for data_type '{data_type}'")

        # eq/ne (#951, Pkt 1): Legacy wertet reine Python-Gleichheit ``value == row``
        # aus. Das ist typübergreifend — inkl. der Python-Äquivalenz ``True == 1`` /
        # ``False == 0`` — und schließt bei ``ne`` Zeilen anderen Typs sowie null EIN.
        # ``_eq_match_predicate`` liefert genau das „Zeile gleicht value"-Prädikat;
        # ``ne`` ist dessen Negation (NULL-sicher, sodass null-Zeilen mit-matchen).
        if operator in {"eq", "ne"}:
            eq_clause, eq_params = SqliteSegmentStore._eq_match_predicate(field_name, value)
            if operator == "eq":
                return (eq_clause, eq_params)
            return (f"NOT ({eq_clause})", eq_params)

        # numerische Range-Operatoren: Vergleich gegen die numerische Spalte. Nicht-
        # numerische Range-Werte wurden oben bereits abgelehnt → value_type == numeric.
        return (f"({num_col} IS NOT NULL AND {num_col} {comparator} ?)", [num])

    @staticmethod
    def _has_json_eq_filter(value_filters: list[dict[str, Any]]) -> bool:
        """True, wenn ein ``eq``/``ne``-Filter einen komplexen (list/dict) Wert trägt.

        Nur dann muss der ``obs_json_eq``-Callback auf der Read-Connection registriert
        werden (#951, Codex :1281). Skalare eq/ne pushen weiter über typisierte Spalten.
        """
        for spec in value_filters:
            if str(spec.get("operator", "")).strip().lower() not in {"eq", "ne"}:
                continue
            if _derive_value_type(spec.get("value")) == "json":
                return True
        return False

    @staticmethod
    def _has_icontains_filter(value_filters: list[dict[str, Any]]) -> bool:
        """True, wenn ein ``contains``-Filter mit ``ignore_case`` vorliegt.

        Nur dann muss der Unicode-fähige ``obs_icontains``-Callback auf der Read-
        Connection registriert werden (#951, Codex :1364).
        """
        for spec in value_filters:
            if str(spec.get("operator", "")).strip().lower() != "contains":
                continue
            if bool(spec.get("ignore_case", False)):
                return True
        return False

    @staticmethod
    def _eq_match_predicate(field_name: str, value: Any) -> tuple[str, list[Any]]:
        """SQL-Prädikat, das genau dann wahr ist, wenn die Zeile Python-``== value`` ist.

        Spiegelt Legacy ``value == row`` inkl. der Python-Bool/Int-Äquivalenz
        (``True == 1``, ``False == 0``): ein bool-Filter matcht auch die numerische
        0/1-Zeile und umgekehrt. Textwerte matchen nur die Text-Spalte. Das Ergebnis
        ist NULL-sicher, damit die Negation (``ne``) null-Zeilen einschließt.
        """
        num_col = f"{field_name}_num"
        text_col = f"{field_name}_text"
        bool_col = f"{field_name}_bool"
        value_type, num, text, bool_val = _typed_columns_for(value)

        # Jeder Teilvergleich wird via IFNULL(..., 0) auf ein definites 0/1
        # reduziert, damit das Prädikat für Nicht-Treffer 0 (nicht NULL) ergibt und
        # die ``ne``-Negation (``NOT (...)``) null-Spalten korrekt als Treffer wertet.
        if value_type == "text":
            return (f"IFNULL({text_col} = ?, 0)", [text])
        if value_type == "bool":
            # bool matcht die bool-Spalte UND — wegen True==1/False==0 — die
            # numerische 1/0-Spalte.
            return (f"(IFNULL({bool_col} = ?, 0) OR IFNULL({num_col} = ?, 0))", [bool_val, float(bool_val)])
        if value_type == "numeric":
            # numeric matcht die num-Spalte; für exakt 0/1 zusätzlich die bool-Spalte.
            if num in (0.0, 1.0):
                return (f"(IFNULL({num_col} = ?, 0) OR IFNULL({bool_col} = ?, 0))", [num, int(num)])
            return (f"IFNULL({num_col} = ?, 0)", [num])
        # value_type == "json" (list/dict): kein 422 mehr (#951, Codex :1281). Der
        # Legacy-Referenzfilter verglich Python-Werte direkt (``actual == expected``);
        # ``eq`` auf dasselbe Objekt/Array matchte, ``ne`` lieferte das Inverse. Hier
        # gegen die gespeicherte volle JSON-Spalte (``{field_name}``) vergleichen –
        # kanonisch (sortierte Keys) über den ``obs_json_eq``-Callback, sodass gleiche
        # Objekte unabhängig von der Key-Reihenfolge treffen. NULL-sicher via IFNULL,
        # damit die ``ne``-Negation null-/andere-Typ-Zeilen einschließt.
        return (f"IFNULL(obs_json_eq({field_name}, ?), 0)", [_canonical_json(value)])

    @staticmethod
    def _guarded_clause(operator: str, field_name: str, spec: dict[str, Any]) -> tuple[str, list[Any]]:
        text_col = f"{field_name}_text"
        ignore_case = bool(spec.get("ignore_case", False))
        if operator == "contains":
            needle = spec.get("value")
            if not isinstance(needle, str):
                raise ValueError("contains requires a string value")
            # ``instr`` statt ``LIKE``: SQLite-``LIKE`` ist für ASCII per Default
            # case-INsensitiv (matcht ``Hello`` auf ``hello``), was der Legacy-
            # Python-Substring-Semantik widerspricht (#951, Pkt 2). ``instr`` ist ein
            # echter binärer Substring-Test — case-SENSITIV bei ignore_case=false —
            # und braucht kein LIKE-Escaping (``%``/``_`` sind keine Wildcards).
            if ignore_case:
                # SQLite-``LOWER()`` foldet auf Standard-Builds nur ASCII (#951, Codex
                # :1364): „HÄLLO"→„häll o" scheiterte, sodass Nicht-ASCII-Text nicht
                # matchte. Der Unicode-fähige Python-Callback (``.lower()``, analog
                # ``obs_regexp``) stellt Parität zum Legacy-Python-Pfad her.
                return (f"({text_col} IS NOT NULL AND obs_icontains(?, {text_col}) = 1)", [needle.lower()])
            return (f"({text_col} IS NOT NULL AND instr({text_col}, ?) > 0)", [needle])

        # regex: Muster härten (Referenz: Legacy _match_regex), dann als Python-
        # Callback über SQLite REGEXP pushen — der WHERE-Kontext bleibt gebunden.
        pattern = spec.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("regex requires a non-empty pattern")
        if len(pattern) > _REGEX_MAX_PATTERN_LEN:
            raise ValueError("unsafe regex pattern: pattern too long")
        if _RE_UNSAFE_NESTED_QUANTIFIERS.search(pattern):
            raise ValueError("unsafe regex pattern: nested quantifiers are not allowed")
        flags = re.IGNORECASE if ignore_case else 0
        try:
            re.compile(pattern, flags)
        except re.error as exc:  # pragma: no cover - message details vary per version
            raise ValueError(f"invalid regex pattern: {exc}") from exc
        return (f"({text_col} IS NOT NULL AND obs_regexp(?, ?, {text_col}) = 1)", [pattern, flags])

    @staticmethod
    def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "global_event_id": row["global_event_id"],
            "ts": row["ts"],
            "datapoint_id": row["datapoint_id"],
            "topic": row["topic"],
            "old_value": json.loads(row["old_value"]) if row["old_value"] is not None else None,
            "new_value": json.loads(row["new_value"]) if row["new_value"] is not None else None,
            "source_adapter": row["source_adapter"],
            "quality": row["quality"],
            "metadata_version": row["metadata_version"],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        }

    # ------------------------------------------------------------------
    # Backend-intern: Rotation
    # ------------------------------------------------------------------

    async def rotate(self) -> SegmentRecord:
        """Schließt das aktive Segment sauber und öffnet genau ein neues aktives.

        Rotation löscht keine Daten. Beim Close wird ``wal_checkpoint(TRUNCATE)``
        versucht; ist es busy (aktive Reader), wird das Segment als
        ``checkpoint_pending`` markiert statt als löschbar behandelt.
        """
        old_segment = self._active_segment
        old_conn = self._active_conn
        if old_segment is not None and old_conn is not None:
            await self._refresh_active_segment_stats()
            checkpoint_ok = await self._try_truncate_checkpoint(old_conn)
            await old_conn.close()
            await self.manifest.close_segment(old_segment.segment_id)
            if checkpoint_ok:
                # Erfolgreicher TRUNCATE (#951, Codex :1346): die WAL/SHM-Bytes sind
                # gerade in die Haupt-DB verschoben/getruncatet worden. Die von
                # _refresh_active_segment_stats gesetzte pre-checkpoint-Größe (inkl.
                # voller WAL) überschätzt jetzt die reale Disk-Nutzung. Größe daher
                # mit der REALEN post-checkpoint-Größe neu schreiben, BEVOR die direkt
                # folgende Retention greift – sonst löschte _enforce_size_budget()
                # WAL-schwere Segmente unnötig zusätzliche geschlossene/Legacy-Segmente.
                await self.manifest.update_segment_size(
                    old_segment.segment_id,
                    size_bytes=self._segment_file_size(old_segment.filename),
                )
            else:
                await self.manifest.mark_checkpoint_pending(old_segment.segment_id)
                # TODO(#936): Hintergrund-Checkpoint-Läufer räumt pending später ab.

        new_segment = await self._create_segment_locked()
        self._active_segment = new_segment
        self._active_conn = await self._open_segment_conn(new_segment.filename)
        return new_segment

    async def _try_truncate_checkpoint(self, conn: aiosqlite.Connection) -> bool:
        """Versucht ``wal_checkpoint(TRUNCATE)``. Liefert False bei busy.

        Hält die Checkpoint-Betriebsdetails (``last_checkpoint_*``, busy-Zähler)
        für die Support-/Admin-Stats fest. Reine SQLite-Interna → backend_extra.
        """
        async with conn.execute("PRAGMA wal_checkpoint(TRUNCATE)") as cur:
            row = await cur.fetchone()
        # PRAGMA-Ergebnis (busy, log, checkpointed): busy != 0 → nicht vollständig.
        ok = not (row is not None and row[0] != 0)
        self._last_checkpoint_at = _utc_now_iso()
        self._last_checkpoint_mode = "TRUNCATE"
        self._last_checkpoint_result = "ok" if ok else "busy"
        if not ok:
            self._wal_checkpoint_busy_count += 1
        return ok

    async def _refresh_active_segment_stats(self) -> None:
        if self._active_conn is None or self._active_segment is None:
            return
        async with self._active_conn.execute("SELECT COUNT(*) AS c, MIN(ts) AS mn, MAX(ts) AS mx FROM ringbuffer") as cur:
            row = await cur.fetchone()
        await self.manifest.update_segment_stats(
            self._active_segment.segment_id,
            row_count=row["c"] if row else 0,
            size_bytes=self._segment_file_size(self._active_segment.filename),
            from_ts=row["mn"] if row else None,
            to_ts=row["mx"] if row else None,
        )

    def _segment_file_size(self, filename: str) -> int:
        """Reale Disk-Nutzung eines v2-Segments inkl. ``-wal``/``-shm`` (#951, Pkt 5).

        ``size_bytes`` wandert ins Manifest und steuert ``_segment_rotation_due`` und
        ``enforce_retention``. Zählte es nur die ``*.sqlite``-Hauptdatei, könnte ein
        WAL-schweres aktives/pending Segment das harte Byte-Budget überschreiten, ohne
        zu rotieren oder korrekt zu melden. Die Sidecars ``-wal``/``-shm`` werden daher
        mitgezählt, damit Rotation und Budget die tatsächliche Disk-Nutzung sehen.
        """
        base = self._segments_dir / filename
        return _safe_getsize(base) + _safe_getsize(Path(f"{base}-wal")) + _safe_getsize(Path(f"{base}-shm"))

    # ------------------------------------------------------------------
    # Contract: stats
    # ------------------------------------------------------------------

    def _compute_prognosis(self, segments: list[SegmentRecord]) -> dict[str, Any]:
        """Datengetriebene Wachstums-/Retention-Prognose aus geschlossenen v2-Segmenten (#919).

        Reine Momentaufnahme: die Rate wird ausschließlich aus den geschlossenen
        v2-Segmenten (nicht Legacy, nicht aktiv) im Manifest geschätzt. Sie wird
        genauer, je mehr geschlossene Segmente vorliegen. Alle Felder fallen robust
        auf ``None`` zurück, wenn zu wenig Daten vorliegen (< 1 geschlossenes
        v2-Segment) oder eine Division durch 0 drohte.

        Felder:

        * ``sample_segment_count`` — Anzahl herangezogener geschlossener v2-Segmente.
        * ``bytes_per_hour`` / ``rows_per_hour`` — Rate = Summe(size/rows) /
          Summe(Segmentdauer aus ``from_ts``/``to_ts``) × 3600. None bei zu wenig Daten.
        * ``avg_segment_seconds`` — Ø-Segmentdauer (effektives Rotationsintervall).
        * ``estimated_retention_seconds`` — falls ``max_file_size_bytes`` gesetzt und
          ``bytes_per_hour > 0``: ``(max_file_size_bytes / bytes_per_hour) * 3600``;
          sonst None (unbegrenzt/unbekannt).
        * ``effective_segment_max_bytes`` — der effektiv wirksame (ggf. beim
          Auto-Start aus dem Size-Budget abgeleitete) Größen-Cap eines Segments
          (``self._segment_config.segment_max_bytes``); None, wenn kein Cap gilt.
          Die Budget-Empfehlung wird nicht mehr hier berechnet, sondern im
          Frontend, damit Label und Wert live beim Tippen zusammenpassen.
        """
        empty = {
            "sample_segment_count": 0,
            "bytes_per_hour": None,
            "rows_per_hour": None,
            "avg_segment_seconds": None,
            "estimated_retention_seconds": None,
            "effective_segment_max_bytes": self._segment_config.segment_max_bytes,
        }
        closed_v2 = [s for s in segments if s.status == SEGMENT_STATUS_CLOSED and not _is_legacy_segment(s)]
        if not closed_v2:
            return empty

        total_seconds = 0.0
        total_bytes = 0
        total_rows = 0
        for segment in closed_v2:
            from_ts = _parse_ts(segment.from_ts)
            to_ts = _parse_ts(segment.to_ts)
            if from_ts is None or to_ts is None:
                continue
            duration = to_ts - from_ts
            if duration <= 0:
                continue
            total_seconds += duration
            total_bytes += segment.size_bytes
            total_rows += segment.row_count

        sample_count = len(closed_v2)
        if total_seconds <= 0:
            # Genug Segmente, aber keine verwertbare Dauer (fehlende/degenerierte ts).
            return {**empty, "sample_segment_count": sample_count}

        bytes_per_hour = total_bytes / total_seconds * 3600
        rows_per_hour = total_rows / total_seconds * 3600
        avg_segment_seconds = total_seconds / sample_count

        estimated_retention_seconds: float | None = None
        budget = self._retention_config.max_file_size_bytes
        if budget is not None and bytes_per_hour > 0:
            estimated_retention_seconds = budget / bytes_per_hour * 3600

        return {
            "sample_segment_count": sample_count,
            "bytes_per_hour": bytes_per_hour,
            "rows_per_hour": rows_per_hour,
            "avg_segment_seconds": avg_segment_seconds,
            "estimated_retention_seconds": estimated_retention_seconds,
            "effective_segment_max_bytes": self._segment_config.segment_max_bytes,
        }

    async def stats(self) -> StoreStats:
        segments = await self.manifest.list_segments()
        total = sum(s.row_count for s in segments)
        oldest = min((s.from_ts for s in segments if s.from_ts), default=None)
        newest = max((s.to_ts for s in segments if s.to_ts), default=None)
        size_bytes = sum(s.size_bytes for s in segments)
        common = {
            "total": total,
            "oldest_ts": oldest,
            "newest_ts": newest,
            "segment_count": len(segments),
            "size_bytes": size_bytes,
            # Datengetriebene Prognose (#919) — reine Momentaufnahme aus retained
            # (geschlossenen v2-)Segmenten; robuste None-Behandlung.
            "prognosis": self._compute_prognosis(segments),
        }
        over_budget, pressure_reason = await self._retention_pressure(segments)
        backend_extra = {
            "active_segment_id": self._active_segment.segment_id if self._active_segment else None,
            "closed_segment_count": sum(1 for s in segments if s.status != SEGMENT_STATUS_ACTIVE),
            # WAL/SHM-Größen des aktiven Segments (SQLite-Interna, kein portables Feld).
            "wal_size_bytes": self._active_wal_size(),
            "shm_size_bytes": self._active_shm_size(),
            "last_checkpoint_at": self._last_checkpoint_at,
            "last_checkpoint_mode": self._last_checkpoint_mode,
            "last_checkpoint_result": self._last_checkpoint_result,
            "wal_checkpoint_busy": self._wal_checkpoint_busy_count,
            "checkpoint_pending": sum(1 for s in segments if s.status == "checkpoint_pending"),
            "retention_over_budget": over_budget,
            "retention_pressure_reason": pressure_reason,
            "storage_on_network_drive": self._storage_on_network_drive(),
            "segments": [self._segment_stat(s) for s in segments],
        }
        return StoreStats(common=common, backend_extra=backend_extra)

    @staticmethod
    def _segment_stat(segment: SegmentRecord) -> dict[str, Any]:
        return {
            "segment_id": segment.segment_id,
            "status": segment.status,
            "row_count": segment.row_count,
            "size_bytes": segment.size_bytes,
            "from_ts": segment.from_ts,
            "to_ts": segment.to_ts,
            "integrity_status": segment.integrity_status,
            "recovery_status": segment.recovery_status,
            "quarantine_reason": segment.quarantine_reason,
        }

    def _active_wal_size(self) -> int:
        if self._active_segment is None:
            return 0
        return self._sidecar_size(self._active_segment.filename, "-wal")

    def _active_shm_size(self) -> int:
        if self._active_segment is None:
            return 0
        return self._sidecar_size(self._active_segment.filename, "-shm")

    def _sidecar_size(self, filename: str, suffix: str) -> int:
        return _safe_getsize(self._segments_dir / f"{filename}{suffix}")

    async def _retention_pressure(self, segments: list[SegmentRecord]) -> tuple[bool, str | None]:
        """Meldet, ob Retention trotz Löschung löschbarer Segmente über Budget bleibt.

        ``retention_over_budget`` ist True, wenn nach Freigabe *aller* löschbaren
        Segmente (sauber geschlossen, quarantäniert und – sofern freigebbar –
        Legacy) das harte Size-Budget noch überschritten bliebe — also nur das
        wirklich nicht löschbare Restvolumen das Budget sprengt. Quarantänierte
        Segmente sind seit #919 in FIFO-Retention löschbar und zählen daher NICHT
        mehr als undeletable — ein einzelnes korruptes Segment löst damit kein
        retention_over_budget mehr aus.

        #951 [P2]: Ein Legacy-Segment, das aktuell NICHT löschbar ist, weil der
        No-Zero-History-Guard greift (es ist die einzige/letzte Datenquelle),
        zählt ebenfalls als undeletable. Sonst meldete ``/stats``
        ``retention_over_budget=false``, obwohl eine übergroße read-only Legacy-DB
        das Byte-Budget real überschreitet und ``enforce_retention()`` sie wegen
        des Guards nicht freigeben kann. Der Guard-Check ist derselbe wie in
        ``_next_size_retention_victim`` (``_has_nonlegacy_data_segment()``), damit
        Meldung und Löschentscheidung konsistent bleiben.
        """
        budget = self._retention_config.max_file_size_bytes
        if budget is None:
            return False, None
        undeletable = sum(s.size_bytes for s in segments if s.status in (SEGMENT_STATUS_ACTIVE, SEGMENT_STATUS_CHECKPOINT_PENDING))
        # Legacy zählt nur solange als undeletable, wie es NICHT freigebbar ist
        # (Guard greift). Sobald ein nicht-Legacy-Segment Zeilen hält, ist Legacy
        # per Size-Retention löschbar und darf das Budget nicht künstlich sprengen.
        if not await self._has_nonlegacy_data_segment():
            undeletable += sum(s.size_bytes for s in segments if _is_legacy_segment(s))
        if undeletable > budget:
            return True, "max_file_size_bytes exceeded by non-deletable segments"
        return False, None

    def _storage_on_network_drive(self) -> bool:
        """Leichtgewichtige Netzlaufwerk-Erkennung für die Storage-Root (#936-Kommentar).

        WAL/mmap ist auf NFS/SMB/manchen FUSE-Mounts unzuverlässig. Wir melden den
        Fall als Flag in den Stats, statt still zu degradieren. Best-effort: wenn die
        Plattform keine mount-Introspektion erlaubt, wird False angenommen.
        """
        try:
            with open("/proc/mounts", encoding="utf-8") as handle:
                mounts = [line.split() for line in handle if line.strip()]
        except OSError:
            return False
        resolved = str(self._root.resolve())
        best_match = ""
        best_fstype = ""
        for parts in mounts:
            if len(parts) < 3:
                continue
            mount_point, fstype = parts[1], parts[2]
            if resolved == mount_point or resolved.startswith(mount_point.rstrip("/") + "/"):
                if len(mount_point) >= len(best_match):
                    best_match = mount_point
                    best_fstype = fstype
        return best_fstype in _NETWORK_FS_TYPES

    # ------------------------------------------------------------------
    # #936: Checkpoint-Läufer für checkpoint_pending-Segmente
    # ------------------------------------------------------------------

    async def run_pending_checkpoints(self) -> int:
        """Versucht ``wal_checkpoint(TRUNCATE)`` erneut für ``checkpoint_pending``.

        Erst nach erfolgreichem Truncate (DB/WAL/SHM konsistent) wird das Segment
        wieder als sauber ``closed`` markiert und damit retention-fähig. Liefert die
        Anzahl der jetzt konsistent geschlossenen Segmente.

        Korruptions-Isolation (#951, Codex :1737): ist die Datei eines pending-Segments
        beim Startup korrupt/unlesbar, würde der ``aiosqlite``-Fehler aus dem Open/
        Checkpoint-Versuch sonst propagieren und den (segmentierten) Ringbuffer-Startup
        abbrechen — obwohl ``enforce_retention()`` diesen Läufer als Vorlauf aufruft.
        Ein korruptes Segment wird deshalb hier — konsistent mit dem Read-Pfad
        (``_quarantine_corrupt_read``) — quarantäniert statt propagiert. Das aktive
        Segment kann nie ``checkpoint_pending`` sein und wird daher nie hier berührt.
        """
        recovered = 0
        for segment in await self.manifest.list_checkpoint_pending_segments():
            try:
                conn = await aiosqlite.connect(str(self._segments_dir / segment.filename))
                try:
                    ok = await self._try_truncate_checkpoint(conn)
                finally:
                    await conn.close()
            except aiosqlite.Error as exc:
                # Unlesbares/korruptes pending-Segment isolieren statt Startup brechen.
                await self.manifest.mark_quarantined(segment.segment_id, reason=str(exc))
                continue
            if ok:
                await self.manifest.mark_checkpoint_done(segment.segment_id)
                # Erfolgreicher TRUNCATE (#951, Codex :1696): ein großes ``-wal``/``-shm``
                # wurde gerade in die Haupt-DB verschoben/entfernt. Das Manifest hält
                # aber noch die pre-checkpoint ``size_bytes`` (WAL-schwer). Da
                # ``enforce_retention()`` diesen Läufer UNMITTELBAR vor
                # ``_enforce_size_budget()`` aufruft, sähe die Budgetrechnung sonst die
                # alte, überhöhte Größe und löschte ältere/Legacy-Segmente unnötig.
                # Reale post-checkpoint-Größe (``_segment_file_size`` zählt WAL/SHM mit)
                # neu schreiben – analog zum Rotate-Pfad (Codex :1346).
                await self.manifest.update_segment_size(
                    segment.segment_id,
                    size_bytes=self._segment_file_size(segment.filename),
                )
                recovered += 1
        return recovered

    # ------------------------------------------------------------------
    # #936: Recovery/Integrity pro Segment (kein globaler Startup-Scan)
    # ------------------------------------------------------------------

    async def check_segment_integrity(self, segment_id: int) -> bool:
        """Prüft *ein* geschlossenes Segment und quarantäniert es bei Korruption.

        Bewusst on-demand pro Segment — NICHT im Startup über 20–30 GB. Ein
        korruptes geschlossenes Segment wird als ``quarantined`` markiert, statt
        den Store-Start zu blockieren. Liefert True, wenn das Segment intakt ist.
        """
        segment = await self.manifest.get_segment(segment_id)
        if segment is None:
            return False
        if self._active_segment is not None and segment_id == self._active_segment.segment_id:
            # Das aktive Segment wird nie quarantäniert/getrimmt.
            return True
        # Read-only öffnen; jede Exception (Öffnen/Lesen/Korruption) wird als
        # korrupt behandelt und quarantäniert, nie propagiert (#919).
        conn: aiosqlite.Connection | None = None
        try:
            conn = await aiosqlite.connect(str(self._segments_dir / segment.filename))
            async with conn.execute("PRAGMA integrity_check") as cur:
                row = await cur.fetchone()
            ok = row is not None and row[0] == "ok"
            reason = None if ok else (row[0] if row is not None else "integrity_check failed")
        except aiosqlite.Error as exc:
            ok = False
            reason = str(exc)
        finally:
            if conn is not None:
                await conn.close()
        if not ok:
            await self.manifest.mark_quarantined(segment_id, reason=reason)
        return ok

    # ------------------------------------------------------------------
    # Contract: enforce_retention (#936, Vertrag aus #930)
    # ------------------------------------------------------------------

    async def enforce_retention(self) -> int:
        """Segmentgenaue Retention — löscht nur ganze, retention-fähige Segmente.

        Vertrag (#930/#919): nie rowweise, nie das aktive Segment, nie ein
        ``checkpoint_pending`` Segment. Retention-fähig sind sauber geschlossene
        **und** quarantänierte Segmente: ein korruptes Segment wird nicht mehr
        für immer behalten, sondern in normaler FIFO-Reihenfolge (ältestes
        zuerst) mitgelöscht, wenn es an der Reihe ist — seine Manifest-Metadaten
        (from_ts/to_ts/row_count) bleiben intakt, nur die Datei ist korrupt.
        Prioritäten:

        1. ``max_file_size_bytes`` (hartes Budget): älteste retention-fähige Segmente
           löschen, bis das Gesamtvolumen unter das Budget fällt — auch wenn dadurch
           weniger Age/Rows aufbewahrt werden als gewünscht.
        2. ``max_age``: retention-fähige Segmente löschen, deren ``to_ts`` vollständig
           älter als der Cutoff ist.
        3. ``max_entries``: Row-Budget mit Segmentgranularität — älteste retention-
           fähige Segmente löschen, bis die aufbewahrte Zeilenzahl unter das Budget
           fällt.

        Liefert die Anzahl freigegebener Segmente.
        """
        cfg = self._retention_config
        if cfg.max_file_size_bytes is None and cfg.max_age is None and cfg.max_entries is None:
            return 0

        # Pending Checkpoints zuerst nachziehen (#951, Pkt 5): ein busy gebliebenes
        # ``checkpoint_pending``-Segment ist retention-UNfähig. Würde der Truncate
        # nie erneut versucht, bliebe es das dauerhaft und könnte ein hartes
        # Byte-Budget dauerhaft überschritten halten. ``run_pending_checkpoints`` lief
        # bisher nur aus Tests; hier im Retention-Pfad wird er bei jeder Erzwingung
        # wiederholt versucht, sodass ein inzwischen freier WAL das Segment wieder
        # ``closed`` (und damit retention-fähig) macht.
        await self.run_pending_checkpoints()

        removed = 0
        # Retention-fähige Segmente sind löschbar; älteste zuerst. Das aktive und
        # checkpoint_pending-Segment bleiben außen vor; quarantänierte werden seit
        # #919 in FIFO-Reihenfolge mitgelöscht.
        removed += await self._enforce_size_budget(cfg.max_file_size_bytes)
        removed += await self._enforce_age_cutoff(cfg.max_age)
        removed += await self._enforce_row_budget(cfg.max_entries)
        return removed

    async def _total_size_bytes(self) -> int:
        return sum(s.size_bytes for s in await self.manifest.list_segments())

    async def _total_row_count(self) -> int:
        return sum(s.row_count for s in await self.manifest.list_segments())

    async def _enforce_size_budget(self, budget: int | None) -> int:
        if budget is None:
            return 0
        removed = 0
        while await self._total_size_bytes() > budget:
            victim = await self._next_size_retention_victim()
            if victim is None:
                break  # kein löschbares Segment mehr → over_budget in stats sichtbar.
            if not await self._delete_segment(victim):
                # Basisdatei nicht löschbar (#951, Pkt 6): Zeile bleibt für den
                # nächsten Versuch erhalten. Nicht weiter loopen, sonst würde das
                # ältestes-zuerst gewählte, undeletbare Segment endlos re-selektiert.
                # over_budget bleibt in den Stats sichtbar; Retention versucht es beim
                # nächsten Durchlauf erneut.
                break
            removed += 1
        return removed

    async def _next_size_retention_victim(self) -> SegmentRecord | None:
        """Wählt das nächste per Size-Budget löschbare Segment — global ältestes zuerst (#919).

        FIFO / Legacy-Rückgewinnung: das read-only eingehängte Legacy-Segment ist
        per Definition am ältesten und zählt voll gegen das Size-Budget. Unter
        Budgetdruck wird es deshalb ZUERST als Einheit zurückgewonnen (Kanten-Drop,
        wie abgestimmt) — SOBALD der No-Zero-History-Guard erfüllt ist —, statt
        dauerhaft Budget zu belegen. Sonst das älteste geschlossene v2-Segment.

        **No-Zero-History-Guard:** das Legacy-Segment darf NICHT gelöscht werden,
        solange es die einzige Datenquelle ist. Erst wenn mindestens ein
        nicht-Legacy-Segment (aktiv oder geschlossen) Zeilen hält (frische Daten
        sind gesichert), wird das Legacy-Segment freigebbar. So verliert eine
        frische Umstellung nie sofort die ganze Historie.

        Reihenfolge: Legacy zuerst (ältestes, Guard erfüllt), sonst ältestes
        retention-fähiges Segment (geschlossen oder quarantäniert, #919).

        No-Zero-History-Guard über Herkunft, nicht Status (#951, Codex :724): ein
        beim Read korrupt erkanntes Legacy wird als ``quarantined`` markiert und
        damit retention-eligible – ``_delete_segment`` löschte über die Schema-
        Erkennung dann die ORIGINALE Single-DB, obwohl der Guard nur ``status=
        'legacy'`` prüfte. Legacy-Herkunft wird deshalb hier per Schema
        (``_is_legacy_segment``) bestimmt und unter dem Guard geschützt; die
        FIFO-Löschbarkeit quarantänierter NICHT-Legacy-Segmente bleibt unberührt.
        """
        has_nonlegacy_data = await self._has_nonlegacy_data_segment()
        # Legacy ist das global älteste → zuerst löschen, sobald der Guard erfüllt
        # ist (mindestens eine nicht-Legacy-Datenquelle hält Zeilen). Legacy-Herkunft
        # per Schema, damit auch ein quarantäniertes Legacy hier (und nicht in der
        # eligible-Liste) landet.
        legacy_segments = [s for s in await self.manifest.list_segments() if _is_legacy_segment(s)]
        if legacy_segments and has_nonlegacy_data:
            return legacy_segments[0]
        # Geschlossene und quarantänierte Segmente in FIFO-Reihenfolge (#919):
        # ein korruptes Segment wird nicht mehr für immer behalten, sondern
        # gelöscht, wenn es an der Reihe ist (ältestes zuerst). Legacy-Herkunft
        # (auch quarantäniert) wird ausgeschlossen – sie ist entweder oben schon
        # freigegeben oder durch den Guard geschützt (nie hier gelöscht).
        eligible = [s for s in await self.manifest.list_retention_eligible_segments() if not _is_legacy_segment(s)]
        if eligible:
            return eligible[0]
        return None

    async def _has_nonlegacy_data_segment(self) -> bool:
        """True, wenn ein ABFRAGBARES nicht-Legacy-Segment Zeilen hält.

        Ein quarantäniertes (korruptes) v2-Segment wird beim Read übersprungen und
        ist damit KEINE lesbare Historie (#951, Codex :1939). Würde es hier trotzdem
        zählen, hebt der No-Zero-History-Guard ab und die attached LESBARE Legacy-DB
        könnte unter Size-Druck gelöscht werden, obwohl keine lesbare nicht-Legacy-
        Historie existiert (Datenverlust). Nur aktive/geschlossene/pending – also
        NICHT quarantänierte – nicht-Legacy-Segmente mit Zeilen werten.
        """
        for segment in await self.manifest.list_segments():
            if _is_legacy_segment(segment) or segment.status == SEGMENT_STATUS_QUARANTINED:
                continue
            if segment.row_count > 0:
                return True
        return False

    async def _enforce_age_cutoff(self, max_age: int | None) -> int:
        if max_age is None:
            return 0
        cutoff = datetime.now(UTC).timestamp() - max_age
        removed = 0
        for segment in await self.manifest.list_retention_eligible_segments():
            to_ts = _parse_ts(segment.to_ts)
            if to_ts is None or to_ts >= cutoff:
                # Ältestes-zuerst: sobald ein Segment neu genug ist, sind alle
                # folgenden ebenfalls neu genug.
                break
            if not await self._delete_segment(segment):
                # Basisdatei nicht löschbar (#951, Pkt 6): Zeile bleibt, Retention
                # versucht es beim nächsten Durchlauf erneut.
                break
            removed += 1
        return removed

    async def _enforce_row_budget(self, max_entries: int | None) -> int:
        if max_entries is None:
            return 0
        removed = 0
        while await self._total_row_count() > max_entries:
            eligible = await self.manifest.list_retention_eligible_segments()
            if not eligible:
                break
            if not await self._delete_segment(eligible[0]):
                # Basisdatei nicht löschbar (#951, Pkt 6): sonst würde das älteste,
                # undeletbare Segment endlos re-selektiert.
                break
            removed += 1
        return removed

    async def _delete_segment(self, segment: SegmentRecord) -> bool:
        """Entfernt Datei (inkl. -wal/-shm) und Manifest-Eintrag konsistent.

        Liefert True, wenn Basisdatei UND Manifest-Zeile entfernt wurden; False,
        wenn die Basisdatei nicht gelöscht werden konnte und die Zeile daher zum
        erneuten Versuch erhalten bleibt (#951, Pkt 6).

        Dies ist ausschließlich der **Retention-Delete-Pfad** (``_enforce_*``): wird
        ein Segment hier gelöscht, hat die FIFO-Retention entschieden, dass diese
        Alt-Daten verworfen werden. Read-only-Query-Fälle rufen ``_delete_segment``
        nie auf — sie überspringen ein fehlendes Segment nur.

        Für Legacy-Segmente (#951, Pkt 3) wird die zugrundeliegende in-place liegende
        Original-Single-DB (inkl. ``-wal``/``-shm``) beim retention-bedingten Löschen
        MIT-gelöscht. Sonst entfernte ``_delete_segment`` nur die Manifest-Zeile, die
        Datei bliebe liegen und würde beim nächsten Start erneut registriert →
        getrimmte Historie und Budgetdruck kehrten zurück. Das Löschen setzt den
        FIFO-Retention-Vertrag durch (Platz wirklich freigeben; „Alt-Daten werden
        verworfen"). Legacy-Datei liegt als absoluter Pfad in ``filename``, v2 unter
        ``segments/``.

        Delete-Durability (#951, Pkt 6): die Manifest-Zeile wird NUR entfernt, wenn
        die BASIS-Segmentdatei erfolgreich gelöscht wurde. Scheitert das Unlink der
        Basisdatei (Permission/Lock/FS-Fehler), bleiben ihre Bytes auf der Platte –
        der Manifest-Eintrag bleibt daher erhalten, damit Retention es beim nächsten
        Durchlauf erneut versucht (statt die Bytes aus den Stats zu verlieren und die
        Datei nie wieder anzufassen). Sidecar-Fehler (``-wal``/``-shm``) bleiben
        tolerant und blockieren das Entfernen der Zeile nicht.
        """
        if _is_legacy_segment(segment):
            base = Path(segment.filename)
            base_removed = self._unlink_with_sidecars(base)
            if base_removed:
                _LOGGER.info(
                    "retention: removed legacy single-db %s (freed via FIFO retention; row/budget pressure)",
                    base,
                )
        else:
            base_removed = self._unlink_with_sidecars(self._segments_dir / segment.filename)
        if not base_removed:
            # Basisdatei blieb liegen → Zeile behalten, Retention versucht es erneut.
            _LOGGER.warning(
                "retention: base segment file for segment_id=%s could not be removed; keeping manifest entry for retry",
                segment.segment_id,
            )
            return False
        await self.manifest.delete_segment(segment.segment_id)
        return True

    @staticmethod
    def _unlink_with_sidecars(base: Path) -> bool:
        """Löscht ``base`` samt ``-wal``/``-shm``; True nur bei erfolgreicher Basis-Löschung.

        Sidecar-Fehler (``-wal``/``-shm`` fehlen oder sind unlöschbar) bleiben
        tolerant. Nur das Ergebnis der BASIS-Datei entscheidet, ob der Aufrufer die
        Manifest-Zeile entfernen darf (#951, Pkt 6). Eine bereits fehlende Basisdatei
        gilt als erfolgreich gelöscht (Platz ist frei).
        """
        base_removed = True
        try:
            base.unlink()
        except FileNotFoundError:
            # Bereits weg → Ziel erreicht (Platz frei).
            base_removed = True
        except OSError:
            base_removed = False
        for sidecar in (Path(f"{base}-wal"), Path(f"{base}-shm")):
            try:
                sidecar.unlink()
            except OSError:
                # Fehlt eine Sidecar-Datei (oder ist unlöschbar), stört das die
                # Retention nicht – nur die Basisdatei entscheidet.
                continue
        return base_removed
