"""Data invariant of #1296: every stored group address text is in the internal notation.

Scans every place OBS stores a group address text. Tests call it after driving an
entrance; a store that bypassed ``normalize_ga`` shows up here regardless of the
module, field name or SQL it used.
"""

from __future__ import annotations

import json

from obs.adapters.knx.group_address import try_normalize_ga

KNX_GA_COLUMNS = (
    ("knx_group_addresses", "address"),
    ("knx_co_ga_links", "ga_address"),
    ("knx_function_ga_links", "ga_address"),
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
