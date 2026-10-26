"""Unit tests — which custom icons a Visu visitor without login may load.

Icons used on public/read-only pages are visible, icons on PIN pages only with a
valid PIN session token for that page, icons only used on login-only pages (or
not used at all) stay hidden. Signed-in callers keep seeing every icon.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

import obs.api.v1.icons as icons_api
from obs.api.auth import Principal, create_access_token
from obs.api.v1.sessions import create_session
from obs.api.v1.visu import anonymous_visible_icon_names, session_tokens_from_request
from obs.db.database import Database

SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>'
ADMIN = Principal(subject="admin", type="user", is_admin=True)


def _request(tokens: str | None = None) -> Request:
    headers = [(b"x-session-tokens", tokens.encode())] if tokens is not None else []
    return Request({"type": "http", "method": "GET", "path": "/api/v1/icons/", "headers": headers, "query_string": b""})


def _page(*icons: str) -> str:
    return json.dumps({"widgets": [{"id": f"w{i}", "config": {"icon": icon}} for i, icon in enumerate(icons)]})


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    now = datetime.now(UTC).isoformat()

    async def node(node_id, parent, access, icon, config):
        await database.execute_and_commit(
            "INSERT INTO visu_nodes (id, parent_id, name, type, node_order, icon, page_config, created_at, updated_at)"
            " VALUES (?, ?, ?, 'PAGE', 0, ?, ?, ?, ?)",
            (node_id, parent, node_id, icon, config, now, now),
        )
        if access:
            await database.execute_and_commit("INSERT INTO authz_visu_page_policies (node_id, access_mode) VALUES (?, ?)", (node_id, access))

    await node("home", None, "public", "svg:house", json.dumps({"widgets": []}))
    # Inherits "public" from home; icons nested in a list and a dict, plus values that are no icon reference.
    await node(
        "living",
        "home",
        None,
        "🛋",
        json.dumps({"widgets": [{"config": {"icon": "svg:lamp", "states": [{"icon": "svg:lamp-on"}], "label": "svg:not an icon"}}]}),
    )
    await node("readonly", "home", "readonly", None, _page("svg:eye"))
    await node("pin", "home", "protected", "svg:pin-tile", _page("svg:pin-only"))
    await node("pin-child", "pin", None, "svg:pin-child-tile", _page("svg:pin-child-only"))  # inherits the PIN of "pin"
    await node("private", "home", "user", "svg:private-tile", _page("svg:private-only"))
    await node("broken", "home", None, "svg:broken-tile", "not json")
    try:
        yield database
    finally:
        await database.disconnect()


PUBLIC = {"house", "lamp", "lamp-on", "eye", "pin-tile", "pin-child-tile", "broken-tile"}


async def test_anonymous_sees_icons_of_public_and_readonly_pages_and_the_tree(db):
    assert await anonymous_visible_icon_names(db, []) == PUBLIC


async def test_a_pin_token_unlocks_the_icons_of_that_pin_area(db):
    token = create_session("pin")

    names = await anonymous_visible_icon_names(db, [token])

    assert names == PUBLIC | {"pin-only", "pin-child-only"}
    assert "private-only" not in names


async def test_tokens_for_other_pages_or_unknown_tokens_unlock_nothing(db):
    assert await anonymous_visible_icon_names(db, [create_session("readonly"), "made-up"]) == PUBLIC


def test_session_tokens_are_read_from_the_comma_separated_header():
    assert session_tokens_from_request(_request(" a , b,,c ")) == ["a", "b", "c"]
    assert session_tokens_from_request(_request("")) == []
    assert session_tokens_from_request(_request()) == []


@pytest.fixture
def icons_dir(tmp_path, monkeypatch):
    for name in ("house", "lamp", "pin-only", "private-only", "unused"):
        (tmp_path / f"{name}.svg").write_bytes(SVG)
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    return tmp_path


async def test_list_icons_filters_for_anonymous_visitors(db, icons_dir):
    result = await icons_api.list_icons(request=_request(), principal=None, db=db)

    assert [icon.name for icon in result.icons] == ["house", "lamp"]
    assert result.total == 2


async def test_list_icons_includes_unlocked_pin_pages(db, icons_dir):
    result = await icons_api.list_icons(request=_request(create_session("pin")), principal=None, db=db)

    assert [icon.name for icon in result.icons] == ["house", "lamp", "pin-only"]


async def test_list_icons_returns_everything_when_signed_in(db, icons_dir):
    result = await icons_api.list_icons(request=_request(), principal=ADMIN, db=db)

    assert [icon.name for icon in result.icons] == ["house", "lamp", "pin-only", "private-only", "unused"]


async def test_get_icon_follows_the_same_visibility(db, icons_dir):
    response = await icons_api.get_icon(name="lamp", request=_request(), principal=None, db=db)
    assert response.media_type == "image/svg+xml"

    for hidden in ("private-only", "unused", "pin-only"):
        with pytest.raises(HTTPException) as exc:
            await icons_api.get_icon(name=hidden, request=_request(), principal=None, db=db)
        assert exc.value.status_code == 401

    unlocked = await icons_api.get_icon(name="pin-only", request=_request(create_session("pin")), principal=None, db=db)
    assert unlocked.media_type == "image/svg+xml"
    signed_in = await icons_api.get_icon(name="private-only", request=_request(), principal=ADMIN, db=db)
    assert signed_in.media_type == "image/svg+xml"


# ── one query for the whole tree ─────────────────────────────────────────────


class _CountingDb:
    """Counts the queries a call issues; everything else goes to the real database."""

    def __init__(self, inner: Database) -> None:
        self.inner = inner
        self.queries = 0

    async def fetchall(self, *args, **kwargs):
        self.queries += 1
        return await self.inner.fetchall(*args, **kwargs)

    async def fetchone(self, *args, **kwargs):
        self.queries += 1
        return await self.inner.fetchone(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.inner, name)


async def test_visibility_is_computed_with_a_single_query(db):
    counting = _CountingDb(db)

    names = await anonymous_visible_icon_names(counting, [create_session("pin")])

    assert counting.queries == 1
    assert names == PUBLIC | {"pin-only", "pin-child-only"}


async def test_inherited_access_is_resolved_in_memory_for_deep_trees(db):
    # A chain of 30 levels below the PIN area: every level inherits "protected" from "pin".
    now = datetime.now(UTC).isoformat()
    parent = "pin-child"
    for depth in range(30):
        node_id = f"deep-{depth}"
        await db.execute_and_commit(
            "INSERT INTO visu_nodes (id, parent_id, name, type, node_order, icon, page_config, created_at, updated_at)"
            " VALUES (?, ?, ?, 'PAGE', 0, ?, ?, ?, ?)",
            (node_id, parent, node_id, f"svg:deep-tile-{depth}", _page(f"svg:deep-{depth}"), now, now),
        )
        parent = node_id
    counting = _CountingDb(db)

    locked = await anonymous_visible_icon_names(counting, [])
    unlocked = await anonymous_visible_icon_names(counting, [create_session("pin")])

    assert counting.queries == 2
    assert {f"deep-tile-{d}" for d in range(30)} <= locked
    assert "deep-29" not in locked
    assert {f"deep-{d}" for d in range(30)} <= unlocked


async def test_get_icon_rejects_an_invalid_name_before_reading_the_tree(db, icons_dir):
    counting = _CountingDb(db)

    with pytest.raises(HTTPException) as exc:
        await icons_api.get_icon(name="../etc/passwd", request=_request(), principal=None, db=counting)

    assert exc.value.status_code == 400
    assert counting.queries == 0


# ── rejected credentials stay a 401 ──────────────────────────────────────────


async def test_no_credentials_means_anonymous(db):
    assert await icons_api._icon_principal(request=_request(), credentials=None, api_key=None, db=db) is None


@pytest.mark.parametrize(
    ("credentials", "api_key"),
    [
        (HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-jwt"), None),
        (None, "unknown-api-key"),
    ],
)
async def test_rejected_credentials_raise_instead_of_falling_back_to_anonymous(db, credentials, api_key):
    with pytest.raises(HTTPException) as exc:
        await icons_api._icon_principal(request=_request(), credentials=credentials, api_key=api_key, db=db)

    assert exc.value.status_code == 401


async def test_valid_credentials_yield_the_principal(db):
    await db.execute_and_commit(
        "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES ('alice', 'x', 0, ?)",
        (datetime.now(UTC).isoformat(),),
    )
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=create_access_token("alice"))

    principal = await icons_api._icon_principal(request=_request(), credentials=credentials, api_key=None, db=db)

    assert principal == Principal(subject="alice", type="user", is_admin=False)
