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
sicher zusammengeführt sind (kein Voll-Merge über alle Segmente). Das
Klassifizieren/Attachen einer Legacy-Single-DB (#934) bleibt außerhalb dieses
Kernels (``store/migration.py``); der Legacy-Lesepfad hier degradiert
kontrolliert auf das v1-Schema.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

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


class LegacyScanPlan(NamedTuple):
    """Plan des Legacy-Roh-Scans (siehe ``_legacy_scan_plan``).

    ``base_sql``/``params`` sind das gedeckelte, per Batch (``LIMIT ? OFFSET ?``)
    gefetchte Kandidaten-SELECT. ``name_hit_sql``/``name_hit_params`` sind der
    separat gedeckelte ``dp_ids_by_name``-``IN``-Arm (nur gesetzt, wenn Freitext-``q``
    UND ``dp_ids_by_name`` vorliegen); er wird einmalig gefetcht und dedupliziert in
    die Kandidatenmenge gemerged (#951, Runde 46, :1686).
    """

    base_sql: str
    params: list[Any]
    has_python_post_filter: bool
    name_hit_sql: str | None
    name_hit_params: list[Any]


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


def _is_missing_ringbuffer_table(exc: Exception) -> bool:
    """True, wenn ein Read an einer fehlenden ``ringbuffer``-Tabelle scheitert (#951, Codex :1057).

    Ein geschlossenes/retained v2-Segment kann auf 0 Bytes truncated oder durch eine
    leere SQLite-DB ersetzt sein: der read-only-Open gelingt (gültige, aber schemalose
    DB), das spätere SELECT wirft dann ``no such table: ringbuffer``. Das ist KEINE
    von ``_is_sqlite_corruption`` erkannte Korruption (malformed/not a database), sodass
    das Segment sonst bei JEDER Query, die es berührt, einen 500 lieferte statt isoliert
    zu werden. Es wird daher – analog zum aktiven Segment (Codex :658) und
    checkpoint_pending (Codex :2220) – als korrupt/verloren klassifiziert und
    quarantäniert.
    """
    if not isinstance(exc, aiosqlite.Error):
        return False
    return "no such table: ringbuffer" in str(exc).lower()


# Legacy-Query darf ohne Zeitfenster nicht unbounded scannen: die JSON-basierte
# Value-Filter-Degradation liest höchstens so viele Kandidatenzeilen.
_LEGACY_DEFAULT_CANDIDATE_CAP = 10_000

# Synthetische global_event_id für Legacy-Zeilen: aus der chronologischen
# Legacy-rowid abgeleitet (NICHT aus der Fetch-Reihenfolge), damit die Ordnung
# unabhängig von der Sort-Richtung des Kandidaten-Fetches stabil bleibt. Der
# Offset hält alle Legacy-IDs strikt negativ (unter allen positiven v2-IDs);
# höhere rowid (neuer) ⇒ höhere (weniger negative) ID.
#
# JS-/JSON-Sicherheit (#951, Runde 23): die synthetischen IDs werden über die
# JSON-API als ``global_event_id`` exponiert. Browser parsen JSON-Zahlen als
# IEEE-754-Doubles; jenseits von ``±(2**53-1)`` kollabieren benachbarte gids auf
# denselben Double und ein JS-Consumer, der per ``id`` keyed/dedupliziert, bricht.
# Das Schema ist daher so skaliert, dass der Worst-Case-Betrag
# ``OFFSET + max_index*STRIDE`` unter ``2**53`` bleibt (statt der früheren
# ``1<<62``/``1<<40``-Werte, die weit im unsicheren Bereich lagen). ``OFFSET =
# 1<<52`` gibt einen negativen Floor weit unter jeder positiven v2-gid; da Legacy
# strikt negativ und v2 strikt positiv ist, können sich beide Bänder nie treffen.
_LEGACY_GID_OFFSET = 1 << 52

# Per-Quelle-Stride, der die synthetischen Legacy-gids mehrerer read-only
# attached Legacy-DBs DISJUNKT hält (#951, Codex :1123). Jede Legacy-Quelle
# bekommt einen eigenen, ``segment_id``-skalierten Block von je ``STRIDE``
# reservierten rowid-Werten. Ohne diesen Block kollidierten – bei bloßem
# ``-(segment_id & 0xFFFF)`` – aufeinanderfolgende Quellen (rowid r der einen
# und rowid r+1 der nächsten trafen dieselbe synthetische ID), sodass als
# entry-IDs exponierte ``global_event_id``s doppelt vorkamen und Multi-
# Filterset-Queries/Exports auf eindeutigen IDs brachen.
#
# Kapazitätsgrenze (JS-sicher, #951, Runde 23): ``1 << 32`` (~4,29e9 rowids je
# Segment/Quelle) deckt eine 20–30 GB-Single-DB mit mehrfachem Headroom ab
# (selbst bei ~30 Byte/Zeile ~1e9 Zeilen). Zusammen mit ``OFFSET = 1<<52`` bleibt
# der Worst-Case-Betrag bis zu einem Segment-/Quell-Index von ``< 1<<20``
# (~1 Mio) innerhalb ``±(2**53-1)`` – dokumentierte Obergrenze: ``segment_id`` bzw.
# ``source_bucket`` müssen ``< _LEGACY_SOURCE_BUCKETS`` (= ``1<<20``) bleiben.
_LEGACY_GID_STRIDE = 1 << 32

# Feste obere Bucket-Schranke ``B`` für die Cross-Source-Ordnung der read-only
# attached Legacy-Segmente (#951, Codex :1558). Die synthetische-ID-Formel spiegelt
# ``segment_id`` an dieser Schranke (``B - 1 - segment_id``), damit eine NEUERE Quelle
# (höhere ``segment_id`` = später registriert) den WENIGER negativen Block bekommt und
# im Default-``id desc`` VOR der älteren Quelle sortiert – konsistent zum FIFO-/
# Retention-Vertrag (``_retention_victim_order``: ältestes Legacy = niedrigste
# segment_id zuerst) und zur finalen ``global_event_id``-Ordnung in ``query()``.
_LEGACY_SOURCE_BUCKETS = 1 << 20


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
# Ziel-String-Längenbegrenzung wie Legacy ``_match_regex`` (#951, Pkt 6 / Codex :499):
# der Regex-Callback läuft synchron je Kandidatenzeile; ohne diese Grenze könnte ein
# sehr langer Wert (kombiniert mit einem Muster) die Query/den Event-Loop lange
# blockieren. Übersteigt ein GESPEICHERTER Wert diese Grenze, wird der Filter – exakt
# wie im Legacy-Pfad – als 422-tauglicher Validierungsfehler ABGELEHNT (nicht auf den
# Prefix truncatet); sonst hinge das Ergebnis von der Truncation-Grenze ab.
_REGEX_MAX_TARGET_LEN = 4096
# Ein Quantifier direkt nach einer schließenden Gruppe: ``*``/``+``/``?`` oder ein
# counted quantifier (``{m}``/``{m,}``/``{m,n}``). Für den Look-ahead im Scanner unten.
_RE_TRAILING_QUANTIFIER = re.compile(r"[*+?]|\{\d+(?:,\d*)?\}")


# Inline-Flag-Buchstaben eines ``(?flags)``/``(?flags:...)``-Präfixes (Python ``re``).
_REGEX_INLINE_FLAG_CHARS = set("aiLmsux")


def _skip_group_prefix(pattern: str, open_idx: int) -> int | None:
    """Überspringt ein Regex-Extension-Präfix nach ``(`` (#951, Codex :338).

    ``open_idx`` zeigt auf das ``(``. Rückgabe: der Index, ab dem der eigentliche
    Gruppen-**Körper** beginnt (nach dem Präfix). Für einen ``(?#...)``-Kommentar –
    der keinen scannbaren Körper hat – wird ``None`` zurückgegeben, damit der Aufrufer
    die ganze Kommentargruppe verwirft.

    Erkannte Präfixe (die Präfix-Zeichen sind KEIN Quantifier/Körper-Inhalt):

    * ``(?:``                     – non-capturing
    * ``(?P<name>`` / ``(?'name'``– named group
    * ``(?P=name)``              – named backreference (kein Körper)
    * ``(?i)`` / ``(?i:`` / ``(?i-s:`` … – inline flags (global oder scoped)
    * ``(?=`` ``(?!`` ``(?<=`` ``(?<!`` – look-around
    * ``(?>``                     – atomic group
    * ``(?#...)``                – Kommentar (→ ``None``)

    Eine gewöhnliche Gruppe (``(`` NICHT gefolgt von ``?``) hat kein Präfix; der
    Körper beginnt direkt nach ``(`` (Rückgabe ``open_idx + 1``).
    """
    n = len(pattern)
    i = open_idx + 1
    if i >= n or pattern[i] != "?":
        # Gewöhnliche (capturing) Gruppe – kein Präfix.
        return i
    i += 1  # ``?`` verbraucht
    if i >= n:
        return i
    ch = pattern[i]
    if ch == "#":
        # Kommentar – kein scannbarer Körper.
        return None
    if ch == ":" or ch == ">":
        # non-capturing / atomic – Körper folgt direkt.
        return i + 1
    if ch == "=" or ch == "!":
        # look-ahead – Körper folgt direkt.
        return i + 1
    if ch == "P":
        i += 1
        if i < n and pattern[i] == "=":
            # ``(?P=name)`` – Backreference, kein quantifizierbarer Körper. Bis ``)``.
            while i < n and pattern[i] != ")":
                i += 1
            return i  # Körper ist leer; ``)`` schließt gleich
        # ``(?P<name>`` – Namen bis ``>`` überspringen.
        while i < n and pattern[i] != ">":
            i += 1
        return i + 1 if i < n else i
    if ch == "'":
        # ``(?'name'`` – Namen bis schließendes ``'`` überspringen.
        i += 1
        while i < n and pattern[i] != "'":
            i += 1
        return i + 1 if i < n else i
    if ch == "<":
        i += 1
        if i < n and pattern[i] in "=!":
            # look-behind ``(?<=`` / ``(?<!`` – Körper folgt.
            return i + 1
        # ``(?<name>`` – named group (alternative Schreibweise).
        while i < n and pattern[i] != ">":
            i += 1
        return i + 1 if i < n else i
    if ch in _REGEX_INLINE_FLAG_CHARS or ch == "-":
        # Inline-Flags: ``(?i)`` (global) oder ``(?i:`` / ``(?i-s:`` (scoped).
        while i < n and (pattern[i] in _REGEX_INLINE_FLAG_CHARS or pattern[i] == "-"):
            i += 1
        if i < n and pattern[i] == ":":
            return i + 1  # scoped: Körper folgt nach ``:``
        # global ``(?flags)`` – kein Körper, direkt ``)`` erwartet.
        return i
    # Unbekanntes Präfix – konservativ hinter dem ``?`` weiterscannen.
    return i


def _scan_unsafe_group_repetition(pattern: str) -> str | None:
    """Nesting-aware Erkennung katastrophaler gruppierter Wiederholungen (#951, Codex :285).

    Ersetzt die früheren FLACHEN Regex-Guards (nested quantifier / quantifizierte
    Alternation). Ein reiner Regex kann balancierte/geschachtelte Klammern nicht robust
    parsen, sodass ein zusätzlicher Wrapper (``((a+))+``) die alten Muster passierte,
    obwohl es dieselbe katastrophale nested-repeat-Form ist. Dieser kleine Scanner geht
    die Regex-Struktur einmal durch, trackt die Gruppen-Verschachtelung über einen Stack
    und flaggt eine quantifizierte Gruppe (Quantifier direkt nach ``)``), wenn sie
    IRGENDWO – auch hinter zusätzlichen Wrapper-Klammern – einen inneren Quantifier oder
    eine Alternation enthält.

    Escapte Klammern (``\\(``/``\\)``) und Zeichenklassen (``[...]``) werden korrekt
    ignoriert (nicht als Gruppen bzw. Sonderzeichen gezählt). Benigne Muster ohne inneren
    Quantifier/Alternation (``(abc)+``, ``((abc))+``, ``a{3}``, ``foo.*bar``) bleiben
    erlaubt.

    Rückgabe: eine Fehlerursache (String) für ein unsicheres Muster, sonst ``None``.

    Jede Stack-Frame beschreibt den INHALT einer offenen Gruppe:

    * ``has_quant``   – enthält irgendwo einen Quantifier (auf einem Zeichen ODER auf
                        einer inneren, quantifizierten Gruppe);
    * ``has_alt``     – enthält eine Top-Level-Alternation (``|``) dieser Gruppe.

    Schließt eine Gruppe und folgt ihr ein Quantifier, ist sie unsicher, sobald ihr
    Inhalt ``has_quant`` oder ``has_alt`` trägt. Beide Flags des Inhalts blubbern beim
    Schließen in die Elterngruppe hoch; ist die Gruppe selbst quantifiziert, zählt dieser
    Quantifier zusätzlich als innerer Quantifier der Elterngruppe.
    """
    # Stack von [has_quant, has_alt] je offener Gruppe.
    stack: list[list[bool]] = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "\\":
            # Escape-Sequenz: das nächste Zeichen ist literal, keine Gruppe/kein Meta.
            i += 2
            continue
        if ch == "[":
            # Zeichenklasse überspringen; enthaltene ``(``/``)``/``|`` sind literal.
            i += 1
            if i < n and pattern[i] == "^":
                i += 1
            if i < n and pattern[i] == "]":  # ``]`` direkt am Anfang ist literal
                i += 1
            while i < n and pattern[i] != "]":
                if pattern[i] == "\\":
                    i += 1
                i += 1
            i += 1  # schließende ``]``
            continue
        if ch == "(":
            # Python-Regex-Extension-Präfixe (``(?:``, ``(?P<name>``, ``(?i:``,
            # ``(?=``, ``(?!`` …) VOR dem Gruppen-Körper überspringen (#951, Codex :338).
            # Sonst läse der Scanner das ``?`` (und ``<``/``=``/``!``/Flags) des Präfixes
            # als INNEREN Quantifier/Inhalt und wiese einen sicheren Filter wie
            # ``(?:abc)+`` fälschlich als „nested quantifiers" ab (Über-Rejection).
            prefix_end = _skip_group_prefix(pattern, i)
            if prefix_end is None:
                # ``(?#...)``-Kommentar: die gesamte Gruppe ist inhaltslos und wird
                # weder als Gruppe gestackt noch gescannt – bis zum ``)`` überspringen.
                j = i + 1
                while j < n and pattern[j] != ")":
                    if pattern[j] == "\\":
                        j += 1
                    j += 1
                i = j + 1  # schließende ``)`` mitüberspringen
                continue
            stack.append([False, False])
            i = prefix_end  # nur der Körper NACH dem Präfix wird gescannt
            continue
        if ch == ")":
            frame = stack.pop() if stack else [False, False]
            # Ist die gerade geschlossene Gruppe quantifiziert?
            m = _RE_TRAILING_QUANTIFIER.match(pattern, i + 1)
            quantified = m is not None
            if quantified and (frame[0] or frame[1]):
                return "nested quantifiers are not allowed" if frame[0] else "quantified alternation is not allowed"
            if stack:
                parent = stack[-1]
                # Inhalts-Flags der Gruppe blubbern in die Elterngruppe hoch.
                parent[0] = parent[0] or frame[0]
                parent[1] = parent[1] or frame[1]
                # Ist die Gruppe selbst quantifiziert, ist das ein innerer Quantifier
                # der Elterngruppe.
                if quantified:
                    parent[0] = True
            if m is not None:
                i = m.end()  # Quantifier mitüberspringen
            else:
                i += 1
            continue
        if ch == "|":
            if stack:
                stack[-1][1] = True
            i += 1
            continue
        if ch in "*+?":
            # Quantifier auf einem Zeichen: innerer Quantifier der aktuellen Gruppe.
            if stack:
                stack[-1][0] = True
            i += 1
            continue
        if ch == "{":
            m = _RE_TRAILING_QUANTIFIER.match(pattern, i)
            if m is not None:
                if stack:
                    stack[-1][0] = True
                i = m.end()
                continue
            i += 1
            continue
        i += 1
    return None


def _assert_safe_regex(pattern: str) -> None:
    """Statisches safe-regex-Gate für Pushdown UND Legacy-Fallback (#951, Codex :285/:307).

    Wirft einen 422-tauglichen ``ValueError`` für Muster, die im synchronen Callback
    katastrophal backtracken könnten. Deckt Länge sowie – nesting-aware – nested
    quantifiers und quantifizierte Alternationen ab (auch hinter Wrapper-Klammern wie
    ``((a+))+``). Die Muster-Ablehnung VOR der Ausführung ist der einzige wirksame Schutz,
    weil ein laufender ``re.search`` in CPython (GIL) nicht per Timeout abbrechbar ist.
    """
    if len(pattern) > _REGEX_MAX_PATTERN_LEN:
        raise ValueError("unsafe regex pattern: pattern too long")
    reason = _scan_unsafe_group_repetition(pattern)
    if reason is not None:
        raise ValueError(f"unsafe regex pattern: {reason}")


def _sqlite_ro_uri(path: Path, *, params: str = "") -> str:
    """Baut eine read-only ``file:``-URI mit korrekt encodiertem Pfad (#951, Codex :1142).

    Enthaelt das Daten-/RingBuffer-Verzeichnis SQLite-URI-Metazeichen (``?``, ``#``,
    ``%``, Leerzeichen), machte das rohe Interpolieren des Filesystem-Pfads in
    ``file:...?mode=ro``, dass SQLite einen Teil des Pfads als URI-Query/Fragment
    parst → falscher DB-Pfad → 500er oder fehlerhafte ``no such table``-Quarantaene.
    ``Path.as_uri()`` prozent-encodiert den (absoluten) Pfad korrekt; die eigentlichen
    Query-Parameter (``mode=ro`` etc.) werden ERST danach angehaengt, sodass sie echte
    Query-Parameter bleiben und nicht Teil des encodierten Pfads werden. ``as_uri()``
    verlangt einen absoluten Pfad, daher wird der (evtl. relative) Store-Pfad zuvor
    aufgeloest – die geoeffnete Datei ist dieselbe wie beim frueheren rohen ``as_posix()``.
    """
    uri = path.resolve().as_uri()
    if params:
        uri = f"{uri}?{params}"
    return uri


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


def _obs_json_eq_impl(raw: Any, expected_json: str) -> int:
    """SQLite-Callback: 1, wenn die gespeicherte JSON-Spalte Python-``== expected`` ist.

    Spiegelt die Legacy-Referenz ``actual == expected`` für komplexe (list/dict)
    Werte (#951, Codex :1281 / Codex :393): die gespeicherte
    ``new_value``/``old_value``-JSON-Spalte UND der Filterwert werden beide zu
    Python-Objekten dekodiert und mit Python-``==`` verglichen – NICHT über ihre
    JSON-Schreibweise. Das ist entscheidend für verschachtelte numerisch/bool
    äquivalente Werte: Python wertet ``True == 1``, ``1 == 1.0`` (rekursiv in
    Listen/Dicts) als gleich, während der kanonische JSON-STRING sie als
    verschiedene Tokens rendert (``true`` vs ``1``, ``1`` vs ``1.0``) und so von der
    Legacy-Referenz abwich (``eq`` verlor Zeilen, ``ne`` nahm sie fälschlich auf).
    Python-``==`` ist zudem für Dicts von Natur aus key-order-unabhängig (wie die
    frühere ``sort_keys``-Kanonisierung) und für Listen ordnungsempfindlich – exakt
    die Referenz-Semantik. Malformed/non-JSON-Spalten matchen nie.
    """
    if not isinstance(raw, str):
        return 0
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return 0
    expected = json.loads(expected_json)
    return 1 if decoded == expected else 0


def _make_obs_regexp_impl(too_long_flag: list[bool]) -> Callable[[str, int, Any], int]:
    """Baut den ``obs_regexp``-Callback mit einem query-gescopten Too-Long-Marker (#951, Codex :499).

    ``too_long_flag`` ist eine geteilte mutable Ein-Element-Liste: trifft der Callback
    einen Zielwert länger als ``_REGEX_MAX_TARGET_LEN``, setzt er ``too_long_flag[0]``
    und liefert 0 (kein Treffer), damit die Query sauber durchläuft. Nach ``execute``
    prüft ``_query_segment`` das Flag und wirft einen 422-tauglichen ``ValueError`` –
    Parität zum Legacy-Pfad (``_match_regex`` in ringbuffer.py wirft „target value too
    long"). Der Marker ist nötig, weil eine im SQLite-Callback GEWORFENE Exception in
    CPython nur als generischer ``OperationalError`` („user-defined function raised
    exception") ohne ``__cause__`` ankommt und vom Korruptions-/Quarantäne-Pfad als
    SQL-Fehler maskiert würde, statt als Validierungsfehler zu propagieren.

    Das Muster ist beim Clause-Bau bereits gehärtet (Länge, nested quantifiers,
    ambiguous alternation, Kompilierbarkeit); der Query-Kontext ist gebunden
    (Zeitfenster/Cap). Der Callback läuft synchron je Kandidatenzeile im aiosqlite-
    Worker-Thread — die Muster-Härtung VOR der Query ist daher der eigentliche
    DoS-Schutz (siehe ``_assert_safe_regex``).
    """

    def _impl(pattern: str, flags: int, value: Any) -> int:
        if not isinstance(value, str):  # pragma: no cover - SQL filtert bereits text_col IS NOT NULL
            return 0
        # Zu langen Zielwert ABLEHNEN statt truncaten (#951, Codex :499): Legacy-Parität
        # (siehe ``_make_obs_regexp_impl``-Docstring). Flag setzen, 0 liefern; der
        # ValueError wird nach der Query aus dem Flag geworfen.
        if len(value) > _REGEX_MAX_TARGET_LEN:
            too_long_flag[0] = True
            return 0
        try:
            return 1 if re.compile(pattern, flags).search(value) else 0
        except re.error:  # pragma: no cover - bereits beim Clause-Bau geprüft
            return 0

    return _impl


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


# Ab diesem Betrag sind benachbarte Integer in einem IEEE-754-double (REAL-Spalte)
# nicht mehr eindeutig unterscheidbar (#951, Codex :332): ``float(2**53) ==
# float(2**53+1)``, d. h. schon ``|v| >= 2**53`` kann bei der ``float()``-Konvertierung
# in die REAL-Pushdown-Spalte mit einem benachbarten Integer kollidieren – sowohl beim
# Filterwert als auch beim GESPEICHERTEN Wert. ``eq``/``ne``/Range gegen die REAL-Spalte
# matchten dann falsche Zeilen ggü. dem JSON-Wert. Liegt der Filterwert in dieser
# unsicheren Zone, wird daher NICHT über die REAL-Spalte verglichen, sondern exakt gegen
# die JSON-Wertspalte (``obs_num_cmp``), analog zum Legacy-Python-Vergleich. Filterwerte
# unter ``2**53`` bleiben exakt (kein anderer Integer kollabiert auf sie) und laufen
# weiter über den schnellen REAL-Pfad.
_MAX_EXACT_INT = 2**53


def _is_unsafe_int(value: Any) -> bool:
    """True für Integer in/über der IEEE-754-Kollisionszone (``|v| >= 2**53``).

    ``bool`` ist eine ``int``-Subklasse, trägt aber nie einen unsicheren Betrag und
    wird ausgeschlossen. Floats sind per Definition schon inexakt und laufen weiter
    über die REAL-Spalte (Parität zum Legacy-Float-Vergleich).
    """
    return isinstance(value, int) and not isinstance(value, bool) and abs(value) >= _MAX_EXACT_INT


def _obs_num_cmp_impl(raw: Any, op: str, expected_text: str) -> int:
    """SQLite-Callback: exakter numerischer Vergleich der JSON-Wertspalte (#951, Codex :332).

    Für unsichere Integer (außerhalb ±2**53) reicht die lossy REAL-Spalte nicht. Der
    Callback dekodiert den gespeicherten JSON-Wert (``old_value``/``new_value``) und
    vergleicht ihn EXAKT gegen den (als Text übergebenen) int-Filterwert – genau wie der
    Legacy-Python-Vergleich auf den dekodierten Werten. Nicht-numerische bzw.
    malformed/non-JSON-Spalten matchen nie (Rückgabe 0). ``bool`` matcht nicht, weil der
    Referenzvergleich Range/eq auf big-int-Ebene numerisch ist.
    """
    if not isinstance(raw, str):
        return 0
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(decoded, (int, float)) or isinstance(decoded, bool):
        return 0
    expected = int(expected_text)
    if op == "eq":
        return 1 if decoded == expected else 0
    if op == "ne":
        return 1 if decoded != expected else 0
    if op == "gt":
        return 1 if decoded > expected else 0
    if op == "gte":
        return 1 if decoded >= expected else 0
    if op == "lt":
        return 1 if decoded < expected else 0
    if op == "lte":
        return 1 if decoded <= expected else 0
    return 0  # pragma: no cover - Operator wird beim Clause-Bau validiert


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

    # regex — dieselbe Härtung wie der v2-Guarded-Zweig (inkl. quantifizierter
    # Alternation, #951 Codex :307). Auch dieser Python-Fallback läuft synchron je
    # Kandidatenzeile und ist nicht per Timeout abbrechbar.
    pattern = spec.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("regex requires a non-empty pattern")
    _assert_safe_regex(pattern)
    if not isinstance(actual, str):
        return False
    flags = re.IGNORECASE if ignore_case else 0
    # Zu langen Zielwert ABLEHNEN statt truncaten (#951, Codex :499): der Legacy-Pfad
    # (``_match_regex`` in ringbuffer.py) wirft für ``len(value) > _REGEX_MAX_TARGET_LEN``
    # einen 422-tauglichen ValueError. Würde hier stattdessen auf den Prefix gekappt und
    # nur dieser durchsucht, hinge das Ergebnis von der Truncation-Grenze ab (ein Match
    # nach Byte 4096 fiele still weg; ``$``-Anker matchte die künstliche Grenze) → keine
    # Parität. Daher identisch zum Legacy ablehnen.
    if len(actual) > _REGEX_MAX_TARGET_LEN:
        raise ValueError("unsafe regex pattern: target value too long")
    try:
        return re.compile(pattern, flags).search(actual) is not None
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
        # Segment-IDs, deren Basisdatei ``_delete_segment`` NICHT unlinken konnte
        # (Permission/Lock/EBUSY), #951 [P2] :2575. Solche Bytes bleiben auf der
        # Platte belegt, obwohl das Segment retention-eligible ist; sie dürfen im
        # ``retention_over_budget``-Pressure-Test NICHT als freigebbar abgezogen
        # werden (sonst meldete ``/stats`` fälschlich unter-Budget). Ein späterer
        # erfolgreicher Delete räumt die ID wieder aus.
        self._unlink_blocked_segment_ids: set[int] = set()
        # Lazy-Schaetzung attachter Legacy-Segmente fuer /stats (#964-Follow-up):
        # (row_estimate, from_ts, to_ts) je segment_id. Die Quelle ist read-only,
        # der Wert damit stabil; beim Delete wird der Eintrag mitgeraeumt.
        self._legacy_stats_cache: dict[int, tuple[int, str | None, str | None]] = {}

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
            await self._reconcile_multiple_active_segments()
            active = await self.manifest.get_active_segment()
            if active is None:
                active = await self._create_segment_locked()
            else:
                active = await self._recover_missing_active_segment(active)
            active = await self._recover_corrupt_active_segment(active)
            self._active_segment = active
            self._active_conn = await self._open_segment_conn(active.filename)
            # Aktive-Segment-Stats aus dem tatsächlichen Inhalt reparieren (#951,
            # Codex :998, Runde 33). Crasht der Prozess / läuft die Disk voll /
            # scheitert das Manifest-Update NACH einem Segment-Commit, aber BEVOR
            # ``_refresh_active_segment_stats()`` in die separate Manifest-DB committet,
            # trägt das aktive Segment über den Restart STALE ``from_ts``/``to_ts``/
            # ``row_count``. Zeitfenster-Queries wählen Segmente anhand dieser Manifest-
            # Grenzen (``list_segments_for_query``) → ein Fenster könnte das aktive
            # Segment ausschließen, obwohl es committete Zeilen im Fenster hält, bis ein
            # weiterer Append die Stats auffrischt. Analog zu den Corrupt-/Empty-Segment-
            # Checks (Runde 19/24) wird die Reparatur daher konsistent in den ``open()``-
            # Pfad gezogen: der Refresh berechnet MIN(ts)/MAX(ts)/row_count/size aus dem
            # committeten Inhalt neu und schreibt sie ins Manifest, bevor das Segment
            # abfragbar wird. Für ein frisch angelegtes (leeres) aktives Segment ist der
            # Refresh idempotent (row_count=0, Grenzen NULL) und schadet nicht.
            await self._refresh_active_segment_stats()
        except Exception:
            # Scheitert ein Schritt NACH erfolgreichem manifest.open() (z.B. ein korruptes/
            # nicht schreibbares aktives Segment in _create_segment_locked/_open_segment_conn),
            # gab der alte Pfad nur die Lease frei. Da RingBuffer._open_segment_store_locked()
            # erst NACH Rueckkehr von open() aufraeumt, leakten dann die Manifest-aiosqlite-
            # Connection/-Thread (und eine evtl. schon geoeffnete aktive Segment-Connection).
            # Daher hier ALLE bereits geoeffneten Ressourcen best-effort schliessen, bevor der
            # Originalfehler propagiert (#951, Codex :564). Fehler beim Aufraeumen duerfen den
            # Originalfehler nicht ueberdecken.
            if self._active_conn is not None:
                with contextlib.suppress(Exception):
                    await self._active_conn.close()
                self._active_conn = None
            self._active_segment = None
            with contextlib.suppress(Exception):
                await self.manifest.close()
            with contextlib.suppress(Exception):
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

    async def _reconcile_multiple_active_segments(self) -> None:
        """Löst einen Zwei-``active``-Zustand beim Öffnen robust auf (#951, Codex :2485).

        ``rotate()`` macht das Ersatz-Segment ZUERST durabel/aktiv und schließt erst
        DANACH das alte Segment (Runde-38-Fix :2463: bei einem Fehler bleibt der alte
        Writer schreibbar). Beendet sich der Prozess WÄHREND der Rotation NACH der
        Aktivierung des neuen Segments, aber BEVOR das alte unten als
        ``closed``/``checkpoint_pending`` markiert ist, bleiben ZWEI ``active``-Zeilen
        im Manifest zurück. Beim Restart wählt ``get_active_segment()`` das neuere
        (höchste ``segment_id``) zum Schreiben; das ältere ``active``-Segment wird nie
        mehr beschrieben, ist aber auch NIE retention-eligible (``active`` ist von der
        Retention ausgenommen) → seine Alt-Daten bleiben permanent unlöschbar und der
        Store bleibt über Budget.

        Analog zum Corrupt-/Missing-Active-Recovery (Runde 19) wird der Zustand daher
        beim Öffnen aufgelöst: Gibt es mehr als ein ``active``-Segment, bleibt das
        neueste (höchste ``segment_id``, exakt das von ``get_active_segment()``
        gewählte) aktiv; alle älteren werden auf ``closed`` demotet und damit
        retention-eligible. So ist WEDER der Runde-38-Fehlerfall (Writer bleibt
        schreibbar) NOCH dieser harte Crash-Fall (zwei active, alt stuck) problematisch.
        """
        active_segments = [s for s in await self.manifest.list_segments() if s.status == SEGMENT_STATUS_ACTIVE]
        if len(active_segments) <= 1:
            return
        # list_segments() liefert aufsteigend nach segment_id → das letzte ist das
        # neueste (bleibt aktiv), alle davor werden geschlossen.
        for stale in active_segments[:-1]:
            await self.manifest.close_segment(stale.segment_id)

    async def _recover_missing_active_segment(self, active: SegmentRecord) -> SegmentRecord:
        """Behandelt ein aktives Segment, dessen Datei beim (Wieder-)Öffnen fehlt (#951, Codex :576).

        Wird ein bestehendes Manifest wieder geöffnet, nachdem die Datei des AKTIVEN
        Segments entfernt wurde, würde der anschließende ``_open_segment_conn()`` mit
        einem normalen SCHREIBBAREN Open still eine frische LEERE DB am alten Dateinamen
        anlegen. Das Manifest beschriebe weiter die alten Zeilen (bis ein späterer
        Stats-Refresh), Queries verlören die Daten still.

        Fehlt die Datei, wird das alte (fehlende) aktive Segment daher als verloren
        markiert (quarantäniert, konsistent zum Missing-File-Skip des Read-Pfads) und
        ein FRISCHES aktives Segment mit neuer Manifest-Zeile eröffnet – so behauptet
        das Manifest keine „lebenden" Zeilen mehr, die es nicht mehr gibt, und es
        entsteht keine leere Ersatz-DB unter altem Namen. Ist die Datei vorhanden,
        bleibt das aktive Segment unverändert.
        """
        if (self._segments_dir / active.filename).exists():
            return active
        await self.manifest.mark_quarantined(active.segment_id, reason="active segment file missing on open")
        return await self._create_segment_locked()

    async def _recover_corrupt_active_segment(self, active: SegmentRecord) -> SegmentRecord:
        """Behandelt ein aktives Segment, dessen Datei beim Startup KORRUPT ist (#951, Pkt 2).

        Ist die Datei des zuvor aktiven Segments vorhanden, aber keine gültige
        SQLite-DB (Bitfehler/abgeschnittener Write/…), scheiterte
        ``_open_segment_conn(active.filename)`` mit einer SQLite-Korruptions-
        Exception. Der ``open()``-Fehlerpfad schloss dann nur Ressourcen und re-raiste
        → ein EINZIGES kaputtes Tail-Segment blockierte den ganzen RingBuffer-/OBS-
        Startup, obwohl geschlossene korrupte Segmente im Read-Pfad sonst isoliert
        (quarantäniert + übersprungen) werden.

        Konsistent zu ``_recover_missing_active_segment`` (fehlende Datei) wird ein
        KORRUPTES aktives Segment daher als ``quarantined``/``corrupt`` markiert und ein
        FRISCHES aktives Segment eröffnet. Nur echte SQLite-Korruption löst das aus;
        andere Fehler (z. B. Permission) propagieren unverändert, damit sie nicht als
        Korruption maskiert werden. Ist die Datei intakt (oder fehlt sie – dann hat
        ``_recover_missing_active_segment`` bereits ein frisches Segment geliefert),
        bleibt das aktive Segment unverändert.
        """
        # Nur eine echte (in-place liegende) Datei kann korrupt sein; ein frisch
        # angelegtes Segment aus dem Missing-Recovery existiert noch nicht auf Platte.
        if not (self._segments_dir / active.filename).exists():
            return active
        probe: aiosqlite.Connection | None = None
        try:
            probe = await self._open_segment_conn(active.filename)
        except aiosqlite.Error as exc:
            if not _is_sqlite_corruption(exc):
                raise
            await self.manifest.mark_quarantined(active.segment_id, reason=str(exc))
            return await self._create_segment_locked()
        # Leere/truncatete Datei als verloren behandeln (#951, Codex :658): wurde die
        # aktive Segment-Datei auf 0 Bytes truncated (abgeschnittener Write/Crash) oder
        # durch eine frische, gültige aber LEERE SQLite-DB ersetzt, wirft
        # ``_open_segment_conn`` KEINE Korruption – es legt still das Segment-Schema neu
        # an und liefert eine leere Tabelle. Das Manifest behauptet dann weiter
        # ``row_count > 0`` (die alten Tail-Zeilen), die aber physisch weg sind →
        # nachfolgende Queries verfehlen sie still. Erwartet das Manifest Zeilen, die
        # Datei enthält aber keine, wird das Segment daher – analog zum Korruptionspfad –
        # quarantäniert und ein frisches aktives Segment eröffnet. Der ``open()``-Fehler-
        # pfad muss die Probe-Connection dabei auch bei einem COUNT-Fehler schließen.
        try:
            if active.row_count > 0 and await self._segment_is_empty(probe):
                await probe.close()
                probe = None
                await self.manifest.mark_quarantined(active.segment_id, reason="active segment file empty but manifest expects rows")
                return await self._create_segment_locked()
        finally:
            if probe is not None:
                # Datei ist eine gültige, befüllte SQLite-DB → die Probe-Connection
                # schließen; der reguläre Pfad öffnet die aktive Connection gleich erneut.
                await probe.close()
        return active

    @staticmethod
    async def _segment_is_empty(conn: aiosqlite.Connection) -> bool:
        """True, wenn die ``ringbuffer``-Tabelle des Segments keine Zeile enthält."""
        async with conn.execute("SELECT EXISTS(SELECT 1 FROM ringbuffer) AS has_rows") as cur:
            row = await cur.fetchone()
        return not (row and row["has_rows"])

    @staticmethod
    async def _segment_missing_rows(conn: aiosqlite.Connection) -> bool:
        """True, wenn das Segment keine Zeilen ODER gar keine ``ringbuffer``-Tabelle hat.

        Robuster als ``_segment_is_empty`` für den pending-Checkpoint-Pfad (#951,
        Codex :2220): eine auf 0 Bytes truncatete Datei öffnet als gültige, aber
        LEERE DB ganz ohne Tabellen – ein ``SELECT ... FROM ringbuffer`` würfe dort
        ``no such table``. Fehlt die Tabelle, gilt das als „keine Zeilen".
        """
        async with conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ringbuffer'") as cur:
            has_table = await cur.fetchone()
        if not has_table:
            return True
        return await SqliteSegmentStore._segment_is_empty(conn)

    async def _open_segment_conn(self, filename: str) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(str(self._segments_dir / filename))
        try:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys=ON")
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.executescript(_SEGMENT_SCHEMA)
            await conn.commit()
        except BaseException:
            # Schlägt eine PRAGMA/Schema-Anweisung fehl (z. B. korruptes File beim
            # Startup-Korruptions-Probe, #951, Pkt 2), darf die bereits geöffnete
            # aiosqlite-Connection/-Thread nicht leaken. Best-effort schließen, bevor
            # der Originalfehler propagiert.
            with contextlib.suppress(Exception):
                await conn.close()
            raise
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
            # commit() MIT im rollback-geschützten Block (#951, Codex :1013): meldet
            # SQLite einen Fehler WÄHREND des commit selbst (volle Disk / I/O-Fehler
            # nach eingereihten Inserts), bliebe die Transaktion sonst offen und ihre
            # Zeilen würden vom nächsten erfolgreichen append() auf derselben Connection
            # MIT-committet, obwohl der Aufrufer einen Fehler sah. Insert(s) UND commit
            # daher gemeinsam absichern.
            await self._active_conn.commit()
        except BaseException:
            # Scheitert ein Insert mitten im Batch (z.B. nicht serialisierbare Metadaten
            # oder ein fehlgeschlagener Metadaten-Index-Insert) ODER das commit selbst,
            # bleiben die früheren Inserts sonst in der offenen Transaktion und würden vom
            # nächsten erfolgreichen append() auf derselben Connection MIT-committet,
            # obwohl der Aufrufer einen Fehler sah (#951, Codex :584/:1013). Aktive
            # Transaktion daher zurückrollen – kein partieller Batch committet später.
            await self._active_conn.rollback()
            raise
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
        #
        # Zustandsabhängiger Frühabbruch (#951, Codex :980): den Frühabbruch NICHT global
        # abschalten, sobald ein attached ``legacy``-Segment im Set liegt, sondern nur
        # den POSITIVEN v2-Prefix früh terminieren lassen. ``list_segments_for_query``
        # liefert die positiven v2-Segmente (segment_id DESC = global_event_id DESC)
        # ZUERST und alle Legacy-Segmente (negative synthetische gids, per Definition
        # älter) ZULETZT.
        #
        # KORREKTHEIT: Solange NUR positive v2-Prefix-Zeilen gesammelt wurden, sind alle
        # noch NICHT gelesenen Segmente (älterer positiver v2 ODER legacy) garantiert
        # kleiner-gid – ein Frühabbruch bei ``offset+limit`` gefüllten Zeilen ist dann
        # korrekt. Eine latest-N-``id desc``-Query, die bereits aus positiven v2-Zeilen
        # voll wird, bricht daher früh ab und fasst den Legacy-Tail NICHT an (bounded
        # latest-page).
        #
        # Sobald der positive Prefix NICHT reicht und wir das ERSTE Legacy-Segment
        # lesen, wird der Frühabbruch konservativ gesperrt: ab dem Tail werden ALLE
        # verbliebenen relevanten Segmente ``offset+limit``-bounded gelesen und der
        # finale Sort nach ``global_event_id`` in ``query()`` ordnet korrekt (positive
        # vor negative gid). Bei abweichender Sortierung (``ts``/``asc``) greift der
        # Frühabbruch gar nicht.
        allow_early_termination = query.sort_field == "id" and query.sort_order == "desc"
        entered_legacy_tail = False
        for segment in segments:
            # Frühabbruch nur, solange ausschließlich positive v2-Prefix-Zeilen gesammelt
            # wurden (``not entered_legacy_tail``). Danach ist die verbliebene gid-Ordnung
            # nicht mehr an die Iterationsreihenfolge gebunden – nicht früh abbrechen.
            if allow_early_termination and not entered_legacy_tail and needed and len(collected) >= needed:
                break
            if _is_legacy_segment(segment):
                entered_legacy_tail = True
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

        Nur echte SQLite-Korruption (malformed/not a database/…) ODER ein
        schemaloses Segment (fehlende ``ringbuffer``-Tabelle, #951 Codex :1057) führt
        zur Quarantäne; andere ``aiosqlite.Error`` werden weitergereicht, damit echte
        Fehler (z. B. Programmierfehler im SQL) nicht als Korruption maskiert werden.
        Das aktive Segment wird nie quarantäniert.
        """
        if not (_is_sqlite_corruption(exc) or _is_missing_ringbuffer_table(exc)):
            raise exc
        if self._active_segment is not None and segment.segment_id == self._active_segment.segment_id:
            raise exc
        await self.manifest.mark_quarantined(segment.segment_id, reason=str(exc))
        return None

    async def _connection_for_read(self, segment: SegmentRecord) -> aiosqlite.Connection:
        if _is_legacy_segment(segment):
            return await self._open_legacy_read_conn(segment)
        # v2-Segment (#951, Pkt 2): read-only-URI (``mode=ro``) statt schreibendem
        # ``connect``. Ein schreibendes Open auf eine zwischenzeitlich gelöschte
        # Datei legte still eine leere Ersatz-DB an → „no such table" → 500. ``mode=ro``
        # wirft in dem Fall (der Aufrufer überspringt das Segment). Zusätzlich werden
        # geschlossene Segmente so nie versehentlich schreibend geöffnet.
        #
        # AKTIVES Segment (#951, Codex :1124): auch der Read des aktiven Segments läuft
        # über eine SEPARATE read-only Connection, NICHT über die gehaltene Writer-
        # Connection ``_active_conn``. Gäbe man ``_active_conn`` zurück, liefe ein SELECT
        # zwischen einem Insert und dessen Commit auf derselben Connection – SQLite zeigt
        # dort uncommittete Zeilen. Eine Monitor-/API-Query könnte so Zeilen sehen, deren
        # Metadaten-Indizes noch unvollständig sind oder die bei einem fehlgeschlagenen
        # Append zurückgerollt werden. Der Store läuft im WAL-Modus, daher sieht eine
        # read-only Connection alle COMMITTETEN Transaktionen (konsistent damit, wie
        # geschlossene Segmente bereits read-only geöffnet werden). Die Connection wird
        # vom Aufrufer (``_read_segment_rows``) nach dem Read geschlossen (``close_after``
        # greift, weil es nicht ``_active_conn`` ist) – kein Leak.
        uri = _sqlite_ro_uri(self._segments_dir / segment.filename, params="mode=ro")
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

        Immutable-vs-WAL-aware-Entscheidung (#951, Codex :1214): ``immutable=1`` ist
        nur zulässig, wenn KEIN dirty ``-wal`` (mehr) neben der Datei liegt – also
        entweder von vornherein keiner vorhanden war ODER der Checkpoint ihn
        erfolgreich in die Haupt-DB gefaltet hat. Konnte der Checkpoint einer kleinen
        dirty-WAL-Legacy-DB NICHT abgeschlossen werden (BUSY/Fehler), stünden die
        jüngsten committeten Frames weiterhin nur im ``-wal``; ein ``immutable=1``-Open
        ignorierte sie und die Monitor-/Export-Query ließe die NEUESTEN Alt-Zeilen
        still weg, bis ein späterer Read den Checkpoint schafft. In diesem Fall wird
        daher WAL-aware mit reinem ``mode=ro`` (OHNE ``immutable``) gelesen, sodass die
        committeten WAL-Frames sichtbar sind. Die Entscheidung fällt anhand des
        physischen ``-wal``-Dirty-Zustands NACH dem Checkpoint-Versuch (``_wal_is_dirty``).
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
        params = "mode=ro" if self._legacy_wal_still_dirty(legacy_path) else "mode=ro&immutable=1"
        uri = _sqlite_ro_uri(legacy_path, params=params)
        conn = await aiosqlite.connect(uri, uri=True)
        conn.row_factory = aiosqlite.Row
        return conn

    @staticmethod
    def _legacy_wal_still_dirty(legacy_path: Path) -> bool:
        """True, wenn neben der Legacy-DB weiterhin ein nicht-leeres ``-wal`` liegt (#951, Codex :1214).

        Entscheidet, ob der Read auf ``immutable=1`` (schnell, WAL-ignorierend) oder auf
        WAL-awares ``mode=ro`` fällt. Nutzt die vorhandene physische Dirty-WAL-Erkennung
        aus ``migration`` (nur Dateisystem-Metadaten, öffnet die DB nicht). Lazy
        importiert, weil ``migration`` seinerseits ``sqlite_backend`` importiert
        (Zyklusvermeidung, analog ``_legacy_is_small``).
        """
        from obs.ringbuffer.store.migration import _wal_is_dirty

        return _wal_is_dirty(legacy_path)

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
        # Query-gescopter Too-Long-Marker für den Regex-Callback (#951, Codex :499).
        regex_target_too_long: list[bool] = [False]
        if any(str(f.get("operator", "")).strip().lower() == "regex" for f in query.value_filters):
            # REGEXP-Callback nur registrieren, wenn ein Regex-Filter vorliegt.
            # Registrierung erfolgt lokal auf der übergebenen Read-Connection. Das Muster
            # wurde beim Clause-Bau bereits als safe gehärtet (#951, Codex :307). Der
            # Callback ist NICHT mehr deterministisch registriert, weil er über den
            # geteilten Marker einen Nebeneffekt (Too-Long-Erkennung) trägt.
            await conn.create_function("obs_regexp", 3, _make_obs_regexp_impl(regex_target_too_long))
        if self._has_json_eq_filter(query.value_filters):
            # JSON-eq/ne-Callback nur registrieren, wenn ein komplexer (list/dict)
            # eq/ne-Filterwert vorliegt (#951, Codex :1281).
            await conn.create_function("obs_json_eq", 2, _obs_json_eq_impl, deterministic=True)
        if self._has_icontains_filter(query.value_filters):
            # Unicode-fähigen contains-Callback nur registrieren, wenn ein
            # case-insensitives ``contains`` vorliegt (#951, Codex :1364).
            await conn.create_function("obs_icontains", 2, _obs_icontains_impl, deterministic=True)
        if self._has_num_cmp_filter(query.value_filters):
            # Exakt-Integer-Callback nur registrieren, wenn ein Filter einen unsicheren
            # Integer (außerhalb ±2**53) trägt (#951, Codex :332).
            await conn.create_function("obs_num_cmp", 3, _obs_num_cmp_impl, deterministic=True)
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        if regex_target_too_long[0]:
            # Ein Kandidat trug einen Zielwert über ``_REGEX_MAX_TARGET_LEN`` (#951,
            # Codex :499). Wie der Legacy-Pfad als Validierungsfehler ablehnen, statt
            # still auf den Prefix zu truncaten – die 422-Meldung propagiert aus diesem
            # ValueError sauber bis zur API.
            raise ValueError("unsafe regex pattern: target value too long")
        return [self._row_to_dict(row) for row in rows]

    async def _legacy_scan_plan(self, conn: aiosqlite.Connection, query: StoreQuery) -> LegacyScanPlan:
        """Baut das batch-gescopte Legacy-Roh-SELECT (WHERE + ORDER BY + LIMIT/OFFSET).

        Liefert einen ``LegacyScanPlan``. ``base_sql`` und ``name_hit_sql`` erwarten
        jeweils zwei Platzhalter am Ende (``LIMIT ? OFFSET ?``), die der Batch-Fetch
        bindet. Vom Legacy-Read (``_query_legacy_segment``) genutzt.
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
        # Freitext-``q`` NICHT in das SQL-WHERE pushen (#951, Pkt 3): ``q`` matcht
        # ``datapoint_id``/``source_adapter`` per ``LIKE '%…%'`` und kann die
        # vorhandenen datapoint/source-Indexe nicht nutzen. Als SQL-Prädikat mit dem
        # ``LIMIT`` zusammen zwänge es SQLite bei seltenen/fehlenden Treffern zu einem
        # Full-Scan über die 20–30 GB Legacy-Datei, bevor das ``LIMIT`` erfüllt ist.
        # Stattdessen wird ``q`` – wie value/metadata – bounded in Python auf der
        # gedeckelten Kandidatenmenge ausgewertet (siehe Post-Filter-Pfad unten).
        #
        # ``q`` und ``dp_ids_by_name`` sind per Legacy-Semantik OR-verknüpft. Ist ``q``
        # gesetzt, muss deshalb der GESAMTE OR-Block in Python laufen: den index-
        # tauglichen ``dp_ids_by_name``-``IN``-Teil als eigenes SQL-``AND`` zu pushen
        # würde die OR- in eine AND-Semantik verwandeln (eine nur über ``q`` matchende
        # Zeile fiele durch das SQL-``IN`` heraus). Nur wenn ``q`` FEHLT (kein Scan-
        # Risiko), bleibt das reine ``dp_ids_by_name``-``IN`` index-tauglich im SQL.
        q_is_scan = bool((query.q or "").strip())
        name_clause, name_params = self._legacy_dp_ids_by_name_clause(query)
        base_clauses = list(clauses)
        base_params = list(params)
        if not q_is_scan and name_clause:
            clauses.append(name_clause)
            params.extend(name_params)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        # Metadaten-Tag/Binding-Filter kann eine v1/Legacy-DB nicht per Index-
        # Subquery bedienen (die Index-Tabellen fehlen dort). Sie werden bounded
        # in Python auf den dekodierten metadata-JSON ausgewertet — wie die
        # Value-Filter. Beides zusammen (und der bounded Freitext-``q``) erzwingt den
        # Kandidaten-Cap.
        has_python_post_filter = bool(query.value_filters) or self._has_metadata_filter(query) or bool((query.q or "").strip())
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
        select_cols = f"SELECT id, ts, datapoint_id, topic, old_value, new_value, source_adapter, quality, {metadata_select} FROM ringbuffer"
        order_suffix = f"ORDER BY {candidate_order} {direction}, id {direction} LIMIT ? OFFSET ?"
        base_sql = f"{select_cols}{where} {order_suffix}"

        # Name-Treffer-Widening für den LEGACY-Read (#951, Runde 46, :1686 – analog
        # zum v2-Fix Runde 36, :2262): ist ``q`` gesetzt, läuft der gesamte ``q``/
        # ``dp_ids_by_name``-OR-Block bounded in Python auf der gedeckelten
        # Kandidatenmenge – der index-taugliche ``dp_ids_by_name``-``IN``-Arm würde
        # damit fälschlich MIT gedeckelt. Eine per NAME gematchte Legacy-Zeile, deren
        # rowids ÄLTER als die neuesten ``candidate_cap`` Roh-Zeilen sind und deren
        # id/source ``q`` nicht enthält, fiele so aus dem Ergebnis, obwohl der
        # ``IN``-Arm über ``datapoint_id`` indizierbar ist. Der IN-Arm wird deshalb
        # als EIGENES, separat gedeckeltes SELECT geführt (eigener Cap NUR über die
        # Namens-Treffer statt Konkurrenz um die globalen Cap-Slots);
        # ``_query_legacy_segment`` fetcht ihn einmalig und merged ihn dedupliziert
        # in die Kandidatenmenge. So bleibt Parität zur un-capped Legacy-OR-Query
        # (``segmented=False``), während die index-untauglichen ``LIKE``-Arme
        # (id/source) weiter im gedeckelten Kandidaten-Pfad bounded ausgewertet werden.
        name_hit_sql: str | None = None
        name_hit_params: list[Any] = []
        if q_is_scan and name_clause:
            name_where_parts = [*base_clauses, name_clause]
            name_where = f" WHERE {' AND '.join(name_where_parts)}"
            name_hit_sql = f"{select_cols}{name_where} {order_suffix}"
            name_hit_params = [*base_params, *name_params]
        return LegacyScanPlan(base_sql, params, has_python_post_filter, name_hit_sql, name_hit_params)

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
          jeder v2-Zeile (positive IDs) – Legacy-Daten sind per Definition älter als
          jedes nach Aktivierung geschriebene v2-Segment – und behalten intern ihre
          ts/rowid-Ordnung.
        * **Value-Filter** werden NICHT typisiert in SQL gepusht (die Spalten fehlen),
          sondern kontrolliert **bounded** in Python auf den dekodierten JSON-Werten
          ausgewertet. Der Kandidatensatz ist auf ``candidate_cap`` bzw. einen Default-
          Cap begrenzt, damit ein Value-Filter über Legacy nicht in einen unbounded
          Full-Scan über 20–30 GB kippt.
        """
        plan = await self._legacy_scan_plan(conn, query)

        needed = max(query.offset, 0) + max(query.limit, 0)
        if not plan.has_python_post_filter:
            # Kein Post-Filter → jede Roh-Zeile ist ein Treffer; ``offset+limit`` roh
            # holen reicht (der Aufrufer sortiert+slict final über alle Segmente).
            batch, _fetched = await self._fetch_legacy_batch(conn, plan.base_sql, plan.params, segment, query, needed, 0)
            return batch

        # Post-Filter aktiv. Zwei Modi, unterschieden am EXPLIZITEN ``is_export``-Flag
        # (#951, Pkt 4) statt an einer ``candidate_cap``-Heuristik:
        #
        # * **Monitor-Live-View** (``is_export=False``): der Cap deckelt die
        #   betrachteten Roh-Kandidaten hart. Treffer jenseits der neuesten
        #   ``candidate_cap`` Zeilen werden bewusst NICHT gefunden — das hält den Scan
        #   auf einer 20–30 GB Legacy-DB gebunden statt in einen Full-Scan zu kippen.
        #   Eine Live-Query mit großem ``limit``/Offset (z. B. ``limit=10000``) bleibt
        #   damit bounded; die frühere ``candidate_cap <= offset+limit``-Heuristik hätte
        #   sie fälschlich als Export eingestuft und über die ganze Legacy-Datei gescannt.
        # * **Gefilterter Export** (``is_export=True``, CSV-Export): der Export MUSS die
        #   vollständige gematchte Menge liefern. Der Roh-Cap darf die gematchte Ausgabe
        #   nicht abschneiden – sonst stoppte die Export-Schleife bei spärlichen Treffern
        #   auf einem leeren/kurzen Chunk, obwohl spätere Legacy-Zeilen matchen
        #   (unvollständiger Export).
        #
        # Speicher-/Vollständigkeits-Abwägung (Export): im Export-Modus wird ``ringbuffer``
        # in ``candidate_cap``-großen Batches gescannt, bis genug GEMATCHTE Zeilen für
        # ``offset+limit`` zusammen sind ODER das Segment erschöpft ist. Für sehr spärliche
        # Treffer kann das den gesamten Legacy-Bestand durchlaufen — bewusst zugunsten der
        # Vollständigkeit; der bounded Monitor-Pfad bleibt davon unberührt. Es liegen dabei
        # nie mehr als ``offset+limit`` gematchte Records gleichzeitig im Speicher (die
        # nicht-matchenden Roh-Zeilen jedes Batches werden sofort verworfen).
        raw_cap = self._legacy_candidate_cap(query)
        is_export = query.is_export
        results: list[dict[str, Any]] = []
        raw_offset = 0
        while True:
            rows, fetched = await self._fetch_legacy_batch(conn, plan.base_sql, plan.params, segment, query, raw_cap, raw_offset)
            results.extend(rows)
            raw_offset += fetched
            if not is_export:
                # Monitor: genau EIN gedeckelter Batch, harte Grenze.
                break
            if len(results) >= needed or fetched < raw_cap:
                # Export: genug Treffer für das Fenster ODER Segment erschöpft.
                break
        # Name-Treffer-Widening (#951, Runde 46, :1686): der ``dp_ids_by_name``-``IN``-
        # Arm läuft im Monitor-Pfad als EIGENER, separat gedeckelter Fetch – Namens-
        # Treffer konkurrieren nicht mit den unrelated neuesten Zeilen um die
        # Cap-Slots. Dedupe über den synthetischen ``global_event_id`` (bijektiv zur
        # rowid). Im Export-Pfad überflüssig: der erschöpfende Batch-Scan oben prüft
        # ohnehin JEDE Zeile über den Python-OR-Block.
        if plan.name_hit_sql is not None and not is_export:
            name_rows, _fetched = await self._fetch_legacy_batch(conn, plan.name_hit_sql, plan.name_hit_params, segment, query, raw_cap, 0)
            seen = {row["global_event_id"] for row in results}
            results.extend(row for row in name_rows if row["global_event_id"] not in seen)
        return results

    async def _fetch_legacy_batch(
        self,
        conn: aiosqlite.Connection,
        base_sql: str,
        base_params: list[Any],
        segment: SegmentRecord,
        query: StoreQuery,
        limit: int,
        raw_offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Ein Roh-Batch der Legacy-DB fetchen + Post-Filter in Python anwenden.

        Liefert ``(gematchte_records, roh_zeilen_im_batch)``. ``roh_zeilen_im_batch``
        (vor Filter) treibt Batch-Fortschritt und Erschöpfungs-Erkennung; die
        gematchten Records sind das gefilterte Ergebnis dieses Batches.
        """
        params = [*base_params, limit, raw_offset]
        async with conn.execute(base_sql, params) as cur:
            raw_rows = await cur.fetchall()
        matched: list[dict[str, Any]] = []
        for row in raw_rows:
            record = self._legacy_row_to_dict(row, segment.segment_id)
            # Freitext-``q`` bounded in Python (#951, Pkt 3): auf der bereits
            # gedeckelten Kandidatenmenge, nicht als unbounded SQL-``LIKE``-Scan.
            if not self._legacy_q_matches(record, query):
                continue
            if query.value_filters and not _legacy_row_matches_filters(record, query.value_filters):
                continue
            if not self._legacy_metadata_matches(record, query):
                continue
            matched.append(record)
        return matched, len(raw_rows)

    @staticmethod
    def _legacy_dp_ids_by_name_clause(query: StoreQuery) -> tuple[str | None, list[Any]]:
        """Nur der index-taugliche ``dp_ids_by_name``-``IN (...)``-Teil des Freitext-Filters (#951, Pkt 3).

        Der ``q``-``LIKE``-Teil wird bewusst NICHT hier gebaut, sondern bounded in
        Python ausgewertet (``_legacy_q_matches``), damit ein seltener/fehlender
        Freitext-Treffer keinen unbounded Full-Scan über die Legacy-Datei auslöst.
        """
        if not query.dp_ids_by_name:
            return None, []
        placeholders = ",".join("?" * len(query.dp_ids_by_name))
        return f"datapoint_id IN ({placeholders})", list(query.dp_ids_by_name)

    @staticmethod
    def _legacy_q_matches(record: dict[str, Any], query: StoreQuery) -> bool:
        """Python-Auswertung des Freitext-``q`` (#951, Pkt 3), Semantik wie das SQL-``LIKE``.

        ``q`` matcht, wenn es (case-insensitiv, wie SQLite-``LIKE`` für ASCII) als
        Teilstring in ``datapoint_id`` ODER ``source_adapter`` vorkommt. Der
        ``dp_ids_by_name``-Teil (OR-verknüpft) ist bereits index-tauglich im SQL
        abgehandelt; ist ``q`` leer, matcht alles (kein Freitext-Filter aktiv).

        Parität zur Legacy-``query_v2``-Semantik: ``q`` und ``dp_ids_by_name`` sind
        OR-verknüpft. Da der ``dp_ids_by_name``-Teil bereits per SQL-``IN`` selektiert
        wurde, würde ein bereits über den Namen selektierter Datensatz hier vom
        ``q``-Teilstring-Test fälschlich verworfen. Deshalb matcht eine Zeile, deren
        ``datapoint_id`` in ``dp_ids_by_name`` liegt, unabhängig vom ``q``-Test (die
        OR-Bedingung ist über den Namens-Zweig bereits erfüllt).
        """
        q = (query.q or "").strip()
        if not q:
            return True
        if query.dp_ids_by_name and record.get("datapoint_id") in query.dp_ids_by_name:
            return True
        needle = q.lower()
        dp_id = str(record.get("datapoint_id") or "").lower()
        adapter = str(record.get("source_adapter") or "").lower()
        return needle in dp_id or needle in adapter

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
        # Die ``segment_id`` skaliert einen disjunkten Per-Quelle-Block
        # (#951, Codex :1123): jede attached Legacy-DB belegt einen eigenen
        # ``_LEGACY_GID_STRIDE``-breiten rowid-Bereich, sodass zwei Legacy-Quellen
        # NIE dieselbe synthetische ID erzeugen (rowid r der einen kollidierte
        # sonst mit rowid r+1 der nächsten).
        #
        # Cross-Source-Ordnung (#951, Codex :1558): ``segment_id`` steigt mit der
        # Registrierungsreihenfolge, d. h. eine HÖHERE ``segment_id`` ist die NEUERE
        # Quelle (so behandelt auch ``_retention_victim_order`` ältestes Legacy =
        # niedrigste segment_id zuerst). Damit die neuere Quelle im Default-``id desc``
        # VOR der älteren pagt, muss sie den WENIGER negativen Block bekommen. Deshalb
        # wird ``segment_id`` an der festen Bucket-Schranke ``B`` gespiegelt
        # (``B - 1 - seg``): höhere segment_id ⇒ kleinerer Faktor ⇒ weniger negativ.
        # (Die frühere ``- segment_id * STRIDE``-Formel gab der ÄLTEREN Quelle die
        # weniger negativen IDs und invertierte so die Cross-Source-Chronologie.)
        #
        # Worst-Case-Grenzen (JS-sicher, ``[-(2**53-1), 0)``, #951, Runde 23): der Betrag
        # ist maximal bei ``seg=0`` (Faktor ``B-1``) und rowid=1:
        #   ``1 - (1<<52) - (2**20 - 1) * (1<<32) = -9_007_194_959_773_695`` (> -(2**53-1),
        #   ~1 STRIDE Reserve). Am wenigsten negativ bei höchstem ``seg`` (Faktor 0) und
        #   maximaler rowid (``STRIDE-1``): ``(1<<32-1) - (1<<52) < 0`` ⇒ strikt negativ,
        #   nie ≥ 0. Positive v2-gids bleiben > 0, Legacy strikt < 0 ⇒ per Vorzeichen
        #   disjunkt. Dokumentierte Kapazität: ``segment_id < _LEGACY_SOURCE_BUCKETS``.
        segment_bounded = int(segment_id) % _LEGACY_SOURCE_BUCKETS
        source_factor = _LEGACY_SOURCE_BUCKETS - 1 - segment_bounded
        synthetic_gid = int(row["id"]) - _LEGACY_GID_OFFSET - source_factor * _LEGACY_GID_STRIDE
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

        # Freitext-``q``-OR-Block (#951, Codex :1603): der leading-wildcard-LIKE auf
        # datapoint_id/source_adapter ist index-untauglich. Trägt der Query einen
        # nicht-leeren ``q``, wird der LIKE-Teil wie ein Guarded-Filter behandelt und
        # (im unwindowed, nicht-Export-Fall) auf die gedeckelte Kandidatenmenge gelegt,
        # statt jedes Segment voll zu scannen. Ohne ``q`` (nur ``dp_ids_by_name``-IN,
        # index-tauglich) bzw. mit Zeitfenster/Export bleibt der ganze OR-Block inline.
        #
        # Name-Treffer aus dem Cap herauslösen (#951, Codex :2262): ist ``q`` gesetzt,
        # darf der index-taugliche ``dp_ids_by_name``-``IN``-Arm NICHT durch den
        # gedeckelten leading-wildcard-Scan laufen – sonst würde eine nur per NAME
        # gematchte Zeile jenseits der neuesten ``candidate_cap`` Roh-Zeilen verpasst,
        # obwohl der IN-Arm indizierbar ist (Parität zur un-capped Legacy-OR-Query).
        # Der IN-Arm wird deshalb im gedeckelten Pfad separat als un-capped Kandidaten-
        # Widening (UNION) und als eigener OR-Zweig geführt (siehe capped-Block unten).
        free_text_clause, free_text_params = self._free_text_clause(query)
        q_is_scan = bool((query.q or "").strip())
        like_parts, like_params = self._free_text_like_arm(query)
        in_clause, in_params = self._free_text_in_arm(query)
        if free_text_clause and not q_is_scan:
            # Kein ``q``-LIKE (nur der index-taugliche IN-Arm) → voll inline.
            clauses.append(free_text_clause)
            params.extend(free_text_params)

        select_cols = "global_event_id, ts, datapoint_id, topic, old_value, new_value, source_adapter, quality, metadata_version, metadata"

        # Metadaten-Tag/Binding-``EXISTS`` (#951, Codex :1861): wie der Freitext-``q``
        # und die guarded value-Filter ein potenziell teurer Scan-Filter. Bei
        # seltenem/fehlendem Tag würde ein inline-``EXISTS`` SQLite die ganze
        # ``ringbuffer``-Ordnung des Segments walken lassen, bevor ``LIMIT`` erfüllt
        # ist. Im unwindowed, nicht-Export-Fall wird er daher auf dieselbe gedeckelte
        # Kandidatenmenge gelegt wie die anderen guarded Prädikate. Die Korrelation
        # muss dann gegen ``capped.id`` (Kapsel-Alias) statt ``ringbuffer.id`` laufen.
        has_metadata = self._has_metadata_filter(query)
        # Cap-Routing der scan-heavy Prädikate: nur eine Zeitgrenze, die den Scan in
        # SORTIER-Richtung STOPPT, umgeht den Cap (#951, Codex-Follow-up :2270). Eine
        # rein nicht-stoppende Grenze (z. B. desc + nur ``to_ts=now``) deckt die ganze
        # History ab und bindet den Scan NICHT → Cap beibehalten.
        use_capped = not self._window_binds_scan_direction(query) and not query.is_export
        route_metadata_capped = has_metadata and use_capped
        route_free_text_capped = q_is_scan and use_capped

        # Der teure Match wird nur dann auf eine gedeckelte Kandidaten-Subquery
        # gelegt, wenn der Query ausschließlich per candidate_cap (ohne Zeitfenster)
        # gebunden ist UND es KEIN Export ist. Mit Zeitfenster bindet bereits das
        # WHERE den Scan; im Export-Modus (#951, Pkt 5) darf die innere Deckelung die
        # gematchte Menge NICHT abschneiden, sonst matchten die ersten (neuesten) ``cap``
        # Roh-Zeilen nicht, obwohl ältere matchen → leerer Chunk, Export stoppt still.
        # Im Export inlinet der guarded-Filter daher (voller Segment-Scan, nur durch das
        # finale ``LIMIT`` = ``offset+limit`` GEMATCHTE Zeilen begrenzt) – analog zum
        # bereits gefixten Legacy-Export-Batch-Scan. Kostenbegrenzt: SQLite terminiert
        # den Scan, sobald ``offset+limit`` Treffer gefunden sind.
        if (guarded_clauses or route_metadata_capped or route_free_text_capped) and use_capped:
            cap = self._effective_candidate_cap(query)
            inner_where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
            # Die innere Subquery muss die typisierten Text-Spalten (für die
            # instr/obs_regexp-Guarded-Prädikate) UND ``id`` (für die Metadaten-
            # ``EXISTS``-Korrelation gegen ``capped.id``) durchreichen – sonst kennt
            # die äußere Query ``*_value_text``/``id`` nicht.
            inner_cols = f"{select_cols}, old_value_text, new_value_text, id"
            capped_arm = f"SELECT {inner_cols} FROM ringbuffer{inner_where} ORDER BY {order_by} LIMIT ?"
            inner_sql = capped_arm
            inner_params: list[Any] = [*params, cap]
            # Name-Treffer-Widening (#951, Codex :2262): der index-taugliche
            # ``dp_ids_by_name``-``IN``-Arm darf NICHT durch den Cap fallen. Er wird
            # per ``UNION`` un-capped in die Kandidatenmenge gehoben (indiziert über
            # ``datapoint_id``), sodass eine per NAME gematchte Zeile jenseits der
            # neuesten ``cap`` Roh-Zeilen trotzdem Kandidat ist. Die äußere
            # Freitext-Bedingung führt den IN-Arm zusätzlich als OR-Zweig (unten),
            # damit diese Zeilen die Freitext-Prüfung passieren (Parität Legacy-OR).
            # Der gedeckelte Arm wird gekapselt (``ORDER BY``/``LIMIT`` sind in einem
            # SQLite-Compound nur in einer Sub-SELECT erlaubt, nicht vor ``UNION``).
            #
            # Auch der Name-Arm bekommt einen EIGENEN ``ORDER BY … LIMIT`` (#951, Codex
            # :2049): un-capped würde ein populärer ``dp_ids_by_name``-Datapoint mit sehr
            # vielen retained Zeilen sie ALLE materialisieren, bevor das äußere
            # ``LIMIT`` greift – die candidate-cap-Garantie fiele, eine Dashboard-Suche
            # scannte ein großes Segment. Der Arm ist über ``datapoint_id`` indiziert
            # (``idx_rb_dp_ts_id``), sodass ``ORDER BY … LIMIT cap`` die neuesten ``cap``
            # Namens-Treffer index-effizient liefert (kein Full-Scan). Der Cap ist derselbe
            # wie beim LIKE-Arm; ein Namens-Treffer jenseits der neuesten ``cap`` Roh-Zeilen
            # bleibt Kandidat, solange er in den neuesten ``cap`` SEINER Datapoint-Gruppe
            # liegt (Parität zum bounded Legacy-Name-Arm, Runde 46).
            if route_free_text_capped and in_clause:
                name_where_parts = [*clauses, in_clause]
                name_where = f" WHERE {' AND '.join(name_where_parts)}"
                name_arm = f"SELECT {inner_cols} FROM ringbuffer{name_where} ORDER BY {order_by} LIMIT ?"
                inner_sql = f"SELECT {inner_cols} FROM ({capped_arm}) UNION SELECT {inner_cols} FROM ({name_arm})"
                inner_params = [*params, cap, *params, *in_params, cap]
            outer_clauses = list(guarded_clauses)
            outer_params = list(guarded_params)
            if route_free_text_capped:
                # LIKE-Arme (gedeckelt) ODER IN-Arm (un-capped via UNION oben):
                # zusammen der Freitext-OR-Block auf der Kandidatenmenge.
                free_parts = list(like_parts)
                free_params = list(like_params)
                if in_clause:
                    free_parts.append(in_clause)
                    free_params.extend(in_params)
                if free_parts:
                    outer_clauses.append(f"({' OR '.join(free_parts)})")
                    outer_params.extend(free_params)
            if route_metadata_capped:
                meta_clause, meta_params = self._metadata_clause(query, entry_ref="capped.id")
                if meta_clause:
                    outer_clauses.append(meta_clause)
                    outer_params.extend(meta_params)
            outer_where = " AND ".join(outer_clauses)
            sql = f"SELECT {select_cols} FROM ({inner_sql}) AS capped WHERE {outer_where} ORDER BY {order_by} LIMIT ?"
            return sql, [*inner_params, *outer_params, final_limit]

        # Mit Zeitfenster (oder ohne cap-fähigen Scan-Filter): alle Klauseln inline.
        clauses.extend(guarded_clauses)
        params.extend(guarded_params)
        if q_is_scan and free_text_clause:
            # Windowed/Export: der volle OR-Block (LIKE + IN) läuft inline (ts-gebunden
            # bzw. durch das finale LIMIT begrenzt) – wie der Legacy-OR-Referenzpfad.
            clauses.append(free_text_clause)
            params.extend(free_text_params)
        if has_metadata:
            meta_clause, meta_params = self._metadata_clause(query)
            if meta_clause:
                clauses.append(meta_clause)
                params.extend(meta_params)
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

        Deckt Zeitfenster, Ein-Wert-Kern (datapoint_id/source_adapter/quality)
        sowie additive ``IN (...)``-Listen (mehrere datapoint_ids/adapter) ab.

        Der Freitext-``q``/``dp_ids_by_name``-OR-Block UND die Metadaten-Tag/
        Binding-``EXISTS``-Subquery werden NICHT hier gehängt: beide sind
        index-untauglich bzw. teuer und müssen – wie ein Guarded-Filter – auf eine
        gedeckelte Kandidatenmenge gelegt werden (#951, Codex :1603/:1861). Das
        Routing (inner-capped vs. inline) übernimmt ``_build_segment_sql``.
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
        return clauses, params

    @staticmethod
    def _free_text_like_arm(query: StoreQuery) -> tuple[list[str], list[Any]]:
        """Nur die leading-wildcard-``LIKE``-Arme des Freitext-``q`` (index-untauglich)."""
        parts: list[str] = []
        params: list[Any] = []
        q = (query.q or "").strip()
        if q:
            parts.append("datapoint_id LIKE ?")
            params.append(f"%{q}%")
            parts.append("source_adapter LIKE ?")
            params.append(f"%{q}%")
        return parts, params

    @staticmethod
    def _free_text_in_arm(query: StoreQuery) -> tuple[str | None, list[Any]]:
        """Nur der index-taugliche ``dp_ids_by_name``-``IN``-Arm des Freitext-``q`` (#951, Codex :2262)."""
        if not query.dp_ids_by_name:
            return None, []
        placeholders = ",".join("?" * len(query.dp_ids_by_name))
        return f"datapoint_id IN ({placeholders})", list(query.dp_ids_by_name)

    @staticmethod
    def _free_text_clause(query: StoreQuery) -> tuple[str | None, list[Any]]:
        """OR-Block für Freitext-``q`` (LIKE) + ``dp_ids_by_name`` (IN) – Legacy-Semantik."""
        parts, params = SqliteSegmentStore._free_text_like_arm(query)
        in_clause, in_params = SqliteSegmentStore._free_text_in_arm(query)
        if in_clause:
            parts.append(in_clause)
            params.extend(in_params)
        if not parts:
            return None, []
        return f"({' OR '.join(parts)})", params

    @staticmethod
    def _metadata_clause(query: StoreQuery, entry_ref: str = "ringbuffer.id") -> tuple[str | None, list[Any]]:
        """EXISTS-Subqueries für Metadaten-Tags/Bindings (Semantik wie Legacy).

        ``entry_ref`` benennt die Spalte, mit der das ``EXISTS`` korreliert.
        Inline (Base-WHERE über ``ringbuffer``) ist das ``ringbuffer.id``; im
        gedeckelten guarded Pfad umschließt eine Subquery-Kapsel (``... AS
        capped``) die Kandidatenmenge, dann muss das ``EXISTS`` gegen ``capped.id``
        korrelieren – die innere Subquery reicht ``id`` dafür durch.
        """
        clauses: list[str] = []
        params: list[Any] = []
        tags = query.metadata_tags_any_of
        if tags:
            placeholders = ",".join("?" * len(tags))
            clauses.append(f"EXISTS (SELECT 1 FROM ringbuffer_metadata_tags rmt WHERE rmt.entry_id = {entry_ref} AND rmt.tag IN ({placeholders}))")
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
                f"EXISTS (SELECT 1 FROM ringbuffer_metadata_bindings rmb WHERE rmb.entry_id = {entry_ref} AND {' AND '.join(binding_clauses)})"
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
        """True, wenn MINDESTENS eine Zeitgrenze den Scan bereits bindet (#951, Codex :2441).

        Auch eine EINSEITIGE Grenze (nur ``from_ts`` ODER nur ``to_ts`` – z. B. der
        last-24h-Filter der UI mit unterer, aber ohne obere Grenze) bindet den Scan:
        das ts-Prädikat läuft indiziert, SQLite liest nur den ts-Bereich. Solche
        Prädikate dürfen daher inline (ts-gebunden) laufen statt durch den
        ``candidate_cap`` – sonst würden Zeilen VERPASST, die im angefragten
        Zeitbereich liegen, aber älter als die neuesten ``candidate_cap`` Roh-Zeilen
        sind (der Legacy-Pfad wendet das Zeit-Prädikat an und sucht weiter). Nur ein
        reiner unbounded Scope (GAR KEINE ts-Grenze) bleibt gedeckelt.
        """
        return query.from_ts is not None or query.to_ts is not None

    @staticmethod
    def _window_binds_scan_direction(query: StoreQuery) -> bool:
        """True, wenn das Zeitfenster den scan-heavy Scan in SORTIER-Richtung STOPPT (#951, Codex-Follow-up :2270).

        Verfeinert ``_query_is_windowed`` FÜR DIE CAP-ROUTING-ENTSCHEIDUNG der
        scan-heavy Prädikate (``q``/Metadaten/``contains``/``regex``): eine
        EINSEITIGE Grenze bindet den Scan nur, wenn sie ihn in der iterierten
        Richtung STOPPT. Bei ``sort_order='desc'`` (neueste→älteste, Default)
        stoppt die UNTERE Grenze (``from_ts``); eine reine OBERE Grenze
        (``to_ts=now``) deckt die ganze retained History ab und begrenzt den
        desc-Scan NICHT. Bei ``sort_order='asc'`` (älteste→neueste) stoppt die
        OBERE Grenze (``to_ts``); eine reine ``from_ts`` begrenzt den asc-Scan
        nicht. Beide Grenzen → immer gebunden. So bleibt eine seltene/fehlende
        Text-/Metadaten-Suche mit nur nicht-stoppender Grenze weiter gedeckelt,
        statt jede Zeile großer Segmente zu scannen.
        """
        has_from = query.from_ts is not None
        has_to = query.to_ts is not None
        if has_from and has_to:
            return True
        if query.sort_order == "asc":
            # älteste→neueste: die OBERE Grenze stoppt den Scan.
            return has_to
        # desc (Default): neueste→älteste, die UNTERE Grenze stoppt den Scan.
        return has_from

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
            # ``between`` == ``gte lower AND lte upper``. Jede Grenze wird UNABHÄNGIG
            # geroutet (#951, Codex :1708): nur eine UNSICHERE Integer-Grenze (außerhalb
            # ±2**53) läuft über den exakten JSON-Vergleich ``obs_num_cmp`` – sonst
            # kollabierte ihre REAL-Repräsentation auf einen benachbarten Wert. Eine
            # SICHERE/fraktionale Grenze bleibt auf dem normalen REAL-Pfad; würde sie mit
            # ``int(...)`` truncatet, matchten z. B. bei ``lower=1.5`` faelschlich Zeilen
            # wie ``1.2`` (Parität zum Legacy/Python-``lower <= x <= upper``).
            lo_clause, lo_params = SqliteSegmentStore._between_bound_clause(field_name, num_col, "gte", lower, lo[1])
            up_clause, up_params = SqliteSegmentStore._between_bound_clause(field_name, num_col, "lte", upper, up[1])
            return (f"({lo_clause} AND {up_clause})", [*lo_params, *up_params])

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

        # Range gegen einen unsicheren Integer (#951, Codex :332): der Filterwert liegt
        # außerhalb ±2**53 und wäre als REAL nicht mehr exakt. Exakt gegen die JSON-
        # Wertspalte vergleichen (``obs_num_cmp``), damit die Grenze nicht auf einen
        # benachbarten Wert kollabiert (Parität zum Legacy-Python-Vergleich).
        if _is_unsafe_int(value):
            return (f"IFNULL(obs_num_cmp({field_name}, ?, ?), 0)", [operator, str(value)])

        # numerische Range-Operatoren: Vergleich gegen die numerische Spalte. Nicht-
        # numerische Range-Werte wurden oben bereits abgelehnt → value_type == numeric.
        return (f"({num_col} IS NOT NULL AND {num_col} {comparator} ?)", [num])

    @staticmethod
    def _between_bound_clause(field_name: str, num_col: str, op: str, bound: Any, bound_num: float | None) -> tuple[str, list[Any]]:
        """Ein Grenz-Prädikat für ``between`` (#951, Codex :1708).

        ``op`` ist ``gte`` (untere Grenze) bzw. ``lte`` (obere Grenze). Eine
        UNSICHERE Integer-Grenze läuft exakt über ``obs_num_cmp`` (kein REAL-Kollaps);
        eine sichere/fraktionale Grenze bleibt auf dem schnellen REAL-Pfad und wird
        NICHT auf einen Integer truncatet.
        """
        if _is_unsafe_int(bound):
            return (f"IFNULL(obs_num_cmp({field_name}, ?, ?), 0)", [op, str(int(bound))])
        comparator = _SQL_COMPARATORS[op]
        return (f"({num_col} IS NOT NULL AND {num_col} {comparator} ?)", [bound_num])

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
    def _has_num_cmp_filter(value_filters: list[dict[str, Any]]) -> bool:
        """True, wenn ein Filter einen unsicheren Integer (außerhalb ±2**53) trägt.

        Nur dann muss der Exakt-Vergleichs-Callback ``obs_num_cmp`` auf der Read-
        Connection registriert werden (#951, Codex :332). Betrifft eq/ne/gt/gte/lt/lte
        (über ``value``) sowie ``between`` (über ``lower``/``upper``).
        """
        for spec in value_filters:
            operator = str(spec.get("operator", "")).strip().lower()
            if operator == "between":
                if _is_unsafe_int(spec.get("lower")) or _is_unsafe_int(spec.get("upper")):
                    return True
            elif operator in _PUSHDOWN_OPERATORS and _is_unsafe_int(spec.get("value")):
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
            # Unsicherer Integer (außerhalb ±2**53, #951 Codex :332): exakt gegen die
            # JSON-Wertspalte vergleichen, weil die REAL-Spalte den Wert auf einen
            # benachbarten Integer kollabiert hätte. Ein solcher Wert ist nie 0/1, also
            # entfällt die Bool-Äquivalenz.
            if _is_unsafe_int(value):
                return (f"IFNULL(obs_num_cmp({field_name}, 'eq', ?), 0)", [str(value)])
            # numeric matcht die num-Spalte; für exakt 0/1 zusätzlich die bool-Spalte.
            if num in (0.0, 1.0):
                return (f"(IFNULL({num_col} = ?, 0) OR IFNULL({bool_col} = ?, 0))", [num, int(num)])
            return (f"IFNULL({num_col} = ?, 0)", [num])
        # value_type == "json" (list/dict): kein 422 mehr (#951, Codex :1281). Der
        # Legacy-Referenzfilter verglich Python-Werte direkt (``actual == expected``);
        # ``eq`` auf dasselbe Objekt/Array matchte, ``ne`` lieferte das Inverse. Hier
        # gegen die gespeicherte volle JSON-Spalte (``{field_name}``) über den
        # ``obs_json_eq``-Callback vergleichen, der BEIDE Seiten zu Python-Objekten
        # dekodiert und mit Python-``==`` vergleicht (#951, Codex :393). Dadurch treffen
        # gleiche Dicts key-order-unabhängig UND verschachtelte numerisch/bool
        # äquivalente Werte (``True == 1``, ``1 == 1.0``) matchen wie in der Referenz –
        # ein reiner JSON-String-Vergleich täte das nicht. Der Filterwert wird als JSON
        # übergeben (``_canonical_json`` liefert gültiges, zum Python-Wert
        # rundreisefähiges JSON); der Callback dekodiert es zurück. NULL-sicher via
        # IFNULL, damit die ``ne``-Negation null-/andere-Typ-Zeilen einschließt.
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
        # Safe-regex-Gate (#951, Codex :307): unsafe Muster VOR der Query ablehnen — der
        # synchrone Callback ist nicht per Timeout abbrechbar (GIL).
        _assert_safe_regex(pattern)
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
            # Safe decode (#951, Codex P2 :2526): analog zum Legacy-Reader. Bleibt ein
            # v2-Segment lesbar, aber der JSON-Wert EINER Zeile ist malformed (partielle
            # Datei-Korruption oder fremder/fehlerhafter Write), degradiert der Wert auf
            # den Rohwert statt mit JSONDecodeError die ganze Query zu brechen (→ 500).
            "old_value": _safe_json_decode(row["old_value"]),
            "new_value": _safe_json_decode(row["new_value"]),
            "source_adapter": row["source_adapter"],
            "quality": row["quality"],
            "metadata_version": row["metadata_version"],
            "metadata": _legacy_metadata_decode(row["metadata"]),
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

        # Ersatz-Segment ZUERST durabel machen (#951, Codex :2463): schlägt das Anlegen
        # oder Öffnen des neuen Segments fehl (Manifest-DB/Disk voll), bleibt die alte
        # aktive Connection ungeschlossen und ``_active_conn``/``_active_segment`` zeigen
        # weiter auf einen brauchbaren Writer. Würde die alte Connection – wie zuvor –
        # vor diesem Punkt geschlossen, liefe der Store nach einem Rotation-Fehler mit
        # einem geschlossenen aktiven Writer weiter und JEDER Folge-``append`` scheiterte
        # dauerhaft mit closed-connection-Fehlern bis zum Neustart. Der aktive Zustand
        # wird deshalb erst getauscht, wenn der neue Writer offen ist; erst danach wird
        # das alte Segment geschlossen und im Manifest nachgezogen.
        new_segment = await self._create_segment_locked()
        try:
            new_conn = await self._open_segment_conn(new_segment.filename)
        except BaseException:
            # Die Manifest-Zeile ist schon angelegt, aber die Connection scheiterte:
            # ohne Rollback bliebe eine zweite „active"-Zeile ohne Writer zurück und
            # das Manifest hätte zwei aktive Segmente. Die verwaiste Zeile daher
            # best-effort wieder entfernen, bevor der Fehler propagiert – das alte
            # aktive Segment bleibt der einzige aktive Writer.
            with contextlib.suppress(Exception):
                await self.manifest.delete_segment(new_segment.segment_id)
            raise
        self._active_segment = new_segment
        self._active_conn = new_conn

        if old_segment is not None and old_conn is not None:
            # Post-switch-Schritte (#951, Codex :2574): der Writer zeigt hier bereits auf
            # ``new_segment``. Wirft ein Schritt (``_try_truncate_checkpoint`` oder ein
            # Manifest-Update) NACHDEM umgeschaltet wurde, aber BEVOR die alte Zeile
            # ``closed`` ist, bliebe das alte Segment dauerhaft ``active`` – nie
            # retention-eligible, Store ggf. über Budget. Der Startup-Reconciler
            # ``_reconcile_multiple_active_segments()`` läuft nur aus ``open()``, würde
            # den Live-Fehler also erst nach einem Neustart reparieren. Deshalb wird die
            # alte Zeile im Fehlerpfad best-effort sofort auf ``closed`` demotet (self-
            # healing), BEVOR der Fehler propagiert; der neue Writer bleibt der einzige
            # aktive und ein Folge-``append`` funktioniert weiter.
            try:
                checkpoint_ok = await self._try_truncate_checkpoint(old_conn)
                await old_conn.close()
                if checkpoint_ok:
                    # Erfolgreicher TRUNCATE (#951, Codex :1346 / R49): Status ``closed``
                    # UND die reale post-checkpoint-Größe in EINEM durablen Write. Ein
                    # separates ``close_segment`` (retention-eligible) gefolgt von einem
                    # zweiten ``update_segment_size`` liesse bei einem Crash dazwischen ein
                    # ``closed`` Segment mit der pre-checkpoint WAL-schweren ``size_bytes``
                    # zurück – die nächste Size-Retention löschte dann auf Basis bereits
                    # freigegebener Bytes unnötig zusätzliche ältere Segmente.
                    await self.manifest.close_segment_with_size(
                        old_segment.segment_id,
                        size_bytes=self._segment_file_size(old_segment.filename),
                    )
                else:
                    # Busy-Checkpoint: Status + closed_at in EINEM durablen Write
                    # (#951, Runde 47) – nie transient ``closed`` (retention-eligible)
                    # persistieren, solange der WAL nicht getruncatet ist.
                    await self.manifest.close_segment_checkpoint_pending(old_segment.segment_id)
                    # Der ``checkpoint_pending``-Läufer räumt das Segment später ab (#936).
            except BaseException:
                # Best-effort-Demote: das alte Segment darf nicht ``active`` bleiben.
                # ``close_segment`` selbst kann bereits gelaufen sein (dann idempotent);
                # ein erneuter Fehler beim Demote wird unterdrückt, damit der originale
                # Rotation-Fehler unmaskiert propagiert.
                with contextlib.suppress(Exception):
                    await self.manifest.close_segment(old_segment.segment_id)
                raise

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

    async def _legacy_stats_estimate(self, segment: SegmentRecord) -> tuple[int, str | None, str | None]:
        """Billige (row_estimate, from_ts, to_ts)-Schaetzung eines attachten Legacy-Segments.

        MAX(rowid) plus ts der ersten/letzten rowid - drei Punkt-Lookups statt
        COUNT/Scan (rowids einer append-only Legacy-DB sind monoton). Gecacht je
        segment_id (die Quelle ist read-only). Unlesbare Quelle -> (0, None, None),
        ebenfalls gecacht, damit ein kaputtes File nicht jeden /stats-Poll kostet.
        """
        cached = self._legacy_stats_cache.get(segment.segment_id)
        if cached is not None:
            return cached
        estimate: tuple[int, str | None, str | None] = (0, None, None)
        try:
            conn = await self._connection_for_read(segment)
        except Exception:
            conn = None
        if conn is not None:
            try:
                async with conn.execute("SELECT MAX(id) FROM ringbuffer") as cur:
                    row = await cur.fetchone()
                rows = int(row[0]) if row and row[0] is not None else 0
                async with conn.execute("SELECT ts FROM ringbuffer ORDER BY id ASC LIMIT 1") as cur:
                    first = await cur.fetchone()
                async with conn.execute("SELECT ts FROM ringbuffer ORDER BY id DESC LIMIT 1") as cur:
                    last = await cur.fetchone()
                estimate = (rows, first[0] if first else None, last[0] if last else None)
            except Exception:
                estimate = (0, None, None)
            finally:
                await conn.close()
        self._legacy_stats_cache[segment.segment_id] = estimate
        return estimate

    async def stats(self) -> StoreStats:
        segments = await self.manifest.list_segments()
        # Attached Legacy-Segmente tragen im Manifest row_count 0 / keine ts-Grenzen
        # (Attach scannt bewusst nicht, #934). Fuer die STATS werden sie lazy und
        # gecacht geschaetzt (#964-Follow-up): drei Punkt-Lookups (MAX(rowid),
        # ts der ersten/letzten rowid) auf der read-only Connection - kein Scan.
        # Bewusst NUR Anzeige-Anreicherung: die Manifest-Zeile bleibt unveraendert,
        # damit die row-Budget-Retention ihr Verhalten nicht aendert.
        legacy_estimates = {s.segment_id: await self._legacy_stats_estimate(s) for s in segments if _is_legacy_segment(s) and s.row_count == 0}
        total = sum(legacy_estimates.get(s.segment_id, (s.row_count, None, None))[0] or s.row_count for s in segments)
        ts_lows = [s.from_ts for s in segments if s.from_ts] + [est[1] for est in legacy_estimates.values() if est[1]]
        ts_highs = [s.to_ts for s in segments if s.to_ts] + [est[2] for est in legacy_estimates.values() if est[2]]
        oldest = min(ts_lows, default=None)
        newest = max(ts_highs, default=None)
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
            # Persistenter Delete-Fehler (#951 [P2] :2575): Segmente, deren Basisdatei
            # nicht unlinkbar war, bleiben hier sichtbar, damit Dashboard/Admin den
            # blockierten Retention-Zustand erkennt (nicht nur das aggregierte Flag).
            "unlink_blocked_segment_ids": sorted(sid for sid in self._unlink_blocked_segment_ids if any(s.segment_id == sid for s in segments)),
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
        undeletable_ids: set[int] = set()
        undeletable = 0
        for s in segments:
            if s.status in (SEGMENT_STATUS_ACTIVE, SEGMENT_STATUS_CHECKPOINT_PENDING):
                undeletable += s.size_bytes
                undeletable_ids.add(s.segment_id)
        # Legacy zählt nur solange als undeletable, wie es NICHT freigebbar ist
        # (Guard greift). Sobald ein nicht-Legacy-Segment Zeilen hält, ist Legacy
        # per Size-Retention löschbar und darf das Budget nicht künstlich sprengen.
        if not await self._has_nonlegacy_data_segment():
            for s in segments:
                if _is_legacy_segment(s) and s.segment_id not in undeletable_ids:
                    undeletable += s.size_bytes
                    undeletable_ids.add(s.segment_id)
        # Unlink-blocked Segmente (#951 [P2] :2575): ein retention-eligibles Segment,
        # dessen Basisdatei ``_delete_segment`` NICHT unlinken konnte (Permission/Lock/
        # EBUSY), belegt seine Bytes weiter auf der Platte und blockiert jeden Pass an
        # derselben Datei. Seine Bytes werden daher NICHT als freigebbar abgezogen,
        # sonst meldete ``retention_over_budget=false`` trotz real über-Budget-Store
        # (konsistent zum Signalmodell Fall B: nicht löschbare non-legacy Segmente über
        # Budget = rot).
        for s in segments:
            if s.segment_id in self._unlink_blocked_segment_ids and s.segment_id not in undeletable_ids:
                undeletable += s.size_bytes
                undeletable_ids.add(s.segment_id)
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

        Fehlende Datei nicht neu erzeugen (#951, Codex :1830): ist die Datei eines
        pending-Segments vor dem Retry verschwunden, legte ein schreibendes
        ``connect`` an diesem Pfad still eine neue LEERE DB an. Der Checkpoint würde
        dann für die alte Manifest-Zeile als ``done`` markiert und spätere Reads
        träfen eine leere DB, statt das Segment als „missing" zu überspringen. Vor
        dem Öffnen wird daher geprüft, ob die Datei existiert; fehlt sie, wird das
        Segment übersprungen (kein Recreate) — konsistent zum Read-Pfad-Skip.

        Transiente Fehler nicht quarantänieren (#951, Codex :1837): nur
        KORRUPTIONS-indizierende ``aiosqlite``-Fehler (malformed/not a database/…)
        führen zur Quarantäne. Ein GESUNDES pending-Segment, das einen TRANSIENTEN
        Open/Checkpoint-Fehler trifft (locked/busy/I-O), bliebe sonst aus Queries
        versteckt UND retention-fähig → gültige Historie könnte verworfen werden.
        Transiente Fehler lassen das Segment daher ``checkpoint_pending`` für einen
        späteren Retry, statt es zu isolieren.

        Leere/truncatete pending-Datei als verloren behandeln (#951, Codex :2220):
        eine ``checkpoint_pending``-Datei kann auf 0 Bytes truncated sein (abgeschnittener
        Write/Crash). ``connect`` öffnet sie als GÜLTIGE leere DB, ``wal_checkpoint(TRUNCATE)``
        meldet ``busy=0`` → ohne Prüfung würde das Segment fälschlich ``closed`` + auf 0
        resized, obwohl das Manifest Zeilen erwartet. Spätere Reads träfen dann die leere DB
        (fehlende ``ringbuffer``-Tabelle) statt das Segment zu überspringen und die alten
        Zeilen wären still weg. Erwartet das Manifest Zeilen, die Datei enthält aber keine
        (oder keine ``ringbuffer``-Tabelle), wird das Segment daher – analog zum aktiven
        Segment (Codex :658) – quarantäniert statt als sauberer Checkpoint markiert.
        """
        recovered = 0
        for segment in await self.manifest.list_checkpoint_pending_segments():
            segment_path = self._segments_dir / segment.filename
            if not segment_path.exists():
                # Datei vor dem Retry verschwunden (#951 [P2] Runde 42, sqlite_backend :2852):
                # ein reines ``continue`` ließe die Manifest-Zeile für immer
                # ``checkpoint_pending`` – retention-UNfähig – und ``_retention_pressure``
                # zählte ihre stale ``size_bytes`` dauerhaft als non-deletable, sodass der
                # Store permanent über Budget bliebe, ohne dass ``enforce_retention()``
                # Fortschritt machen kann. Ein pending-Segment, dessen Datei fehlt UND das
                # Zeilen erwartet, wird daher als verloren quarantäniert (konsistent zur
                # missing-/leeren-Datei-Behandlung Codex :1830/:2220). Danach zählen seine
                # Bytes nicht mehr als non-deletable und die FIFO-Retention räumt die Zeile.
                # NICHT neu anlegen; ein leeres (row_count<=0) pending-Segment ohne Datei
                # wird nur übersprungen (keine Historie verloren).
                if segment.row_count > 0:
                    await self.manifest.mark_quarantined(
                        segment.segment_id,
                        reason="checkpoint_pending base file missing but manifest expects rows",
                    )
                continue
            try:
                conn = await aiosqlite.connect(str(segment_path))
                conn.row_factory = aiosqlite.Row
                try:
                    if segment.row_count > 0 and await self._segment_missing_rows(conn):
                        # Manifest erwartet Zeilen, die Datei ist aber leer/truncatet →
                        # als verloren/korrupt behandeln, nicht als sauberen Checkpoint.
                        await self.manifest.mark_quarantined(
                            segment.segment_id,
                            reason="checkpoint_pending file empty but manifest expects rows",
                        )
                        continue
                    ok = await self._try_truncate_checkpoint(conn)
                finally:
                    await conn.close()
            except aiosqlite.Error as exc:
                # Nur echte Korruption isolieren; transiente Fehler (locked/busy/I-O)
                # bleiben checkpoint_pending für einen späteren Retry.
                if _is_sqlite_corruption(exc):
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
        #
        # Missing-File-Recreate (#951, Pkt 2): ein schreibendes ``connect`` legte
        # an einem zwischenzeitlich gelöschten/verschobenen Segmentpfad still eine
        # leere Ersatz-DB an – ``PRAGMA integrity_check`` meldete dann ``ok``, das
        # Manifest bewarb weiter die alten Zeilen, aber spätere Queries sahen die
        # neue DB ohne ``ringbuffer``-Tabelle und scheiterten. ``mode=ro`` legt eine
        # fehlende Datei NICHT an, sondern wirft – der Fehler landet im ``except``
        # unten und das Segment wird als fehlend/nicht-ok quarantäniert (konsistent
        # zum bestehenden Missing-File-Handling im Read-Pfad).
        conn: aiosqlite.Connection | None = None
        uri = _sqlite_ro_uri(self._segments_dir / segment.filename, params="mode=ro")
        try:
            conn = await aiosqlite.connect(uri, uri=True)
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
        # Pending Checkpoints IMMER zuerst nachziehen (#951, Pkt 5 / Codex R49): ein
        # busy gebliebenes ``checkpoint_pending``-Segment ist retention-UNfähig. Würde
        # der Truncate nie erneut versucht, bliebe es das dauerhaft und hielte seine
        # WAL/SHM-Sidecars. ``run_pending_checkpoints`` ist der EINZIGE Retry-Pfad
        # (der RingBuffer ruft ihn nur über ``enforce_retention``). Er muss deshalb VOR
        # dem Unlimited-Early-Return laufen – sonst blieben checkpoint_pending-Segmente
        # auf Installationen OHNE Retention-Limits (alle Limits ``None``) für immer hängen.
        await self.run_pending_checkpoints()
        if cfg.max_file_size_bytes is None and cfg.max_age is None and cfg.max_entries is None:
            return 0

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
        victims = await self._retention_victims_in_order()
        return victims[0] if victims else None

    async def _retention_victims_in_order(self) -> list[SegmentRecord]:
        """FIFO-geordnete, guard-geschützte Löschkandidaten für ALLE Retention-Pfade (#951, Pkt 7).

        Size-, Age- UND Row-Retention teilen sich diese eine Liste, damit sie
        konsistent denselben No-Zero-History-Guard und dieselbe Legacy-Herkunfts-
        Behandlung anwenden:

        * **Legacy zuerst:** das read-only eingehängte Legacy-Segment ist per
          Definition am ältesten → zuerst zurückgewinnen, SOBALD der Guard erfüllt
          ist (mindestens eine lesbare nicht-Legacy-Datenquelle hält Zeilen).
          Legacy-Herkunft wird per Schema (``_is_legacy_segment``) bestimmt, damit
          auch ein quarantäniertes Legacy hier – und nicht in der eligible-Liste –
          landet und unter dem Guard geschützt bleibt.
        * **Dann** geschlossene/quarantänierte NICHT-Legacy-Segmente in FIFO-
          Reihenfolge (ältestes zuerst). Legacy-Herkunft ist hier ausgeschlossen –
          sie ist entweder oben schon freigegeben oder durch den Guard geschützt.

        Ohne diese gemeinsame Route iterierten Age-/Row-Retention direkt
        ``list_retention_eligible_segments()`` (schließt gesundes ``status='legacy'``
        AUS, schließt quarantäniertes Legacy EIN): gesundes Legacy würde per
        max_age/max_entries NIE zurückgewonnen, WÄHREND ein quarantäniertes Legacy
        fälschlich – am Guard vorbei – gelöscht werden könnte.
        """
        # Entscheidungs-Guard (#964): solange der Migrations-Assistent keine
        # informierte Entscheidung hat (``protect_legacy``), ist das Legacy-Segment
        # KEIN Löschkandidat – unabhängig vom No-Zero-History-Guard. Der Store darf
        # dann über Budget bleiben; ``/stats`` weist das aus und die GUI eskaliert.
        protect_legacy = self._retention_config.protect_legacy
        has_nonlegacy_data = await self._has_nonlegacy_data_segment()
        victims: list[SegmentRecord] = []
        legacy_segments = [] if protect_legacy else [s for s in await self.manifest.list_segments() if _is_legacy_segment(s)]
        if legacy_segments and has_nonlegacy_data:
            # ältestes Legacy zuerst (#951, Codex :2267): segment_id steigt mit der
            # Registrierung, list_legacy_segments dokumentiert ascending = ältestes
            # zuerst. Bei MEHREREN attached Legacy-Quellen muss deshalb ASCENDING
            # sortiert werden – FIFO-konform. Ein DESCENDING-Sort würde die NEUESTE
            # Legacy-Quelle vor älteren für Size/Age/Row-Retention wählen und den
            # FIFO-Vertrag umkehren (neuere Legacy-Historie verwerfen, ältere behalten).
            victims.extend(sorted(legacy_segments, key=lambda s: s.segment_id))
        victims.extend(s for s in await self.manifest.list_retention_eligible_segments() if not _is_legacy_segment(s))
        return victims

    async def _has_nonlegacy_data_segment(self) -> bool:
        """True, wenn ein ABFRAGBARES nicht-Legacy-Segment Zeilen hält.

        Ein quarantäniertes (korruptes) v2-Segment wird beim Read übersprungen und
        ist damit KEINE lesbare Historie (#951, Codex :1939). Würde es hier trotzdem
        zählen, hebt der No-Zero-History-Guard ab und die attached LESBARE Legacy-DB
        könnte unter Size-Druck gelöscht werden, obwohl keine lesbare nicht-Legacy-
        Historie existiert (Datenverlust). Nur aktive/geschlossene/pending – also
        NICHT quarantänierte – nicht-Legacy-Segmente mit Zeilen werten.

        Fehlende v2-Datei NICHT als lesbare Historie werten (#951, Codex :3068):
        ist die Datei eines nicht-Legacy-Segments außerhalb des Retention-Pfads
        verschwunden (manuelles Aufräumen, Race), überspringt der Read-Pfad sie
        (``_segment_read_file_missing``) – ihr Manifest-``row_count`` beschreibt dann
        keine tatsächlich lesbaren Zeilen mehr. Zählte der Guard sie hier trotzdem,
        hübe er unter Size-Druck ab und die attached LESBARE Legacy-DB könnte als
        Opfer gelöscht werden, obwohl keine lesbare nicht-Legacy-Historie existiert
        → letzte lesbare Kopie der Historie verloren. Die Existenzprüfung ist konsistent
        zum missing-file-Skip des Read-Pfads (``_segment_read_file_missing``).
        """
        hidden_statuses = {SEGMENT_STATUS_QUARANTINED}
        for segment in await self.manifest.list_segments():
            if _is_legacy_segment(segment) or segment.status in hidden_statuses:
                continue
            if segment.row_count > 0 and not self._segment_read_file_missing(segment):
                return True
        return False

    async def _enforce_age_cutoff(self, max_age: int | None) -> int:
        if max_age is None:
            return 0
        cutoff = datetime.now(UTC).timestamp() - max_age
        removed = 0
        # Durch dieselbe guard-geschützte Victim-Liste wie Size/Row (#951, Pkt 7):
        # gesundes Legacy ist so age-retention-fähig (sobald der Guard erfüllt ist),
        # quarantäniertes Legacy bleibt unter dem Guard geschützt.
        for segment in await self._retention_victims_in_order():
            to_ts = _parse_ts(segment.to_ts)
            if to_ts is None and _is_legacy_segment(segment):
                # Attached read-only Legacy hat per Design to_ts=NULL (attach_readonly
                # scannt die 20–30 GB-Datei bewusst nicht). Legacy steht als ältestes
                # vorne; ein break hier würde spätere geschlossene v2-Segmente NIE per
                # Alter trimmen, bis die Legacy-Zeile entfernt ist (#951, Codex :2313).
                # Ein Legacy mit UNBEKANNTER to_ts deshalb ÜBERSPRINGEN (nicht break),
                # damit die v2-Age-Retention weiterläuft. Ein Legacy wird per Age nur
                # gelöscht, wenn seine to_ts bekannt UND vor dem Cutoff ist.
                continue
            if to_ts is None and not _is_legacy_segment(segment) and segment.row_count <= 0:
                # LEERES geschlossenes v2-Segment mit to_ts=NULL (#951, Codex :2603):
                # ein rotiertes idle-leeres aktives Segment hat row_count=0 und
                # MAX(ts)=NULL. Steht es in der FIFO-Reihenfolge VOR älteren, über-
                # Cutoff-Daten-Segmenten, würde ein break den gesamten Age-Pass
                # beenden → alle späteren, tatsächlich zu alten v2-Segmente blieben
                # unbegrenzt retained. Ein LEERES unknown-age-Segment trägt kein
                # relevantes Alter, daher – wie unknown-age-Legacy – ÜBERSPRINGEN
                # (continue), statt den Lauf abzubrechen. Ein NICHT-leeres v2-Segment
                # mit to_ts=NULL (unbekanntes, evtl. relevantes Alter) fällt bewusst
                # NICHT hierunter und wird unten konservativ per break behalten.
                continue
            if to_ts is None or to_ts >= cutoff:
                # Ältestes-zuerst: sobald ein nicht-Legacy-Segment neu genug ist (oder
                # keine to_ts-Grenze trägt), wird es und alles danach nicht mehr per
                # Alter gelöscht.
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
            # Durch dieselbe guard-geschützte Victim-Liste wie Size/Age (#951, Pkt 7).
            # Legacy-Segmente mit UNBEKANNTEM (0/unscanned) row_count vom Row-Trimming
            # AUSSCHLIESSEN (#951, Codex :2330): attach_readonly scannt die Datei nicht,
            # daher hat attached Legacy meist row_count=0. Übersteigen allein die
            # v2-Zeilen das Budget, wäre victims[0] sonst dieses Legacy → das Löschen
            # wirft die GANZE Legacy-DB weg, senkt _total_row_count aber NICHT (war 0),
            # sodass die Schleife weiterläuft und fälschlich auch v2-Segmente löscht.
            # Nur Segmente mit bekannter row_count > 0 werden per Row-Budget getrimmt;
            # Size-/FIFO-Retention über die bekannte Dateigröße bleibt für Legacy.
            victims = [s for s in await self._retention_victims_in_order() if not (_is_legacy_segment(s) and s.row_count <= 0)]
            if not victims:
                break
            if not await self._delete_segment(victims[0]):
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
            # Segment als unlink-blocked markieren (#951 [P2] :2575), damit seine Bytes
            # im ``retention_over_budget``-Pressure-Test als NICHT-freigebbar zählen –
            # sonst meldete ``/stats`` unter-Budget, obwohl der Store über
            # ``max_file_size_bytes`` bleibt und jeder Pass an derselben Datei blockiert.
            self._unlink_blocked_segment_ids.add(segment.segment_id)
            _LOGGER.warning(
                "retention: base segment file for segment_id=%s could not be removed; keeping manifest entry for retry",
                segment.segment_id,
            )
            return False
        # Erfolgreich gelöscht → evtl. früherer unlink-blocked-Zustand ist aufgehoben.
        self._unlink_blocked_segment_ids.discard(segment.segment_id)
        self._legacy_stats_cache.pop(segment.segment_id, None)
        await self.manifest.delete_segment(segment.segment_id)
        return True

    @staticmethod
    def _unlink_with_sidecars(base: Path) -> bool:
        """Löscht ``base`` samt ``-wal``/``-shm``; True nur bei erfolgreicher Basis-Löschung.

        Sidecar-Fehler (``-wal``/``-shm`` fehlen oder sind unlöschbar) bleiben
        tolerant. Nur das Ergebnis der BASIS-Datei entscheidet, ob der Aufrufer die
        Manifest-Zeile entfernen darf (#951, Pkt 6). Eine bereits fehlende Basisdatei
        gilt als erfolgreich gelöscht (Platz ist frei).

        Sidecars nur bei Basis-Erfolg entfernen (#951, Codex :2126): scheitert das
        Unlink der Basisdatei (Permission/Lock), bleibt das Segment über die
        behaltene Manifest-Zeile registriert und wird beim nächsten Lauf erneut
        versucht. Würden die ``-wal``/``-shm``-Sidecars trotzdem gelöscht, verlöre
        ein behaltenes Segment (v. a. eine Legacy dirty-WAL-Quelle) seine noch nicht
        gecheckpointeten Frames, während es weiter gelesen wird. Die Sidecars werden
        deshalb nur angefasst, wenn die Basisdatei erfolgreich entfernt wurde.
        """
        base_removed = True
        try:
            base.unlink()
        except FileNotFoundError:
            # Bereits weg → Ziel erreicht (Platz frei).
            base_removed = True
        except OSError:
            base_removed = False
        if not base_removed:
            # Basis blieb liegen → Segment wird für den Retry behalten; Sidecars mit
            # den ungecheckpointeten Frames dürfen NICHT verwaist gelöscht werden.
            return False
        for sidecar in (Path(f"{base}-wal"), Path(f"{base}-shm")):
            try:
                sidecar.unlink()
            except OSError:
                # Fehlt eine Sidecar-Datei (oder ist unlöschbar), stört das die
                # Retention nicht – nur die Basisdatei entscheidet.
                continue
        return base_removed
