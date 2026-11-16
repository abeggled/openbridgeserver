"""Parity contract Python <-> JavaScript for the DPT keep rule (#1260).

``gui/tests/fixtures/dpt-keep-parity.json`` holds what ``keeps_stored_subtype`` answers
for every pair of sample DPTs. This test fails when the checked-in table is not what the
generator produces from the current ``keeps_stored_subtype``; ``gui/tests/utils/dpt.spec.js``
fails when the binding form's ``keepsStoredSubtype`` does not reproduce it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from obs.adapters.knx.dpt_registry import keeps_stored_subtype

_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "gen_dpt_keep_parity.py"


def _generator():
    spec = importlib.util.spec_from_file_location("gen_dpt_keep_parity", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_checked_in_table_is_current():
    generator = _generator()
    assert generator.TABLE.read_text(encoding="utf-8") == generator.render(), "run: tools/with-venv python tools/gen_dpt_keep_parity.py"


def test_table_holds_both_outcomes_and_the_digit_boundary():
    rows = json.loads(_generator().TABLE.read_text(encoding="utf-8"))["rows"]
    keep = {(row["new"], row["stored"]): row["keep"] for row in rows}
    assert keep[("DPT5", "DPT5.001")] is True
    assert keep[("DPT1", "DPT10.001")] is False, "DPT10.001 is no subtype of DPT1"
    assert keep[("DPT5.004", "DPT5.001")] is False
    assert keep[("DPT7", "DPT5.001")] is False
    assert keep[("DPT5", None)] is False
    # the table is what keeps_stored_subtype says
    for row in rows:
        assert row["keep"] == keeps_stored_subtype(row["new"], row["stored"])


def test_check_mode_reports_a_stale_table(monkeypatch, tmp_path):
    generator = _generator()
    stale = tmp_path / "dpt-keep-parity.json"
    stale.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(generator, "TABLE", stale)
    monkeypatch.setattr(sys, "argv", ["gen_dpt_keep_parity.py", "--check"])
    assert generator.main() == 1

    monkeypatch.setattr(sys, "argv", ["gen_dpt_keep_parity.py"])
    assert generator.main() == 0
    monkeypatch.setattr(sys, "argv", ["gen_dpt_keep_parity.py", "--check"])
    assert generator.main() == 0
