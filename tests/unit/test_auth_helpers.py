"""Unit Tests — auth.py helper functions

Covers:
  verify_password  — malformed stored hash returns False  (lines 70-71)
  hash_password    — round-trip with verify_password
  hash_api_key     — deterministic SHA-256
  generate_api_key — correct prefix and length
"""

from __future__ import annotations

from obs.api.auth import generate_api_key, hash_api_key, hash_password, verify_password


# ---------------------------------------------------------------------------
# verify_password
# ---------------------------------------------------------------------------


def test_verify_password_correct():
    stored = hash_password("secret")
    assert verify_password("secret", stored) is True


def test_verify_password_wrong():
    stored = hash_password("secret")
    assert verify_password("wrong", stored) is False


def test_verify_password_malformed_hash_returns_false():
    # Line 70-71: the except branch — not a valid pbkdf2$… string
    assert verify_password("anything", "notahash") is False


def test_verify_password_empty_stored_returns_false():
    assert verify_password("secret", "") is False


def test_verify_password_partial_hash_returns_false():
    assert verify_password("secret", "pbkdf2$260000$badhex") is False


# ---------------------------------------------------------------------------
# hash_password uniqueness
# ---------------------------------------------------------------------------


def test_hash_password_uses_random_salt():
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != h2  # different salts each call


def test_hash_password_format():
    h = hash_password("pw")
    parts = h.split("$")
    assert parts[0] == "pbkdf2"
    assert parts[1] == "260000"


# ---------------------------------------------------------------------------
# hash_api_key
# ---------------------------------------------------------------------------


def test_hash_api_key_deterministic():
    key = "obs_" + "a" * 64
    assert hash_api_key(key) == hash_api_key(key)


def test_hash_api_key_different_inputs_differ():
    assert hash_api_key("key1") != hash_api_key("key2")


# ---------------------------------------------------------------------------
# generate_api_key
# ---------------------------------------------------------------------------


def test_generate_api_key_prefix():
    assert generate_api_key().startswith("obs_")


def test_generate_api_key_length():
    key = generate_api_key()
    # "obs_" + 64 hex chars
    assert len(key) == 4 + 64


def test_generate_api_key_unique():
    assert generate_api_key() != generate_api_key()
