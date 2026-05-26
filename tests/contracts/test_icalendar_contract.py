"""Contract tests for the icalendar and recurring_ical_events libraries.

Verifies the exact import paths and API surface that obs/logic/executor.py
uses for the iCal node.  Run without Docker; completes in < 2 seconds.

Purpose: catch breaking changes when Renovate bumps either library.
  - icalendar: Calendar.from_ical(), component.name, component.get()
  - recurring_ical_events: of(cal).between(start, end)
"""

from __future__ import annotations

import datetime

import pytest

icalendar = pytest.importorskip("icalendar")
rie = pytest.importorskip("recurring_ical_events")


# ---------------------------------------------------------------------------
# icalendar
# ---------------------------------------------------------------------------

_SAMPLE_ICS = b"""\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:contract-test-1@obs
DTSTART;VALUE=DATE:20260101
DTEND;VALUE=DATE:20260102
SUMMARY:Neujahr
LOCATION:Zuhause
DESCRIPTION:Frohes neues Jahr
END:VEVENT
BEGIN:VEVENT
UID:contract-test-2@obs
DTSTART:20260615T090000Z
DTEND:20260615T100000Z
SUMMARY:Meeting
RRULE:FREQ=WEEKLY;COUNT=3
END:VEVENT
END:VCALENDAR
"""


class TestIcalendarContract:
    """Verify icalendar API surface used by the executor."""

    def test_calendar_import(self):
        from icalendar import Calendar  # noqa: PLC0415

        assert callable(Calendar.from_ical)

    def test_from_ical_returns_calendar(self):
        from icalendar import Calendar  # noqa: PLC0415

        cal = Calendar.from_ical(_SAMPLE_ICS)
        assert cal is not None

    def test_walk_yields_vevents(self):
        from icalendar import Calendar  # noqa: PLC0415

        cal = Calendar.from_ical(_SAMPLE_ICS)
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        assert len(events) >= 1

    def test_component_name_attribute(self):
        from icalendar import Calendar  # noqa: PLC0415

        cal = Calendar.from_ical(_SAMPLE_ICS)
        for comp in cal.walk():
            assert hasattr(comp, "name")
            break

    def test_dtstart_get(self):
        from icalendar import Calendar  # noqa: PLC0415

        cal = Calendar.from_ical(_SAMPLE_ICS)
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        ev = events[0]
        dtstart = ev.get("DTSTART")
        assert dtstart is not None
        assert hasattr(dtstart, "dt")  # executor uses dtstart.dt

    def test_allday_dtstart_is_date(self):
        from icalendar import Calendar  # noqa: PLC0415

        cal = Calendar.from_ical(_SAMPLE_ICS)
        allday = [c for c in cal.walk() if c.name == "VEVENT" and "Neujahr" in str(c.get("SUMMARY", ""))][0]
        dt = allday.get("DTSTART").dt
        assert isinstance(dt, datetime.date)
        assert not isinstance(dt, datetime.datetime)  # pure DATE, not DATETIME

    def test_timed_dtstart_is_datetime(self):
        from icalendar import Calendar  # noqa: PLC0415

        cal = Calendar.from_ical(_SAMPLE_ICS)
        timed = [c for c in cal.walk() if c.name == "VEVENT" and "Meeting" in str(c.get("SUMMARY", ""))][0]
        dt = timed.get("DTSTART").dt
        assert isinstance(dt, datetime.datetime)

    def test_get_summary(self):
        from icalendar import Calendar  # noqa: PLC0415

        cal = Calendar.from_ical(_SAMPLE_ICS)
        ev = [c for c in cal.walk() if c.name == "VEVENT"][0]
        assert str(ev.get("SUMMARY", "")) != ""

    def test_get_location(self):
        from icalendar import Calendar  # noqa: PLC0415

        cal = Calendar.from_ical(_SAMPLE_ICS)
        ev = [c for c in cal.walk() if "Neujahr" in str(c.get("SUMMARY", ""))][0]
        assert str(ev.get("LOCATION", "")) == "Zuhause"

    def test_get_description(self):
        from icalendar import Calendar  # noqa: PLC0415

        cal = Calendar.from_ical(_SAMPLE_ICS)
        ev = [c for c in cal.walk() if "Neujahr" in str(c.get("SUMMARY", ""))][0]
        assert "Jahr" in str(ev.get("DESCRIPTION", ""))

    def test_get_dtend(self):
        from icalendar import Calendar  # noqa: PLC0415

        cal = Calendar.from_ical(_SAMPLE_ICS)
        ev = [c for c in cal.walk() if c.name == "VEVENT"][0]
        dtend = ev.get("DTEND")
        assert dtend is not None
        assert hasattr(dtend, "dt")


# ---------------------------------------------------------------------------
# recurring_ical_events
# ---------------------------------------------------------------------------


class TestRecurringIcalEventsContract:
    """Verify recurring_ical_events API surface used by the executor."""

    def test_module_import(self):
        import recurring_ical_events  # noqa: PLC0415

        assert recurring_ical_events is not None

    def test_of_function_exists(self):
        import recurring_ical_events  # noqa: PLC0415

        assert callable(recurring_ical_events.of)

    def test_between_method_exists(self):
        from icalendar import Calendar  # noqa: PLC0415
        import recurring_ical_events  # noqa: PLC0415

        cal = Calendar.from_ical(_SAMPLE_ICS)
        adapter = recurring_ical_events.of(cal)
        assert hasattr(adapter, "between")
        assert callable(adapter.between)

    def test_between_returns_iterable(self):
        from icalendar import Calendar  # noqa: PLC0415
        import recurring_ical_events  # noqa: PLC0415

        cal = Calendar.from_ical(_SAMPLE_ICS)
        start = datetime.date(2026, 1, 1)
        end = datetime.date(2026, 12, 31)
        events = recurring_ical_events.of(cal).between(start, end)
        event_list = list(events)
        assert isinstance(event_list, list)

    def test_between_expands_recurring(self):
        """The RRULE;FREQ=WEEKLY;COUNT=3 event should expand to 3 occurrences."""
        from icalendar import Calendar  # noqa: PLC0415
        import recurring_ical_events  # noqa: PLC0415

        cal = Calendar.from_ical(_SAMPLE_ICS)
        start = datetime.date(2026, 6, 1)
        end = datetime.date(2026, 12, 31)
        events = list(recurring_ical_events.of(cal).between(start, end))
        meeting_events = [e for e in events if "Meeting" in str(e.get("SUMMARY", ""))]
        assert len(meeting_events) == 3

    def test_between_date_range_parameter(self):
        """between() must accept datetime.date objects (not just datetime)."""
        from icalendar import Calendar  # noqa: PLC0415
        import recurring_ical_events  # noqa: PLC0415

        cal = Calendar.from_ical(_SAMPLE_ICS)
        # Pure date objects — executor passes date, not datetime
        events = recurring_ical_events.of(cal).between(
            datetime.date(2026, 1, 1),
            datetime.date(2026, 1, 31),
        )
        assert list(events) is not None  # must not raise

    def test_expanded_event_has_dtstart(self):
        """Expanded events must expose DTSTART the same way as raw events."""
        from icalendar import Calendar  # noqa: PLC0415
        import recurring_ical_events  # noqa: PLC0415

        cal = Calendar.from_ical(_SAMPLE_ICS)
        events = list(recurring_ical_events.of(cal).between(datetime.date(2026, 1, 1), datetime.date(2026, 12, 31)))
        for ev in events:
            dtstart = ev.get("DTSTART")
            assert dtstart is not None
            assert hasattr(dtstart, "dt")
