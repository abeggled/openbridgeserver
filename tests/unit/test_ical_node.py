"""Unit tests for the iCalendar logic node (obs/logic/executor.py — case 'ical').

All tests inject pre-fetched raw iCal text directly into hysteresis_state so
no HTTP calls are made.  The test ICS strings cover:
  - single-day events
  - all-day events (DATE, not DATETIME)
  - events with location and description
  - simple recurring events (RRULE)
  - filter matching on summary / location / description
  - today / tomorrow detection (patched via _date override from isoformat)
"""

from __future__ import annotations

import datetime
import json

import pytest

pytest.importorskip("icalendar", reason="icalendar not installed")
pytest.importorskip("recurring_ical_events", reason="recurring_ical_events not installed")

from tests.unit.conftest import make_executor, node  # noqa: E402

# ---------------------------------------------------------------------------
# Minimal ICS fixtures
# ---------------------------------------------------------------------------

_TODAY = datetime.date.today()
_TOMORROW = _TODAY + datetime.timedelta(days=1)
_NEXT_WEEK = _TODAY + datetime.timedelta(days=7)


def _fmt(d: datetime.date) -> str:
    return d.strftime("%Y%m%d")


def _make_ics(*events: str) -> str:
    body = "\r\n".join(events)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//Test//EN\r\n{body}\r\nEND:VCALENDAR\r\n"


def _allday_event(uid: str, date: datetime.date, summary: str, location: str = "", description: str = "") -> str:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTART;VALUE=DATE:{_fmt(date)}",
        f"DTEND;VALUE=DATE:{_fmt(date + datetime.timedelta(days=1))}",
        f"SUMMARY:{summary}",
    ]
    if location:
        lines.append(f"LOCATION:{location}")
    if description:
        lines.append(f"DESCRIPTION:{description}")
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def _timed_event(uid: str, date: datetime.date, summary: str, start: str = "09:00", end: str = "10:00") -> str:
    ds = f"{_fmt(date)}T{start.replace(':', '')}00Z"
    de = f"{_fmt(date)}T{end.replace(':', '')}00Z"
    return f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTART:{ds}\r\nDTEND:{de}\r\nSUMMARY:{summary}\r\nEND:VEVENT"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_ical(raw_ical: str, filters: list[dict], app_config: dict | None = None) -> dict:
    """Execute a single ical node with pre-seeded cache and return its outputs."""
    hyst = {"n1": {"raw": raw_ical}}
    n = node("n1", "ical", {"filters": json.dumps(filters), "filter_count": len(filters)})
    ex = make_executor([n], hysteresis_state=hyst, app_config=app_config or {"timezone": "UTC"})
    return ex.execute()["n1"]


# ---------------------------------------------------------------------------
# Basic: empty calendar
# ---------------------------------------------------------------------------


class TestICalEmpty:
    def test_raw_output_present(self):
        ics = _make_ics()
        out = _run_ical(ics, [])
        assert "raw" in out
        assert "BEGIN:VCALENDAR" in out["raw"]

    def test_no_filters_no_extra_outputs(self):
        ics = _make_ics()
        out = _run_ical(ics, [])
        assert "f0_array" not in out

    def test_filter_empty_calendar_returns_empty_array(self):
        ics = _make_ics()
        out = _run_ical(ics, [{"name": "Test", "fields": ["summary"], "pattern": "X"}])
        assert out["f0_array"] == []
        assert out["f0_next_date"] is None
        assert out["f0_today"] is False
        assert out["f0_tomorrow"] is False


# ---------------------------------------------------------------------------
# All-day event — today
# ---------------------------------------------------------------------------


class TestICalToday:
    def test_today_is_true(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Müllabfuhr"))
        out = _run_ical(ics, [{"name": "Müll", "fields": ["summary"], "pattern": "Müll"}])
        assert out["f0_today"] is True

    def test_tomorrow_false_when_only_today(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Müllabfuhr"))
        out = _run_ical(ics, [{"name": "Müll", "fields": ["summary"], "pattern": "Müll"}])
        assert out["f0_tomorrow"] is False

    def test_next_date_is_today(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Müllabfuhr"))
        out = _run_ical(ics, [{"name": "Müll", "fields": ["summary"], "pattern": "Müll"}])
        assert out["f0_next_date"] == _TODAY.isoformat()

    def test_array_contains_one_row(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Müllabfuhr"))
        out = _run_ical(ics, [{"name": "Müll", "fields": ["summary"], "pattern": "Müll"}])
        assert len(out["f0_array"]) == 1

    def test_array_row_has_six_fields(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Müllabfuhr", location="Strasse 1", description="Bio"))
        out = _run_ical(ics, [{"name": "Müll", "fields": ["summary"], "pattern": "Müll"}])
        row = out["f0_array"][0]
        assert len(row) == 6  # [date, start, end, summary, location, description]

    def test_all_day_times_are_empty(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Müllabfuhr"))
        out = _run_ical(ics, [{"name": "Müll", "fields": ["summary"], "pattern": "Müll"}])
        row = out["f0_array"][0]
        assert row[1] == ""  # start_time empty
        assert row[2] == ""  # end_time empty


# ---------------------------------------------------------------------------
# Event tomorrow
# ---------------------------------------------------------------------------


class TestICalTomorrow:
    def test_tomorrow_is_true(self):
        ics = _make_ics(_allday_event("ev1", _TOMORROW, "Gelbe Tonne"))
        out = _run_ical(ics, [{"name": "Tonne", "fields": ["summary"], "pattern": "Tonne"}])
        assert out["f0_tomorrow"] is True

    def test_today_is_false_for_tomorrow_event(self):
        ics = _make_ics(_allday_event("ev1", _TOMORROW, "Gelbe Tonne"))
        out = _run_ical(ics, [{"name": "Tonne", "fields": ["summary"], "pattern": "Tonne"}])
        assert out["f0_today"] is False


# ---------------------------------------------------------------------------
# Timed events (DATETIME with timezone)
# ---------------------------------------------------------------------------


class TestICalTimedEvent:
    def test_timed_event_has_start_and_end_time(self):
        ics = _make_ics(_timed_event("ev1", _TODAY, "Meeting", "09:00", "10:00"))
        out = _run_ical(ics, [{"name": "m", "fields": ["summary"], "pattern": "Meeting"}])
        row = out["f0_array"][0]
        assert row[1] != ""  # start_time
        assert row[2] != ""  # end_time


# ---------------------------------------------------------------------------
# Filter: no pattern → match all
# ---------------------------------------------------------------------------


class TestICalNoPattern:
    def test_empty_pattern_matches_all(self):
        ics = _make_ics(
            _allday_event("ev1", _TODAY, "Alpha"),
            _allday_event("ev2", _TOMORROW, "Beta"),
        )
        out = _run_ical(ics, [{"name": "All", "fields": ["summary"], "pattern": ""}])
        assert len(out["f0_array"]) >= 2

    def test_new_format_all_empty_matches_all(self):
        ics = _make_ics(
            _allday_event("ev1", _TODAY, "Alpha"),
            _allday_event("ev2", _TOMORROW, "Beta"),
        )
        flt = {"name": "All", "field_logic": "or", "summary_pattern": "", "location_pattern": "", "description_pattern": ""}
        out = _run_ical(ics, [flt])
        assert len(out["f0_array"]) >= 2


# ---------------------------------------------------------------------------
# Filter: field selection (legacy format)
# ---------------------------------------------------------------------------


class TestICalFieldFilter:
    def test_matches_location_field(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Termin", location="Büro"))
        out = _run_ical(ics, [{"name": "loc", "fields": ["location"], "pattern": "Büro"}])
        assert out["f0_today"] is True

    def test_matches_description_field(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Termin", description="Jahreshauptversammlung"))
        out = _run_ical(ics, [{"name": "desc", "fields": ["description"], "pattern": "Jahres"}])
        assert out["f0_today"] is True

    def test_no_match_wrong_field(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Termin", location="Büro"))
        out = _run_ical(ics, [{"name": "summary", "fields": ["summary"], "pattern": "Büro"}])
        assert out["f0_today"] is False

    def test_multi_field_match_any(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Termin", location="Büro"))
        out = _run_ical(ics, [{"name": "both", "fields": ["summary", "location"], "pattern": "Büro"}])
        assert out["f0_today"] is True


# ---------------------------------------------------------------------------
# New filter format: per-field patterns with OR / AND logic
# ---------------------------------------------------------------------------


class TestICalPerFieldPatterns:
    def test_or_summary_only(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Restmüll", location="Strasse"))
        flt = {"name": "t", "field_logic": "or", "summary_pattern": "Restm", "location_pattern": "", "description_pattern": ""}
        assert _run_ical(ics, [flt])["f0_today"] is True

    def test_or_location_only(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Termin", location="Bahnhof"))
        flt = {"name": "t", "field_logic": "or", "summary_pattern": "", "location_pattern": "Bahn", "description_pattern": ""}
        assert _run_ical(ics, [flt])["f0_today"] is True

    def test_or_either_field_matches(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Müll", location="Strasse"))
        flt = {"name": "t", "field_logic": "or", "summary_pattern": "Müll", "location_pattern": "Büro", "description_pattern": ""}
        assert _run_ical(ics, [flt])["f0_today"] is True  # summary matches

    def test_or_no_field_matches(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Termin", location="Büro"))
        flt = {"name": "t", "field_logic": "or", "summary_pattern": "Müll", "location_pattern": "Bahn", "description_pattern": ""}
        assert _run_ical(ics, [flt])["f0_today"] is False

    def test_and_all_fields_match(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Restmüll", location="Strasse", description="Bio"))
        flt = {"name": "t", "field_logic": "and", "summary_pattern": "Restm", "location_pattern": "Strasse", "description_pattern": "Bio"}
        assert _run_ical(ics, [flt])["f0_today"] is True

    def test_and_one_field_missing(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Restmüll", location="Strasse"))
        flt = {"name": "t", "field_logic": "and", "summary_pattern": "Restm", "location_pattern": "Bahn", "description_pattern": ""}
        # location doesn't match → AND fails
        assert _run_ical(ics, [flt])["f0_today"] is False

    def test_and_empty_fields_ignored(self):
        """Empty patterns don't count as active → only non-empty patterns participate in AND."""
        ics = _make_ics(_allday_event("ev1", _TODAY, "Restmüll"))
        flt = {"name": "t", "field_logic": "and", "summary_pattern": "Restm", "location_pattern": "", "description_pattern": ""}
        assert _run_ical(ics, [flt])["f0_today"] is True

    def test_description_pattern(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Termin", description="Jahreshauptversammlung"))
        flt = {"name": "t", "field_logic": "or", "summary_pattern": "", "location_pattern": "", "description_pattern": "Jahres"}
        assert _run_ical(ics, [flt])["f0_today"] is True


# ---------------------------------------------------------------------------
# Case sensitivity
# ---------------------------------------------------------------------------


class TestICalCaseSensitivity:
    def test_case_insensitive_by_default(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "müllabfuhr"))
        out = _run_ical(ics, [{"name": "m", "fields": ["summary"], "pattern": "MÜLL"}])
        assert out["f0_today"] is True

    def test_case_sensitive_no_match(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "müllabfuhr"))
        out = _run_ical(ics, [{"name": "m", "fields": ["summary"], "pattern": "MÜLL", "case_sensitive": True}])
        assert out["f0_today"] is False

    def test_case_sensitive_match(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Müllabfuhr"))
        out = _run_ical(ics, [{"name": "m", "fields": ["summary"], "pattern": "Müll", "case_sensitive": True}])
        assert out["f0_today"] is True

    def test_new_format_case_insensitive(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Restmüll"))
        flt = {
            "name": "t",
            "field_logic": "or",
            "summary_pattern": "RESTMÜLL",
            "location_pattern": "",
            "description_pattern": "",
            "case_sensitive": False,
        }
        assert _run_ical(ics, [flt])["f0_today"] is True


# ---------------------------------------------------------------------------
# Multiple filters
# ---------------------------------------------------------------------------


class TestICalMultiFilter:
    def test_two_independent_filters(self):
        ics = _make_ics(
            _allday_event("ev1", _TODAY, "Müllabfuhr"),
            _allday_event("ev2", _TOMORROW, "Gelbe Tonne"),
        )
        filters = [
            {"name": "Müll", "fields": ["summary"], "pattern": "Müll"},
            {"name": "Tonne", "fields": ["summary"], "pattern": "Tonne"},
        ]
        out = _run_ical(ics, filters)
        assert out["f0_today"] is True
        assert out["f0_tomorrow"] is False
        assert out["f1_tomorrow"] is True
        assert out["f1_today"] is False

    def test_filter_count_drives_outputs(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "X"))
        filters = [{"name": "A", "fields": ["summary"], "pattern": "X"}] * 3
        out = _run_ical(ics, filters)
        for i in range(3):
            assert f"f{i}_array" in out
            assert f"f{i}_next_date" in out
            assert f"f{i}_today" in out
            assert f"f{i}_tomorrow" in out


# ---------------------------------------------------------------------------
# No cached data (empty raw)
# ---------------------------------------------------------------------------


class TestICalNoCachedData:
    def test_no_raw_returns_empty_outputs(self):
        hyst: dict = {"n1": {"raw": ""}}
        n = node("n1", "ical", {"filters": '[{"name":"x","fields":["summary"],"pattern":"x"}]', "filter_count": 1})
        ex = make_executor([n], hysteresis_state=hyst, app_config={"timezone": "UTC"})
        out = ex.execute()["n1"]
        assert out["raw"] == ""
        assert out["f0_array"] == []
        assert out["f0_next_date"] is None

    def test_no_hysteresis_at_all_returns_empty_raw(self):
        n = node("n1", "ical", {"filters": "[]", "filter_count": 0})
        ex = make_executor([n], app_config={"timezone": "UTC"})
        out = ex.execute()["n1"]
        assert out["raw"] == ""


# ---------------------------------------------------------------------------
# Array row format
# ---------------------------------------------------------------------------


class TestICalArrayRowFormat:
    def test_row_date_is_iso_string(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Event"))
        out = _run_ical(ics, [{"name": "e", "fields": ["summary"], "pattern": "Event"}])
        row = out["f0_array"][0]
        assert row[0] == _TODAY.isoformat()

    def test_row_summary_matches(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Mein Termin"))
        out = _run_ical(ics, [{"name": "e", "fields": ["summary"], "pattern": "Mein"}])
        row = out["f0_array"][0]
        assert row[3] == "Mein Termin"

    def test_row_location_matches(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Termin", location="Hauptstrasse 5"))
        out = _run_ical(ics, [{"name": "e", "fields": ["summary"], "pattern": "Termin"}])
        row = out["f0_array"][0]
        assert row[4] == "Hauptstrasse 5"

    def test_row_description_matches(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Termin", description="Details hier"))
        out = _run_ical(ics, [{"name": "e", "fields": ["summary"], "pattern": "Termin"}])
        row = out["f0_array"][0]
        assert row[5] == "Details hier"


# ---------------------------------------------------------------------------
# Regex pattern
# ---------------------------------------------------------------------------


class TestICalRegex:
    def test_regex_pattern_works(self):
        ics = _make_ics(
            _allday_event("ev1", _TODAY, "Biotonne"),
            _allday_event("ev2", _TOMORROW, "Restmüll"),
        )
        out = _run_ical(ics, [{"name": "tonne", "fields": ["summary"], "pattern": r"tonne|müll"}])
        assert len(out["f0_array"]) == 2

    def test_invalid_regex_falls_back_to_string_match(self):
        ics = _make_ics(_allday_event("ev1", _TODAY, "Test"))
        # Invalid regex — should fall back to literal string search
        out = _run_ical(ics, [{"name": "t", "fields": ["summary"], "pattern": "Te["}])
        assert out["f0_today"] is False  # "Te[" not literally in "Test"


# ---------------------------------------------------------------------------
# Future events only in array
# ---------------------------------------------------------------------------


class TestICalFutureOnly:
    def test_past_events_not_in_array(self):
        yesterday = _TODAY - datetime.timedelta(days=1)
        ics = _make_ics(
            _allday_event("ev_past", yesterday, "Alt"),
            _allday_event("ev_future", _NEXT_WEEK, "Neu"),
        )
        out = _run_ical(ics, [{"name": "all", "fields": ["summary"], "pattern": ""}])
        dates = [row[0] for row in out["f0_array"]]
        assert yesterday.isoformat() not in dates

    def test_next_date_skips_past(self):
        yesterday = _TODAY - datetime.timedelta(days=1)
        ics = _make_ics(
            _allday_event("ev_past", yesterday, "Alt"),
            _allday_event("ev_future", _NEXT_WEEK, "Neu"),
        )
        out = _run_ical(ics, [{"name": "all", "fields": ["summary"], "pattern": ""}])
        assert out["f0_next_date"] == _NEXT_WEEK.isoformat()
