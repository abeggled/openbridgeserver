"""Safe formula evaluator for binding value transformations.

Variable: x  — der aktuelle Wert (float)
Erlaubte Operatoren: + - * / // % **
Erlaubte Funktionen: abs, round, min, max, sowie alle math.*-Funktionen

Beispiele:
  x * 0.1          → Festkomma ÷10
  x / 3600         → Sekunden → Stunden
  round(x * 0.01)  → auf ganze Zahlen runden
  max(0, x - 20)   → Untergrenze 0
"""

from __future__ import annotations

import ast
import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Erlaubte AST-Knoten (kein Import, kein Aufruf beliebiger Funktionen)
# ---------------------------------------------------------------------------

_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,  # Python 3.8+
    ast.Name,  # für 'x' und math-Funktionen
    ast.Attribute,  # nur math.<funktion>
    ast.Call,  # nur erlaubte Funktionsaufrufe
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.UAdd,
    ast.USub,
    ast.Load,
)


_ALLOWED_FUNC_NAMES = {"abs", "round", "min", "max"}
_ALLOWED_MATH_NAMES = {k for k in math.__dict__ if not k.startswith("_")}
_ALLOWED_MATH_ATTRS = {k for k, v in math.__dict__.items() if not k.startswith("_") and callable(v)}


def _is_allowed_call(node: ast.Call) -> bool:
    if node.keywords:
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in _ALLOWED_FUNC_NAMES or func.id in _ALLOWED_MATH_ATTRS
    if isinstance(func, ast.Attribute):
        return isinstance(func.value, ast.Name) and func.value.id == "math" and func.attr in _ALLOWED_MATH_ATTRS and not func.attr.startswith("_")
    return False


def _validate_tree(tree: ast.AST) -> str | None:
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return f"Nicht erlaubter Ausdruck: '{type(node).__name__}'"

        if isinstance(node, ast.Name) and node.id not in {"x", "math", *_ALLOWED_FUNC_NAMES, *_ALLOWED_MATH_NAMES}:
            return f"Nicht erlaubter Name: '{node.id}'"

        if isinstance(node, ast.Attribute):
            if not (
                isinstance(node.value, ast.Name) and node.value.id == "math" and node.attr in _ALLOWED_MATH_NAMES and not node.attr.startswith("_")
            ):
                return "Nicht erlaubter Attributzugriff"

        if isinstance(node, ast.Call) and not _is_allowed_call(node):
            return "Nicht erlaubter Funktionsaufruf"

    return None


_SAFE_GLOBALS: dict[str, Any] = {
    "__builtins__": {},  # kein Zugriff auf builtins
    "x": 0.0,  # Platzhalter; wird pro Aufruf überschrieben
    # Eingebaute Funktionen
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    # math-Modul als Namespace und direkte Funktionen
    "math": math,
    **{k: v for k, v in math.__dict__.items() if not k.startswith("_")},
}


# ---------------------------------------------------------------------------
# Öffentliche API
# ---------------------------------------------------------------------------


def validate_formula(formula: str) -> str | None:
    """Prüft Syntax und erlaubte Knoten.
    Gibt eine Fehlermeldung zurück oder None wenn gültig.
    """
    formula = formula.strip()
    if not formula:
        return None  # leer = kein Filter → OK

    # 1. Syntaxcheck
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        return f"Syntaxfehler: {exc.msg}"

    # 2. Erlaubte Knoten/Funktionen prüfen
    err = _validate_tree(tree)
    if err:
        return err

    # 3. Testlauf mit x=1 und x=0 (fängt offensichtliche Div-by-Zero)
    for test_val, label in ((1.0, "x=1"), (0.0, "x=0")):
        err = _try_eval(formula, test_val)
        if err:
            return f"Auswertungsfehler bei {label}: {err}"

    return None


class _PreciseInt(int):
    """int subclass that uses exact Fraction arithmetic for true division.

    When a Counter64 value (Python int) is divided by a float literal inside a
    formula (e.g. ``x / 1_000_000_000.0``), Python normally converts the large
    int to float first, losing precision for values > 2^53. This wrapper
    overrides ``__truediv__`` / ``__rtruediv__`` to compute the exact rational
    quotient via ``fractions.Fraction`` and convert only the (typically small)
    result to float, preserving adjacent-value distinguishability.
    """

    def __truediv__(self, other: Any) -> float:
        from fractions import Fraction

        try:
            return float(Fraction(int(self)) / Fraction(other))
        except (ValueError, ZeroDivisionError, TypeError):
            return NotImplemented  # type: ignore[return-value]

    def __rtruediv__(self, other: Any) -> float:
        from fractions import Fraction

        try:
            return float(Fraction(other) / Fraction(int(self)))
        except (ValueError, ZeroDivisionError, TypeError):
            return NotImplemented  # type: ignore[return-value]


def apply_formula(formula: str, value: Any) -> Any:
    """Wendet die Formel auf *value* an.
    Bei Division durch Null oder anderen Fehlern wird der Originalwert zurückgegeben.
    """
    formula = formula.strip()
    if not formula:
        return value
    try:
        if isinstance(value, bool):
            x: _PreciseInt | float = float(value)
        elif isinstance(value, int):
            # Wrap in _PreciseInt so that integer arithmetic (%, //, +, *)
            # stays exact for large Counter64 values (> 2^53), and that true
            # division (x / float_literal) uses exact Fraction arithmetic
            # instead of converting the large int to float first.
            # Note: float64 has an inherent limit — adjacent Counter64 values
            # at 2^60 range after dividing by 10^9 may still be indistinguishable
            # (~10^-9 difference, but result ULP ≈ 10^-7). Use x//divisor for
            # formulas where the WriteRouter must detect every counter increment.
            x = _PreciseInt(value)
        else:
            x = float(value)
    except (TypeError, ValueError):
        return value  # Nicht-numerisch → unverändert

    try:
        locals_: dict[str, Any] = {**_SAFE_GLOBALS, "x": x}
        tree = ast.parse(formula, mode="eval")
        err = _validate_tree(tree)
        if err:
            logger.warning("Formula '%s' rejected by AST validation: %s", formula, err)
            return value
        code = compile(tree, "<formula>", "eval")
        result = eval(code, {"__builtins__": {}}, locals_)  # noqa: S307

        if not isinstance(result, (int, float)):
            logger.warning("Formula '%s' returned non-numeric: %r", formula, result)
            return value
        if math.isnan(result) or math.isinf(result):
            logger.warning("Formula '%s' returned nan/inf for x=%s", formula, x)
            return value
        return result

    except ZeroDivisionError:
        logger.warning(
            "Formula '%s': Division durch Null für x=%s — Originalwert behalten",
            formula,
            x,
        )
        return value
    except Exception:
        logger.exception("Formula '%s' fehlgeschlagen für x=%s", formula, x)
        return value


# ---------------------------------------------------------------------------
# Intern
# ---------------------------------------------------------------------------


def _try_eval(formula: str, x: float) -> str | None:
    """Gibt Fehlermeldung oder None zurück."""
    try:
        locals_: dict[str, Any] = {**_SAFE_GLOBALS, "x": x}
        tree = ast.parse(formula, mode="eval")
        code = compile(tree, "<formula>", "eval")
        result = eval(code, {"__builtins__": {}}, locals_)  # noqa: S307
        if isinstance(result, (int, float)) and (math.isnan(result) or math.isinf(result)):
            return "Ergebnis ist nan oder inf"
        return None
    except ZeroDivisionError:
        return "Division durch Null"
    except Exception as exc:
        return str(exc)
