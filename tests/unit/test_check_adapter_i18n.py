"""Tests for tools/check_adapter_i18n.py — the backend adapter i18n hard gate (issue #779)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "tools" / "check_adapter_i18n.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_adapter_i18n", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the @dataclass in the module can resolve its own module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_module()


REL = "obs/adapters/sample/adapter.py"


def test_string_literal_detail_without_code_is_flagged():
    src = 'async def f(self):\n    await self._publish_status(False, "Hardcoded German")\n'
    violations, referenced = gate.scan_file(REL, src)
    assert referenced == []
    assert len(violations) == 1
    assert "no code=" in violations[0].message


def test_literal_detail_with_code_passes_and_is_referenced():
    src = 'async def f(self):\n    await self._publish_status(False, "Disconnected", code="disconnected")\n'
    violations, referenced = gate.scan_file(REL, src)
    assert violations == []
    assert referenced == [(2, gate.STATUS_NS, "disconnected")]


def test_fstring_and_str_exc_details_need_no_code():
    src = (
        "async def f(self, exc, host, port):\n"
        "    await self._publish_status(False, str(exc))\n"
        '    await self._publish_status(True, f"{host}:{port}")\n'
    )
    violations, referenced = gate.scan_file(REL, src)
    assert violations == []
    assert referenced == []


def test_testresult_uses_testresult_namespace():
    src = 'def f():\n    return TestResult(success=False, detail="x", detail_code="connectFailed")\n'
    violations, referenced = gate.scan_file(REL, src)
    assert violations == []
    assert referenced == [(2, gate.TESTRESULT_NS, "connectFailed")]


def test_testresult_literal_detail_without_code_is_flagged():
    src = 'def f():\n    return TestResult(success=True, detail="Verbindung erfolgreich")\n'
    violations, _ = gate.scan_file(REL, src)
    assert len(violations) == 1
    assert "detail_code=" in violations[0].message


def test_passthrough_variable_code_is_not_flagged_or_referenced():
    # Wrapper forwarding a variable code (e.g. code=code) cannot be validated statically.
    src = "async def f(self, detail, code):\n    await self._publish_status(False, detail, code=code)\n"
    violations, referenced = gate.scan_file(REL, src)
    assert violations == []
    assert referenced == []


def test_is_target_matches_adapters_and_adapter_api_only():
    assert gate.is_target("obs/adapters/mqtt/adapter.py")
    assert gate.is_target("obs/api/v1/adapters.py")
    assert not gate.is_target("obs/core/event_bus.py")
    assert not gate.is_target("obs/adapters/registry.md")


def test_lookup_resolves_nested_keys():
    data = {"adapters": {"statusDetail": {"disconnected": "Disconnected"}}}
    assert gate.lookup(data, "adapters.statusDetail.disconnected")
    assert not gate.lookup(data, "adapters.statusDetail.missing")
    assert not gate.lookup(data, "adapters.testResult.connectOk")


@pytest.mark.parametrize("rel", ["obs/adapters/mqtt/adapter.py", "obs/api/v1/adapters.py"])
def test_real_files_reference_only_existing_codes(rel):
    """Every code referenced by the shipped backend files must exist in both locales."""
    source = (REPO_ROOT / rel).read_text(encoding="utf-8")
    _, referenced = gate.scan_file(rel, source)
    en = gate.load_locale(REPO_ROOT, "gui/src/locales/en.json")
    de = gate.load_locale(REPO_ROOT, "gui/src/locales/de.json")
    for _line, ns, code in referenced:
        dotted = f"{ns}.{code}"
        assert gate.lookup(en, dotted), f"{dotted} missing from en.json"
        assert gate.lookup(de, dotted), f"{dotted} missing from de.json"
