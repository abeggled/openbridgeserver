"""Parity contract Python ↔ JavaScript for group address display (#1296).

``gui/tests/fixtures/ga-format-parity.json`` holds what ``format_ga`` returns for
boundary values, whitespace, invalid texts and a fixed sample in every style.
This test fails when the checked-in table is not what the generator produces
from the current ``format_ga``; ``gui/tests/utils/groupAddress.spec.js`` fails when
the GUI's ``formatGa`` does not reproduce it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from obs.adapters.knx.group_address import GROUP_ADDRESS_STYLES, format_ga

_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "gen_ga_format_parity.py"


def _generator():
    spec = importlib.util.spec_from_file_location("gen_ga_format_parity", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_checked_in_table_is_current():
    generator = _generator()
    assert generator.TABLE.read_text(encoding="utf-8") == generator.render(), "run: tools/with-venv python tools/gen_ga_format_parity.py"


def test_table_covers_every_style_and_both_outcomes():
    rows = json.loads(_generator().TABLE.read_text(encoding="utf-8"))["rows"]
    valid = [row for row in rows if "expected" in row]
    assert {style for row in valid for style in row["expected"]} == set(GROUP_ADDRESS_STYLES)
    assert any(row.get("invalid") for row in rows)
    # the table is what format_ga says, not a copy of the generator's own arithmetic
    for row in valid:
        assert row["expected"] == {style: format_ga(row["input"], style) for style in GROUP_ADDRESS_STYLES}


def test_check_mode_reports_a_stale_table(monkeypatch, tmp_path):
    generator = _generator()
    stale = tmp_path / "ga-format-parity.json"
    stale.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(generator, "TABLE", stale)
    monkeypatch.setattr(sys, "argv", ["gen_ga_format_parity.py", "--check"])
    assert generator.main() == 1

    monkeypatch.setattr(sys, "argv", ["gen_ga_format_parity.py"])
    assert generator.main() == 0
    monkeypatch.setattr(sys, "argv", ["gen_ga_format_parity.py", "--check"])
    assert generator.main() == 0
