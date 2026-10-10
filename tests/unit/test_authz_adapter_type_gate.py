"""Contract gate: no adapter-type exceptions in authorization helpers (issue #1303).

Authorization helpers decide by principal type and code-declared adapter
delegation capabilities.  An ``if adapter_type == ...: return`` inside such a
helper silently widens access for one adapter and bypasses the declaration
model — exactly how the WEBHOOK binding exception hid an API-key gap.
"""

from __future__ import annotations

import textwrap
from collections import Counter
from pathlib import Path

import pytest

from tools import check_authz_contract
from tools.check_authz_contract import _adapter_type_authz_bypasses, validate_contracts

REPO_ROOT = Path(__file__).resolve().parents[2]


def _scan(tmp_path: Path, source: str) -> Counter:
    module = tmp_path / "obs" / "api" / "v1" / "sample.py"
    module.parent.mkdir(parents=True)
    module.write_text(textwrap.dedent(source), encoding="utf-8")
    return _adapter_type_authz_bypasses(tmp_path)


def test_flags_an_adapter_type_short_circuit_in_an_authz_helper(tmp_path: Path) -> None:
    found = _scan(
        tmp_path,
        """
        WEBHOOK = "WEBHOOK"

        def _ensure_adapter_delegates_binding(principal, adapter_type):
            if adapter_type == WEBHOOK and principal.type == "user":
                return
            raise PermissionError
        """,
    )

    assert found == Counter({("obs/api/v1/sample.py", "_ensure_adapter_delegates_binding", "adapter_type == WEBHOOK"): 1})


@pytest.mark.parametrize(
    "expression",
    ["row['adapter_type'] in {'MQTT', 'IOBROKER'}", "instance.adapter_type != 'KNX'"],
)
def test_flags_subscript_and_attribute_comparisons_in_async_helpers(tmp_path: Path, expression: str) -> None:
    found = _scan(
        tmp_path,
        f"""
        async def _ensure_instance_scope(db, principal, row, instance):
            if {expression}:
                return
        """,
    )

    assert list(found) == [("obs/api/v1/sample.py", "_ensure_instance_scope", expression)]


def test_ignores_refusals_validation_helpers_and_non_authz_functions(tmp_path: Path) -> None:
    found = _scan(
        tmp_path,
        """
        def _ensure_iobroker_import_authority(principal, row):
            # Narrowing a route to one adapter type refuses; it never grants.
            if row["adapter_type"] != "IOBROKER":
                raise ValueError("wrong adapter")

        def _validate_adapter_binding(adapter_type, direction):
            if adapter_type == "MESSAGE" and direction != "SOURCE":
                return

        def _ensure_webhook_target_allowed(dp_id, adapter_type):
            # No principal: a validation helper, not an authorization decision.
            if adapter_type == "WEBHOOK":
                return

        def _ensure_admin(principal):
            if principal.type == "user" and principal.is_admin:
                return
        """,
    )

    assert found == Counter()


def test_current_tree_has_no_adapter_type_authz_exception() -> None:
    assert _adapter_type_authz_bypasses(REPO_ROOT) == Counter()


def test_validate_contracts_reports_a_new_adapter_type_exception(monkeypatch) -> None:
    key = ("obs/api/v1/bindings.py", "_ensure_adapter_delegates_binding", "adapter_type == 'WEBHOOK'")
    monkeypatch.setattr(check_authz_contract, "_adapter_type_authz_bypasses", lambda _root: Counter({key: 1}))

    errors = validate_contracts(REPO_ROOT)

    assert errors == [f"new adapter-type exception in authz helper {key!r} (count 1, baseline 0)"]
