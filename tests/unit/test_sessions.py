"""Unit Tests — sessions.py (in-memory PIN session store)

Covers:
  create_session  — token generation + storage
  validate_session — success, wrong token, wrong node_id, expired
"""

from __future__ import annotations

import time

import pytest

from obs.api.v1 import sessions as _sessions_module
from obs.api.v1.sessions import create_session, validate_session


@pytest.fixture(autouse=True)
def _clear_sessions():
    """Isolate each test — clear the in-memory store before and after."""
    _sessions_module._sessions.clear()
    yield
    _sessions_module._sessions.clear()


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


def test_create_session_returns_token():
    token = create_session("node-1")
    assert isinstance(token, str)
    assert len(token) > 0


def test_create_session_unique_tokens():
    t1 = create_session("node-1")
    t2 = create_session("node-1")
    assert t1 != t2


def test_create_session_stored():
    token = create_session("node-abc")
    assert token in _sessions_module._sessions


def test_create_session_stores_correct_node_id():
    token = create_session("node-xyz")
    stored_node_id, _ = _sessions_module._sessions[token]
    assert stored_node_id == "node-xyz"


def test_create_session_custom_expiry():
    token = create_session("node-1", expires_in=7200)
    _, expires_at = _sessions_module._sessions[token]
    assert expires_at > time.time() + 7100


# ---------------------------------------------------------------------------
# validate_session — success
# ---------------------------------------------------------------------------


def test_validate_session_success():
    token = create_session("page-1")
    assert validate_session(token, "page-1") is True


# ---------------------------------------------------------------------------
# validate_session — failure paths
# ---------------------------------------------------------------------------


def test_validate_session_unknown_token():
    assert validate_session("nonexistent-token", "page-1") is False


def test_validate_session_wrong_node_id():
    token = create_session("page-correct")
    assert validate_session(token, "page-wrong") is False


def test_validate_session_expired_returns_false():
    token = create_session("page-1", expires_in=-1)  # already expired
    assert validate_session(token, "page-1") is False


def test_validate_session_expired_removes_entry():
    token = create_session("page-1", expires_in=-1)
    validate_session(token, "page-1")
    assert token not in _sessions_module._sessions


def test_validate_session_does_not_remove_valid_session():
    token = create_session("page-1", expires_in=3600)
    validate_session(token, "page-1")
    assert token in _sessions_module._sessions


def test_sessions_module_exposes_no_http_router_surface():
    assert not hasattr(_sessions_module, "router")
