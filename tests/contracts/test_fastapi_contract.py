"""Contract tests for FastAPI — verifies routing, error response format, and middleware
behavior that OBS depends on.

A FastAPI version upgrade that changes the 422 validation error structure, status code
behavior, or dependency injection would break the OBS frontend and tests that parse
those responses.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Minimal test app
# ---------------------------------------------------------------------------


class _ItemIn(BaseModel):
    name: str
    value: float


class _ItemOut(BaseModel):
    name: str
    value: float
    processed: bool = True


_app = FastAPI()


@_app.get("/ping")
async def _ping():
    return {"pong": True}


@_app.post("/items/", response_model=_ItemOut, status_code=status.HTTP_201_CREATED)
async def _create_item(item: _ItemIn) -> _ItemOut:
    return _ItemOut(name=item.name, value=item.value)


@_app.get("/protected/")
async def _protected(token: str = Depends(lambda: "test-token")):
    return {"token": token}


@_app.get("/error/")
async def _raise_error():
    raise HTTPException(status_code=404, detail="Not found")


_client = TestClient(_app)


# ---------------------------------------------------------------------------
# Basic routing
# ---------------------------------------------------------------------------


class TestRouting:
    def test_get_returns_200(self):
        resp = _client.get("/ping")
        assert resp.status_code == 200

    def test_response_body_parsed(self):
        resp = _client.get("/ping")
        assert resp.json() == {"pong": True}

    def test_post_with_valid_body_returns_201(self):
        resp = _client.post("/items/", json={"name": "sensor", "value": 21.5})
        assert resp.status_code == 201

    def test_post_response_matches_response_model(self):
        resp = _client.post("/items/", json={"name": "sensor", "value": 21.5})
        body = resp.json()
        assert body["name"] == "sensor"
        assert body["value"] == pytest.approx(21.5)
        assert body["processed"] is True


# ---------------------------------------------------------------------------
# Validation errors (422)
# ---------------------------------------------------------------------------


class TestValidationErrors:
    def test_missing_required_field_returns_422(self):
        resp = _client.post("/items/", json={"name": "sensor"})  # missing 'value'
        assert resp.status_code == 422, (
            "FastAPI must return 422 for missing required fields. "
            "OBS integration tests and the GUI rely on this status code."
        )

    def test_422_response_has_detail_key(self):
        resp = _client.post("/items/", json={})
        assert "detail" in resp.json(), (
            "FastAPI 422 response must contain a 'detail' key. "
            "Format may have changed in this FastAPI version."
        )

    def test_422_detail_is_list(self):
        resp = _client.post("/items/", json={})
        detail = resp.json()["detail"]
        assert isinstance(detail, list), (
            "FastAPI 422 detail must be a list of error objects. "
            "OBS integration tests assert isinstance(body['detail'], list)."
        )

    def test_422_detail_items_have_loc_and_msg(self):
        resp = _client.post("/items/", json={})
        errors = resp.json()["detail"]
        assert len(errors) > 0
        first = errors[0]
        assert "loc" in first, "each 422 error item must have 'loc'"
        assert "msg" in first, "each 422 error item must have 'msg'"

    def test_wrong_type_returns_422(self):
        resp = _client.post("/items/", json={"name": "test", "value": "not_a_number"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# HTTPException
# ---------------------------------------------------------------------------


class TestHttpException:
    def test_404_status_code(self):
        resp = _client.get("/error/")
        assert resp.status_code == 404

    def test_404_detail_in_body(self):
        resp = _client.get("/error/")
        assert resp.json()["detail"] == "Not found"

    def test_http_exception_is_exception(self):
        assert issubclass(HTTPException, Exception)


# ---------------------------------------------------------------------------
# Unknown route
# ---------------------------------------------------------------------------


class TestUnknownRoute:
    def test_missing_route_returns_404(self):
        resp = _client.get("/this/does/not/exist")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# APIRouter integration
# ---------------------------------------------------------------------------


class TestApiRouter:
    def test_include_router_works(self):
        from fastapi import APIRouter

        sub_app = FastAPI()
        router = APIRouter(prefix="/sub")

        @router.get("/hello")
        async def _hello():
            return {"hello": "world"}

        sub_app.include_router(router)
        test_client = TestClient(sub_app)
        resp = test_client.get("/sub/hello")
        assert resp.status_code == 200
        assert resp.json() == {"hello": "world"}
