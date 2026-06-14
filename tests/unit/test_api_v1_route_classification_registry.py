from __future__ import annotations

from obs.api.v1.route_classification_registry import (
    PUBLIC_ROUTE_ALLOWLIST,
    ROUTE_CLASSIFICATIONS,
)


def _collect_v1_route_signatures() -> set[tuple[str, str]]:
    # Import fresh inside the function so the router and FastAPI route classes
    # come from the same module-cache state at call time.  Module-level imports
    # can diverge from what the router actually holds if FastAPI is reloaded or
    # installed in a newer minor version between CI runs.
    import importlib

    router_mod = importlib.import_module("obs.api.router")
    fastapi_routing = importlib.import_module("fastapi.routing")
    APIRoute = fastapi_routing.APIRoute
    APIWebSocketRoute = fastapi_routing.APIWebSocketRoute

    signatures: set[tuple[str, str]] = set()
    for route in router_mod.router.routes:
        if isinstance(route, APIRoute):
            for method in route.methods or set():
                if method in {"HEAD", "OPTIONS"}:
                    continue
                signatures.add((method, f"/api/v1{route.path}"))
            continue

        if isinstance(route, APIWebSocketRoute):
            signatures.add(("WEBSOCKET", f"/api/v1{route.path}"))
    return signatures


def test_all_v1_routes_are_classified_and_registry_has_no_stale_entries() -> None:
    discovered = _collect_v1_route_signatures()
    classified = set(ROUTE_CLASSIFICATIONS)

    assert discovered == classified


def test_public_allowlist_is_explicit() -> None:
    public_classified = {route for route, category in ROUTE_CLASSIFICATIONS.items() if category == "public"}
    assert public_classified == set(PUBLIC_ROUTE_ALLOWLIST)


def test_weather_fetch_requires_authenticated_read_classification() -> None:
    assert ROUTE_CLASSIFICATIONS[("GET", "/api/v1/weather/fetch")] == "read_live"
    assert ("GET", "/api/v1/weather/fetch") not in PUBLIC_ROUTE_ALLOWLIST
