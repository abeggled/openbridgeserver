"""Unit tests for the KNX Keyfile API endpoint.

Testet den Upload-/Parse-Endpunkt mit gemockter xknx-Keyring-Bibliothek —
kein Docker, keine echte .knxkeys Datei erforderlich.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from obs.api.v1.knxkeyfile import router

# ---------------------------------------------------------------------------
# App-Fixture mit überschriebenem Auth
# ---------------------------------------------------------------------------


def _create_test_app() -> FastAPI:
    from obs.api.auth import get_current_user

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: "testuser"
    app.include_router(router, prefix="/knx")
    return app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """TestClient mit temporärem Datenpfad und gemocktem get_settings."""
    settings_mock = MagicMock()
    settings_mock.database.path = str(tmp_path / "obs.db")

    with patch("obs.api.v1.knxkeyfile.get_settings", return_value=settings_mock):
        app = _create_test_app()
        yield TestClient(app)


# ---------------------------------------------------------------------------
# Hilfsfunktion: minimales Keyring-Mock bauen
# ---------------------------------------------------------------------------


def _make_keyring_mock(
    project_name: str = "Testprojekt",
    tunnels: list[dict] | None = None,
    has_backbone: bool = False,
) -> MagicMock:
    keyring = MagicMock()
    keyring.project_name = project_name

    if tunnels is None:
        tunnels = [
            {"individual_address": "1.1.100", "host": "1.1.50", "user_id": 2, "ga_count": 5},
            {"individual_address": "1.1.101", "host": "1.1.50", "user_id": 3, "ga_count": 3},
        ]

    from xknx.secure.keyring import InterfaceType

    iface_mocks = []
    for t in tunnels:
        iface = MagicMock()
        iface.type = InterfaceType.TUNNELING
        iface.individual_address = MagicMock()
        iface.individual_address.__str__ = lambda self, ia=t["individual_address"]: ia
        iface.host = MagicMock()
        iface.host.__str__ = lambda self, h=t["host"]: h
        iface.user_id = t["user_id"]
        iface.group_addresses = {f"1/0/{i}": [] for i in range(t["ga_count"])}
        iface_mocks.append(iface)

    keyring.interfaces = iface_mocks

    if has_backbone:
        keyring.backbone = MagicMock()
        keyring.backbone.multicast_address = "224.0.23.12"
        keyring.backbone.latency = 1000
    else:
        keyring.backbone = None

    return keyring


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("xknx"),
    reason="xknx nicht installiert",
)
class TestUploadKeyfile:
    def test_wrong_extension_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/knx/keyfile",
            files={"file": ("config.xml", b"<xml/>", "application/xml")},
            data={"password": "secret"},
        )
        assert resp.status_code == 400

    def test_empty_file_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/knx/keyfile",
            files={"file": ("keys.knxkeys", b"", "application/octet-stream")},
            data={"password": "secret"},
        )
        assert resp.status_code == 400

    def test_successful_parse_returns_tunnels(self, client: TestClient, tmp_path: Path) -> None:
        keyring = _make_keyring_mock()
        settings_mock = MagicMock()
        settings_mock.database.path = str(tmp_path / "obs.db")

        with (
            patch("obs.api.v1.knxkeyfile.get_settings", return_value=settings_mock),
            patch("obs.api.v1.knxkeyfile._parse_keyring", return_value=keyring),
        ):
            resp = client.post(
                "/knx/keyfile",
                files={"file": ("keys.knxkeys", b"dummy", "application/octet-stream")},
                data={"password": "geheim"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["project_name"] == "Testprojekt"
        assert len(data["tunnels"]) == 2
        assert data["tunnels"][0]["individual_address"] == "1.1.100"
        assert data["tunnels"][0]["user_id"] == 2
        assert data["tunnels"][0]["secure_ga_count"] == 5
        assert data["tunnels"][1]["individual_address"] == "1.1.101"
        assert data["backbone"] is None
        assert "file_id" in data
        assert "file_path" in data

    def test_routing_secure_returns_backbone(self, client: TestClient, tmp_path: Path) -> None:
        keyring = _make_keyring_mock(tunnels=[], has_backbone=True)
        settings_mock = MagicMock()
        settings_mock.database.path = str(tmp_path / "obs.db")

        with (
            patch("obs.api.v1.knxkeyfile.get_settings", return_value=settings_mock),
            patch("obs.api.v1.knxkeyfile._parse_keyring", return_value=keyring),
        ):
            resp = client.post(
                "/knx/keyfile",
                files={"file": ("keys.knxkeys", b"dummy", "application/octet-stream")},
                data={"password": "geheim"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["backbone"]["multicast_address"] == "224.0.23.12"
        assert data["backbone"]["latency_ms"] == 1000
        assert data["tunnels"] == []

    def test_invalid_password_returns_400(self, client: TestClient, tmp_path: Path) -> None:
        from fastapi import HTTPException

        settings_mock = MagicMock()
        settings_mock.database.path = str(tmp_path / "obs.db")

        with (
            patch("obs.api.v1.knxkeyfile.get_settings", return_value=settings_mock),
            patch(
                "obs.api.v1.knxkeyfile._parse_keyring",
                side_effect=HTTPException(status_code=400, detail="Keyfile-Fehler: Falsches Passwort"),
            ),
        ):
            resp = client.post(
                "/knx/keyfile",
                files={"file": ("keys.knxkeys", b"dummy", "application/octet-stream")},
                data={"password": "falsch"},
            )

        assert resp.status_code == 400

    def test_delete_valid_file_id(self, client: TestClient, tmp_path: Path) -> None:
        file_id = str(uuid.uuid4())
        keyfile_path = tmp_path / "knxkeys" / f"{file_id}.knxkeys"
        keyfile_path.parent.mkdir(parents=True, exist_ok=True)
        keyfile_path.write_bytes(b"dummy")

        settings_mock = MagicMock()
        settings_mock.database.path = str(tmp_path / "obs.db")

        with patch("obs.api.v1.knxkeyfile.get_settings", return_value=settings_mock):
            resp = client.delete(f"/knx/keyfile/{file_id}")

        assert resp.status_code == 204
        assert not keyfile_path.exists()

    def test_delete_invalid_file_id_returns_400(self, client: TestClient) -> None:
        resp = client.delete("/knx/keyfile/KEIN-UUID")
        assert resp.status_code == 400

    def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        resp = client.delete(f"/knx/keyfile/{uuid.uuid4()}")
        assert resp.status_code == 404
