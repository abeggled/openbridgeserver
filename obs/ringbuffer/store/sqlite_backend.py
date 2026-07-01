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

Volle segmentbewusste Query (#932), Legacy-Migration (#934), die eigentliche
Segment-Retention-Ausführung und Recovery-Details (#936) sind NICHT Teil dieses
Foundation-Kernels; die Nahtstellen sind mit ``# TODO(#…)`` markiert.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from obs.core.json import json_dumps
from obs.ringbuffer.ringbuffer import (
    _extract_metadata_binding_index_rows,
    _extract_metadata_tags,
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
from obs.ringbuffer.store.manifest import Manifest, SegmentRecord
from obs.ringbuffer.store.writer_lock import WriterLease

SEGMENT_SCHEMA_VERSION = 2

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


# Einfache Operatoren, die als typisiertes SQL-WHERE gepusht werden.
_PUSHDOWN_OPERATORS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "between"})
# contains/regex: nur mit gebundenem Query (Zeitfenster oder Kandidaten-Cap).
_GUARDED_OPERATORS = frozenset({"contains", "regex"})
_VALID_OPERATORS = _PUSHDOWN_OPERATORS | _GUARDED_OPERATORS
# Erlaubte Zielspalten eines Wertfilters (engine-neutrale field-Namen).
_FILTER_FIELDS = frozenset({"new_value", "old_value"})
# Regex-Härtung (Referenz: Legacy _match_regex in ringbuffer.py).
_REGEX_MAX_PATTERN_LEN = 256
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
    # Listen/Dicts o.ä. sind für typisierte Pushdown-Filter nicht adressierbar.
    return "null"


def _obs_regexp_impl(pattern: str, flags: int, value: Any) -> int:
    """SQLite-Callback für gepushtes ``regex``. 1 bei Treffer, sonst 0.

    Das Muster ist beim Clause-Bau bereits gehärtet (Länge, nested quantifiers,
    Kompilierbarkeit); der Query-Kontext ist gebunden (Zeitfenster/Cap).
    """
    if not isinstance(value, str):  # pragma: no cover - SQL filtert bereits text_col IS NOT NULL
        return 0
    try:
        return 1 if re.compile(pattern, flags).search(value) else 0
    except re.error:  # pragma: no cover - bereits beim Clause-Bau geprüft
        return 0


def _typed_columns_for(value: Any) -> tuple[str, float | None, str | None, int | None]:
    """(type, num, text, bool) — genau eine Nutzspalte ist je nach Typ gesetzt."""
    value_type = _derive_value_type(value)
    if value_type == "bool":
        return ("bool", None, None, 1 if value else 0)
    if value_type == "numeric":
        return ("numeric", float(value), None, None)
    if value_type == "text":
        return ("text", None, value, None)
    return ("null", None, None, None)


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

    # ------------------------------------------------------------------
    # Contract: Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> StoreCapabilities:
        return StoreCapabilities(
            supports_native_retention=True,
            # #933: typisierte Wertspalten + SQL-Pushdown für einfache Operatoren.
            supports_typed_pushdown=True,
            ordering_guarantee=OrderingGuarantee.GLOBAL_MONOTONIC,
            # TODO(#932): streaming/segmentuebergreifender Export.
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
        for offset, event in enumerate(events):
            await self._insert_event(self._active_conn, start_id + offset, event)
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
        # Foundation-Query: liest über alle Segmente und führt nach globaler
        # Event-ID (neueste zuerst) stabil zusammen.
        # TODO(#932): segmentbewusste Auswahl per Zeitfenster + streambares,
        # deterministisches Cross-Segment-Paging statt Voll-Merge.
        rows = await self._collect_rows_across_segments(query)
        rows.sort(key=lambda r: r["global_event_id"], reverse=True)
        start = max(query.offset, 0)
        end = start + max(query.limit, 0)
        return rows[start:end]

    async def _collect_rows_across_segments(self, query: StoreQuery) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        for segment in await self.manifest.list_segments():
            conn = await self._connection_for_read(segment)
            close_after = conn is not self._active_conn
            try:
                collected.extend(await self._query_segment(conn, query))
            finally:
                if close_after:
                    await conn.close()
        return collected

    async def _connection_for_read(self, segment: SegmentRecord) -> aiosqlite.Connection:
        if self._active_segment is not None and segment.segment_id == self._active_segment.segment_id and self._active_conn is not None:
            return self._active_conn
        conn = await aiosqlite.connect(str(self._segments_dir / segment.filename))
        conn.row_factory = aiosqlite.Row
        return conn

    async def _query_segment(self, conn: aiosqlite.Connection, query: StoreQuery) -> list[dict[str, Any]]:
        sql, params = self._build_segment_sql(query)
        if any(str(f.get("operator", "")).strip().lower() == "regex" for f in query.value_filters):
            # REGEXP-Callback nur registrieren, wenn ein Regex-Filter vorliegt.
            # Registrierung erfolgt lokal auf der übergebenen Read-Connection.
            await conn.create_function("obs_regexp", 3, _obs_regexp_impl, deterministic=True)
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [self._row_to_dict(row) for row in rows]

    def _build_segment_sql(self, query: StoreQuery) -> tuple[str, list[Any]]:
        """Baut das segment-lokale SELECT inkl. gepushter Wertfilter.

        Einfache Wertfilter (eq/ne/gt/gte/lt/lte/between) landen als typisiertes
        WHERE-Prädikat, damit ``LIMIT`` NICHT durch einen Python-Post-Filter
        ausgehebelt wird. ``contains``/``regex`` werden als SQL-``LIKE`` bzw.
        ``REGEXP``-taugliches Prädikat nur zugelassen, wenn der Query gebunden ist
        (Zeitfenster oder ``candidate_cap``); sonst ``ValueError`` (422-tauglich).
        """
        clauses: list[str] = []
        params: list[Any] = []
        if query.from_ts is not None:
            clauses.append("ts >= ?")
            params.append(query.from_ts)
        if query.to_ts is not None:
            clauses.append("ts <= ?")
            params.append(query.to_ts)
        if query.datapoint_id is not None:
            clauses.append("datapoint_id = ?")
            params.append(query.datapoint_id)
        if query.source_adapter is not None:
            clauses.append("source_adapter = ?")
            params.append(query.source_adapter)
        if query.quality is not None:
            clauses.append("quality = ?")
            params.append(query.quality)
        for spec in query.value_filters:
            clause, filter_params = self._value_filter_clause(spec, query)
            clauses.append(clause)
            params.extend(filter_params)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT global_event_id, ts, datapoint_id, topic, old_value, new_value, "
            "source_adapter, quality, metadata_version, metadata "
            f"FROM ringbuffer{where} ORDER BY global_event_id DESC LIMIT ?"
        )
        params.append(max(query.offset, 0) + max(query.limit, 0))
        return sql, params

    @staticmethod
    def _query_is_bounded(query: StoreQuery) -> bool:
        """contains/regex nur mit engem Zeitfenster oder Kandidaten-Cap zulassen."""
        has_window = query.from_ts is not None and query.to_ts is not None
        has_cap = query.candidate_cap is not None and query.candidate_cap > 0
        return has_window or has_cap

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
        text_col = f"{field_name}_text"
        bool_col = f"{field_name}_bool"

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
        value_type, num, text, bool_val = _typed_columns_for(value)
        comparator = _SQL_COMPARATORS[operator]
        # Gemischte Typen dürfen nicht fälschlich matchen: der Vergleich läuft nur
        # gegen die typgleiche Spalte, die anderen Typspalten sind NULL.
        if value_type == "numeric":
            return (f"({num_col} IS NOT NULL AND {num_col} {comparator} ?)", [num])
        if value_type == "text":
            return (f"({text_col} IS NOT NULL AND {text_col} {comparator} ?)", [text])
        if value_type == "bool":
            return (f"({bool_col} IS NOT NULL AND {bool_col} {comparator} ?)", [bool_val])
        raise ValueError(f"operator '{operator}' needs a numeric, text or bool value")

    @staticmethod
    def _guarded_clause(operator: str, field_name: str, spec: dict[str, Any]) -> tuple[str, list[Any]]:
        text_col = f"{field_name}_text"
        ignore_case = bool(spec.get("ignore_case", False))
        if operator == "contains":
            needle = spec.get("value")
            if not isinstance(needle, str):
                raise ValueError("contains requires a string value")
            escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            if ignore_case:
                return (
                    f"({text_col} IS NOT NULL AND LOWER({text_col}) LIKE ? ESCAPE '\\')",
                    [f"%{escaped.lower()}%"],
                )
            return (f"({text_col} IS NOT NULL AND {text_col} LIKE ? ESCAPE '\\')", [f"%{escaped}%"])

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
            if not checkpoint_ok:
                await self.manifest.mark_checkpoint_pending(old_segment.segment_id)
                # TODO(#936): Hintergrund-Checkpoint-Läufer räumt pending später ab.

        new_segment = await self._create_segment_locked()
        self._active_segment = new_segment
        self._active_conn = await self._open_segment_conn(new_segment.filename)
        return new_segment

    async def _try_truncate_checkpoint(self, conn: aiosqlite.Connection) -> bool:
        """Versucht ``wal_checkpoint(TRUNCATE)``. Liefert False bei busy."""
        async with conn.execute("PRAGMA wal_checkpoint(TRUNCATE)") as cur:
            row = await cur.fetchone()
        # PRAGMA-Ergebnis (busy, log, checkpointed): busy != 0 → nicht vollständig.
        return not (row is not None and row[0] != 0)

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
        path = self._segments_dir / filename
        return os.path.getsize(path) if path.exists() else 0

    # ------------------------------------------------------------------
    # Contract: stats
    # ------------------------------------------------------------------

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
        }
        backend_extra = {
            "active_segment_id": self._active_segment.segment_id if self._active_segment else None,
            "closed_segment_count": sum(1 for s in segments if s.status != "active"),
            # TODO(#936): wal_size_bytes/shm_size_bytes/last_checkpoint_* ergänzen.
        }
        return StoreStats(common=common, backend_extra=backend_extra)

    # ------------------------------------------------------------------
    # Contract: enforce_retention (Naht für #936)
    # ------------------------------------------------------------------

    async def enforce_retention(self) -> int:
        """Segmentgenaue Retention — Foundation-Naht.

        Der Vertrag steht (nur ganze geschlossene Segmente löschen, segmentgenau,
        nie rowweise). Die eigentliche Ausführung (Size-/Age-/Rows-Budget gegen
        geschlossene Segmente, Löschen inkl. Manifest-Update) ist #936-Scope.
        """
        # TODO(#936): geschlossene Segmente gegen retention_config auswählen und
        # als ganze Einheiten löschen; Anzahl freigegebener Segmente zurückgeben.
        return 0
