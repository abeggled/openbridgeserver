"""Contract tests for python-jose — verifies the API surface used by obs.api.auth."""

from __future__ import annotations

import pytest
from jose import JWTError, jwt


class TestJwtEncodeDecode:
    def test_encode_returns_string(self):
        token = jwt.encode({"sub": "testuser"}, "secret", algorithm="HS256")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_decode_roundtrip(self):
        claims = {"sub": "testuser", "role": "admin"}
        token = jwt.encode(claims, "secret", algorithm="HS256")
        decoded = jwt.decode(token, "secret", algorithms=["HS256"])
        assert decoded["sub"] == "testuser"
        assert decoded["role"] == "admin"

    def test_wrong_secret_raises_jwt_error(self):
        token = jwt.encode({"sub": "user"}, "correct_secret", algorithm="HS256")
        with pytest.raises(JWTError):
            jwt.decode(token, "wrong_secret", algorithms=["HS256"])

    def test_encode_with_expiry(self):
        from datetime import UTC, datetime, timedelta

        exp = datetime.now(UTC) + timedelta(hours=1)
        token = jwt.encode({"sub": "user", "exp": exp}, "secret", algorithm="HS256")
        decoded = jwt.decode(token, "secret", algorithms=["HS256"])
        assert decoded["sub"] == "user"

    def test_expired_token_raises_jwt_error(self):
        from datetime import UTC, datetime, timedelta

        exp = datetime.now(UTC) - timedelta(seconds=1)
        token = jwt.encode({"sub": "user", "exp": exp}, "secret", algorithm="HS256")
        with pytest.raises(JWTError):
            jwt.decode(token, "secret", algorithms=["HS256"])

    def test_token_has_three_parts(self):
        token = jwt.encode({"sub": "user"}, "secret", algorithm="HS256")
        assert token.count(".") == 2, "JWT must have exactly 3 dot-separated segments"


class TestJwtError:
    def test_jwt_error_is_exception(self):
        assert issubclass(JWTError, Exception)

    def test_jwt_error_importable_from_jose(self):
        from jose import JWTError as _JWTError  # noqa: F401
