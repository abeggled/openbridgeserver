"""RingBuffer API.

Filterset schema (#431):
    Filtersets are flat — one filter criteria per set. Multiple sets can be active
    in the topbar simultaneously; the multi-query endpoint OR-unions their hits
    and annotates each entry with the IDs of the sets it matched.

The DB column is named ``filter_json`` (a serialized :class:`FilterCriteria`),
explicitly distinct from the legacy ``query_json`` column which previously stored
a complete :class:`RingBufferQueryV2` (filters + sort + pagination). Renaming
makes the semantic shift unambiguous: a filterset now stores filter *criteria*,
while sort and pagination remain owned by the caller of the query endpoint.
"""

from __future__ import annotations

import asyncio
import csv
import json
import re
import tempfile
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from obs.api.auth import get_admin_user, get_current_user
from obs.db.database import Database, get_db
from obs.ringbuffer.persisted_config import persist_ringbuffer_config
from obs.ringbuffer.ringbuffer import get_ringbuffer

router = APIRouter(tags=["ringbuffer"])

_FILTERSET_QUERY_LIMIT_CAP = 2000
_FILTERSET_QUERY_OFFSET_CAP = 5000
_FILTERSET_MULTI_QUERY_SET_CAP = 50
_FILTERSET_MULTI_QUERY_PER_SET_LIMIT = 2000

_CSV_EXPORT_MAX_ROWS = 100000
_CSV_EXPORT_CHUNK_SIZE = 1000
_CSV_EXPORT_QUERY_TIMEOUT_SECONDS = 3.0
_CSV_EXPORT_TOTAL_TIMEOUT_SECONDS = 20.0
_CSV_EXPORT_SPOOL_MAX_BYTES = 1_000_000
_CSV_EXPORT_HEADERS = (
    "id",
    "ts",
    "datapoint_id",
    "name",
    "topic",
    "old_value_json",
    "new_value_json",
    "source_adapter",
    "quality",
    "metadata_version",
    "metadata_json",
)

_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_DEFAULT_COLOR = "#3b82f6"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Query models (v2 — used by /query and /export/csv)
# ---------------------------------------------------------------------------


class RingBufferEntryOut(BaseModel):
    id: int
    ts: str
    datapoint_id: str
    name: str | None
    topic: str
    old_value: Any
    new_value: Any
    source_adapter: str
    quality: str
    metadata_version: int
    metadata: dict[str, Any]
    unit: str | None = None


class RingBufferMultiEntryOut(RingBufferEntryOut):
    """Entry plus the list of filterset IDs the entry matched in a multi-query.

    Each entry appears at most once even if it matches multiple sets — the
    ``matched_set_ids`` list captures the OR-union membership.
    """

    matched_set_ids: list[str]


class RingBufferStats(BaseModel):
    total: int
    oldest_ts: str | None
    newest_ts: str | None
    storage: str
    max_entries: int | None
    effective_retention_seconds: int | None = None
    max_file_size_bytes: int | None
    max_age: int | None
    file_size_bytes: int
    last_recovery_at: str | None = None
    last_recovery_file_count: int = 0


class RingBufferConfig(BaseModel):
    storage: str = "file"
    max_entries: int | None = Field(default=None, ge=1)
    max_file_size_bytes: int | None = Field(default=None, ge=1)
    max_age: int | None = Field(default=None, ge=0)


class RingBufferTimeFilterV2(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_ts: str | None = Field(default=None, alias="from")
    to_ts: str | None = Field(default=None, alias="to")
    from_relative_seconds: int | None = None
    to_relative_seconds: int | None = None


class RingBufferAdapterFilterV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    any_of: list[str] = Field(default_factory=list)


class RingBufferDatapointFilterV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(default_factory=list)


class RingBufferValueFilterV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "between", "contains", "regex"]
    value: Any | None = None
    lower: Any | None = None
    upper: Any | None = None
    pattern: str | None = None
    ignore_case: bool = False


class RingBufferMetadataFilterV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tags_any_of: list[str] = Field(default_factory=list)
    adapter_types_any_of: list[str] = Field(default_factory=list)
    adapter_instance_ids_any_of: list[str] = Field(default_factory=list)
    group_addresses_any_of: list[str] = Field(default_factory=list)
    topics_any_of: list[str] = Field(default_factory=list)
    entity_ids_any_of: list[str] = Field(default_factory=list)
    register_types_any_of: list[str] = Field(default_factory=list)
    register_addresses_any_of: list[str] = Field(default_factory=list)


class RingBufferFiltersV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str = ""
    time: RingBufferTimeFilterV2 | None = None
    adapters: RingBufferAdapterFilterV2 | None = None
    datapoints: RingBufferDatapointFilterV2 | None = None
    values: list[RingBufferValueFilterV2] | None = None
    metadata: RingBufferMetadataFilterV2 | None = None


class RingBufferSortV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Literal["id", "ts"] = "id"
    order: Literal["asc", "desc"] = "desc"


class RingBufferPaginationV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=100, ge=1, le=10000)
    offset: int = Field(default=0, ge=0)


class RingBufferQueryV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: RingBufferFiltersV2 = Field(default_factory=RingBufferFiltersV2)
    sort: RingBufferSortV2 = Field(default_factory=RingBufferSortV2)
    pagination: RingBufferPaginationV2 = Field(default_factory=RingBufferPaginationV2)


# ---------------------------------------------------------------------------
# Filterset models — flat schema (#431)
# ---------------------------------------------------------------------------


class NodeRef(BaseModel):
    """Reference to a node in a hierarchy tree (e.g. KNX function/spaces tree).

    ``include_descendants`` decides whether descendant nodes count as matches —
    expansion to concrete datapoint IDs is performed by the consumer.
    """

    model_config = ConfigDict(extra="forbid")

    tree_id: str
    node_id: str
    include_descendants: bool = True


class FilterCriteria(BaseModel):
    """Flat filter criteria for a single filterset (#431).

    Field-internal lists are OR-combined; the criteria as a whole are AND-combined.
    The time filter is *not* part of the criteria — it is supplied at query time
    so the same filterset works across different time windows.
    """

    model_config = ConfigDict(extra="forbid")

    hierarchy_nodes: list[NodeRef] = Field(default_factory=list)
    datapoints: list[str] = Field(default_factory=list)
    devices: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    adapters: list[str] = Field(default_factory=list)
    q: str | None = None
    value_filter: RingBufferValueFilterV2 | None = None


def _is_empty_criteria(c: FilterCriteria | None) -> bool:
    """A FilterCriteria with no populated field. Used to skip ad-hoc sets
    that have no filter configured yet — UX feedback (#36): such a set must
    show *nothing* in the table, not *everything*.
    """
    if c is None:
        return True
    if c.hierarchy_nodes or c.datapoints or c.devices or c.tags or c.adapters:
        return False
    if c.q and c.q.strip():
        return False
    if c.value_filter and c.value_filter.operator:
        return False
    return True


def _color_must_be_hex(value: str) -> str:
    if not isinstance(value, str) or not _COLOR_RE.match(value):
        raise ValueError("color must be a hex color like #3b82f6 or #abc")
    return value


class RingBufferFiltersetIn(BaseModel):
    """Input model for POST /filtersets and PUT /filtersets/{id}."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    dsl_version: int = Field(default=2, ge=1)
    is_active: bool = True
    color: str = _DEFAULT_COLOR
    topbar_active: bool = False
    topbar_order: int = 0
    filter: FilterCriteria = Field(default_factory=FilterCriteria)

    @field_validator("color")
    @classmethod
    def _validate_color(cls, value: str) -> str:
        return _color_must_be_hex(value)


class RingBufferFiltersetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    dsl_version: int | None = Field(default=None, ge=1)
    is_active: bool | None = None
    color: str | None = None
    topbar_active: bool | None = None
    topbar_order: int | None = None
    filter: FilterCriteria | None = None

    @field_validator("color")
    @classmethod
    def _validate_color(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _color_must_be_hex(value)


class RingBufferFiltersetCloneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None


class RingBufferFiltersetOut(BaseModel):
    id: str
    name: str
    description: str
    dsl_version: int
    is_active: bool
    color: str
    topbar_active: bool
    topbar_order: int
    filter: FilterCriteria
    created_at: str
    updated_at: str
    # #478: NULL on rows created before V33 — treated as "shared, admin-only editable".
    created_by: str | None = None


class RingBufferFiltersetTopbarPatch(BaseModel):
    """Lightweight per-set toggles for the topbar.

    Carries the optional ``is_active`` flag (filter on/off, the dot-button in
    each chip) alongside the topbar-membership and topbar-order. Any of the
    three may be ``None`` to leave the current value untouched.
    """

    model_config = ConfigDict(extra="forbid")

    topbar_active: bool | None = None
    topbar_order: int | None = None
    is_active: bool | None = None


class RingBufferFiltersetOrderItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    topbar_order: int


class RingBufferFiltersetOrderPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RingBufferFiltersetOrderItem] = Field(default_factory=list)


class RingBufferMultiExportRequest(BaseModel):
    """Request body for ``POST /filtersets/export/csv`` (multi-set CSV export).

    Streams the full OR-union of entries matching any of the active filtersets,
    plus an optional time filter, as a delimiter-separated text file. The
    delimiter, quote character and escape character are configurable — the
    defaults follow RFC 4180. The export is independent of UI pagination.
    """

    model_config = ConfigDict(extra="forbid")

    set_ids: list[str] = Field(default_factory=list)
    time: RingBufferTimeFilterV2 | None = None
    # Single-character delimiter (e.g. ',' for CSV, '\t' for TSV, ';' for
    # German Excel). Always exactly one character.
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    # Quote character around fields that contain the delimiter, the quote
    # character itself, or newlines. Default '"' per RFC 4180.
    quote_char: str = Field(default='"', min_length=1, max_length=1)
    # Escape character for the quote character inside a quoted field. Empty
    # string (default) selects RFC 4180 behaviour: the quote char inside a
    # quoted field is doubled. Setting a single character switches the csv
    # writer to backslash-style escaping (doublequote=False).
    escape_char: str = Field(default="", max_length=1)
    encoding: Literal["utf8", "utf8-bom"] = "utf8"
    include_unit: bool = True
    include_matched_set_ids: bool = False


class RingBufferExportSettings(BaseModel):
    """Persisted user defaults for the CSV export dialog (#427)."""

    # `extra="ignore"` so legacy persisted blobs that still carry `format`
    # (csv|tsv) load without raising. The legacy fields are mapped to the
    # new delimiter-based schema in get_ringbuffer_export_settings.
    model_config = ConfigDict(extra="ignore")

    delimiter: str = Field(default=",", min_length=1, max_length=1)
    quote_char: str = Field(default='"', min_length=1, max_length=1)
    escape_char: str = Field(default="", max_length=1)
    encoding: Literal["utf8", "utf8-bom"] = "utf8"
    include_unit: bool = True
    include_matched_set_ids: bool = False


class RingBufferMultiExportCountRequest(BaseModel):
    """Request body for ``POST /filtersets/export/count`` — preflight row count.

    Mirrors the set/time selection of :class:`RingBufferMultiExportRequest` so
    the UI can warn the user before triggering a large download. Format/encoding
    options are intentionally omitted: they do not influence the row count.
    """

    model_config = ConfigDict(extra="forbid")

    set_ids: list[str] = Field(default_factory=list)
    time: RingBufferTimeFilterV2 | None = None


class RingBufferMultiExportCountResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_count: int = Field(ge=0)


class RingBufferMultiQueryRequest(BaseModel):
    """Request body for ``POST /filtersets/query`` (multi-set OR-union).

    Behaviour:
        - ``set_ids=[]`` and no ``time`` → returns the most recent entries with
          the default pagination (no filterset filter at all).
        - ``set_ids=[]`` with a ``time`` filter → returns the most recent entries
          inside that time window (still no filterset filter).
        - ``set_ids=[a, b, ...]`` → OR-union of the matching entries across the
          named sets, each entry carrying its ``matched_set_ids``.
        - Unknown / missing set IDs are skipped with a logger warning, never an
          error — the caller may have a stale list after another client deleted
          a set; failing here would break the topbar for everyone.
    """

    model_config = ConfigDict(extra="forbid")

    set_ids: list[str] = Field(default_factory=list)
    time: RingBufferTimeFilterV2 | None = None
    limit: int = Field(default=500, ge=1, le=_FILTERSET_MULTI_QUERY_PER_SET_LIMIT)
    offset: int = Field(default=0, ge=0, le=_FILTERSET_QUERY_OFFSET_CAP)
    sort: RingBufferSortV2 = Field(default_factory=RingBufferSortV2)


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------


def _decode_filter(raw: str | None) -> FilterCriteria:
    payload: Any = {}
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        return FilterCriteria.model_validate(payload)
    except ValidationError:
        return FilterCriteria()


def _encode_filter(filter_: FilterCriteria) -> str:
    return json.dumps(filter_.model_dump(), separators=(",", ":"))


def _normalize_color(value: str | None) -> str:
    return value if isinstance(value, str) and _COLOR_RE.match(value) else _DEFAULT_COLOR


async def _resolve_hierarchy_to_datapoints(
    hierarchy_nodes: list[NodeRef],
    db: Database,
) -> list[str]:
    """Resolve a list of hierarchy node references to the concrete DataPoint IDs
    linked under them.

    - When ``include_descendants`` is True (default), the entire sub-tree rooted
      at the node is walked via a SQLite recursive CTE and every DP linked to
      *any* node within the sub-tree is returned.
    - When ``include_descendants`` is False, only DPs directly linked to the
      node itself are returned.

    The result is de-duplicated. Unknown / deleted nodes are silently skipped.
    Returns an empty list when ``hierarchy_nodes`` is empty.
    """
    if not hierarchy_nodes:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for node in hierarchy_nodes:
        if node.include_descendants:
            rows = await db.fetchall(
                """WITH RECURSIVE subtree(id) AS (
                       SELECT id FROM hierarchy_nodes WHERE id = ?
                       UNION ALL
                       SELECT hn.id FROM hierarchy_nodes hn
                       JOIN subtree st ON hn.parent_id = st.id
                   )
                   SELECT DISTINCT hdl.datapoint_id AS dp_id
                   FROM hierarchy_datapoint_links hdl
                   WHERE hdl.node_id IN (SELECT id FROM subtree)""",
                (node.node_id,),
            )
        else:
            rows = await db.fetchall(
                "SELECT DISTINCT datapoint_id AS dp_id FROM hierarchy_datapoint_links WHERE node_id = ?",
                (node.node_id,),
            )
        for row in rows:
            dp_id = row["dp_id"]
            if dp_id not in seen:
                seen.add(dp_id)
                out.append(dp_id)
    return out


def _normalize_nonempty(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = value.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


async def _table_columns(db: Database, table_name: str) -> set[str]:
    rows = await db.fetchall(f"PRAGMA table_info({table_name})")
    return {str(row["name"]) for row in rows if "name" in row.keys()}


async def _resolve_device_pas_to_group_addresses(
    device_pas: list[str],
    db: Database,
) -> list[str]:
    """Resolve physical addresses to KNX group addresses via persisted knxproj tables.

    The helper is schema-tolerant so it keeps working while the KNX device
    import tables are introduced incrementally across sub-issues.
    """
    normalized_pas = _normalize_nonempty(device_pas)
    if not normalized_pas:
        return []

    co_cols = await _table_columns(db, "knx_comm_objects")
    link_cols = await _table_columns(db, "knx_co_ga_links")
    dev_cols = await _table_columns(db, "knx_devices")

    ga_col = next((c for c in ("group_address", "ga_address", "ga", "address") if c in link_cols), None)
    if not ga_col:
        return []

    placeholders = ",".join("?" * len(normalized_pas))
    co_id_col = next((c for c in ("id", "comm_object_id", "communication_object_id", "co_id") if c in co_cols), None)
    link_co_col = next((c for c in ("comm_object_id", "communication_object_id", "co_id") if c in link_cols), None)

    # Most direct shape: knx_comm_objects holds the device physical address.
    co_pa_col = next(
        (c for c in ("physical_address", "device_physical_address", "device_pa", "pa", "address") if c in co_cols),
        None,
    )
    if co_id_col and link_co_col and co_pa_col:
        rows = await db.fetchall(
            f"""SELECT DISTINCT l.{ga_col} AS ga
                   FROM knx_comm_objects co
                   JOIN knx_co_ga_links l ON l.{link_co_col} = co.{co_id_col}
                  WHERE co.{co_pa_col} IN ({placeholders})""",
            tuple(normalized_pas),
        )
        resolved = _normalize_nonempty([str(row["ga"]) for row in rows if row["ga"] is not None])
        if resolved:
            return resolved

    # Fallback shape: knx_devices linked via device_id in knx_comm_objects.
    dev_id_col = next((c for c in ("id", "device_id") if c in dev_cols), None)
    dev_pa_col = next((c for c in ("individual_address", "physical_address", "pa", "address") if c in dev_cols), None)
    co_dev_id_col = next((c for c in ("device_id", "knx_device_id") if c in co_cols), None)
    if dev_id_col and dev_pa_col and co_id_col and link_co_col and co_dev_id_col:
        rows = await db.fetchall(
            f"""SELECT DISTINCT l.{ga_col} AS ga
                   FROM knx_devices d
                   JOIN knx_comm_objects co ON co.{co_dev_id_col} = d.{dev_id_col}
                   JOIN knx_co_ga_links l ON l.{link_co_col} = co.{co_id_col}
                  WHERE d.{dev_pa_col} IN ({placeholders})""",
            tuple(normalized_pas),
        )
        return _normalize_nonempty([str(row["ga"]) for row in rows if row["ga"] is not None])

    return []


def _apply_group_addresses_to_filter_query(
    query: RingBufferQueryV2,
    group_addresses: list[str],
) -> RingBufferQueryV2:
    normalized_group_addresses = _normalize_nonempty(group_addresses)
    if not normalized_group_addresses:
        return query

    metadata_payload = query.filters.metadata.model_dump() if query.filters.metadata else {}
    existing = _normalize_nonempty(metadata_payload.get("group_addresses_any_of", []))
    if existing:
        allowed_set = set(normalized_group_addresses)
        allowed = [ga for ga in existing if ga in allowed_set]
        if not allowed:
            allowed = ["__obs_no_matching_group_address__"]
    else:
        allowed = normalized_group_addresses

    metadata_payload["group_addresses_any_of"] = allowed
    return query.model_copy(
        update={"filters": query.filters.model_copy(update={"metadata": RingBufferMetadataFilterV2.model_validate(metadata_payload)})}
    )


async def _build_query_from_filter_criteria(
    filter_: FilterCriteria,
    *,
    time_filter: RingBufferTimeFilterV2 | None,
    db: Database,
    sort: RingBufferSortV2 | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> RingBufferQueryV2 | None:
    effective_filter = filter_
    if filter_.hierarchy_nodes:
        resolved_dps = await _resolve_hierarchy_to_datapoints(filter_.hierarchy_nodes, db)
        if resolved_dps:
            merged = list({*filter_.datapoints, *resolved_dps})
            effective_filter = filter_.model_copy(update={"datapoints": merged})

    query = _filter_to_query_v2(effective_filter, time_filter)

    if filter_.devices:
        resolved_group_addresses = await _resolve_device_pas_to_group_addresses(filter_.devices, db)
        if not resolved_group_addresses:
            return None
        query = _apply_group_addresses_to_filter_query(query, resolved_group_addresses)

    updates: dict[str, Any] = {}
    if sort is not None:
        updates["sort"] = sort
    if limit is not None or offset is not None:
        updates["pagination"] = query.pagination.model_copy(
            update={
                "limit": limit if limit is not None else query.pagination.limit,
                "offset": offset if offset is not None else query.pagination.offset,
            }
        )
    if updates:
        query = query.model_copy(update=updates)

    return query


def _filter_to_query_v2(filter_: FilterCriteria, time: RingBufferTimeFilterV2 | None) -> RingBufferQueryV2:
    """Translate a flat :class:`FilterCriteria` plus a time filter into the legacy
    :class:`RingBufferQueryV2` shape that :func:`_query_v2_entries` expects.

    hierarchy_nodes is intentionally NOT expanded here — concrete datapoint IDs
    are expected to already be supplied in ``filter.datapoints`` by the caller
    (the frontend resolves hierarchy_nodes via the trees API). For #431 we
    persist the hierarchy_nodes reference verbatim so the UI can re-display it,
    but server-side matching today only uses ``datapoints``.
    """
    filters: dict[str, Any] = {}
    if filter_.q:
        filters["q"] = filter_.q
    if filter_.adapters:
        filters["adapters"] = {"any_of": list(filter_.adapters)}
    if filter_.datapoints:
        filters["datapoints"] = {"ids": list(filter_.datapoints)}
    if filter_.tags:
        filters["metadata"] = {"tags_any_of": list(filter_.tags)}
    if filter_.value_filter is not None:
        filters["values"] = [filter_.value_filter.model_dump()]
    if time is not None:
        filters["time"] = time.model_dump(by_alias=True, exclude_none=True)
    return RingBufferQueryV2.model_validate(
        {
            "filters": filters,
            "sort": {"field": "id", "order": "desc"},
            "pagination": {"limit": _FILTERSET_QUERY_LIMIT_CAP, "offset": 0},
        }
    )


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------


def _csv_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _entry_to_csv_row(entry: RingBufferEntryOut) -> dict[str, str]:
    return {
        "id": str(entry.id),
        "ts": entry.ts,
        "datapoint_id": entry.datapoint_id,
        "name": entry.name or "",
        "topic": entry.topic,
        "old_value_json": _csv_json(entry.old_value),
        "new_value_json": _csv_json(entry.new_value),
        "source_adapter": entry.source_adapter,
        "quality": entry.quality,
        "metadata_version": str(entry.metadata_version),
        "metadata_json": _csv_json(entry.metadata),
    }


# ---------------------------------------------------------------------------
# Core query helper (used by /query, /export/csv and the multi-filterset query)
# ---------------------------------------------------------------------------


async def _query_v2_entries(
    body: RingBufferQueryV2,
    *,
    limit_override: int | None = None,
    offset_override: int | None = None,
) -> list[RingBufferEntryOut]:
    from obs.core.registry import get_registry

    registry = get_registry()
    registry_entries = list(registry.all())
    name_map: dict[str, str] = {str(dp.id): dp.name for dp in registry_entries}
    unit_map: dict[str, str | None] = {str(dp.id): getattr(dp, "unit", None) for dp in registry_entries}

    q = body.filters.q.strip()
    dp_ids_by_name: list[str] = []
    if q:
        q_lower = q.lower()
        dp_ids_by_name = [str(dp.id) for dp in registry_entries if q_lower in dp.name.lower()]

    adapters = [value.strip() for value in (body.filters.adapters.any_of if body.filters.adapters else []) if value.strip()]
    datapoints = [value.strip() for value in (body.filters.datapoints.ids if body.filters.datapoints else []) if value.strip()]
    value_filters = [value_filter.model_dump() for value_filter in (body.filters.values or [])]
    metadata_filter = body.filters.metadata
    metadata_tags = [value.strip() for value in (metadata_filter.tags_any_of if metadata_filter else []) if value.strip()]
    metadata_adapter_types = [value.strip() for value in (metadata_filter.adapter_types_any_of if metadata_filter else []) if value.strip()]
    metadata_adapter_instances = [
        value.strip() for value in (metadata_filter.adapter_instance_ids_any_of if metadata_filter else []) if value.strip()
    ]
    metadata_group_addresses = [value.strip() for value in (metadata_filter.group_addresses_any_of if metadata_filter else []) if value.strip()]
    metadata_topics = [value.strip() for value in (metadata_filter.topics_any_of if metadata_filter else []) if value.strip()]
    metadata_entity_ids = [value.strip() for value in (metadata_filter.entity_ids_any_of if metadata_filter else []) if value.strip()]
    metadata_register_types = [value.strip() for value in (metadata_filter.register_types_any_of if metadata_filter else []) if value.strip()]
    metadata_register_addresses = [value.strip() for value in (metadata_filter.register_addresses_any_of if metadata_filter else []) if value.strip()]

    if body.filters.adapters and not adapters:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "filters.adapters.any_of must contain at least one adapter",
        )
    if body.filters.datapoints and not datapoints:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "filters.datapoints.ids must contain at least one datapoint id",
        )
    if body.filters.values is not None and not value_filters:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "filters.values must contain at least one value filter rule",
        )
    if metadata_filter and not any(
        (
            metadata_tags,
            metadata_adapter_types,
            metadata_adapter_instances,
            metadata_group_addresses,
            metadata_topics,
            metadata_entity_ids,
            metadata_register_types,
            metadata_register_addresses,
        )
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "filters.metadata must contain at least one metadata filter rule",
        )

    time_filter = body.filters.time
    datapoint_types = {str(dp.id): dp.data_type for dp in registry_entries}
    rb = get_ringbuffer()
    try:
        entries = await rb.query_v2(
            q=q,
            adapter_any_of=adapters or None,
            datapoint_ids=datapoints or None,
            value_filters=value_filters or None,
            metadata_tags_any_of=metadata_tags or None,
            metadata_adapter_types_any_of=metadata_adapter_types or None,
            metadata_adapter_instance_ids_any_of=metadata_adapter_instances or None,
            metadata_group_addresses_any_of=metadata_group_addresses or None,
            metadata_topics_any_of=metadata_topics or None,
            metadata_entity_ids_any_of=metadata_entity_ids or None,
            metadata_register_types_any_of=metadata_register_types or None,
            metadata_register_addresses_any_of=metadata_register_addresses or None,
            datapoint_types=datapoint_types,
            from_ts=time_filter.from_ts if time_filter else None,
            to_ts=time_filter.to_ts if time_filter else None,
            from_relative_seconds=time_filter.from_relative_seconds if time_filter else None,
            to_relative_seconds=time_filter.to_relative_seconds if time_filter else None,
            limit=limit_override if limit_override is not None else body.pagination.limit,
            offset=offset_override if offset_override is not None else body.pagination.offset,
            sort_field=body.sort.field,
            sort_order=body.sort.order,
            dp_ids_by_name=dp_ids_by_name or None,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    return [
        RingBufferEntryOut(
            id=e.id,
            ts=e.ts,
            datapoint_id=e.datapoint_id,
            name=name_map.get(e.datapoint_id),
            topic=e.topic,
            old_value=e.old_value,
            new_value=e.new_value,
            source_adapter=e.source_adapter,
            quality=e.quality,
            metadata_version=e.metadata_version,
            metadata=e.metadata,
            unit=unit_map.get(e.datapoint_id),
        )
        for e in entries
    ]


# ---------------------------------------------------------------------------
# Filterset DB helpers (flat schema)
# ---------------------------------------------------------------------------


def _row_to_filterset(row: Any, *, user_state: tuple[bool, bool, int] | None = None) -> RingBufferFiltersetOut:
    """Project a DB row into the API model.

    ``user_state`` holds the per-user override
    ``(is_active, topbar_active, topbar_order)`` from
    ``ringbuffer_filterset_user_state``. When absent (no row for this user),
    the set is treated as active and un-pinned for that user. The global
    ``is_active`` / ``topbar_active`` / ``topbar_order`` columns on
    ``ringbuffer_filtersets`` are no longer surfaced via the API (#478 —
    fully isolated per-user view).
    """
    if user_state is not None:
        is_active, topbar_active, topbar_order = user_state
    else:
        is_active, topbar_active, topbar_order = True, False, 0
    created_by = row["created_by"] if "created_by" in row.keys() else None
    return RingBufferFiltersetOut(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        dsl_version=int(row["dsl_version"]),
        is_active=is_active,
        color=_normalize_color(row["color"] if "color" in row.keys() else None),
        topbar_active=topbar_active,
        topbar_order=topbar_order,
        filter=_decode_filter(row["filter_json"] if "filter_json" in row.keys() else None),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        created_by=created_by,
    )


async def _fetch_user_state(db: Database, username: str, filterset_id: str) -> tuple[bool, bool, int] | None:
    """Return ``(is_active, topbar_active, topbar_order)`` for the user, or ``None``."""
    row = await db.fetchone(
        "SELECT is_active, topbar_active, topbar_order FROM ringbuffer_filterset_user_state WHERE username=? AND filterset_id=?",
        (username, filterset_id),
    )
    if not row:
        return None
    return bool(row["is_active"]), bool(row["topbar_active"]), int(row["topbar_order"])


async def _fetch_filterset(db: Database, filterset_id: str, *, username: str | None = None) -> RingBufferFiltersetOut | None:
    row = await db.fetchone("SELECT * FROM ringbuffer_filtersets WHERE id=?", (filterset_id,))
    if not row:
        return None
    user_state = await _fetch_user_state(db, username, filterset_id) if username else None
    return _row_to_filterset(row, user_state=user_state)


async def _is_admin(db: Database, username: str) -> bool:
    row = await db.fetchone("SELECT is_admin FROM users WHERE username=?", (username,))
    return row is not None and bool(row["is_admin"])


async def _require_filterset_ownership(db: Database, filterset_id: str, current_user: str) -> RingBufferFiltersetOut:
    """Load a set or raise: 404 if missing, 403 if the caller is neither admin
    nor the owner. Mirrors the API-key admin-or-owner pattern in ``obs/api/auth.py``.

    Rows with ``created_by IS NULL`` (pre-#478 sets) are treated as shared and
    admin-only mutable.
    """
    current = await _fetch_filterset(db, filterset_id, username=current_user)
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ringbuffer filterset not found")
    if await _is_admin(db, current_user):
        return current
    if current.created_by != current_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Nur Admin oder Eigentümer")
    return current


async def _upsert_user_state(
    db: Database,
    *,
    username: str,
    filterset_id: str,
    is_active: bool,
    topbar_active: bool,
    topbar_order: int,
) -> None:
    await db.execute(
        """INSERT INTO ringbuffer_filterset_user_state
             (username, filterset_id, is_active, topbar_active, topbar_order)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(username, filterset_id) DO UPDATE SET
             is_active=excluded.is_active,
             topbar_active=excluded.topbar_active,
             topbar_order=excluded.topbar_order""",
        (username, filterset_id, int(is_active), int(topbar_active), int(topbar_order)),
    )


async def _insert_filterset(
    db: Database,
    *,
    payload: RingBufferFiltersetIn,
    created_by: str | None,
) -> RingBufferFiltersetOut:
    now = _now_iso()
    filterset_id = _new_id()

    await db.execute(
        """INSERT INTO ringbuffer_filtersets
           (id, name, description, dsl_version, is_active,
            color, topbar_active, topbar_order, filter_json, created_at, updated_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            filterset_id,
            payload.name,
            payload.description,
            payload.dsl_version,
            int(payload.is_active),
            payload.color,
            int(payload.topbar_active),
            int(payload.topbar_order),
            _encode_filter(payload.filter),
            now,
            now,
            created_by,
        ),
    )
    # Seed the creator's own per-user state from the POST body so GET as that
    # user returns is_active/topbar_active/topbar_order as requested. We only
    # write a row when something diverges from the defaults (active+un-pinned)
    # to keep the override table lean.
    if created_by is not None and (not payload.is_active or payload.topbar_active or payload.topbar_order):
        await _upsert_user_state(
            db,
            username=created_by,
            filterset_id=filterset_id,
            is_active=bool(payload.is_active),
            topbar_active=bool(payload.topbar_active),
            topbar_order=int(payload.topbar_order),
        )
    await db.commit()
    created = await _fetch_filterset(db, filterset_id, username=created_by)
    if not created:
        raise RuntimeError("failed to create filterset")
    return created


# ---------------------------------------------------------------------------
# RingBuffer query (existing endpoints — unchanged behaviour)
# ---------------------------------------------------------------------------


@router.get("/", response_model=list[RingBufferEntryOut])
async def query_ringbuffer(
    q: str = Query("", description="Substring in datapoint name, id or source_adapter"),
    adapter: str = Query("", description="Exact source_adapter match"),
    from_ts: str = Query("", alias="from", description="ISO-8601 timestamp (exclusive lower bound)"),
    limit: int = Query(100, ge=1, le=10000),
    _user: str = Depends(get_current_user),
) -> list[RingBufferEntryOut]:
    from obs.core.registry import get_registry

    registry = get_registry()
    registry_entries = list(registry.all())
    name_map: dict[str, str] = {str(dp.id): dp.name for dp in registry_entries}
    unit_map: dict[str, str | None] = {str(dp.id): getattr(dp, "unit", None) for dp in registry_entries}
    dp_ids_by_name: list[str] = []
    if q:
        q_lower = q.lower()
        dp_ids_by_name = [str(dp.id) for dp in registry.all() if q_lower in dp.name.lower()]

    rb = get_ringbuffer()
    entries = await rb.query(
        q=q,
        adapter=adapter,
        from_ts=from_ts,
        limit=limit,
        dp_ids=dp_ids_by_name or None,
    )
    return [
        RingBufferEntryOut(
            id=e.id,
            ts=e.ts,
            datapoint_id=e.datapoint_id,
            name=name_map.get(e.datapoint_id),
            topic=e.topic,
            old_value=e.old_value,
            new_value=e.new_value,
            source_adapter=e.source_adapter,
            quality=e.quality,
            metadata_version=e.metadata_version,
            metadata=e.metadata,
            unit=unit_map.get(e.datapoint_id),
        )
        for e in entries
    ]


@router.post("/query", response_model=list[RingBufferEntryOut])
async def query_ringbuffer_v2(
    body: RingBufferQueryV2,
    _user: str = Depends(get_current_user),
) -> list[RingBufferEntryOut]:
    return await _query_v2_entries(body)


@router.post("/export/csv")
async def export_ringbuffer_csv(
    body: RingBufferQueryV2,
    background_tasks: BackgroundTasks,
    _user: str = Depends(get_current_user),
) -> StreamingResponse:
    # CSV export always returns the full filtered result set independent of UI pagination.
    started = time.monotonic()
    offset = 0
    exported_rows = 0

    spool = tempfile.SpooledTemporaryFile(
        mode="w+",
        encoding="utf-8",
        newline="",
        max_size=_CSV_EXPORT_SPOOL_MAX_BYTES,
    )
    writer = csv.DictWriter(spool, fieldnames=list(_CSV_EXPORT_HEADERS))
    writer.writeheader()

    try:
        while True:
            if time.monotonic() - started > _CSV_EXPORT_TOTAL_TIMEOUT_SECONDS:
                raise HTTPException(
                    status.HTTP_504_GATEWAY_TIMEOUT,
                    "ringbuffer CSV export timed out",
                )

            remaining = _CSV_EXPORT_MAX_ROWS - exported_rows
            if remaining <= 0:
                break
            chunk_size = min(_CSV_EXPORT_CHUNK_SIZE, remaining)

            try:
                chunk = await asyncio.wait_for(
                    _query_v2_entries(
                        body,
                        limit_override=chunk_size,
                        offset_override=offset,
                    ),
                    timeout=_CSV_EXPORT_QUERY_TIMEOUT_SECONDS,
                )
            except TimeoutError as exc:
                raise HTTPException(
                    status.HTTP_504_GATEWAY_TIMEOUT,
                    "ringbuffer CSV export timed out",
                ) from exc

            if not chunk:
                break

            for entry in chunk:
                writer.writerow(_entry_to_csv_row(entry))

            fetched = len(chunk)
            exported_rows += fetched
            offset += fetched

            if fetched < chunk_size:
                break

        if exported_rows == _CSV_EXPORT_MAX_ROWS:
            probe = await asyncio.wait_for(
                _query_v2_entries(
                    body,
                    limit_override=1,
                    offset_override=offset,
                ),
                timeout=_CSV_EXPORT_QUERY_TIMEOUT_SECONDS,
            )
            if probe:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    f"export row limit exceeded (max {_CSV_EXPORT_MAX_ROWS})",
                )
    except Exception:
        spool.close()
        raise

    spool.seek(0)
    filename = f"ringbuffer_export_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.csv"
    background_tasks.add_task(spool.close)
    return StreamingResponse(
        spool,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-RingBuffer-Export-Rows": str(exported_rows),
        },
        background=background_tasks,
    )


# ---------------------------------------------------------------------------
# Filterset CRUD (flat schema, with legacy shim on POST/PUT)
# ---------------------------------------------------------------------------


def _parse_filterset_in(raw: dict[str, Any]) -> RingBufferFiltersetIn:
    try:
        return RingBufferFiltersetIn.model_validate(raw)
    except ValidationError as exc:
        # ``include_context=False`` strips the raw Python exception object from
        # the error list — FastAPI's default JSON encoder chokes on the bundled
        # ``ValueError`` instance otherwise (see #431).
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            exc.errors(include_url=False, include_context=False),
        ) from exc


async def _read_json_body(request: Request) -> dict[str, Any]:
    try:
        raw = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid JSON body") from exc
    if not isinstance(raw, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "request body must be a JSON object")
    return raw


@router.get("/filtersets", response_model=list[RingBufferFiltersetOut])
async def list_ringbuffer_filtersets(
    current_user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> list[RingBufferFiltersetOut]:
    rows = await db.fetchall("SELECT * FROM ringbuffer_filtersets")
    states = {
        row["filterset_id"]: (bool(row["is_active"]), bool(row["topbar_active"]), int(row["topbar_order"]))
        for row in await db.fetchall(
            "SELECT filterset_id, is_active, topbar_active, topbar_order FROM ringbuffer_filterset_user_state WHERE username=?",
            (current_user,),
        )
    }
    out = [_row_to_filterset(row, user_state=states.get(row["id"])) for row in rows]
    out.sort(key=lambda fs: (fs.topbar_order, fs.created_at, fs.id))
    return out


@router.post("/filtersets", response_model=RingBufferFiltersetOut, status_code=status.HTTP_201_CREATED)
async def create_ringbuffer_filterset(
    request: Request,
    current_user: str = Depends(get_admin_user),
    db: Database = Depends(get_db),
) -> RingBufferFiltersetOut:
    raw = await _read_json_body(request)
    payload = _parse_filterset_in(raw)
    if _is_empty_criteria(payload.filter):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "filterset.filter must declare at least one criterion (hierarchy_nodes, datapoints, devices, tags, adapters, q, or value_filter)",
        )
    return await _insert_filterset(db, payload=payload, created_by=current_user)


@router.get("/filtersets/{filterset_id}", response_model=RingBufferFiltersetOut)
async def get_ringbuffer_filterset(
    filterset_id: str,
    current_user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> RingBufferFiltersetOut:
    current = await _fetch_filterset(db, filterset_id, username=current_user)
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ringbuffer filterset not found")
    return current


@router.put("/filtersets/{filterset_id}", response_model=RingBufferFiltersetOut)
async def update_ringbuffer_filterset(
    filterset_id: str,
    request: Request,
    current_user: str = Depends(get_admin_user),
    db: Database = Depends(get_db),
) -> RingBufferFiltersetOut:
    current = await _require_filterset_ownership(db, filterset_id, current_user)

    raw = await _read_json_body(request)
    try:
        body = RingBufferFiltersetUpdate.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            exc.errors(include_url=False, include_context=False),
        ) from exc

    now = _now_iso()
    name = body.name if body.name is not None else current.name
    description = body.description if body.description is not None else current.description
    dsl_version = body.dsl_version if body.dsl_version is not None else current.dsl_version
    color = body.color if body.color is not None else current.color
    new_filter = body.filter if body.filter is not None else current.filter

    if _is_empty_criteria(new_filter):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "filterset.filter must declare at least one criterion (hierarchy_nodes, datapoints, devices, tags, adapters, q, or value_filter)",
        )

    # is_active, topbar_active and topbar_order live in
    # ringbuffer_filterset_user_state per-user (#478); PUT only changes the
    # shared payload (name/description/filter/color/dsl_version) and never
    # touches the global is_active / topbar_* columns.
    await db.execute(
        """UPDATE ringbuffer_filtersets
           SET name=?, description=?, dsl_version=?,
               color=?, filter_json=?, updated_at=?
           WHERE id=?""",
        (
            name,
            description,
            dsl_version,
            color,
            _encode_filter(new_filter),
            now,
            filterset_id,
        ),
    )
    # Allow body.is_active/topbar_active/topbar_order to update the caller's
    # own per-user state for backward compat with clients that still bundle
    # these fields into a PUT.
    if body.is_active is not None or body.topbar_active is not None or body.topbar_order is not None:
        prior = await _fetch_user_state(db, current_user, filterset_id)
        active = body.is_active if body.is_active is not None else (prior[0] if prior else True)
        topbar_active = body.topbar_active if body.topbar_active is not None else (prior[1] if prior else False)
        order = body.topbar_order if body.topbar_order is not None else (prior[2] if prior else 0)
        await _upsert_user_state(
            db,
            username=current_user,
            filterset_id=filterset_id,
            is_active=bool(active),
            topbar_active=bool(topbar_active),
            topbar_order=int(order),
        )
    await db.commit()
    updated = await _fetch_filterset(db, filterset_id, username=current_user)
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ringbuffer filterset not found")
    return updated


@router.delete("/filtersets/{filterset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ringbuffer_filterset(
    filterset_id: str,
    current_user: str = Depends(get_admin_user),
    db: Database = Depends(get_db),
) -> None:
    await _require_filterset_ownership(db, filterset_id, current_user)
    await db.execute_and_commit("DELETE FROM ringbuffer_filtersets WHERE id=?", (filterset_id,))


@router.post("/filtersets/{filterset_id}/clone", response_model=RingBufferFiltersetOut, status_code=status.HTTP_201_CREATED)
async def clone_ringbuffer_filterset(
    filterset_id: str,
    body: RingBufferFiltersetCloneRequest,
    current_user: str = Depends(get_admin_user),
    db: Database = Depends(get_db),
) -> RingBufferFiltersetOut:
    # Cloning stays open for everyone — that's how a non-admin gets a writable
    # copy of an admin-curated or someone else's set (#478).
    source = await _fetch_filterset(db, filterset_id)
    if not source:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ringbuffer filterset not found")

    clone_name = body.name if body.name else f"{source.name} (Copy)"
    clone_payload = RingBufferFiltersetIn(
        name=clone_name,
        description=source.description,
        dsl_version=source.dsl_version,
        is_active=source.is_active,
        color=source.color,
        # Clones do not inherit topbar activation — the user explicitly opts in
        # via the PATCH /topbar endpoint after deciding the clone is ready.
        topbar_active=False,
        topbar_order=0,
        filter=source.filter,
    )
    return await _insert_filterset(db, payload=clone_payload, created_by=current_user)


# ---------------------------------------------------------------------------
# Topbar PATCH endpoints (#431)
# ---------------------------------------------------------------------------


@router.patch("/filtersets/order", response_model=list[RingBufferFiltersetOut])
async def patch_ringbuffer_filtersets_order(
    body: RingBufferFiltersetOrderPatch,
    current_user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> list[RingBufferFiltersetOut]:
    """Persist a new topbar order for several sets in one batch — per-user.

    Sets not mentioned in ``items`` keep their existing per-user ``topbar_order``.
    Unknown IDs are ignored silently — a racing delete must not break drag-and-
    drop reordering.
    """
    known_ids = {row["id"] for row in await db.fetchall("SELECT id FROM ringbuffer_filtersets")}
    for item in body.items:
        if item.id not in known_ids:
            continue
        prior = await _fetch_user_state(db, current_user, item.id)
        is_active = prior[0] if prior else True
        topbar_active = prior[1] if prior else False
        await _upsert_user_state(
            db,
            username=current_user,
            filterset_id=item.id,
            is_active=bool(is_active),
            topbar_active=bool(topbar_active),
            topbar_order=int(item.topbar_order),
        )
    await db.commit()

    rows = await db.fetchall("SELECT * FROM ringbuffer_filtersets")
    states = {
        row["filterset_id"]: (bool(row["is_active"]), bool(row["topbar_active"]), int(row["topbar_order"]))
        for row in await db.fetchall(
            "SELECT filterset_id, is_active, topbar_active, topbar_order FROM ringbuffer_filterset_user_state WHERE username=?",
            (current_user,),
        )
    }
    out = [_row_to_filterset(row, user_state=states.get(row["id"])) for row in rows]
    out.sort(key=lambda fs: (fs.topbar_order, fs.created_at, fs.id))
    return out


@router.patch("/filtersets/{filterset_id}/topbar", response_model=RingBufferFiltersetOut)
async def patch_ringbuffer_filterset_topbar(
    filterset_id: str,
    body: RingBufferFiltersetTopbarPatch,
    current_user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> RingBufferFiltersetOut:
    """Update the per-user view (``is_active``, ``topbar_active``, ``topbar_order``).

    All three fields live in ``ringbuffer_filterset_user_state`` (#478) so every
    authenticated user maintains their own active/pinned state. No ownership
    check is required — this is the user's *own* view of the shared filterset.
    """
    fs_row = await db.fetchone("SELECT id FROM ringbuffer_filtersets WHERE id=?", (filterset_id,))
    if not fs_row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ringbuffer filterset not found")

    prior = await _fetch_user_state(db, current_user, filterset_id)
    cur_is_active = prior[0] if prior else True
    cur_topbar_active = prior[1] if prior else False
    cur_topbar_order = prior[2] if prior else 0

    is_active = body.is_active if body.is_active is not None else cur_is_active
    topbar_active = body.topbar_active if body.topbar_active is not None else cur_topbar_active
    topbar_order = body.topbar_order if body.topbar_order is not None else cur_topbar_order

    # When the set transitions from "not in topbar" to "in topbar" *and* the
    # caller did not pin an explicit order, assign one that ranks the new
    # set after every currently topbar-active set IN THIS USER'S VIEW. This
    # avoids many sets piling up at topbar_order=0 and keeps the deterministic
    # first-color-wins tie-break sane.
    if topbar_active and not cur_topbar_active and body.topbar_order is None:
        max_row = await db.fetchone(
            "SELECT COALESCE(MAX(topbar_order), -1) AS max_order FROM ringbuffer_filterset_user_state "
            "WHERE username=? AND topbar_active=1 AND filterset_id != ?",
            (current_user, filterset_id),
        )
        max_order = int(max_row["max_order"]) if max_row else -1
        topbar_order = max_order + 1

    await _upsert_user_state(
        db,
        username=current_user,
        filterset_id=filterset_id,
        is_active=bool(is_active),
        topbar_active=bool(topbar_active),
        topbar_order=int(topbar_order),
    )
    await db.commit()

    updated = await _fetch_filterset(db, filterset_id, username=current_user)
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ringbuffer filterset not found")
    return updated


# ---------------------------------------------------------------------------
# Multi-set query (#431) — OR-union across all named sets, annotated entries
# ---------------------------------------------------------------------------


@router.post("/filtersets/query", response_model=list[RingBufferMultiEntryOut])
async def query_ringbuffer_filtersets_multi(
    body: RingBufferMultiQueryRequest,
    current_user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> list[RingBufferMultiEntryOut]:
    if len(body.set_ids) > _FILTERSET_MULTI_QUERY_SET_CAP:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"too many filtersets requested (max {_FILTERSET_MULTI_QUERY_SET_CAP})",
        )

    # No filtersets requested → return entries filtered only by the time window
    # (or no filter at all if ``time`` is also None). This mirrors how the topbar
    # looks before the user toggles any set on.
    if not body.set_ids:
        empty_filter = FilterCriteria()
        query = _filter_to_query_v2(empty_filter, body.time)
        query = query.model_copy(
            update={
                "sort": body.sort,
                "pagination": query.pagination.model_copy(
                    update={"limit": body.limit, "offset": body.offset},
                ),
            }
        )
        entries = await _query_v2_entries(query)
        return [RingBufferMultiEntryOut(**entry.model_dump(), matched_set_ids=[]) for entry in entries]

    # Resolve sets — skip missing/inactive (per-user) ones rather than fail.
    # ``is_active`` is now part of the caller's per-user state, so two users may
    # see different OR-unions across the same set_ids.
    resolved: list[RingBufferFiltersetOut] = []
    for set_id in body.set_ids:
        current = await _fetch_filterset(db, set_id, username=current_user)
        if current is None:
            continue
        if not current.is_active:
            continue
        # Empty FilterCriteria → no real filter configured yet. Skip so the
        # user sees the empty result (#36 UX): topbar chip will show the
        # warn marker so the misconfiguration is obvious.
        if _is_empty_criteria(current.filter):
            continue
        resolved.append(current)

    # Per-set query, generously paginated; OR-union by entry id and remember
    # which sets contributed to the union for each entry.
    per_set_limit = min(body.limit + body.offset + _FILTERSET_QUERY_LIMIT_CAP, _FILTERSET_MULTI_QUERY_PER_SET_LIMIT)
    matched: dict[int, list[str]] = {}
    entries_by_id: dict[int, RingBufferEntryOut] = {}
    for fs in resolved:
        query = await _build_query_from_filter_criteria(
            fs.filter,
            time_filter=body.time,
            db=db,
            sort=body.sort,
            limit=per_set_limit,
            offset=0,
        )
        if query is None:
            continue
        try:
            rows = await _query_v2_entries(query)
        except HTTPException:
            # An empty-but-present filter criterion (e.g. tags=[]) reduces to a
            # no-op match — skip it instead of failing the whole multi-query.
            continue
        for entry in rows:
            matched.setdefault(entry.id, []).append(fs.id)
            entries_by_id.setdefault(entry.id, entry)

    # Apply final sort + pagination on the union.
    ordered_ids = sorted(
        matched.keys(),
        key=lambda eid: (entries_by_id[eid].ts, entries_by_id[eid].id) if body.sort.field == "ts" else entries_by_id[eid].id,
        reverse=(body.sort.order == "desc"),
    )
    paginated = ordered_ids[body.offset : body.offset + body.limit]
    return [
        RingBufferMultiEntryOut(
            **entries_by_id[eid].model_dump(),
            matched_set_ids=matched[eid],
        )
        for eid in paginated
    ]


@router.post("/filtersets/{filterset_id}/query", response_model=list[RingBufferEntryOut])
async def query_ringbuffer_filterset(
    filterset_id: str,
    current_user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> list[RingBufferEntryOut]:
    """Single-set query (back-compat for callers that target one set at a time).

    The body is intentionally ignored — the time filter and pagination are
    fixed defaults here. Callers that need custom time/pagination should use
    ``POST /filtersets/query`` with a single-element ``set_ids`` list. The
    set's ``is_active`` flag is taken from the caller's per-user state (#478).
    """
    current = await _fetch_filterset(db, filterset_id, username=current_user)
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ringbuffer filterset not found")
    if not current.is_active:
        return []

    query = await _build_query_from_filter_criteria(current.filter, time_filter=None, db=db)
    if query is None:
        return []
    return await _query_v2_entries(query)


# ---------------------------------------------------------------------------
# Multi-set CSV/TSV export (#427)
# ---------------------------------------------------------------------------


_EXPORT_SETTINGS_KEY = "ringbuffer.export_settings"


async def _collect_multi_entries(
    body: RingBufferMultiExportRequest,
    db: Database,
    *,
    username: str | None = None,
) -> tuple[list[RingBufferEntryOut], dict[int, list[str]]]:
    """Collect the OR-union of entries across the requested filtersets.

    Mirrors the logic of ``POST /filtersets/query`` but with the export-specific
    row cap and pagination semantics: we want **all** rows in the union, capped
    only by the global ``_CSV_EXPORT_MAX_ROWS`` guard the streaming writer
    enforces.
    """
    if len(body.set_ids) > _FILTERSET_MULTI_QUERY_SET_CAP:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"too many filtersets requested (max {_FILTERSET_MULTI_QUERY_SET_CAP})",
        )

    if not body.set_ids:
        empty_filter = FilterCriteria()
        query = _filter_to_query_v2(empty_filter, body.time)
        query = query.model_copy(
            update={
                "pagination": query.pagination.model_copy(
                    update={"limit": _CSV_EXPORT_MAX_ROWS, "offset": 0},
                ),
            }
        )
        entries = await _query_v2_entries(query)
        return entries, {e.id: [] for e in entries}

    resolved: list[RingBufferFiltersetOut] = []
    for set_id in body.set_ids:
        current = await _fetch_filterset(db, set_id, username=username)
        if current is None or not current.is_active:
            continue
        # Empty FilterCriteria → the set has no real filter configured yet.
        # Treat it as a no-op so the user sees an empty table (and the chip
        # warn-icon in the UI) rather than every row being painted (#36).
        if _is_empty_criteria(current.filter):
            continue
        resolved.append(current)

    matched: dict[int, list[str]] = {}
    entries_by_id: dict[int, RingBufferEntryOut] = {}
    for fs in resolved:
        query = await _build_query_from_filter_criteria(
            fs.filter,
            time_filter=body.time,
            db=db,
            limit=_CSV_EXPORT_MAX_ROWS,
            offset=0,
        )
        if query is None:
            continue
        try:
            rows = await _query_v2_entries(query)
        except HTTPException:
            continue
        for entry in rows:
            matched.setdefault(entry.id, []).append(fs.id)
            entries_by_id.setdefault(entry.id, entry)

    ordered_ids = sorted(matched.keys(), key=lambda eid: entries_by_id[eid].ts, reverse=True)
    ordered_entries = [entries_by_id[eid] for eid in ordered_ids]
    return ordered_entries, matched


@router.post("/filtersets/export/count", response_model=RingBufferMultiExportCountResponse)
async def count_ringbuffer_filtersets_export(
    body: RingBufferMultiExportCountRequest,
    current_user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> RingBufferMultiExportCountResponse:
    """Preflight: how many rows would the corresponding CSV export produce?

    Used by the UI to warn the user before triggering a large download. The
    set/time semantics match ``POST /filtersets/export/csv`` exactly, so the
    returned count is the row count of the union the export would write.
    Per-user ``is_active`` applies — a set the caller has deactivated for
    themselves is excluded from the count too (#478).
    """
    export_body = RingBufferMultiExportRequest(set_ids=body.set_ids, time=body.time)
    entries, _ = await _collect_multi_entries(export_body, db, username=current_user)
    return RingBufferMultiExportCountResponse(row_count=len(entries))


@router.post("/filtersets/export/csv")
async def export_ringbuffer_filtersets_csv(
    body: RingBufferMultiExportRequest,
    background_tasks: BackgroundTasks,
    current_user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> StreamingResponse:
    """Multi-set CSV/TSV export — OR-union of all requested active sets.

    Optional columns (``unit`` from #434, ``matched_set_ids`` from #431) and
    encoding (UTF-8 with optional BOM) are toggleable via the request body. The
    persisted user defaults live behind ``GET/PUT /ringbuffer/export/settings``.
    Per-user ``is_active`` filters the OR-union to what the caller has enabled.
    """
    entries, matched = await _collect_multi_entries(body, db, username=current_user)
    if len(entries) > _CSV_EXPORT_MAX_ROWS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"export row limit exceeded (max {_CSV_EXPORT_MAX_ROWS})",
        )

    # Tab delimiter conventionally produces .tsv with the matching media type;
    # everything else lands as .csv. Extension and media type are derived from
    # the delimiter, not from a separate format selector.
    extension = "tsv" if body.delimiter == "\t" else "csv"
    media_type = "text/tab-separated-values" if body.delimiter == "\t" else "text/csv"

    fieldnames = list(_CSV_EXPORT_HEADERS)
    if body.include_unit:
        fieldnames.append("unit")
    if body.include_matched_set_ids:
        fieldnames.append("matched_set_ids")

    spool = tempfile.SpooledTemporaryFile(
        mode="w+",
        encoding="utf-8",
        newline="",
        max_size=_CSV_EXPORT_SPOOL_MAX_BYTES,
    )
    if body.encoding == "utf8-bom":
        spool.write("﻿")
    # Empty escape_char selects RFC 4180 behaviour (doublequote=True). Setting
    # an escape_char switches the writer to backslash-style escaping; csv
    # requires doublequote=False in that mode.
    writer_kwargs: dict[str, Any] = {
        "delimiter": body.delimiter,
        "quotechar": body.quote_char,
    }
    if body.escape_char:
        writer_kwargs["escapechar"] = body.escape_char
        writer_kwargs["doublequote"] = False
    writer = csv.DictWriter(spool, fieldnames=fieldnames, **writer_kwargs)
    writer.writeheader()
    for entry in entries:
        row = _entry_to_csv_row(entry)
        if body.include_unit:
            row["unit"] = entry.unit or ""
        if body.include_matched_set_ids:
            row["matched_set_ids"] = ",".join(matched.get(entry.id, []))
        writer.writerow(row)

    spool.seek(0)
    filename = f"ringbuffer_export_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.{extension}"
    background_tasks.add_task(spool.close)
    return StreamingResponse(
        spool,
        media_type=f"{media_type}; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-RingBuffer-Export-Rows": str(len(entries)),
        },
        background=background_tasks,
    )


@router.get("/export/settings", response_model=RingBufferExportSettings)
async def get_ringbuffer_export_settings(
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> RingBufferExportSettings:
    row = await db.fetchone("SELECT value FROM app_settings WHERE key=?", (_EXPORT_SETTINGS_KEY,))
    if not row or not row["value"]:
        return RingBufferExportSettings()
    try:
        raw = json.loads(row["value"])
    except json.JSONDecodeError:
        return RingBufferExportSettings()
    # Legacy format: pre-#427 the dialog stored a CSV/TSV radio selection.
    # Translate to the new delimiter when the new key is absent so users
    # don't silently lose their old TSV preference on first load.
    if isinstance(raw, dict) and "delimiter" not in raw and raw.get("format") == "tsv":
        raw["delimiter"] = "\t"
    try:
        return RingBufferExportSettings(**raw)
    except ValidationError:
        return RingBufferExportSettings()


@router.put("/export/settings", response_model=RingBufferExportSettings)
async def put_ringbuffer_export_settings(
    body: RingBufferExportSettings,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> RingBufferExportSettings:
    payload = json.dumps(body.model_dump())
    await db.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (_EXPORT_SETTINGS_KEY, payload),
    )
    await db.commit()
    return body


# ---------------------------------------------------------------------------
# Stats / config
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=RingBufferStats)
async def ringbuffer_stats(
    _user: str = Depends(get_current_user),
) -> RingBufferStats:
    stats = await get_ringbuffer().stats()
    return RingBufferStats(**stats)


@router.post("/config", response_model=RingBufferStats)
async def configure_ringbuffer(
    body: RingBufferConfig,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> RingBufferStats:
    if body.storage != "file":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "storage must be 'file' (memory and disk are no longer supported)",
        )

    rb = get_ringbuffer()
    reconfigure_kwargs: dict[str, Any] = {}
    if "max_entries" in body.model_fields_set:
        reconfigure_kwargs["max_entries"] = body.max_entries
    if "max_file_size_bytes" in body.model_fields_set:
        reconfigure_kwargs["max_file_size_bytes"] = body.max_file_size_bytes
    if "max_age" in body.model_fields_set:
        reconfigure_kwargs["max_age"] = body.max_age
    await rb.reconfigure(body.storage, **reconfigure_kwargs)
    stats = await rb.stats()
    await persist_ringbuffer_config(
        db,
        max_entries=stats["max_entries"],
        max_file_size_bytes=stats["max_file_size_bytes"],
        max_age=stats["max_age"],
    )
    return RingBufferStats(**stats)
