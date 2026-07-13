"""Atomic lifecycle helpers for hierarchy resources and central grants."""

from __future__ import annotations

from obs.db.database import Database

_SQLITE_ID_CHUNK_SIZE = 500


def _chunks(values: list[str]) -> list[list[str]]:
    return [values[index : index + _SQLITE_ID_CHUNK_SIZE] for index in range(0, len(values), _SQLITE_ID_CHUNK_SIZE)]


async def collect_hierarchy_subtree_node_ids(db: Database, node_id: str) -> list[str]:
    rows = await db.fetchall(
        """WITH RECURSIVE subtree(id) AS (
               SELECT id FROM hierarchy_nodes WHERE id=?
               UNION
               SELECT child.id
               FROM hierarchy_nodes AS child
               JOIN subtree ON child.parent_id=subtree.id
           )
           SELECT id FROM subtree""",
        (node_id,),
    )
    return [row["id"] for row in rows]


async def collect_hierarchy_tree_node_ids(db: Database, tree_ids: list[str]) -> list[str]:
    node_ids: list[str] = []
    for chunk in _chunks(tree_ids):
        placeholders = ",".join("?" * len(chunk))
        rows = await db.fetchall(
            f"SELECT id FROM hierarchy_nodes WHERE tree_id IN ({placeholders})",
            chunk,
        )
        node_ids.extend(row["id"] for row in rows)
    return node_ids


async def delete_hierarchy_grants(db: Database, node_ids: list[str]) -> None:
    for chunk in _chunks(node_ids):
        placeholders = ",".join("?" * len(chunk))
        await db.execute(
            f"DELETE FROM authz_node_roles WHERE node_type='hierarchy' AND node_id IN ({placeholders})",
            chunk,
        )
