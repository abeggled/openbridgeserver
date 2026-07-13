from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from obs.api.auth import get_admin_user, get_current_principal
from obs.api.v1 import adapters, logic, visu
from obs.api.v1.route_classification_registry import ROUTE_CLASSIFICATIONS


CREATION_ROUTE_MATRIX = (
    (adapters.router, "/instances", "POST", get_admin_user, "/api/v1/adapters/instances"),
    (
        adapters.router,
        "/instances/{instance_id}/iobroker/import-preview",
        "POST",
        get_current_principal,
        "/api/v1/adapters/instances/{instance_id}/iobroker/import-preview",
    ),
    (
        adapters.router,
        "/instances/{instance_id}/iobroker/import",
        "POST",
        get_current_principal,
        "/api/v1/adapters/instances/{instance_id}/iobroker/import",
    ),
    (
        adapters.router,
        "/instances/{instance_id}/anwesenheit/sync-bindings",
        "POST",
        get_current_principal,
        "/api/v1/adapters/instances/{instance_id}/anwesenheit/sync-bindings",
    ),
    (logic.router, "/graphs", "POST", get_current_principal, "/api/v1/logic/graphs"),
    (logic.router, "/graphs/import", "POST", get_current_principal, "/api/v1/logic/graphs/import"),
    (
        logic.router,
        "/graphs/{graph_id}/duplicate",
        "POST",
        get_current_principal,
        "/api/v1/logic/graphs/{graph_id}/duplicate",
    ),
    (visu.router, "/nodes", "POST", get_current_principal, "/api/v1/visu/nodes"),
    (visu.router, "/nodes/import", "POST", get_current_principal, "/api/v1/visu/nodes/import"),
    (
        visu.router,
        "/nodes/{node_id}/copy",
        "POST",
        get_current_principal,
        "/api/v1/visu/nodes/{node_id}/copy",
    ),
)


@pytest.mark.parametrize(("router", "path", "method", "dependency", "classified_path"), CREATION_ROUTE_MATRIX)
def test_creation_route_authority_matrix(router, path, method, dependency, classified_path) -> None:
    route = next(route for route in router.routes if isinstance(route, APIRoute) and route.path == path and method in route.methods)

    assert any(item.call is dependency for item in route.dependant.dependencies)
    assert ROUTE_CLASSIFICATIONS[(method, classified_path)] == "config_mutation"
