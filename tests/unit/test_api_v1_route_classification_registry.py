from __future__ import annotations

from obs.api.v1.route_classification_registry import (
    PUBLIC_ROUTE_ALLOWLIST,
    ROUTE_CLASSIFICATIONS,
)


def _collect_v1_route_signatures() -> set[tuple[str, str]]:
    # Import fresh to avoid isinstance() mismatches when FastAPI is reloaded.
    import importlib

    fastapi_routing = importlib.import_module("fastapi.routing")
    router_mod = importlib.import_module("obs.api.router")
    APIRoute = fastapi_routing.APIRoute
    APIWebSocketRoute = fastapi_routing.APIWebSocketRoute

    signatures: set[tuple[str, str]] = set()

    def _walk(routes: list, prefix: str) -> None:
        for route in routes:
            if isinstance(route, APIRoute):
                for method in route.methods or set():
                    if method not in {"HEAD", "OPTIONS"}:
                        signatures.add((method, f"{prefix}{route.path}"))
            elif isinstance(route, APIWebSocketRoute):
                signatures.add(("WEBSOCKET", f"{prefix}{route.path}"))
            elif hasattr(route, "original_router") and hasattr(route, "include_context"):
                # FastAPI 0.137+ / Starlette 1.x: include_router() no longer flattens
                # sub-routers into APIRoute copies — it stores them as lazy
                # _IncludedRouter wrappers.  Recurse with the accumulated prefix.
                sub_prefix = getattr(route.include_context, "prefix", "") or ""
                _walk(route.original_router.routes, prefix + sub_prefix)

    _walk(router_mod.router.routes, "/api/v1")
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
