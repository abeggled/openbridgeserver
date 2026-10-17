#!/usr/bin/env python3
"""Record the whitespace CPython skips, for the GUI parity tests.

JavaScript's trim() is not Python's whitespace, and the two backend paths do
not agree with each other either: str.strip() (behind GraphExecutor._to_bool)
removes U+001C-U+001F, which float() (behind _to_num) rejects. The GUI mirrors
both, so the sets are recorded here rather than assumed.

Run after a Python upgrade:
    tools/with-venv python3 tools/gen-python-whitespace-fixture.py
"""

import json
import pathlib

SCAN_TO = 0x3100
EXTRA = [0xFEFF, 0x180E, 0x200B]
OUT = pathlib.Path(__file__).resolve().parent.parent / "gui/tests/utils/pythonWhitespace.fixture.json"


def main() -> None:
    float_skips, strip_removes = [], []
    for cp in list(range(SCAN_TO)) + EXTRA:
        ch = chr(cp)
        if ch.isdigit() or ch in "+-.eE_":
            continue
        try:
            if float(ch + "4" + ch) == 4.0:
                float_skips.append(cp)
        except (ValueError, TypeError):
            pass
        if (ch + "false" + ch).strip() == "false":
            strip_removes.append(cp)

    OUT.write_text(
        json.dumps(
            {
                "_comment": (
                    "Recorded from CPython: code points float() skips around a number "
                    "and str.strip() removes around a string. Regenerate with "
                    "tools/gen-python-whitespace-fixture.py when the backend Python changes."
                ),
                "scannedTo": SCAN_TO,
                "extraScanned": EXTRA,
                "floatSkips": sorted(float_skips),
                "stripRemoves": sorted(strip_removes),
            },
            indent=2,
        )
        + "\n"
    )
    print(f"{OUT}: {len(float_skips)} float, {len(strip_removes)} strip")


if __name__ == "__main__":
    main()
