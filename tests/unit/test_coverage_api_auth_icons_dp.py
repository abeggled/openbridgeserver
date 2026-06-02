"""Comprehensive coverage tests for auth, icons, datapoints, bindings, search APIs.

Covers the previously-uncovered statements in:
  - obs/api/auth.py
  - obs/api/v1/icons.py
  - obs/api/v1/datapoints.py
  - obs/api/v1/bindings.py
  - obs/api/v1/search.py
"""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jose import jwt

import obs.api.auth as auth_module
import obs.api.v1.bindings as bindings_api
import obs.api.v1.datapoints as dp_api
import obs.api.v1.icons as icons_api
import obs.api.v1.search as search_api
from obs.api.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    SetMqttPasswordRequest,
    UserCreate,
    UserUpdate,
    create_access_token,
    create_refresh_token,
    decode_token,
    ensure_default_user,
    hash_password,
)
from obs.api.v1.icons import (
    DeleteRequest,
    ExportRequest,
    FontAwesomeRequest,
    IconsSettingsIn,
    _build_export_zip,
    _secure_filename,
)
from obs.models.binding import AdapterBindingCreate, AdapterBindingUpdate
from obs.models.datapoint import DataPointCreate, DataPointUpdate


# ---------------------------------------------------------------------------
# Helpers / Stubs
# ---------------------------------------------------------------------------


def _make_row(**kwargs) -> dict:
    """Build a dict-like row object supporting __getitem__ and .get()."""

    class _Row(dict):
        def __getitem__(self, key):
            return super().__getitem__(key)

    return _Row(kwargs)


class _DbStub:
    """Minimal async DB stub."""

    def __init__(self, rows=None, fetchone_result=None):
        self._rows = rows or []
        self._fetchone_result = fetchone_result
        self._fetchone_call_count = 0
        self._fetchone_side_effects: list = []
        self._fetchall_side_effects: list = []
        self.executed: list = []

    async def fetchone(self, query: str, params=()):
        self._fetchone_call_count += 1
        if self._fetchone_side_effects:
            result = self._fetchone_side_effects.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return self._fetchone_result

    async def fetchall(self, query: str, params=()):
        if self._fetchall_side_effects:
            return list(self._fetchall_side_effects.pop(0))
        return list(self._rows)

    async def execute_and_commit(self, query: str, params=()):
        self.executed.append((query, params))

    async def execute(self, query: str, params=()):
        self.executed.append((query, params))

    async def commit(self):
        pass


class _DpStub:
    """Minimal DataPoint stub."""

    def __init__(self, dp_id=None, name="Test DP", data_type="FLOAT", tags=None):
        self.id = dp_id or uuid.uuid4()
        self.name = name
        self.data_type = data_type
        self.unit = "°C"
        self.tags = tags or []
        self.mqtt_topic = f"dp/{self.id}/value"
        self.mqtt_alias = None
        self.persist_value = True
        self.record_history = True
        self.created_at = datetime.now(UTC)
        self.updated_at = datetime.now(UTC)


class _RegistryStub:
    def __init__(self, dps=None):
        self._dps: dict[uuid.UUID, _DpStub] = {}
        self._values: dict[uuid.UUID, Any] = {}
        if dps:
            for dp in dps:
                self._dps[dp.id] = dp

    def get(self, dp_id):
        return self._dps.get(dp_id)

    def all(self):
        return list(self._dps.values())

    def get_value(self, dp_id):
        return self._values.get(dp_id)

    async def create(self, body):
        dp = _DpStub(name=body.name, data_type=body.data_type)
        self._dps[dp.id] = dp
        return dp

    async def update(self, dp_id, body):
        dp = self._dps[dp_id]
        if body.name is not None:
            dp.name = body.name
        return dp

    async def delete(self, dp_id):
        self._dps.pop(dp_id, None)


# ---------------------------------------------------------------------------
# Fixture: settings with a known JWT secret
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    """Ensure JWT operations use a fixed secret without touching config.yaml."""

    class _Security:
        jwt_secret = "test-secret-key-for-unit-tests"
        jwt_expire_minutes = 60

    class _MosquittoConf:
        passwd_file = "/tmp/test_passwd"
        service_username = "obs"
        service_password = "testpass"
        reload_command = None
        reload_pid = None

    class _FakeSettings:
        security = _Security()
        mosquitto = _MosquittoConf()

    monkeypatch.setattr(auth_module, "get_settings", lambda: _FakeSettings())


# ---------------------------------------------------------------------------
# auth.py — decode_token
# ---------------------------------------------------------------------------


class TestDecodeToken:
    def test_valid_access_token(self):
        token = create_access_token("alice")
        sub = decode_token(token, expected_type="access")
        assert sub == "alice"

    def test_valid_refresh_token(self):
        token = create_refresh_token("bob")
        sub = decode_token(token, expected_type="refresh")
        assert sub == "bob"

    def test_wrong_token_type_raises_401(self):
        token = create_refresh_token("alice")
        with pytest.raises(HTTPException) as exc:
            decode_token(token, expected_type="access")
        assert exc.value.status_code == 401

    def test_expired_token_raises_401(self):
        expired_payload = {
            "sub": "alice",
            "type": "access",
            "exp": datetime.now(UTC) - timedelta(seconds=1),
        }
        token = jwt.encode(expired_payload, "test-secret-key-for-unit-tests", algorithm="HS256")
        with pytest.raises(HTTPException) as exc:
            decode_token(token)
        assert exc.value.status_code == 401

    def test_invalid_signature_raises_401(self):
        token = jwt.encode({"sub": "alice", "type": "access"}, "wrong-secret", algorithm="HS256")
        with pytest.raises(HTTPException) as exc:
            decode_token(token)
        assert exc.value.status_code == 401

    def test_missing_sub_raises_401(self):
        payload = {"type": "access", "exp": datetime.now(UTC) + timedelta(hours=1)}
        token = jwt.encode(payload, "test-secret-key-for-unit-tests", algorithm="HS256")
        with pytest.raises(HTTPException) as exc:
            decode_token(token)
        assert exc.value.status_code == 401

    def test_garbage_token_raises_401(self):
        with pytest.raises(HTTPException) as exc:
            decode_token("not.a.valid.jwt")
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# auth.py — create_access_token / create_refresh_token
# ---------------------------------------------------------------------------


class TestCreateTokens:
    def test_access_token_round_trip(self):
        token = create_access_token("user1")
        assert decode_token(token) == "user1"

    def test_refresh_token_round_trip(self):
        token = create_refresh_token("user2")
        assert decode_token(token, expected_type="refresh") == "user2"


# ---------------------------------------------------------------------------
# auth.py — get_current_user
# ---------------------------------------------------------------------------


class TestGetCurrentUser:
    @pytest.mark.asyncio
    async def test_with_valid_bearer_token(self):
        from fastapi.security import HTTPAuthorizationCredentials

        token = create_access_token("alice")
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        db = _DbStub()
        result = await auth_module.get_current_user(credentials=creds, api_key=None, db=db)
        assert result == "alice"

    @pytest.mark.asyncio
    async def test_with_valid_api_key(self):
        api_key = "obs_" + "a" * 64
        row = _make_row(subject="alice")
        db = _DbStub(fetchone_result=row)
        result = await auth_module.get_current_user(credentials=None, api_key=api_key, db=db)
        assert result == "alice"

    @pytest.mark.asyncio
    async def test_with_invalid_api_key_raises_401(self):
        db = _DbStub(fetchone_result=None)
        with pytest.raises(HTTPException) as exc:
            await auth_module.get_current_user(credentials=None, api_key="bad_key", db=db)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_with_no_credentials_raises_401(self):
        db = _DbStub()
        with pytest.raises(HTTPException) as exc:
            await auth_module.get_current_user(credentials=None, api_key=None, db=db)
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# auth.py — optional_current_user
# ---------------------------------------------------------------------------


class TestOptionalCurrentUser:
    @pytest.mark.asyncio
    async def test_returns_none_on_no_credentials(self):
        db = _DbStub()
        result = await auth_module.optional_current_user(credentials=None, api_key=None, db=db)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_user_on_valid_token(self):
        from fastapi.security import HTTPAuthorizationCredentials

        token = create_access_token("carol")
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        db = _DbStub()
        result = await auth_module.optional_current_user(credentials=creds, api_key=None, db=db)
        assert result == "carol"


# ---------------------------------------------------------------------------
# auth.py — get_admin_user
# ---------------------------------------------------------------------------


class TestGetAdminUser:
    @pytest.mark.asyncio
    async def test_admin_user_allowed(self):
        row = _make_row(is_admin=1)
        db = _DbStub(fetchone_result=row)
        result = await auth_module.get_admin_user(current_user="admin", db=db)
        assert result == "admin"

    @pytest.mark.asyncio
    async def test_non_admin_raises_403(self):
        row = _make_row(is_admin=0)
        db = _DbStub(fetchone_result=row)
        with pytest.raises(HTTPException) as exc:
            await auth_module.get_admin_user(current_user="normaluser", db=db)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_user_raises_403(self):
        db = _DbStub(fetchone_result=None)
        with pytest.raises(HTTPException) as exc:
            await auth_module.get_admin_user(current_user="ghost", db=db)
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# auth.py — ensure_default_user
# ---------------------------------------------------------------------------


class TestEnsureDefaultUser:
    @pytest.mark.asyncio
    async def test_creates_admin_when_no_users(self):
        db = _DbStub(fetchone_result=_make_row(c=0))
        await ensure_default_user(db)
        assert len(db.executed) == 1
        assert "INSERT INTO users" in db.executed[0][0]

    @pytest.mark.asyncio
    async def test_skips_when_users_exist(self):
        db = _DbStub(fetchone_result=_make_row(c=1))
        await ensure_default_user(db)
        assert len(db.executed) == 0


# ---------------------------------------------------------------------------
# auth.py — login endpoint
# ---------------------------------------------------------------------------


class TestLoginEndpoint:
    @pytest.mark.asyncio
    async def test_valid_login_returns_tokens(self):
        stored_hash = hash_password("password")
        db = _DbStub(fetchone_result=_make_row(password_hash=stored_hash))
        request = MagicMock()
        request.state = MagicMock()
        body = LoginRequest(username="admin", password="password")
        result = await auth_module.login.__wrapped__(request=request, body=body, db=db)
        assert result.access_token
        assert result.refresh_token
        assert result.token_type == "bearer"

    @pytest.mark.asyncio
    async def test_invalid_password_raises_401(self):
        stored_hash = hash_password("correct_password")
        db = _DbStub(fetchone_result=_make_row(password_hash=stored_hash))
        request = MagicMock()
        body = LoginRequest(username="admin", password="wrong_password")
        with pytest.raises(HTTPException) as exc:
            await auth_module.login.__wrapped__(request=request, body=body, db=db)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_user_raises_401(self):
        db = _DbStub(fetchone_result=None)
        request = MagicMock()
        body = LoginRequest(username="nobody", password="pw")
        with pytest.raises(HTTPException) as exc:
            await auth_module.login.__wrapped__(request=request, body=body, db=db)
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# auth.py — refresh endpoint
# ---------------------------------------------------------------------------


class TestRefreshEndpoint:
    @pytest.mark.asyncio
    async def test_valid_refresh_token(self):
        refresh_tok = create_refresh_token("alice")
        request = MagicMock()
        body = RefreshRequest(refresh_token=refresh_tok)
        result = await auth_module.refresh.__wrapped__(request=request, body=body)
        assert result.access_token
        assert result.token_type == "bearer"

    @pytest.mark.asyncio
    async def test_access_token_as_refresh_raises_401(self):
        access_tok = create_access_token("alice")
        request = MagicMock()
        body = RefreshRequest(refresh_token=access_tok)
        with pytest.raises(HTTPException) as exc:
            await auth_module.refresh.__wrapped__(request=request, body=body)
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# auth.py — list_api_keys endpoint
# ---------------------------------------------------------------------------


class TestListApiKeys:
    @pytest.mark.asyncio
    async def test_admin_sees_all_keys(self):
        admin_row = _make_row(is_admin=1)
        key_rows = [
            _make_row(id="k1", name="key1", created_at="2024-01-01", last_used_at=None),
        ]
        db = _DbStub(rows=key_rows, fetchone_result=admin_row)
        result = await auth_module.list_api_keys(current_user="admin", db=db)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_non_admin_sees_own_keys(self):
        user_row = _make_row(is_admin=0)
        key_rows = [
            _make_row(id="k2", name="mykey", created_at="2024-01-01", last_used_at=None),
        ]
        db = _DbStub(rows=key_rows, fetchone_result=user_row)
        result = await auth_module.list_api_keys(current_user="user1", db=db)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# auth.py — create_api_key endpoint
# ---------------------------------------------------------------------------


class TestCreateApiKey:
    @pytest.mark.asyncio
    async def test_creates_key_successfully(self):
        from obs.api.auth import ApiKeyCreate

        db = _DbStub()
        request = MagicMock()
        body = ApiKeyCreate(name="automation-key")
        result = await auth_module.create_api_key.__wrapped__(request=request, body=body, _user="admin", db=db)
        assert result.key.startswith("obs_")
        assert result.name == "automation-key"
        assert len(db.executed) == 1


# ---------------------------------------------------------------------------
# auth.py — delete_api_key endpoint
# ---------------------------------------------------------------------------


class TestDeleteApiKey:
    @pytest.mark.asyncio
    async def test_admin_can_delete_any_key(self):
        key_row = _make_row(owner="user1")
        admin_row = _make_row(is_admin=1)
        db = _DbStub()
        db._fetchone_side_effects = [key_row, admin_row]
        await auth_module.delete_api_key(key_id="k1", current_user="admin", db=db)
        assert any("DELETE" in q for q, _ in db.executed)

    @pytest.mark.asyncio
    async def test_user_can_delete_own_key(self):
        key_row = _make_row(owner="alice")
        user_row = _make_row(is_admin=0)
        db = _DbStub()
        db._fetchone_side_effects = [key_row, user_row]
        await auth_module.delete_api_key(key_id="k1", current_user="alice", db=db)
        assert any("DELETE" in q for q, _ in db.executed)

    @pytest.mark.asyncio
    async def test_user_cannot_delete_others_key_raises_403(self):
        key_row = _make_row(owner="bob")
        user_row = _make_row(is_admin=0)
        db = _DbStub()
        db._fetchone_side_effects = [key_row, user_row]
        with pytest.raises(HTTPException) as exc:
            await auth_module.delete_api_key(key_id="k1", current_user="alice", db=db)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_key_raises_404(self):
        db = _DbStub(fetchone_result=None)
        with pytest.raises(HTTPException) as exc:
            await auth_module.delete_api_key(key_id="missing", current_user="admin", db=db)
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# auth.py — list_users endpoint
# ---------------------------------------------------------------------------


class TestListUsers:
    @pytest.mark.asyncio
    async def test_list_users_returns_all(self):
        rows = [
            _make_row(
                id=str(uuid.uuid4()),
                username="admin",
                is_admin=1,
                mqtt_enabled=0,
                mqtt_password_hash=None,
                created_at="2024-01-01T00:00:00",
            ),
        ]
        db = _DbStub(rows=rows)
        result = await auth_module.list_users(_admin="admin", db=db)
        assert len(result) == 1
        assert result[0].username == "admin"


# ---------------------------------------------------------------------------
# auth.py — create_user endpoint
# ---------------------------------------------------------------------------


class TestCreateUser:
    @pytest.mark.asyncio
    async def test_create_user_success(self):
        new_user_row = _make_row(
            id=str(uuid.uuid4()),
            username="newuser",
            is_admin=0,
            mqtt_enabled=0,
            mqtt_password_hash=None,
            created_at="2024-01-01T00:00:00",
        )
        db = _DbStub()
        db._fetchone_side_effects = [None, new_user_row]  # no conflict, then the new user row

        body = UserCreate(username="newuser", password="pass123", is_admin=False)
        result = await auth_module.create_user(body=body, _admin="admin", db=db)
        assert result.username == "newuser"

    @pytest.mark.asyncio
    async def test_create_user_conflict_raises_409(self):
        existing_row = _make_row(id=str(uuid.uuid4()))
        db = _DbStub(fetchone_result=existing_row)
        body = UserCreate(username="existing", password="pass")
        with pytest.raises(HTTPException) as exc:
            await auth_module.create_user(body=body, _admin="admin", db=db)
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_create_user_with_mqtt_password(self, monkeypatch):
        new_user_row = _make_row(
            id=str(uuid.uuid4()),
            username="mqttuser",
            is_admin=0,
            mqtt_enabled=1,
            mqtt_password_hash="somehash",
            created_at="2024-01-01T00:00:00",
        )
        db = _DbStub()
        db._fetchone_side_effects = [None, new_user_row]

        import obs.core.mqtt_passwd as mqtt_passwd_mod

        monkeypatch.setattr(mqtt_passwd_mod, "mosquitto_hash", lambda pw: "hashed")
        monkeypatch.setattr(auth_module, "_sync_mqtt", AsyncMock())
        body = UserCreate(username="mqttuser", password="pass", mqtt_enabled=True, mqtt_password="mqtt123")
        result = await auth_module.create_user(body=body, _admin="admin", db=db)
        assert result.username == "mqttuser"


# ---------------------------------------------------------------------------
# auth.py — get_user endpoint
# ---------------------------------------------------------------------------


class TestGetUser:
    @pytest.mark.asyncio
    async def test_admin_can_get_any_user(self):
        caller_row = _make_row(is_admin=1)
        target_row = _make_row(
            id=str(uuid.uuid4()),
            username="other",
            is_admin=0,
            mqtt_enabled=0,
            mqtt_password_hash=None,
            created_at="2024-01-01T00:00:00",
        )
        db = _DbStub()
        db._fetchone_side_effects = [caller_row, target_row]
        result = await auth_module.get_user(username="other", current_user="admin", db=db)
        assert result.username == "other"

    @pytest.mark.asyncio
    async def test_user_can_get_self(self):
        caller_row = _make_row(is_admin=0)
        target_row = _make_row(
            id=str(uuid.uuid4()),
            username="alice",
            is_admin=0,
            mqtt_enabled=0,
            mqtt_password_hash=None,
            created_at="2024-01-01T00:00:00",
        )
        db = _DbStub()
        db._fetchone_side_effects = [caller_row, target_row]
        result = await auth_module.get_user(username="alice", current_user="alice", db=db)
        assert result.username == "alice"

    @pytest.mark.asyncio
    async def test_non_admin_cannot_get_other_raises_403(self):
        caller_row = _make_row(is_admin=0)
        db = _DbStub(fetchone_result=caller_row)
        with pytest.raises(HTTPException) as exc:
            await auth_module.get_user(username="bob", current_user="alice", db=db)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_caller_raises_401(self):
        db = _DbStub(fetchone_result=None)
        with pytest.raises(HTTPException) as exc:
            await auth_module.get_user(username="anyone", current_user="ghost", db=db)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_target_not_found_raises_404(self):
        caller_row = _make_row(is_admin=1)
        db = _DbStub()
        db._fetchone_side_effects = [caller_row, None]
        with pytest.raises(HTTPException) as exc:
            await auth_module.get_user(username="nobody", current_user="admin", db=db)
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# auth.py — update_user endpoint
# ---------------------------------------------------------------------------


class TestUpdateUser:
    @pytest.mark.asyncio
    async def test_update_username_success(self):
        uid = str(uuid.uuid4())
        target_row = _make_row(
            id=uid,
            username="alice",
            is_admin=0,
            mqtt_enabled=0,
            mqtt_password_hash=None,
            created_at="2024-01-01T00:00:00",
        )
        updated_row = _make_row(
            id=uid,
            username="alice-new",
            is_admin=0,
            mqtt_enabled=0,
            mqtt_password_hash=None,
            created_at="2024-01-01T00:00:00",
        )
        db = _DbStub()
        db._fetchone_side_effects = [target_row, None, updated_row]  # target, no conflict, updated
        body = UserUpdate(username="alice-new")
        result = await auth_module.update_user(username="alice", body=body, _admin="admin", db=db)
        assert result.username == "alice-new"

    @pytest.mark.asyncio
    async def test_update_user_not_found_raises_404(self):
        db = _DbStub(fetchone_result=None)
        body = UserUpdate(username="new")
        with pytest.raises(HTTPException) as exc:
            await auth_module.update_user(username="ghost", body=body, _admin="admin", db=db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_username_conflict_raises_409(self):
        uid = str(uuid.uuid4())
        target_row = _make_row(
            id=uid,
            username="alice",
            is_admin=0,
            mqtt_enabled=0,
            mqtt_password_hash=None,
            created_at="2024-01-01T00:00:00",
        )
        conflict_row = _make_row(id=str(uuid.uuid4()))
        db = _DbStub()
        db._fetchone_side_effects = [target_row, conflict_row]
        body = UserUpdate(username="existing-name")
        with pytest.raises(HTTPException) as exc:
            await auth_module.update_user(username="alice", body=body, _admin="admin", db=db)
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_update_mqtt_enabled_triggers_sync(self, monkeypatch):
        uid = str(uuid.uuid4())
        target_row = _make_row(
            id=uid,
            username="alice",
            is_admin=0,
            mqtt_enabled=0,
            mqtt_password_hash=None,
            created_at="2024-01-01T00:00:00",
        )
        updated_row = _make_row(
            id=uid,
            username="alice",
            is_admin=0,
            mqtt_enabled=1,
            mqtt_password_hash="hash",
            created_at="2024-01-01T00:00:00",
        )
        db = _DbStub()
        db._fetchone_side_effects = [target_row, updated_row]
        sync_mock = AsyncMock()
        monkeypatch.setattr(auth_module, "_sync_mqtt", sync_mock)
        body = UserUpdate(mqtt_enabled=True)
        await auth_module.update_user(username="alice", body=body, _admin="admin", db=db)
        sync_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# auth.py — delete_user endpoint
# ---------------------------------------------------------------------------


class TestDeleteUser:
    @pytest.mark.asyncio
    async def test_delete_own_account_raises_400(self):
        db = _DbStub()
        with pytest.raises(HTTPException) as exc:
            await auth_module.delete_user(username="admin", admin_user="admin", db=db)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises_404(self):
        db = _DbStub(fetchone_result=None)
        with pytest.raises(HTTPException) as exc:
            await auth_module.delete_user(username="ghost", admin_user="admin", db=db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_user_success(self):
        user_row = _make_row(mqtt_enabled=0)
        db = _DbStub(fetchone_result=user_row)
        await auth_module.delete_user(username="alice", admin_user="admin", db=db)
        assert any("DELETE" in q for q, _ in db.executed)

    @pytest.mark.asyncio
    async def test_delete_mqtt_user_triggers_sync(self, monkeypatch):
        user_row = _make_row(mqtt_enabled=1)
        db = _DbStub(fetchone_result=user_row)
        sync_mock = AsyncMock()
        monkeypatch.setattr(auth_module, "_sync_mqtt", sync_mock)
        await auth_module.delete_user(username="mqttuser", admin_user="admin", db=db)
        sync_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# auth.py — set_mqtt_password endpoint
# ---------------------------------------------------------------------------


class TestSetMqttPassword:
    @pytest.mark.asyncio
    async def test_admin_sets_mqtt_password(self, monkeypatch):
        caller_row = _make_row(is_admin=1)
        target_row = _make_row(id=str(uuid.uuid4()))
        db = _DbStub()
        db._fetchone_side_effects = [caller_row, target_row]
        sync_mock = AsyncMock()
        monkeypatch.setattr(auth_module, "_sync_mqtt", sync_mock)
        import obs.core.mqtt_passwd as mqtt_passwd_mod

        monkeypatch.setattr(mqtt_passwd_mod, "mosquitto_hash", lambda pw: "hashed")
        body = SetMqttPasswordRequest(password="newmqttpass")
        await auth_module.set_mqtt_password(username="alice", body=body, current_user="admin", db=db)
        sync_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_caller_not_found_raises_401(self):
        db = _DbStub(fetchone_result=None)
        body = SetMqttPasswordRequest(password="pass")
        with pytest.raises(HTTPException) as exc:
            await auth_module.set_mqtt_password(username="alice", body=body, current_user="ghost", db=db)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_non_admin_cannot_set_other_mqtt_password(self):
        caller_row = _make_row(is_admin=0)
        db = _DbStub(fetchone_result=caller_row)
        body = SetMqttPasswordRequest(password="pass")
        with pytest.raises(HTTPException) as exc:
            await auth_module.set_mqtt_password(username="bob", body=body, current_user="alice", db=db)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_target_not_found_raises_404(self, monkeypatch):
        caller_row = _make_row(is_admin=1)
        db = _DbStub()
        db._fetchone_side_effects = [caller_row, None]
        body = SetMqttPasswordRequest(password="pass")
        with pytest.raises(HTTPException) as exc:
            await auth_module.set_mqtt_password(username="ghost", body=body, current_user="admin", db=db)
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# auth.py — delete_mqtt_password endpoint
# ---------------------------------------------------------------------------


class TestDeleteMqttPassword:
    @pytest.mark.asyncio
    async def test_delete_mqtt_password_success(self, monkeypatch):
        target_row = _make_row(id=str(uuid.uuid4()))
        db = _DbStub(fetchone_result=target_row)
        sync_mock = AsyncMock()
        monkeypatch.setattr(auth_module, "_sync_mqtt", sync_mock)
        await auth_module.delete_mqtt_password(username="alice", _admin="admin", db=db)
        sync_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_mqtt_password_not_found_raises_404(self):
        db = _DbStub(fetchone_result=None)
        with pytest.raises(HTTPException) as exc:
            await auth_module.delete_mqtt_password(username="ghost", _admin="admin", db=db)
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# auth.py — /me endpoints
# ---------------------------------------------------------------------------


class TestMeEndpoints:
    @pytest.mark.asyncio
    async def test_get_me_success(self):
        user_row = _make_row(
            id=str(uuid.uuid4()),
            username="alice",
            is_admin=0,
            mqtt_enabled=0,
            mqtt_password_hash=None,
            created_at="2024-01-01T00:00:00",
        )
        db = _DbStub(fetchone_result=user_row)
        result = await auth_module.get_me(current_user="alice", db=db)
        assert result.username == "alice"

    @pytest.mark.asyncio
    async def test_get_me_not_found_raises_404(self):
        db = _DbStub(fetchone_result=None)
        with pytest.raises(HTTPException) as exc:
            await auth_module.get_me(current_user="ghost", db=db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_change_password_success(self):
        stored = hash_password("old_pass")
        pw_row = _make_row(password_hash=stored)
        db = _DbStub(fetchone_result=pw_row)
        body = ChangePasswordRequest(current_password="old_pass", new_password="new_pass")
        await auth_module.change_password(body=body, current_user="alice", db=db)
        assert len(db.executed) == 1

    @pytest.mark.asyncio
    async def test_change_password_wrong_current_raises_400(self):
        stored = hash_password("real_pass")
        pw_row = _make_row(password_hash=stored)
        db = _DbStub(fetchone_result=pw_row)
        body = ChangePasswordRequest(current_password="wrong_pass", new_password="new")
        with pytest.raises(HTTPException) as exc:
            await auth_module.change_password(body=body, current_user="alice", db=db)
        assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# auth.py — _user_row helper
# ---------------------------------------------------------------------------


class TestUserRow:
    def test_user_row_with_mqtt_hash(self):
        row = _make_row(
            id=str(uuid.uuid4()),
            username="bob",
            is_admin=0,
            mqtt_enabled=1,
            mqtt_password_hash="somehash",
            created_at="2024-01-01T00:00:00",
        )
        result = auth_module._user_row(row)
        assert result.mqtt_password_set is True
        assert result.mqtt_enabled is True

    def test_user_row_without_mqtt_hash(self):
        row = _make_row(
            id=str(uuid.uuid4()),
            username="carol",
            is_admin=0,
            mqtt_enabled=0,
            mqtt_password_hash=None,
            created_at="2024-01-01T00:00:00",
        )
        result = auth_module._user_row(row)
        assert result.mqtt_password_set is False


# ---------------------------------------------------------------------------
# icons.py — _secure_filename
# ---------------------------------------------------------------------------


class TestSecureFilename:
    def test_normal_filename(self):
        assert _secure_filename("home.svg") == "home.svg"

    def test_strips_path_separator(self):
        result = _secure_filename("../etc/passwd")
        assert "/" not in result
        assert "\\" not in result

    def test_strips_null_bytes(self):
        result = _secure_filename("evil\x00name.svg")
        assert "\x00" not in result

    def test_strips_leading_dots(self):
        result = _secure_filename(".hidden.svg")
        assert not result.startswith(".")

    def test_empty_becomes_empty(self):
        # Leading dots/underscores stripped, result may be empty
        result = _secure_filename("   ")
        # Whitespace is stripped, then special chars replaced — just check no crash
        assert isinstance(result, str)

    def test_replaces_special_chars(self):
        result = _secure_filename("icon with spaces!.svg")
        assert " " not in result
        assert "!" not in result


# ---------------------------------------------------------------------------
# icons.py — list_icons endpoint
# ---------------------------------------------------------------------------


class TestListIcons:
    @pytest.mark.asyncio
    async def test_empty_dir_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        result = await icons_api.list_icons(_user="admin")
        assert result.total == 0
        assert result.icons == []

    @pytest.mark.asyncio
    async def test_lists_svg_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>'
        (tmp_path / "home.svg").write_bytes(svg)
        result = await icons_api.list_icons(_user="admin")
        assert result.total == 1
        assert result.icons[0].name == "home"

    @pytest.mark.asyncio
    async def test_ignores_invalid_svg_gracefully(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        # Write a file that is valid enough for _sanitize_svg to be called
        # but that might produce empty bytes — use an SVG that passes
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>'
        (tmp_path / "good.svg").write_bytes(svg)
        result = await icons_api.list_icons(_user="admin")
        assert result.total == 1


# ---------------------------------------------------------------------------
# icons.py — import_icons endpoint
# ---------------------------------------------------------------------------


class _UploadFileMock:
    def __init__(self, filename, content, content_type="image/svg+xml"):
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self):
        return self._content


class TestImportIcons:
    @pytest.mark.asyncio
    async def test_import_single_svg(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>'
        upload = _UploadFileMock("arrow.svg", svg)
        result = await icons_api.import_icons(files=[upload], _user="admin")
        assert result.imported == 1
        assert "arrow" in result.names

    @pytest.mark.asyncio
    async def test_import_non_svg_raises_422(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        upload = _UploadFileMock("image.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        with pytest.raises(HTTPException) as exc:
            await icons_api.import_icons(files=[upload], _user="admin")
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_import_empty_files_list_raises_400(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        with pytest.raises(HTTPException) as exc:
            await icons_api.import_icons(files=[], _user="admin")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_import_zip_with_svgs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>'
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("icons/star.svg", svg)
        buf.seek(0)
        upload = _UploadFileMock("icons.zip", buf.read(), content_type="application/zip")
        result = await icons_api.import_icons(files=[upload], _user="admin")
        assert result.imported >= 1

    @pytest.mark.asyncio
    async def test_import_bad_zip_raises_400(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        upload = _UploadFileMock("bad.zip", b"not a zip", content_type="application/zip")
        with pytest.raises(HTTPException) as exc:
            await icons_api.import_icons(files=[upload], _user="admin")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_import_svg_with_unsafe_name_is_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>'
        upload = _UploadFileMock("../evil.svg", svg)
        # _safe_name("../evil.svg") returns None → skipped
        result = await icons_api.import_icons(files=[upload], _user="admin")
        assert result.imported == 0
        assert result.skipped == 1


# ---------------------------------------------------------------------------
# icons.py — _build_export_zip
# ---------------------------------------------------------------------------


class TestBuildExportZip:
    def test_export_all_icons(self, tmp_path):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>'
        (tmp_path / "a.svg").write_bytes(svg)
        (tmp_path / "b.svg").write_bytes(svg)
        buf = _build_export_zip(tmp_path, [])
        with zipfile.ZipFile(buf) as zf:
            assert len(zf.namelist()) == 2

    def test_export_selected_icons(self, tmp_path):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>'
        (tmp_path / "a.svg").write_bytes(svg)
        (tmp_path / "b.svg").write_bytes(svg)
        buf = _build_export_zip(tmp_path, ["a"])
        with zipfile.ZipFile(buf) as zf:
            assert "a.svg" in zf.namelist()
            assert "b.svg" not in zf.namelist()

    def test_export_empty_raises_404(self, tmp_path):
        with pytest.raises(HTTPException) as exc:
            _build_export_zip(tmp_path, [])
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# icons.py — export_icons_post endpoint
# ---------------------------------------------------------------------------


class TestExportIconsPost:
    @pytest.mark.asyncio
    async def test_export_returns_streaming_response(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>'
        (tmp_path / "test.svg").write_bytes(svg)
        body = ExportRequest(names=[])
        response = await icons_api.export_icons_post(body=body, _user="admin")
        assert response.media_type == "application/zip"


# ---------------------------------------------------------------------------
# icons.py — delete_icons endpoint
# ---------------------------------------------------------------------------


class TestDeleteIcons:
    @pytest.mark.asyncio
    async def test_delete_existing_icon(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>'
        (tmp_path / "home.svg").write_bytes(svg)
        result = await icons_api.delete_icons(body=DeleteRequest(names=["home"]), _user="admin")
        assert result["deleted"] == 1
        assert "home" in result["names"]

    @pytest.mark.asyncio
    async def test_delete_nonexistent_icon_in_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        result = await icons_api.delete_icons(body=DeleteRequest(names=["missing"]), _user="admin")
        assert "missing" in result["not_found"]

    @pytest.mark.asyncio
    async def test_delete_invalid_name_raises_400(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        with pytest.raises(HTTPException) as exc:
            await icons_api.delete_icons(body=DeleteRequest(names=["../evil"]), _user="admin")
        assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# icons.py — get_icons_settings / update_icons_settings
# ---------------------------------------------------------------------------


class TestIconsSettings:
    @pytest.mark.asyncio
    async def test_get_settings_no_key(self):
        db = _DbStub(fetchone_result=None)
        result = await icons_api.get_icons_settings(_user="admin", db=db)
        assert result.fa_api_key is None

    @pytest.mark.asyncio
    async def test_get_settings_with_key(self):
        row = _make_row(value="fa-test-key")
        db = _DbStub(fetchone_result=row)
        result = await icons_api.get_icons_settings(_user="admin", db=db)
        assert result.fa_api_key == "fa-test-key"

    @pytest.mark.asyncio
    async def test_update_settings_store_key(self):
        db = _DbStub()
        body = IconsSettingsIn(fa_api_key="new-key")
        result = await icons_api.update_icons_settings(body=body, _user="admin", db=db)
        assert result.fa_api_key == "new-key"
        assert len(db.executed) == 1

    @pytest.mark.asyncio
    async def test_update_settings_clear_key(self):
        db = _DbStub()
        body = IconsSettingsIn(fa_api_key=None)
        result = await icons_api.update_icons_settings(body=body, _user="admin", db=db)
        assert result.fa_api_key is None
        assert len(db.executed) == 1

    @pytest.mark.asyncio
    async def test_update_settings_empty_string_clears(self):
        db = _DbStub()
        body = IconsSettingsIn(fa_api_key="")
        result = await icons_api.update_icons_settings(body=body, _user="admin", db=db)
        assert result.fa_api_key is None


# ---------------------------------------------------------------------------
# icons.py — get_icon endpoint
# ---------------------------------------------------------------------------


class TestGetIcon:
    @pytest.mark.asyncio
    async def test_get_existing_icon(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>'
        (tmp_path / "home.svg").write_bytes(svg)
        response = await icons_api.get_icon(name="home", _user="admin")
        assert response.media_type == "image/svg+xml"

    @pytest.mark.asyncio
    async def test_get_nonexistent_icon_raises_404(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        with pytest.raises(HTTPException) as exc:
            await icons_api.get_icon(name="missing", _user="admin")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_icon_invalid_name_raises_400(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        with pytest.raises(HTTPException) as exc:
            await icons_api.get_icon(name="../evil", _user="admin")
        assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# icons.py — import_fontawesome (no-key CDN path — mocked)
# ---------------------------------------------------------------------------


class TestImportFontawesome:
    @pytest.mark.asyncio
    async def test_no_icons_raises_400(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        db = _DbStub(fetchone_result=None)
        body = FontAwesomeRequest(icons=[])
        with pytest.raises(HTTPException) as exc:
            await icons_api.import_fontawesome(body=body, _user="admin", db=db)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_icon_name_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        db = _DbStub(fetchone_result=None)
        monkeypatch.setattr(icons_api, "_fa_cdn_svg", AsyncMock(return_value=None))
        monkeypatch.setattr(icons_api, "_fa_exchange_token", AsyncMock(return_value=None))

        import httpx

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        body = FontAwesomeRequest(icons=["../invalid"], style="solid")
        with patch("httpx.AsyncClient", return_value=mock_http):
            result = await icons_api.import_fontawesome(body=body, _user="admin", db=db)
        assert result.skipped >= 1

    @pytest.mark.asyncio
    async def test_cdn_success(self, tmp_path, monkeypatch):
        monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
        db = _DbStub(fetchone_result=None)
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>'
        monkeypatch.setattr(icons_api, "_fa_cdn_svg", AsyncMock(return_value=svg))
        monkeypatch.setattr(icons_api, "_fa_exchange_token", AsyncMock(return_value=None))

        import httpx

        mock_http = AsyncMock(spec=httpx.AsyncClient)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        body = FontAwesomeRequest(icons=["home"], style="solid")
        with patch("httpx.AsyncClient", return_value=mock_http):
            result = await icons_api.import_fontawesome(body=body, _user="admin", db=db)
        assert result.imported >= 1


# ---------------------------------------------------------------------------
# datapoints.py — list_tags
# ---------------------------------------------------------------------------


class TestListTags:
    @pytest.mark.asyncio
    async def test_list_tags_empty(self, monkeypatch):
        monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub())
        result = await dp_api.list_tags(_user="admin")
        assert result == []

    @pytest.mark.asyncio
    async def test_list_tags_deduped_sorted(self, monkeypatch):
        dp1 = _DpStub(tags=["lighting", "knx"])
        dp2 = _DpStub(tags=["lighting", "temperature"])
        reg = _RegistryStub(dps=[dp1, dp2])
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        result = await dp_api.list_tags(_user="admin")
        assert result == sorted({"lighting", "knx", "temperature"})


# ---------------------------------------------------------------------------
# datapoints.py — list_datapoints
# ---------------------------------------------------------------------------


class TestListDatapoints:
    @pytest.mark.asyncio
    async def test_list_empty(self, monkeypatch):
        monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub())
        result = await dp_api.list_datapoints(page=0, size=50, sort="name", order="asc", _user="admin")
        assert result.total == 0
        assert result.items == []
        assert result.pages == 1

    @pytest.mark.asyncio
    async def test_list_with_datapoints(self, monkeypatch):
        dp1 = _DpStub(name="Wohnzimmer Temperatur")
        dp2 = _DpStub(name="Büro Temperatur")
        reg = _RegistryStub(dps=[dp1, dp2])
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        result = await dp_api.list_datapoints(page=0, size=50, sort="name", order="asc", _user="admin")
        assert result.total == 2

    @pytest.mark.asyncio
    async def test_pagination(self, monkeypatch):
        dps = [_DpStub(name=f"DP-{i:02d}") for i in range(10)]
        reg = _RegistryStub(dps=dps)
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        result = await dp_api.list_datapoints(page=1, size=3, sort="name", order="asc", _user="admin")
        assert len(result.items) == 3
        assert result.page == 1

    @pytest.mark.asyncio
    async def test_sort_descending(self, monkeypatch):
        dp1 = _DpStub(name="Alpha")
        dp2 = _DpStub(name="Zeta")
        reg = _RegistryStub(dps=[dp1, dp2])
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        result = await dp_api.list_datapoints(page=0, size=50, sort="name", order="desc", _user="admin")
        assert result.items[0].name == "Zeta"


# ---------------------------------------------------------------------------
# datapoints.py — create_datapoint
# ---------------------------------------------------------------------------


class TestCreateDatapoint:
    @pytest.mark.asyncio
    async def test_create_valid_datapoint(self, monkeypatch):
        reg = _RegistryStub()
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        from obs.models.types import DataTypeRegistry

        monkeypatch.setattr(DataTypeRegistry, "is_registered", lambda dt: True)
        body = DataPointCreate(name="Test DP", data_type="FLOAT")
        result = await dp_api.create_datapoint(body=body, _user="admin")
        assert result.name == "Test DP"

    @pytest.mark.asyncio
    async def test_create_invalid_data_type_raises_422(self, monkeypatch):
        reg = _RegistryStub()
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        from obs.models.types import DataTypeRegistry

        monkeypatch.setattr(DataTypeRegistry, "is_registered", lambda dt: False)
        monkeypatch.setattr(DataTypeRegistry, "names", lambda: ["FLOAT", "INT"])
        body = DataPointCreate(name="Test DP", data_type="INVALID")
        with pytest.raises(HTTPException) as exc:
            await dp_api.create_datapoint(body=body, _user="admin")
        assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# datapoints.py — get_datapoint
# ---------------------------------------------------------------------------


class TestGetDatapoint:
    @pytest.mark.asyncio
    async def test_get_existing_datapoint(self, monkeypatch):
        dp = _DpStub()
        reg = _RegistryStub(dps=[dp])
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        result = await dp_api.get_datapoint(dp_id=dp.id, _user="admin")
        assert result.id == dp.id

    @pytest.mark.asyncio
    async def test_get_nonexistent_raises_404(self, monkeypatch):
        monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub())
        with pytest.raises(HTTPException) as exc:
            await dp_api.get_datapoint(dp_id=uuid.uuid4(), _user="admin")
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# datapoints.py — update_datapoint
# ---------------------------------------------------------------------------


class TestUpdateDatapoint:
    @pytest.mark.asyncio
    async def test_update_existing(self, monkeypatch):
        dp = _DpStub(name="Old Name")
        reg = _RegistryStub(dps=[dp])
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        body = DataPointUpdate(name="New Name")
        result = await dp_api.update_datapoint(dp_id=dp.id, body=body, _user="admin")
        assert result.name == "New Name"

    @pytest.mark.asyncio
    async def test_update_nonexistent_raises_404(self, monkeypatch):
        monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub())
        body = DataPointUpdate(name="X")
        with pytest.raises(HTTPException) as exc:
            await dp_api.update_datapoint(dp_id=uuid.uuid4(), body=body, _user="admin")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_invalid_data_type_raises_422(self, monkeypatch):
        dp = _DpStub()
        reg = _RegistryStub(dps=[dp])
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        from obs.models.types import DataTypeRegistry

        monkeypatch.setattr(DataTypeRegistry, "is_registered", lambda dt: False)
        body = DataPointUpdate(data_type="INVALID")
        with pytest.raises(HTTPException) as exc:
            await dp_api.update_datapoint(dp_id=dp.id, body=body, _user="admin")
        assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# datapoints.py — delete_datapoint
# ---------------------------------------------------------------------------


class TestDeleteDatapoint:
    @pytest.mark.asyncio
    async def test_delete_existing(self, monkeypatch):
        dp = _DpStub()
        reg = _RegistryStub(dps=[dp])
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        await dp_api.delete_datapoint(dp_id=dp.id, _user="admin")
        assert reg.get(dp.id) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises_404(self, monkeypatch):
        monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub())
        with pytest.raises(HTTPException) as exc:
            await dp_api.delete_datapoint(dp_id=uuid.uuid4(), _user="admin")
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# datapoints.py — get_value endpoint (authenticated path)
# ---------------------------------------------------------------------------


class TestGetValue:
    @pytest.mark.asyncio
    async def test_get_value_authenticated(self, monkeypatch):
        from obs.core.registry import ValueState

        dp = _DpStub()
        state = ValueState()
        state.update(42.0, "good")
        reg = _RegistryStub(dps=[dp])
        reg._values[dp.id] = state
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)

        request = MagicMock()
        db = _DbStub()
        result = await dp_api.get_value(dp_id=dp.id, request=request, user="admin", db=db)
        assert result.value == 42.0
        assert result.quality == "good"

    @pytest.mark.asyncio
    async def test_get_value_no_state(self, monkeypatch):
        dp = _DpStub()
        reg = _RegistryStub(dps=[dp])
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)

        request = MagicMock()
        db = _DbStub()
        result = await dp_api.get_value(dp_id=dp.id, request=request, user="admin", db=db)
        assert result.value is None
        assert result.quality == "uncertain"

    @pytest.mark.asyncio
    async def test_get_value_not_found_raises_404(self, monkeypatch):
        monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub())
        request = MagicMock()
        db = _DbStub()
        with pytest.raises(HTTPException) as exc:
            await dp_api.get_value(dp_id=uuid.uuid4(), request=request, user="admin", db=db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_value_unauthenticated_no_page_id_raises_401(self, monkeypatch):
        dp = _DpStub()
        reg = _RegistryStub(dps=[dp])
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)

        request = MagicMock()
        request.headers = {}
        db = _DbStub()
        with pytest.raises(HTTPException) as exc:
            await dp_api.get_value(dp_id=dp.id, request=request, user=None, db=db)
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# datapoints.py — write_value endpoint
# ---------------------------------------------------------------------------


class TestWriteValue:
    @pytest.mark.asyncio
    async def test_write_value_authenticated(self, monkeypatch):
        from obs.api.v1.datapoints import WriteValueIn

        dp = _DpStub()
        reg = _RegistryStub(dps=[dp])
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)

        bus_mock = MagicMock()
        bus_mock.publish = AsyncMock()

        # write_value imports get_event_bus locally, so patch at source module
        import obs.core.event_bus as event_bus_mod

        monkeypatch.setattr(event_bus_mod, "get_event_bus", lambda: bus_mock)

        request = MagicMock()
        db = _DbStub()
        body = WriteValueIn(value=22.5)
        await dp_api.write_value(dp_id=dp.id, body=body, request=request, user="admin", db=db)
        bus_mock.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_write_value_not_found_raises_404(self, monkeypatch):
        from obs.api.v1.datapoints import WriteValueIn

        monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub())
        request = MagicMock()
        db = _DbStub()
        body = WriteValueIn(value=1)
        with pytest.raises(HTTPException) as exc:
            await dp_api.write_value(dp_id=uuid.uuid4(), body=body, request=request, user="admin", db=db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_write_value_unauthenticated_no_page_raises_401(self, monkeypatch):
        from obs.api.v1.datapoints import WriteValueIn

        dp = _DpStub()
        reg = _RegistryStub(dps=[dp])
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)

        request = MagicMock()
        request.headers = {}
        db = _DbStub()
        body = WriteValueIn(value=1)
        with pytest.raises(HTTPException) as exc:
            await dp_api.write_value(dp_id=dp.id, body=body, request=request, user=None, db=db)
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# datapoints.py — _enrich helper (bytes value)
# ---------------------------------------------------------------------------


class TestEnrichHelper:
    def test_bytes_value_serialized_as_hex(self, monkeypatch):
        from obs.core.registry import ValueState

        dp = _DpStub()
        state = ValueState()
        state.update(b"\xde\xad\xbe\xef", "good")
        reg = _RegistryStub(dps=[dp])
        reg._values[dp.id] = state
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)

        out = dp_api._enrich(dp)
        # Calling model serialization
        data = out.model_dump()
        assert data["value"] == "deadbeef"


# ---------------------------------------------------------------------------
# bindings.py — _row_out helper
# ---------------------------------------------------------------------------


class TestRowOut:
    def test_row_out_full(self):
        bid = str(uuid.uuid4())
        dp_id = str(uuid.uuid4())
        inst_id = str(uuid.uuid4())
        row = _make_row(
            id=bid,
            datapoint_id=dp_id,
            adapter_type="KNX",
            adapter_instance_id=inst_id,
            direction="SOURCE",
            config='{"group_address": "1/1/1"}',
            enabled=1,
            send_throttle_ms=500,
            send_on_change=0,
            send_min_delta=None,
            send_min_delta_pct=None,
            value_formula=None,
            value_map=None,
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )
        name_map = {inst_id: "My KNX Instance"}
        out = bindings_api._row_out(row, name_map)
        assert out.adapter_type == "KNX"
        assert out.send_throttle_ms == 500
        assert out.instance_name == "My KNX Instance"

    def test_row_out_no_instance(self):
        bid = str(uuid.uuid4())
        dp_id = str(uuid.uuid4())
        row = _make_row(
            id=bid,
            datapoint_id=dp_id,
            adapter_type="MQTT",
            adapter_instance_id=None,
            direction="DEST",
            config="{}",
            enabled=1,
            send_throttle_ms=None,
            send_on_change=1,
            send_min_delta=0.5,
            send_min_delta_pct=5.0,
            value_formula="x * 2",
            value_map='{"0": "off", "1": "on"}',
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )
        out = bindings_api._row_out(row, None)
        assert out.adapter_instance_id is None
        assert out.send_on_change is True
        assert out.value_map == {"0": "off", "1": "on"}


# ---------------------------------------------------------------------------
# bindings.py — list_bindings endpoint
# ---------------------------------------------------------------------------


class TestListBindings:
    @pytest.mark.asyncio
    async def test_list_bindings_dp_not_found(self, monkeypatch):
        monkeypatch.setattr(bindings_api, "get_registry", lambda: _RegistryStub())
        db = _DbStub()
        with pytest.raises(HTTPException) as exc:
            await bindings_api.list_bindings(dp_id=uuid.uuid4(), _user="admin", db=db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_list_bindings_returns_empty(self, monkeypatch):
        dp = _DpStub()
        reg = _RegistryStub(dps=[dp])
        monkeypatch.setattr(bindings_api, "get_registry", lambda: reg)
        db = _DbStub(rows=[])
        result = await bindings_api.list_bindings(dp_id=dp.id, _user="admin", db=db)
        assert result == []


# ---------------------------------------------------------------------------
# bindings.py — create_binding endpoint
# ---------------------------------------------------------------------------


class TestCreateBinding:
    @pytest.mark.asyncio
    async def test_create_binding_dp_not_found(self, monkeypatch):
        monkeypatch.setattr(bindings_api, "get_registry", lambda: _RegistryStub())
        db = _DbStub()
        body = AdapterBindingCreate(adapter_instance_id=uuid.uuid4(), direction="SOURCE")
        with pytest.raises(HTTPException) as exc:
            await bindings_api.create_binding(dp_id=uuid.uuid4(), body=body, _user="admin", db=db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_create_binding_instance_not_found(self, monkeypatch):
        dp = _DpStub()
        reg = _RegistryStub(dps=[dp])
        monkeypatch.setattr(bindings_api, "get_registry", lambda: reg)
        db = _DbStub(fetchone_result=None)
        body = AdapterBindingCreate(adapter_instance_id=uuid.uuid4(), direction="SOURCE")
        with pytest.raises(HTTPException) as exc:
            await bindings_api.create_binding(dp_id=dp.id, body=body, _user="admin", db=db)
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_create_binding_success(self, monkeypatch):
        dp = _DpStub()
        reg = _RegistryStub(dps=[dp])
        monkeypatch.setattr(bindings_api, "get_registry", lambda: reg)

        inst_id = uuid.uuid4()
        binding_id = str(uuid.uuid4())
        instance_row = _make_row(id=str(inst_id), adapter_type="MQTT", name="Test MQTT")
        binding_row = _make_row(
            id=binding_id,
            datapoint_id=str(dp.id),
            adapter_type="MQTT",
            adapter_instance_id=str(inst_id),
            direction="SOURCE",
            config="{}",
            enabled=1,
            send_throttle_ms=None,
            send_on_change=0,
            send_min_delta=None,
            send_min_delta_pct=None,
            value_formula=None,
            value_map=None,
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )
        inst_name_row = _make_row(id=str(inst_id), name="Test MQTT")

        db = _DbStub(rows=[inst_name_row])
        db._fetchone_side_effects = [instance_row, binding_row]

        # Mock adapter class lookup
        from obs.adapters import registry as adapter_registry

        monkeypatch.setattr(adapter_registry, "get_class", lambda at: None)
        monkeypatch.setattr(bindings_api, "_reload_adapter_instance", AsyncMock())

        body = AdapterBindingCreate(adapter_instance_id=inst_id, direction="SOURCE")
        result = await bindings_api.create_binding(dp_id=dp.id, body=body, _user="admin", db=db)
        assert result.adapter_type == "MQTT"


# ---------------------------------------------------------------------------
# bindings.py — update_binding endpoint
# ---------------------------------------------------------------------------


class TestUpdateBinding:
    @pytest.mark.asyncio
    async def test_update_binding_not_found_raises_404(self, monkeypatch):
        db = _DbStub(fetchone_result=None)
        body = AdapterBindingUpdate(enabled=False)
        with pytest.raises(HTTPException) as exc:
            await bindings_api.update_binding(dp_id=uuid.uuid4(), binding_id=uuid.uuid4(), body=body, _user="admin", db=db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_binding_success(self, monkeypatch):
        dp_id = uuid.uuid4()
        bid = uuid.uuid4()
        inst_id = str(uuid.uuid4())
        current_row = _make_row(
            id=str(bid),
            datapoint_id=str(dp_id),
            adapter_type="KNX",
            adapter_instance_id=inst_id,
            direction="SOURCE",
            config="{}",
            enabled=1,
            send_throttle_ms=None,
            send_on_change=0,
            send_min_delta=None,
            send_min_delta_pct=None,
            value_formula=None,
            value_map=None,
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )
        updated_row = _make_row(
            id=str(bid),
            datapoint_id=str(dp_id),
            adapter_type="KNX",
            adapter_instance_id=inst_id,
            direction="SOURCE",
            config="{}",
            enabled=0,
            send_throttle_ms=None,
            send_on_change=0,
            send_min_delta=None,
            send_min_delta_pct=None,
            value_formula=None,
            value_map=None,
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-02T00:00:00",
        )
        inst_name_row = _make_row(id=inst_id, name="KNX Instance")

        db = _DbStub(rows=[inst_name_row])
        db._fetchone_side_effects = [current_row, updated_row]

        monkeypatch.setattr(bindings_api, "_reload_adapter_instance", AsyncMock())

        body = AdapterBindingUpdate(enabled=False)
        result = await bindings_api.update_binding(dp_id=dp_id, binding_id=bid, body=body, _user="admin", db=db)
        assert result.enabled is False


# ---------------------------------------------------------------------------
# bindings.py — delete_binding endpoint
# ---------------------------------------------------------------------------


class TestDeleteBinding:
    @pytest.mark.asyncio
    async def test_delete_binding_not_found_raises_404(self, monkeypatch):
        db = _DbStub(fetchone_result=None)
        with pytest.raises(HTTPException) as exc:
            await bindings_api.delete_binding(dp_id=uuid.uuid4(), binding_id=uuid.uuid4(), _user="admin", db=db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_binding_success(self, monkeypatch):
        inst_id = str(uuid.uuid4())
        row = _make_row(adapter_instance_id=inst_id)
        db = _DbStub(fetchone_result=row)
        monkeypatch.setattr(bindings_api, "_reload_adapter_instance", AsyncMock())
        await bindings_api.delete_binding(dp_id=uuid.uuid4(), binding_id=uuid.uuid4(), _user="admin", db=db)
        assert any("DELETE" in q for q, _ in db.executed)

    @pytest.mark.asyncio
    async def test_delete_binding_no_instance(self, monkeypatch):
        row = _make_row(adapter_instance_id=None)
        db = _DbStub(fetchone_result=row)
        reload_mock = AsyncMock()
        monkeypatch.setattr(bindings_api, "_reload_adapter_instance", reload_mock)
        await bindings_api.delete_binding(dp_id=uuid.uuid4(), binding_id=uuid.uuid4(), _user="admin", db=db)
        reload_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# search.py — search endpoint
# ---------------------------------------------------------------------------


class TestSearchEndpoint:
    """All search tests patch _add_hierarchy to avoid hierarchy DB query complexity."""

    @pytest.mark.asyncio
    async def test_search_no_filters(self, monkeypatch):
        dp = _DpStub(name="Living Room Temp")
        reg = _RegistryStub(dps=[dp])
        monkeypatch.setattr(search_api, "get_registry", lambda: reg)
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        monkeypatch.setattr(search_api, "_add_hierarchy", AsyncMock())
        db = _DbStub(rows=[])
        result = await search_api.search(
            q="",
            tag="",
            type="",
            adapter="",
            quality="",
            node_id="",
            tree_id="",
            sort="name",
            order="asc",
            page=0,
            size=50,
            _user="admin",
            db=db,
        )
        assert result.total == 1
        assert result.items[0].name == "Living Room Temp"

    @pytest.mark.asyncio
    async def test_search_by_type(self, monkeypatch):
        dp_float = _DpStub(name="Temp", data_type="FLOAT")
        dp_bool = _DpStub(name="Switch", data_type="BOOL")
        reg = _RegistryStub(dps=[dp_float, dp_bool])
        monkeypatch.setattr(search_api, "get_registry", lambda: reg)
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        monkeypatch.setattr(search_api, "_add_hierarchy", AsyncMock())
        db = _DbStub(rows=[])
        result = await search_api.search(
            q="",
            tag="",
            type="FLOAT",
            adapter="",
            quality="",
            node_id="",
            tree_id="",
            sort="name",
            order="asc",
            page=0,
            size=50,
            _user="admin",
            db=db,
        )
        assert result.total == 1
        assert result.items[0].data_type == "FLOAT"

    @pytest.mark.asyncio
    async def test_search_by_tag(self, monkeypatch):
        dp1 = _DpStub(name="Heater", tags=["heating"])
        dp2 = _DpStub(name="Light", tags=["lighting"])
        reg = _RegistryStub(dps=[dp1, dp2])
        monkeypatch.setattr(search_api, "get_registry", lambda: reg)
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        monkeypatch.setattr(search_api, "_add_hierarchy", AsyncMock())
        db = _DbStub(rows=[])
        result = await search_api.search(
            q="",
            tag="heating",
            type="",
            adapter="",
            quality="",
            node_id="",
            tree_id="",
            sort="name",
            order="asc",
            page=0,
            size=50,
            _user="admin",
            db=db,
        )
        assert result.total == 1
        assert result.items[0].name == "Heater"

    @pytest.mark.asyncio
    async def test_search_by_tag_comma_separated(self, monkeypatch):
        dp1 = _DpStub(name="Heater", tags=["heating"])
        dp2 = _DpStub(name="Light", tags=["lighting"])
        dp3 = _DpStub(name="Pump", tags=["water"])
        reg = _RegistryStub(dps=[dp1, dp2, dp3])
        monkeypatch.setattr(search_api, "get_registry", lambda: reg)
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        monkeypatch.setattr(search_api, "_add_hierarchy", AsyncMock())
        db = _DbStub(rows=[])
        result = await search_api.search(
            q="",
            tag="heating,lighting",
            type="",
            adapter="",
            quality="",
            node_id="",
            tree_id="",
            sort="name",
            order="asc",
            page=0,
            size=50,
            _user="admin",
            db=db,
        )
        assert result.total == 2

    @pytest.mark.asyncio
    async def test_search_by_name_query(self, monkeypatch):
        dp1 = _DpStub(name="Living Room Temperature")
        dp2 = _DpStub(name="Bedroom Light")
        reg = _RegistryStub(dps=[dp1, dp2])
        monkeypatch.setattr(search_api, "get_registry", lambda: reg)
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        monkeypatch.setattr(search_api, "_add_hierarchy", AsyncMock())
        # q-filter uses fetchall for binding configs, then _add_hierarchy is patched
        config_rows = [_make_row(datapoint_id=str(dp1.id), config="{}"), _make_row(datapoint_id=str(dp2.id), config="{}")]
        db = _DbStub(rows=config_rows)
        result = await search_api.search(
            q="temperature",
            tag="",
            type="",
            adapter="",
            quality="",
            node_id="",
            tree_id="",
            sort="name",
            order="asc",
            page=0,
            size=50,
            _user="admin",
            db=db,
        )
        assert result.total == 1
        assert "temperature" in result.items[0].name.lower()

    @pytest.mark.asyncio
    async def test_search_by_adapter(self, monkeypatch):
        dp1 = _DpStub(name="KNX DP")
        dp2 = _DpStub(name="MQTT DP")
        reg = _RegistryStub(dps=[dp1, dp2])
        monkeypatch.setattr(search_api, "get_registry", lambda: reg)
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        monkeypatch.setattr(search_api, "_add_hierarchy", AsyncMock())
        # adapter filter uses fetchall, return matching dp ids
        adapter_rows = [_make_row(datapoint_id=str(dp1.id))]
        db = _DbStub(rows=adapter_rows)
        result = await search_api.search(
            q="",
            tag="",
            type="",
            adapter="KNX",
            quality="",
            node_id="",
            tree_id="",
            sort="name",
            order="asc",
            page=0,
            size=50,
            _user="admin",
            db=db,
        )
        assert result.total == 1
        assert result.items[0].name == "KNX DP"

    @pytest.mark.asyncio
    async def test_search_by_quality(self, monkeypatch):
        from obs.core.registry import ValueState

        dp1 = _DpStub(name="Good DP")
        dp2 = _DpStub(name="Bad DP")
        good_state = ValueState()
        good_state.update(1, "good")
        bad_state = ValueState()
        bad_state.update(0, "bad")

        reg = _RegistryStub(dps=[dp1, dp2])
        reg._values[dp1.id] = good_state
        reg._values[dp2.id] = bad_state
        monkeypatch.setattr(search_api, "get_registry", lambda: reg)
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        monkeypatch.setattr(search_api, "_add_hierarchy", AsyncMock())
        db = _DbStub(rows=[])
        result = await search_api.search(
            q="",
            tag="",
            type="",
            adapter="",
            quality="good",
            node_id="",
            tree_id="",
            sort="name",
            order="asc",
            page=0,
            size=50,
            _user="admin",
            db=db,
        )
        assert result.total == 1
        assert result.items[0].name == "Good DP"

    @pytest.mark.asyncio
    async def test_search_result_query_metadata(self, monkeypatch):
        monkeypatch.setattr(search_api, "get_registry", lambda: _RegistryStub())
        monkeypatch.setattr(dp_api, "get_registry", lambda: _RegistryStub())
        monkeypatch.setattr(search_api, "_add_hierarchy", AsyncMock())
        db = _DbStub(rows=[])
        result = await search_api.search(
            q="test",
            tag="tag1",
            type="FLOAT",
            adapter="",
            quality="",
            node_id="",
            tree_id="",
            sort="name",
            order="desc",
            page=0,
            size=50,
            _user="admin",
            db=db,
        )
        assert result.query["q"] == "test"
        assert result.query["tag"] == "tag1"
        assert result.query["sort"] == "name"
        assert result.query["order"] == "desc"

    @pytest.mark.asyncio
    async def test_search_pagination(self, monkeypatch):
        dps = [_DpStub(name=f"DP-{i:03d}") for i in range(15)]
        reg = _RegistryStub(dps=dps)
        monkeypatch.setattr(search_api, "get_registry", lambda: reg)
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        monkeypatch.setattr(search_api, "_add_hierarchy", AsyncMock())
        db = _DbStub(rows=[])
        result = await search_api.search(
            q="",
            tag="",
            type="",
            adapter="",
            quality="",
            node_id="",
            tree_id="",
            sort="name",
            order="asc",
            page=1,
            size=5,
            _user="admin",
            db=db,
        )
        assert len(result.items) == 5
        assert result.total == 15
        assert result.pages == 3

    @pytest.mark.asyncio
    async def test_search_by_node_id(self, monkeypatch):
        dp1 = _DpStub(name="Living Room")
        dp2 = _DpStub(name="Kitchen")
        reg = _RegistryStub(dps=[dp1, dp2])
        monkeypatch.setattr(search_api, "get_registry", lambda: reg)
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        monkeypatch.setattr(search_api, "_add_hierarchy", AsyncMock())
        node_rows = [_make_row(datapoint_id=str(dp1.id))]
        db = _DbStub(rows=node_rows)
        result = await search_api.search(
            q="",
            tag="",
            type="",
            adapter="",
            quality="",
            node_id="some-node-id",
            tree_id="",
            sort="name",
            order="asc",
            page=0,
            size=50,
            _user="admin",
            db=db,
        )
        assert result.total == 1
        assert result.items[0].name == "Living Room"

    @pytest.mark.asyncio
    async def test_search_by_tree_id(self, monkeypatch):
        dp1 = _DpStub(name="Floor 1")
        dp2 = _DpStub(name="Floor 2")
        reg = _RegistryStub(dps=[dp1, dp2])
        monkeypatch.setattr(search_api, "get_registry", lambda: reg)
        monkeypatch.setattr(dp_api, "get_registry", lambda: reg)
        monkeypatch.setattr(search_api, "_add_hierarchy", AsyncMock())
        tree_rows = [_make_row(datapoint_id=str(dp1.id))]
        db = _DbStub(rows=tree_rows)
        result = await search_api.search(
            q="",
            tag="",
            type="",
            adapter="",
            quality="",
            node_id="",
            tree_id="some-tree-id",
            sort="name",
            order="asc",
            page=0,
            size=50,
            _user="admin",
            db=db,
        )
        assert result.total == 1
        assert result.items[0].name == "Floor 1"


# ---------------------------------------------------------------------------
# search.py — _add_hierarchy helper
# ---------------------------------------------------------------------------


class TestAddHierarchy:
    @pytest.mark.asyncio
    async def test_add_hierarchy_empty_items(self):
        db = _DbStub(rows=[])
        await search_api._add_hierarchy([], db)  # should not raise

    @pytest.mark.asyncio
    async def test_add_hierarchy_with_nodes(self):
        dp = _DpStub()
        from obs.api.v1.datapoints import DataPointOut

        item = DataPointOut(
            id=dp.id,
            name=dp.name,
            data_type=dp.data_type,
            unit=dp.unit,
            tags=dp.tags,
            mqtt_topic=dp.mqtt_topic,
            mqtt_alias=dp.mqtt_alias,
            persist_value=dp.persist_value,
            record_history=dp.record_history,
            created_at=dp.created_at.isoformat(),
            updated_at=dp.updated_at.isoformat(),
        )

        node_id = str(uuid.uuid4())
        tree_id = str(uuid.uuid4())
        hier_rows = [
            _make_row(
                datapoint_id=str(dp.id),
                node_id=node_id,
                node_name="Wohnzimmer",
                tree_id=tree_id,
                tree_name="Gebäude",
                display_depth=2,
            )
        ]

        call_count = 0

        async def _mock_fetchall(query, params=()):
            nonlocal call_count
            call_count += 1
            # First call: hierarchy_datapoint_links query → return node rows
            # Second call: recursive CTE for ancestor paths → return empty
            if call_count == 1:
                return hier_rows
            return []

        db = MagicMock()
        db.fetchall = _mock_fetchall

        await search_api._add_hierarchy([item], db)
        assert len(item.hierarchy_nodes) == 1
        assert item.hierarchy_nodes[0].node_name == "Wohnzimmer"
