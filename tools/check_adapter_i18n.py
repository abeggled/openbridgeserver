#!/usr/bin/env python3
"""Diff-scoped i18n hard gate for backend adapter status/test strings (issue #779).

The frontend guard (``check_i18n_guard.py``) only scans ``gui/src`` + ``frontend/src``,
so user-facing German that originates in backend adapter code escapes detection. This
gate closes that gap with two structural checks on the changed backend files:

1. **Status/test calls must use a code.** Any call to ``_publish_status`` /
   ``TestResult`` (and the thin adapter/registry wrappers around them) whose ``detail``
   argument is a non-empty *string literal* must also pass a stable ``code=`` /
   ``detail_code=`` key (under ``adapters.statusDetail.*`` / ``adapters.testResult.*``).
   Dynamic fallbacks (f-strings, variables, ``str(exc)``) are allowed without a code —
   they are the agreed non-localized technical fallback.

2. **Referenced codes must exist with locale parity.** Every ``code`` / ``detail_code``
   literal referenced in a changed file must exist in both ``gui/src/locales/en.json``
   and ``de.json`` under the matching namespace.

Scope: ``obs/adapters/**/*.py`` and ``obs/api/v1/adapters.py``.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Functions that publish a user-facing status detail, mapped to the positional index
# of their ``detail`` argument (``detail`` may also be passed by keyword).
STATUS_FUNCS = {
    "_publish_status": 1,  # (connected, detail, ...)
    "_publish_disconnected_if_needed": 0,  # (detail, ...)
    "_publish_warning_status": 0,
    "_publish_connected_status": 0,
    "_set_instance_status": 2,  # (instance, severity, detail, ...)
}
# Calls whose ``detail``/``detail_code`` live under adapters.testResult.* instead.
TESTRESULT_FUNC = "TestResult"

STATUS_NS = "adapters.statusDetail"
TESTRESULT_NS = "adapters.testResult"

LOCALES = ("gui/src/locales/en.json", "gui/src/locales/de.json")


@dataclass
class Violation:
    path: str
    line: int
    message: str


def run_git_diff(repo_root: Path, base: str | None, head: str | None) -> list[str]:
    candidates: list[list[str]] = []
    if base and head:
        candidates.append(["git", "diff", "--name-only", f"{base}...{head}"])
        candidates.append(["git", "diff", "--name-only", base, head])
    candidates.extend(
        [
            ["git", "diff", "--name-only", "origin/main...HEAD"],
            ["git", "diff", "--name-only", "main...HEAD"],
            ["git", "diff", "--name-only", "HEAD~1...HEAD"],
        ],
    )
    for cmd in candidates:
        proc = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True)  # noqa: S603
        if proc.returncode == 0:
            files = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
            return sorted(set(files)) if files else []
    tried = " | ".join(" ".join(c) for c in candidates)
    raise RuntimeError(f"Could not determine changed files via git diff ({tried})")


def is_target(rel_path: str) -> bool:
    return (rel_path.startswith("obs/adapters/") and rel_path.endswith(".py")) or rel_path == "obs/api/v1/adapters.py"


def _string_const(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _detail_node(call: ast.Call, positional_index: int) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == "detail":
            return kw.value
    if len(call.args) > positional_index:
        return call.args[positional_index]
    return None


def _code_literal(call: ast.Call, code_kw: str) -> str | None | bool:
    """Return the code string literal, None if absent, or False if present but non-literal."""
    for kw in call.keywords:
        if kw.arg == code_kw:
            lit = _string_const(kw.value)
            return lit if lit is not None else False
    return None


def _call_name(call: ast.Call) -> str | None:
    fn = call.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return None


def scan_file(rel_path: str, source: str) -> tuple[list[Violation], list[tuple[int, str, str]]]:
    """Return (violations, referenced_codes) where referenced_codes = [(line, namespace, code)]."""
    violations: list[Violation] = []
    referenced: list[tuple[int, str, str]] = []
    tree = ast.parse(source, filename=rel_path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in STATUS_FUNCS:
            ns, code_kw, det_idx = STATUS_NS, "code", STATUS_FUNCS[name]
        elif name == TESTRESULT_FUNC:
            ns, code_kw, det_idx = TESTRESULT_NS, "detail_code", -1
            # TestResult always passes detail by keyword; no positional detail slot.
        else:
            continue

        detail_literal = _string_const(_detail_node(node, det_idx))
        code = _code_literal(node, code_kw)

        if detail_literal and (code is None):
            violations.append(
                Violation(
                    rel_path,
                    node.lineno,
                    f"{name}(...) has a hardcoded detail {detail_literal!r} but no {code_kw}= "
                    f"(add a stable code under {ns}.* or pass dynamic text instead)",
                ),
            )
        # A literal code is validated against the locale files; a variable/pass-through
        # code (e.g. wrappers forwarding code=code) cannot be checked statically and is
        # accepted as-is — its callers supply the literal codes that do get validated.
        if isinstance(code, str):
            referenced.append((node.lineno, ns, code))
    return violations, referenced


def load_locale(repo_root: Path, rel: str) -> dict:
    return json.loads((repo_root / rel).read_text(encoding="utf-8"))


def lookup(locale: dict, dotted: str) -> bool:
    cur: object = locale
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diff-scoped backend adapter i18n hard gate (issue #779)")
    parser.add_argument("--base", default=None)
    parser.add_argument("--head", default=None)
    parser.add_argument("files", nargs="*", help="Explicit files to scan instead of deriving from git diff")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    changed = sorted(set(args.files)) if args.files else run_git_diff(repo_root, args.base, args.head)
    targets = [p for p in changed if is_target(p)]
    if not targets:
        print("adapter i18n guard: no changed backend adapter files in diff; nothing to do.")
        return 0

    violations: list[Violation] = []
    referenced: list[tuple[str, int, str, str]] = []
    for rel in targets:
        path = repo_root / rel
        if not path.is_file():
            continue
        file_violations, file_refs = scan_file(rel, path.read_text(encoding="utf-8"))
        violations.extend(file_violations)
        referenced.extend((rel, line, ns, code) for line, ns, code in file_refs)

    locales = {rel: load_locale(repo_root, rel) for rel in LOCALES}
    for rel, line, ns, code in referenced:
        dotted = f"{ns}.{code}"
        for loc_rel, data in locales.items():
            if not lookup(data, dotted):
                violations.append(Violation(rel, line, f"code {dotted!r} is missing from {loc_rel}"))

    if violations:
        print("adapter i18n guard: violations detected:")
        for v in sorted(violations, key=lambda x: (x.path, x.line)):
            print(f"  {v.path}:{v.line}: {v.message}")
        print("adapter i18n guard: FAILED")
        return 1

    print(f"adapter i18n guard: OK (scanned {len(targets)} backend file(s), {len(referenced)} code reference(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
