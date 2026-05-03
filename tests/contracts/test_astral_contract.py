"""Contract tests for astral — verifies the API surface used by
obs.adapters.zeitschaltuhr and obs.logic.executor.

Imports used in production code:
  from astral import LocationInfo
  from astral import SunDirection
  from astral.sun import sun
  from astral.sun import time_at_elevation
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

astral = pytest.importorskip("astral", reason="astral not installed")

from astral import LocationInfo, SunDirection
from astral.sun import sun, time_at_elevation


_BERLIN = LocationInfo(
    name="Berlin",
    region="Germany",
    timezone="Europe/Berlin",
    latitude=52.52,
    longitude=13.405,
)


class TestLocationInfo:
    def test_construction(self):
        loc = LocationInfo("TestCity", "TestRegion", "UTC", 48.0, 11.0)
        assert loc.name == "TestCity"
        assert loc.latitude == 48.0
        assert loc.longitude == 11.0

    def test_has_observer(self):
        assert hasattr(_BERLIN, "observer"), (
            "astral.LocationInfo no longer has an 'observer' attribute. zeitschaltuhr passes location.observer to sun() and time_at_elevation()."
        )

    def test_observer_has_latitude_longitude(self):
        obs = _BERLIN.observer
        assert hasattr(obs, "latitude")
        assert hasattr(obs, "longitude")


class TestSunFunction:
    def test_returns_dict_with_sunrise_sunset(self):
        result = sun(_BERLIN.observer, date=date(2024, 6, 21))
        assert isinstance(result, dict), "astral.sun.sun() must return a dict"
        assert "sunrise" in result, "sun() result missing 'sunrise' key"
        assert "sunset" in result, "sun() result missing 'sunset' key"

    def test_sunrise_is_datetime(self):
        result = sun(_BERLIN.observer, date=date(2024, 6, 21))
        assert isinstance(result["sunrise"], datetime)

    def test_sunset_is_datetime(self):
        result = sun(_BERLIN.observer, date=date(2024, 6, 21))
        assert isinstance(result["sunset"], datetime)

    def test_sunrise_before_sunset(self):
        result = sun(_BERLIN.observer, date=date(2024, 6, 21))
        assert result["sunrise"] < result["sunset"]

    def test_accepts_tzinfo_kwarg(self):
        result = sun(_BERLIN.observer, date=date(2024, 6, 21), tzinfo=timezone.utc)
        assert "sunrise" in result


class TestSunDirection:
    def test_rising_exists(self):
        assert hasattr(SunDirection, "RISING"), (
            "astral.SunDirection.RISING no longer exists. Used in obs/adapters/zeitschaltuhr/adapter.py for time_at_elevation()."
        )

    def test_setting_exists(self):
        assert hasattr(SunDirection, "SETTING"), "astral.SunDirection.SETTING no longer exists."


class TestTimeAtElevation:
    def test_callable(self):
        assert callable(time_at_elevation)

    def test_returns_datetime(self):
        result = time_at_elevation(
            _BERLIN.observer,
            elevation=6.0,
            date=date(2024, 6, 21),
            direction=SunDirection.RISING,
        )
        assert isinstance(result, datetime), (
            "astral.sun.time_at_elevation() must return a datetime. Used in zeitschaltuhr for civil/nautical/astronomical twilight calculations."
        )
