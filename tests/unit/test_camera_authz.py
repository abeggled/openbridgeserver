from __future__ import annotations

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


async def _insert_camera_page(db: Database, *, access: str = "public", url: str = CAMERA_URL) -> None:
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
        VALUES ('page-camera', NULL, 'Camera Page', 'PAGE', 0, NULL, ?, NULL, ?, ?, ?)
        """,
        (access, page_config, NOW, NOW),
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
async def test_proxy_camera_allows_assigned_user_page_scope(monkeypatch: pytest.MonkeyPatch, db: Database):
    await _insert_user(db, "alice")
    await _insert_camera_page(db, access="user")
    await db.execute_and_commit("INSERT INTO visu_node_users (node_id, username) VALUES ('page-camera', 'alice')")
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
