#!/usr/bin/env python3
"""Regenerate tests/fixtures/timer_value_parity.json from the backend implementation.

The fixture pins the Zeitschaltuhr switching-value contract (issue #1008) across all
three implementations — `obs/models/types.py`, `gui/src/utils/timerValue.js` and
`frontend/src/utils/timerValue.ts`. Run this after changing the backend parser, then
re-run the three test suites; a frontend that now accepts something the backend
rejects will fail its parity test.

Usage:  tools/with-venv python tools/gen-timer-value-parity-fixture.py
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from obs.models.types import coerce_text_value_for_type

VALUES = [
    # boolean literals and casing/whitespace variants
    "1", "0", "true", "false", "on", "off", "ein", "aus", "yes", "no", "ja", "nein",
    "TRUE", "Ein", "  on  ",
    # numbers, including literals JS parses but Python does not, and non-finite values
    "50", "-3", "50.0", "50.5", ".5", "1e3", "1.5e2", "+7", "007", "abc", "", "   ",
    "0x10", "0b101", "0o17", "inf", "-inf", "Infinity", "nan", "NaN", "1e999", "-1e999",
    # decimals whose integrality binary float would misjudge (Codex review, #1155)
    "1.0000000000000001", "9007199254740993.0", "1.5e1", "1.55e1", "5.", "1e-3", "1000e-3",
    # dates, including impossible calendar dates
    "2026-12-24", "2026-02-30", "2026-13-01", "2026-04-31", "2026-00-10", "2026-01-00",
    "2024-02-29", "2026-02-29", "2000-02-29", "1900-02-29", "24.12.2026", "2026-1-1",
    # year bounds — datetime.MINYEAR is 1, so year zero is out of range (Codex review, #1155)
    "0000-01-01", "0001-01-01", "9999-12-31",
    # times, including out-of-range components and offset/fraction suffixes
    "08:00", "08:00:00", "8:00", "25:00", "08:60", "08:00:60", "23:59:59", "00:00",
    "08:00:00.5", "08:00:00+02:00", "08:00:00Z", "08:00:00z", "08:00:00+0200", "morgens",
    # datetimes, including separator variants and a bare date
    "2026-12-24T08:00", "2026-12-24 08:00", "2026-12-24t08:00", "2026-12-24T08:00:00+02:00",
    "2026-12-24T08:00:00Z", "2026-12-24T08:00:00z", "2026-12-24T25:00", "2026-02-30T08:00",
    "2026-12-24T", "2026-12-24T08:00:00.123456", "T08:00", "0000-12-24T08:00", "0000-12-24",
]  # fmt: skip

TYPES = ["BOOLEAN", "INTEGER", "FLOAT", "STRING", "DATE", "TIME", "DATETIME", "UNKNOWN"]

COMMENT = (
    "Cross-implementation parity fixture for the Zeitschaltuhr switching value (issue #1008). "
    "`backendValid[TYPE][i]` is whether obs.models.types.coerce_text_value_for_type(values[i], TYPE) "
    "succeeds. The Python test asserts exact agreement; the two frontend validators assert the "
    "one-directional invariant: never report a value as valid that the backend rejects, because "
    "that would green-light a request the API answers with 422. Regenerate with "
    "tools/gen-timer-value-parity-fixture.py after changing any of the three implementations."
)


def main() -> None:
    backend: dict[str, list[bool]] = {}
    for data_type in TYPES:
        row = []
        for value in VALUES:
            try:
                coerce_text_value_for_type(value, data_type)
                row.append(True)
            except ValueError:
                row.append(False)
        backend[data_type] = row

    doc = {"_comment": COMMENT, "values": VALUES, "types": TYPES, "backendValid": backend}
    target = pathlib.Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "timer_value_parity.json"
    target.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target} ({len(VALUES)} values x {len(TYPES)} types = {len(VALUES) * len(TYPES)} cases)")


if __name__ == "__main__":
    main()
