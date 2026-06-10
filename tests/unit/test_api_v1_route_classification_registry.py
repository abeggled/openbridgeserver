from __future__ import annotations

from fastapi.routing import APIRoute, APIWebSocketRoute

from obs.api.router import router as api_v1_router
from obs.api.v1.route_classification_registry import (
    PUBLIC_ROUTE_ALLOWLIST,
    ROUTE_CLASSIFICATIONS,
)


def _collect_v1_route_signatures() -> set[tuple[str, str]]:
    signatures: set[tuple[str, str]] = set()
    for route in api_v1_router.routes:
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


def test_public_allowlist_is_explicit_and_covers_weather() -> None:
    weather_route = ("GET", "/api/v1/weather/fetch")
    assert weather_route in PUBLIC_ROUTE_ALLOWLIST
    assert ROUTE_CLASSIFICATIONS[weather_route] == "public"

    public_classified = {route for route, category in ROUTE_CLASSIFICATIONS.items() if category == "public"}
    assert public_classified == set(PUBLIC_ROUTE_ALLOWLIST)
