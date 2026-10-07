"""Data invariant of #1296: every stored group address text is in the internal notation.

Scans every place OBS stores a group address text. Tests call it after driving an
entrance; a store that bypassed ``normalize_ga`` shows up here regardless of the
module, field name or SQL it used.

The list of storage places was taken from the schemas (``obs/db/database.py``,
``obs/ringbuffer/ringbuffer.py``, ``obs/ringbuffer/store/sqlite_backend.py``) and
from every JSON document OBS persists, searched for ``group_address``/``ga``
fields:

- ``knx_group_addresses.address``, ``knx_co_ga_links.ga_address``,
  ``knx_function_ga_links.ga_address``, ``knx_ga_merge_conflicts.address`` (its
  ``spelling`` is the raw text on purpose) — :func:`non_internal_group_addresses`;
- ``adapter_bindings.config`` → ``group_address``/``state_group_address`` of KNX
  bindings — :func:`non_internal_group_addresses`;
- ringbuffer entries: the binding snapshot in ``metadata`` and the
  ``ringbuffer_metadata_bindings.group_address`` column, both written from one
  snapshot (``_normalize_binding_metadata``) — :func:`non_internal_ringbuffer_bindings`
  on entries returned by the ringbuffer API.

Not a storage place: ring buffer filter sets store device PAs, not group
addresses; logic graphs, Visu nodes, app settings and ``knx_project`` hold none.
"""

from __future__ import annotations

import json

from obs.adapters.knx.group_address import try_normalize_ga

KNX_GA_COLUMNS = (
    ("knx_group_addresses", "address"),
    ("knx_co_ga_links", "ga_address"),
    ("knx_function_ga_links", "ga_address"),
    ("knx_ga_merge_conflicts", "address"),
)
BINDING_GA_KEYS = ("group_address", "state_group_address")


async def non_internal_group_addresses(db, binding_ids: set[str] | None = None) -> list[str]:
    """``location: text`` for every stored GA text that is not internally normalized.

    ``binding_ids`` limits the binding scan to bindings a test created itself, for
    databases shared with tests that deliberately store raw bindings.
    """
    found: list[str] = []
    for table, column in KNX_GA_COLUMNS:
        for row in await db.fetchall(f"SELECT {column} AS ga FROM {table}"):
            if try_normalize_ga(row["ga"]) != row["ga"]:
                found.append(f"{table}.{column}: {row['ga']!r}")
    for row in await db.fetchall("SELECT id, config FROM adapter_bindings WHERE UPPER(adapter_type) = 'KNX'"):
        if binding_ids is not None and row["id"] not in binding_ids:
            continue
        config = json.loads(row["config"] or "{}")
        for key in BINDING_GA_KEYS:
            value = config.get(key)
            if value is not None and str(value).strip() and try_normalize_ga(value) != value:
                found.append(f"adapter_bindings[{row['id']}].{key}: {value!r}")
    return found


def non_internal_ringbuffer_bindings(entries: list[dict]) -> list[str]:
    """``entry: text`` for every KNX binding snapshot of ringbuffer entries that is not internal."""
    found: list[str] = []
    for entry in entries:
        for binding in (entry.get("metadata") or {}).get("bindings") or []:
            if str(binding.get("adapter_type", "")).upper() != "KNX":
                continue
            for key in BINDING_GA_KEYS:
                value = (binding.get("normalized") or {}).get(key)
                if value and try_normalize_ga(value) != value:
                    found.append(f"ringbuffer[{entry.get('id')}].{key}: {value!r}")
    return found
