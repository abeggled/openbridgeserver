from __future__ import annotations

import pytest
from fastapi import HTTPException

from obs.api.auth import Principal, get_admin_user, get_current_principal, get_current_user, optional_current_user


class _DbStub:
    def __init__(self, *, api_key_row: dict | None = None, user_is_admin: int | None = None) -> None:
        self.api_key_row = api_key_row
        self.user_is_admin = user_is_admin
        self.updated = False

    async def fetchone(self, query: str, params: tuple):
        if "FROM api_keys" in query:
            return self.api_key_row
        if "FROM users" in query:
            if self.user_is_admin is None:
                return None
            return {"is_admin": self.user_is_admin}
        raise AssertionError(f"Unexpected query: {query} params={params}")

    async def execute_and_commit(self, _query: str, _params: tuple) -> None:
        self.updated = True


@pytest.mark.asyncio
async def test_get_current_principal_user_has_type_subject_and_admin(monkeypatch):
    monkeypatch.setattr("obs.api.auth.decode_token", lambda token: "alice")
    db = _DbStub(user_is_admin=1)

    principal = await get_current_principal(credentials=type("Cred", (), {"credentials": "jwt"})(), api_key=None, db=db)

    assert principal == Principal(subject="alice", type="user", is_admin=True)


@pytest.mark.asyncio
async def test_get_current_principal_user_without_row_is_not_admin(monkeypatch):
    monkeypatch.setattr("obs.api.auth.decode_token", lambda token: "alice")
    db = _DbStub(user_is_admin=None)

    principal = await get_current_principal(credentials=type("Cred", (), {"credentials": "jwt"})(), api_key=None, db=db)

    assert principal == Principal(subject="alice", type="user", is_admin=False)


@pytest.mark.asyncio
async def test_get_current_principal_api_key_uses_non_username_subject(monkeypatch):
    monkeypatch.setattr("obs.api.auth.hash_api_key", lambda key: f"hash:{key}")
    db = _DbStub(api_key_row={"id": "3ff3e934-8d4d-45f6-b4d0-5f6f2272681d", "owner": "admin"})

    principal = await get_current_principal(credentials=None, api_key="obs_valid", db=db)

    assert principal == Principal(
        subject="api_key:3ff3e934-8d4d-45f6-b4d0-5f6f2272681d",
        type="api_key",
        is_admin=False,
        owner="admin",
    )
    assert db.updated is True


@pytest.mark.asyncio
async def test_get_current_principal_api_key_keeps_ownerless_legacy_key_owner_empty(monkeypatch):
    monkeypatch.setattr("obs.api.auth.hash_api_key", lambda key: f"hash:{key}")
    db = _DbStub(api_key_row={"id": "3ff3e934-8d4d-45f6-b4d0-5f6f2272681d", "owner": ""})

    principal = await get_current_principal(credentials=None, api_key="obs_valid", db=db)

    assert principal == Principal(
        subject="api_key:3ff3e934-8d4d-45f6-b4d0-5f6f2272681d",
        type="api_key",
        is_admin=False,
        owner=None,
    )
    assert db.updated is True


@pytest.mark.asyncio
async def test_get_current_principal_invalid_api_key_raises_401(monkeypatch):
    monkeypatch.setattr("obs.api.auth.hash_api_key", lambda key: f"hash:{key}")
    db = _DbStub(api_key_row=None)

    with pytest.raises(HTTPException) as exc:
        await get_current_principal(credentials=None, api_key="obs_invalid", db=db)

    assert exc.value.status_code == 401
    assert "Invalid API key" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_get_current_principal_without_any_auth_raises_401():
    db = _DbStub()

    with pytest.raises(HTTPException) as exc:
        await get_current_principal(credentials=None, api_key=None, db=db)

    assert exc.value.status_code == 401
    assert exc.value.headers == {"WWW-Authenticate": "Bearer"}


@pytest.mark.asyncio
async def test_get_current_user_keeps_legacy_string_for_user_principal(monkeypatch):
    async def _principal(*_args, **_kwargs):
        return Principal(subject="alice", type="user", is_admin=False)

    monkeypatch.setattr(
        "obs.api.auth.get_current_principal",
        _principal,
    )

    user = await get_current_user(credentials=None, api_key=None, db=None)

    assert user == "alice"


@pytest.mark.asyncio
async def test_optional_current_user_returns_none_on_auth_failure(monkeypatch):
    async def _raise(*_args, **_kwargs):
        raise HTTPException(status_code=401, detail="no auth")

    monkeypatch.setattr("obs.api.auth.get_current_user", _raise)

    user = await optional_current_user(credentials=None, api_key=None, db=None)

    assert user is None


@pytest.mark.asyncio
async def test_get_admin_user_rejects_api_key_even_with_admin_flag():
    principal = Principal(subject="ci-key", type="api_key", is_admin=True)

    with pytest.raises(HTTPException) as exc:
        await get_admin_user(principal=principal)

    assert exc.value.status_code == 403
    assert "Admin" in exc.value.detail


@pytest.mark.asyncio
async def test_get_admin_user_rejects_non_admin_user_principal():
    principal = Principal(subject="alice", type="user", is_admin=False)

    with pytest.raises(HTTPException) as exc:
        await get_admin_user(principal=principal)

    assert exc.value.status_code == 403
