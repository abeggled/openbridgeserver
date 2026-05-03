"""Contract tests for slowapi — verifies the API surface used by obs.api.auth and obs.main.

Production usage:
  from slowapi import Limiter, _rate_limit_exceeded_handler
  from slowapi.errors import RateLimitExceeded
  from slowapi.util import get_remote_address
  limiter = Limiter(key_func=get_remote_address)
  app.state.limiter = limiter
  app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
"""

from __future__ import annotations

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


class TestLimiter:
    def test_limiter_importable(self):
        assert Limiter is not None

    def test_limiter_constructor_accepts_key_func(self):
        limiter = Limiter(key_func=get_remote_address)
        assert limiter is not None

    def test_limiter_has_limit_decorator(self):
        limiter = Limiter(key_func=get_remote_address)
        assert hasattr(limiter, "limit"), "slowapi.Limiter no longer has a 'limit' method. This is used as @limiter.limit('X/minute') in auth routes."


class TestRateLimitExceeded:
    def test_importable(self):
        assert RateLimitExceeded is not None

    def test_is_exception(self):
        assert issubclass(RateLimitExceeded, Exception), (
            "slowapi.errors.RateLimitExceeded must be an Exception subclass. "
            "obs/main.py registers it as an exception handler: "
            "app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)"
        )


class TestRateLimitExceededHandler:
    def test_importable(self):
        assert _rate_limit_exceeded_handler is not None

    def test_is_callable(self):
        assert callable(_rate_limit_exceeded_handler)


class TestGetRemoteAddress:
    def test_importable(self):
        assert get_remote_address is not None

    def test_is_callable(self):
        assert callable(get_remote_address)
