from __future__ import annotations

from datetime import UTC, datetime

import pytest

from obs.api.v1.ringbuffer import FilterCriteria, NodeRef, _build_query_from_filter_criteria
from obs.db.database import Database


async def _insert_empty_hierarchy_node(db: Database) -> NodeRef:
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO hierarchy_trees (id, name, description, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        ("tree-empty", "Empty tree", "", now, now),
    )
    await db.execute(
        """INSERT INTO hierarchy_nodes
           (id, tree_id, parent_id, name, description, node_order, icon, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("node-empty", "tree-empty", None, "Empty node", "", 0, None, now, now),
    )
    await db.commit()
    return NodeRef(tree_id="tree-empty", node_id="node-empty")


@pytest.mark.asyncio
async def test_build_query_empty_hierarchy_subtree_matches_nothing():
    db = Database(":memory:")
    await db.connect()
    try:
        node = await _insert_empty_hierarchy_node(db)

        query = await _build_query_from_filter_criteria(
            FilterCriteria(
                hierarchy_nodes=[node],
                datapoints=["", "  "],
                tags=["would-otherwise-match"],
            ),
            time_filter=None,
            db=db,
        )

        assert query is None
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_build_query_unknown_hierarchy_node_matches_nothing():
    db = Database(":memory:")
    await db.connect()
    try:
        query = await _build_query_from_filter_criteria(
            FilterCriteria(
                hierarchy_nodes=[NodeRef(tree_id="deleted-tree", node_id="deleted-node")],
            ),
            time_filter=None,
            db=db,
        )

        assert query is None
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_build_query_keeps_explicit_datapoints_when_hierarchy_is_empty():
    db = Database(":memory:")
    await db.connect()
    try:
        node = await _insert_empty_hierarchy_node(db)

        query = await _build_query_from_filter_criteria(
            FilterCriteria(hierarchy_nodes=[node], datapoints=["explicit-dp"]),
            time_filter=None,
            db=db,
        )

        assert query is not None
        assert query.filters.datapoints is not None
        assert query.filters.datapoints.ids == ["explicit-dp"]
    finally:
        await db.disconnect()
