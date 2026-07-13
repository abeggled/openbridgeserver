from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from obs.api.v1 import camera as camera_api
from obs.db.database import Database


NOW = "2026-06-10T00:00:00+00:00"
CAMERA_URL = "http://camera.local/stream"


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


async def _insert_user(db: Database, username: str, *, is_admin: bool = False) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO users (id, username, password_hash, created_at, is_admin)
        VALUES (?, ?, 'hash', ?, ?)
        """,
        (f"user-{username}", username, NOW, int(is_admin)),
    )


async def _insert_camera_page(
    db: Database,
    *,
    access: str = "public",
    url: str = CAMERA_URL,
    config_extra: str = "",
) -> None:
    page_config = f"""
    {{
      "grid_cols": 12,
      "grid_row_height": 80,
      "grid_cell_width": 120,
      "background": null,
      "widgets": [
        {{
          "id": "camera-widget",
          "name": "Front Door",
          "type": "Kamera",
          "datapoint_id": null,
          "status_datapoint_id": null,
          "x": 0,
          "y": 0,
          "w": 4,
          "h": 3,
          "config": {{"url": "{url}", "useProxy": true{config_extra}}}
        }}
      ]
    }}
    """
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES ('page-camera', NULL, 'Camera Page', 'PAGE', 0, NULL, ?, NULL, ?, ?, ?)
        """,
        (access, page_config, NOW, NOW),
    )
    await db.execute_and_commit(
        "INSERT INTO authz_visu_page_policies (node_id, access_mode) VALUES ('page-camera', ?)",
        (access,),
    )


async def _insert_inherited_protected_camera_page(db: Database, *, url: str = CAMERA_URL) -> None:
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES ('protected-root', NULL, 'Protected Root', 'LOCATION', 0, NULL, 'protected', 'hash', '{}', ?, ?)
        """,
        (NOW, NOW),
    )
    await db.execute_and_commit(
        "INSERT INTO authz_visu_page_policies (node_id, access_mode) VALUES ('protected-root', 'protected')",
    )
    page_config = f"""
    {{
      "grid_cols": 12,
      "grid_row_height": 80,
      "grid_cell_width": 120,
      "background": null,
      "widgets": [
        {{
          "id": "camera-widget",
          "name": "Front Door",
          "type": "Kamera",
          "datapoint_id": null,
          "status_datapoint_id": null,
          "x": 0,
          "y": 0,
          "w": 4,
          "h": 3,
          "config": {{"url": "{url}", "useProxy": true}}
        }}
      ]
    }}
    """
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES ('page-camera', 'protected-root', 'Camera Page', 'PAGE', 0, NULL, NULL, NULL, ?, ?, ?)
        """,
        (page_config, NOW, NOW),
    )


async def _insert_grundriss_camera_page(db: Database, *, url: str = CAMERA_URL) -> None:
    page_config = f"""
    {{
      "grid_cols": 12,
      "grid_row_height": 80,
      "grid_cell_width": 120,
      "background": null,
      "widgets": [
        {{
          "id": "grundriss-widget",
          "name": "Floorplan",
          "type": "Grundriss",
          "datapoint_id": null,
          "status_datapoint_id": null,
          "x": 0,
          "y": 0,
          "w": 8,
          "h": 6,
          "config": {{
            "miniWidgets": [
              {{
                "id": "mini-camera",
                "label": "Door",
                "widgetType": "Kamera",
                "datapointId": null,
                "statusDatapointId": null,
                "config": {{"url": "{url}", "useProxy": true}},
                "x": 100,
                "y": 100,
                "wPx": 320,
                "hPx": 180,
                "visible": true
              }}
            ]
          }}
        }}
      ]
    }}
    """
    await db.execute_and_commit(
        """
        INSERT INTO visu_nodes
            (id, parent_id, name, type, node_order, icon, access, access_pin, page_config, created_at, updated_at)
        VALUES ('page-camera', NULL, 'Camera Page', 'PAGE', 0, NULL, 'public', NULL, ?, ?, ?)
        """,
        (page_config, NOW, NOW),
    )


def _mock_camera_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        camera_api,
        "_build_fetch_targets",
        AsyncMock(return_value=([CAMERA_URL], {}, {})),
    )
    mock_head = MagicMock(status_code=200, headers={"content-type": "video/mjpeg"})
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.head = AsyncMock(return_value=mock_head)
    monkeypatch.setattr(camera_api.httpx, "AsyncClient", lambda **kw: mock_client)


@pytest.mark.asyncio
async def test_camera_auth_treats_stale_query_token_as_optional_with_page_scope(
    monkeypatch: pytest.MonkeyPatch,
):
    def _decode_token(_token: str) -> str:
        raise HTTPException(401, "Token invalid")

    monkeypatch.setattr(camera_api, "decode_token", _decode_token)
    request = SimpleNamespace(
        headers={},
        query_params={"page_id": "page-camera"},
    )

    assert await camera_api._camera_auth(request, _token="stale.jwt") is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_camera_auth_rejects_stale_query_token_without_page_scope(
    monkeypatch: pytest.MonkeyPatch,
):
    def _decode_token(_token: str) -> str:
        raise HTTPException(401, "Token invalid")

    monkeypatch.setattr(camera_api, "decode_token", _decode_token)
    request = SimpleNamespace(headers={}, query_params={})

    with pytest.raises(HTTPException):
        await camera_api._camera_auth(request, _token="stale.jwt")  # noqa: SLF001


@pytest.mark.asyncio
async def test_camera_auth_rejects_deleted_user_bearer_token(monkeypatch: pytest.MonkeyPatch, db: Database):
    monkeypatch.setattr(camera_api, "decode_token", lambda _token: "deleted")
    request = SimpleNamespace(headers={"Authorization": "Bearer valid.jwt"}, query_params={})

    with pytest.raises(HTTPException) as exc:
        await camera_api._camera_auth(request, _token="", db=db)  # noqa: SLF001

    assert exc.value.status_code == 401
    assert exc.value.detail == "User not found"


@pytest.mark.asyncio
async def test_proxy_camera_allows_assigned_user_page_scope(monkeypatch: pytest.MonkeyPatch, db: Database):
    await _insert_user(db, "alice")
    await _insert_camera_page(db, access="user")
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES ('user', 'alice', 'visu_page', 'page-camera', 'guest', 'allow')""",
    )
    _mock_camera_fetch(monkeypatch)

    result = await camera_api.proxy_camera(
        url=CAMERA_URL,
        username="",
        password="",
        apikey_param="",
        apikey_value="",
        page_id="page-camera",
        _user="alice",
        db=db,
    )

    assert isinstance(result, StreamingResponse)


@pytest.mark.asyncio
async def test_proxy_camera_allows_grundriss_mini_widget_page_scope(monkeypatch: pytest.MonkeyPatch, db: Database):
    await _insert_grundriss_camera_page(db)
    _mock_camera_fetch(monkeypatch)

    result = await camera_api.proxy_camera(
        url=CAMERA_URL,
        username="",
        password="",
        apikey_param="",
        apikey_value="",
        page_id="page-camera",
        _user="alice",
        db=db,
    )

    assert isinstance(result, StreamingResponse)


@pytest.mark.asyncio
async def test_proxy_camera_allows_public_page_scope_without_jwt(monkeypatch: pytest.MonkeyPatch, db: Database):
    await _insert_camera_page(db, access="public")
    _mock_camera_fetch(monkeypatch)

    result = await camera_api.proxy_camera(
        url=CAMERA_URL,
        username="",
        password="",
        apikey_param="",
        apikey_value="",
        page_id="page-camera",
        _user=None,
        db=db,
    )

    assert isinstance(result, StreamingResponse)


@pytest.mark.asyncio
async def test_proxy_camera_validates_api_key_target_against_page_scope(monkeypatch: pytest.MonkeyPatch, db: Database):
    await _insert_camera_page(
        db,
        access="public",
        config_extra=', "authType": "apikey", "apiKeyParam": "token", "apiKeyValue": "secret"',
    )
    scoped_url = f"{CAMERA_URL}?token=secret"
    build_targets = AsyncMock(return_value=([scoped_url], {}, {}))
    monkeypatch.setattr(camera_api, "_build_fetch_targets", build_targets)
    mock_head = MagicMock(status_code=200, headers={"content-type": "video/mjpeg"})
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.head = AsyncMock(return_value=mock_head)
    monkeypatch.setattr(camera_api.httpx, "AsyncClient", lambda **kw: mock_client)

    result = await camera_api.proxy_camera(
        url=CAMERA_URL,
        username="",
        password="",
        apikey_param="token",
        apikey_value="secret",
        page_id="page-camera",
        _user=None,
        db=db,
    )

    assert isinstance(result, StreamingResponse)
    build_targets.assert_awaited_once_with(scoped_url)


@pytest.mark.asyncio
async def test_proxy_camera_encodes_api_key_query_target_against_page_scope(monkeypatch: pytest.MonkeyPatch, db: Database):
    await _insert_camera_page(
        db,
        access="public",
        config_extra=', "authType": "apikey", "apiKeyParam": "tok&en", "apiKeyValue": "abc&debug=1 +"',
    )
    scoped_url = f"{CAMERA_URL}?tok%26en=abc%26debug%3D1%20%2B"
    build_targets = AsyncMock(return_value=([scoped_url], {}, {}))
    monkeypatch.setattr(camera_api, "_build_fetch_targets", build_targets)
    mock_head = MagicMock(status_code=200, headers={"content-type": "video/mjpeg"})
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.head = AsyncMock(return_value=mock_head)
    monkeypatch.setattr(camera_api.httpx, "AsyncClient", lambda **kw: mock_client)

    result = await camera_api.proxy_camera(
        url=CAMERA_URL,
        username="",
        password="",
        apikey_param="tok&en",
        apikey_value="abc&debug=1 +",
        page_id="page-camera",
        _user=None,
        db=db,
    )

    assert isinstance(result, StreamingResponse)
    build_targets.assert_awaited_once_with(scoped_url)


@pytest.mark.asyncio
async def test_proxy_camera_normalizes_legacy_api_key_auth_for_page_scope(monkeypatch: pytest.MonkeyPatch, db: Database):
    await _insert_camera_page(
        db,
        access="public",
        config_extra=', "authType": "API-Key (Query-Parameter)", "apiKeyParam": "token", "apiKeyValue": "secret"',
    )
    scoped_url = f"{CAMERA_URL}?token=secret"
    build_targets = AsyncMock(return_value=([scoped_url], {}, {}))
    monkeypatch.setattr(camera_api, "_build_fetch_targets", build_targets)
    mock_head = MagicMock(status_code=200, headers={"content-type": "video/mjpeg"})
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.head = AsyncMock(return_value=mock_head)
    monkeypatch.setattr(camera_api.httpx, "AsyncClient", lambda **kw: mock_client)

    result = await camera_api.proxy_camera(
        url=CAMERA_URL,
        username="",
        password="",
        apikey_param="token",
        apikey_value="secret",
        page_id="page-camera",
        _user=None,
        db=db,
    )

    assert isinstance(result, StreamingResponse)
    build_targets.assert_awaited_once_with(scoped_url)


@pytest.mark.asyncio
async def test_proxy_camera_normalizes_legacy_basic_auth_for_page_scope(monkeypatch: pytest.MonkeyPatch, db: Database):
    await _insert_camera_page(
        db,
        access="public",
        config_extra=', "authType": "Basic Auth (Benutzername / Passwort)", "username": "cam-user", "password": "secret"',
    )
    _mock_camera_fetch(monkeypatch)

    result = await camera_api.proxy_camera(
        url=CAMERA_URL,
        username="cam-user",
        password="secret",
        apikey_param="",
        apikey_value="",
        page_id="page-camera",
        _user=None,
        db=db,
    )

    assert isinstance(result, StreamingResponse)


@pytest.mark.asyncio
async def test_proxy_camera_rejects_unconfigured_api_key_target(monkeypatch: pytest.MonkeyPatch, db: Database):
    await _insert_camera_page(
        db,
        access="public",
        config_extra=', "authType": "apikey", "apiKeyParam": "token", "apiKeyValue": "secret"',
    )
    build_targets = AsyncMock()
    monkeypatch.setattr(camera_api, "_build_fetch_targets", build_targets)

    with pytest.raises(HTTPException) as exc_info:
        await camera_api.proxy_camera(
            url=CAMERA_URL,
            username="",
            password="",
            apikey_param="token",
            apikey_value="other",
            page_id="page-camera",
            _user=None,
            db=db,
        )

    assert exc_info.value.status_code == 404
    build_targets.assert_not_called()


@pytest.mark.asyncio
async def test_proxy_camera_rejects_basic_credentials_that_do_not_match_page_scope(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
):
    await _insert_camera_page(
        db,
        access="public",
        config_extra=', "authType": "basic", "username": "cam-user", "password": "secret"',
    )
    build_targets = AsyncMock()
    monkeypatch.setattr(camera_api, "_build_fetch_targets", build_targets)

    with pytest.raises(HTTPException) as exc_info:
        await camera_api.proxy_camera(
            url=CAMERA_URL,
            username="cam-user",
            password="other",
            apikey_param="",
            apikey_value="",
            page_id="page-camera",
            _user=None,
            db=db,
        )

    assert exc_info.value.status_code == 404
    build_targets.assert_not_called()


@pytest.mark.asyncio
async def test_proxy_camera_allows_admin_editor_preview_for_unsaved_url(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
):
    await _insert_user(db, "alice", is_admin=True)
    await _insert_camera_page(db, access="protected", url="http://camera.local/persisted")
    draft_url = "http://camera.local/draft"
    build_targets = AsyncMock(return_value=([draft_url], {}, {}))
    monkeypatch.setattr(camera_api, "_build_fetch_targets", build_targets)
    mock_head = MagicMock(status_code=200, headers={"content-type": "video/mjpeg"})
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.head = AsyncMock(return_value=mock_head)
    monkeypatch.setattr(camera_api.httpx, "AsyncClient", lambda **kw: mock_client)

    result = await camera_api.proxy_camera(
        url=draft_url,
        username="",
        password="",
        apikey_param="",
        apikey_value="",
        page_id="page-camera",
        editor_preview=True,
        _user="alice",
        db=db,
    )

    assert isinstance(result, StreamingResponse)
    build_targets.assert_awaited_once_with(draft_url)


@pytest.mark.asyncio
async def test_proxy_camera_rejects_non_admin_editor_preview(monkeypatch: pytest.MonkeyPatch, db: Database):
    await _insert_user(db, "alice", is_admin=False)
    await _insert_camera_page(db, access="public", url="http://camera.local/persisted")
    build_targets = AsyncMock()
    monkeypatch.setattr(camera_api, "_build_fetch_targets", build_targets)

    with pytest.raises(HTTPException) as exc_info:
        await camera_api.proxy_camera(
            url="http://camera.local/draft",
            username="",
            password="",
            apikey_param="",
            apikey_value="",
            page_id="page-camera",
            editor_preview=True,
            _user="alice",
            db=db,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Admin access required"
    build_targets.assert_not_called()


@pytest.mark.asyncio
async def test_proxy_camera_rejects_anonymous_editor_preview(monkeypatch: pytest.MonkeyPatch, db: Database):
    await _insert_camera_page(db, access="public", url="http://camera.local/persisted")
    build_targets = AsyncMock()
    monkeypatch.setattr(camera_api, "_build_fetch_targets", build_targets)

    with pytest.raises(HTTPException) as exc_info:
        await camera_api.proxy_camera(
            url="http://camera.local/draft",
            username="",
            password="",
            apikey_param="",
            apikey_value="",
            page_id="page-camera",
            editor_preview=True,
            _user=None,
            db=db,
        )

    assert exc_info.value.status_code == 401
    build_targets.assert_not_called()


@pytest.mark.asyncio
async def test_proxy_camera_blocks_unassigned_user_page_scope(monkeypatch: pytest.MonkeyPatch, db: Database):
    await _insert_user(db, "alice")
    await _insert_camera_page(db, access="user")
    build_targets = AsyncMock()
    monkeypatch.setattr(camera_api, "_build_fetch_targets", build_targets)

    with pytest.raises(HTTPException) as exc_info:
        await camera_api.proxy_camera(
            url=CAMERA_URL,
            username="",
            password="",
            apikey_param="",
            apikey_value="",
            page_id="page-camera",
            _user="alice",
            db=db,
        )

    assert exc_info.value.status_code == 403
    build_targets.assert_not_called()


@pytest.mark.asyncio
async def test_proxy_camera_requires_jwt_for_user_page_scope(monkeypatch: pytest.MonkeyPatch, db: Database):
    await _insert_user(db, "alice")
    await _insert_camera_page(db, access="user")
    await db.execute_and_commit(
        """INSERT INTO authz_node_roles
               (principal_type, principal_id, node_type, node_id, role, effect)
           VALUES ('user', 'alice', 'visu_page', 'page-camera', 'guest', 'allow')""",
    )
    build_targets = AsyncMock()
    monkeypatch.setattr(camera_api, "_build_fetch_targets", build_targets)

    with pytest.raises(HTTPException) as exc_info:
        await camera_api.proxy_camera(
            url=CAMERA_URL,
            username="",
            password="",
            apikey_param="",
            apikey_value="",
            page_id="page-camera",
            _user=None,
            db=db,
        )

    assert exc_info.value.status_code == 401
    build_targets.assert_not_called()


@pytest.mark.asyncio
async def test_proxy_camera_requires_session_for_inherited_protected_page_scope(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
):
    await _insert_inherited_protected_camera_page(db)
    build_targets = AsyncMock()
    monkeypatch.setattr(camera_api, "_build_fetch_targets", build_targets)

    with pytest.raises(HTTPException) as exc_info:
        await camera_api.proxy_camera(
            url=CAMERA_URL,
            username="",
            password="",
            apikey_param="",
            apikey_value="",
            page_id="page-camera",
            _user=None,
            db=db,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Valid session token required"
    build_targets.assert_not_called()


@pytest.mark.asyncio
async def test_proxy_camera_allows_authenticated_inherited_protected_page_scope(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
):
    await _insert_inherited_protected_camera_page(db)
    _mock_camera_fetch(monkeypatch)
    validate_session = MagicMock(return_value=False)
    monkeypatch.setattr(camera_api, "validate_session", validate_session)

    result = await camera_api.proxy_camera(
        url=CAMERA_URL,
        username="",
        password="",
        apikey_param="",
        apikey_value="",
        page_id="page-camera",
        _user="alice",
        db=db,
    )

    assert isinstance(result, StreamingResponse)
    validate_session.assert_not_called()


@pytest.mark.asyncio
async def test_proxy_camera_checks_protected_access_before_url_membership(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
):
    await _insert_inherited_protected_camera_page(db, url="http://camera.local/other")
    build_targets = AsyncMock()
    monkeypatch.setattr(camera_api, "_build_fetch_targets", build_targets)

    with pytest.raises(HTTPException) as exc_info:
        await camera_api.proxy_camera(
            url=CAMERA_URL,
            username="",
            password="",
            apikey_param="",
            apikey_value="",
            page_id="page-camera",
            _user=None,
            db=db,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Valid session token required"
    build_targets.assert_not_called()


@pytest.mark.asyncio
async def test_proxy_camera_accepts_session_for_inherited_protected_page_scope(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
):
    await _insert_inherited_protected_camera_page(db)
    _mock_camera_fetch(monkeypatch)
    validate_session = MagicMock(return_value=True)
    monkeypatch.setattr(camera_api, "validate_session", validate_session)

    result = await camera_api.proxy_camera(
        url=CAMERA_URL,
        username="",
        password="",
        apikey_param="",
        apikey_value="",
        page_id="page-camera",
        session_token="session-1",
        _user=None,
        db=db,
    )

    assert isinstance(result, StreamingResponse)
    validate_session.assert_called_once_with("session-1", "protected-root")


@pytest.mark.asyncio
async def test_proxy_camera_requires_page_scope(monkeypatch: pytest.MonkeyPatch, db: Database):
    build_targets = AsyncMock()
    monkeypatch.setattr(camera_api, "_build_fetch_targets", build_targets)

    with pytest.raises(HTTPException) as exc_info:
        await camera_api.proxy_camera(
            url=CAMERA_URL,
            username="",
            password="",
            apikey_param="",
            apikey_value="",
            page_id="",
            _user="alice",
            db=db,
        )

    assert exc_info.value.status_code == 400
    assert "Page-Scope" in exc_info.value.detail
    build_targets.assert_not_called()


@pytest.mark.asyncio
async def test_proxy_camera_requires_matching_camera_url_for_page_scope(monkeypatch: pytest.MonkeyPatch, db: Database):
    await _insert_user(db, "alice")
    await _insert_camera_page(db, access="public", url="http://camera.local/other")
    build_targets = AsyncMock()
    monkeypatch.setattr(camera_api, "_build_fetch_targets", build_targets)

    with pytest.raises(HTTPException) as exc_info:
        await camera_api.proxy_camera(
            url=CAMERA_URL,
            username="",
            password="",
            apikey_param="",
            apikey_value="",
            page_id="page-camera",
            _user="alice",
            db=db,
        )

    assert exc_info.value.status_code == 404
    build_targets.assert_not_called()
