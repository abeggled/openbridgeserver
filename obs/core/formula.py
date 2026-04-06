"""
Safe formula evaluator for binding value transformations.

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
    ast.BinOp, ast.UnaryOp,
    ast.Constant,                      # Python 3.8+
    ast.Name,                          # für 'x' und math-Funktionen
    ast.Attribute,                     # für math.sqrt etc.
    ast.Call,                          # für abs(), round() etc.
    ast.Add, ast.Sub, ast.Mult, ast.Div,
    ast.FloorDiv, ast.Mod, ast.Pow,
    ast.UAdd, ast.USub,
    ast.Load,
)

_SAFE_GLOBALS: dict[str, Any] = {
    "__builtins__": {},                # kein Zugriff auf builtins
    "x": 0.0,                         # Platzhalter; wird pro Aufruf überschrieben
    # Eingebaute Funktionen
    "abs":   abs,
    "round": round,
    "min":   min,
    "max":   max,
    # math-Modul als Namespace und direkte Funktionen
    "math":  math,
    **{k: v for k, v in math.__dict__.items() if not k.startswith("_")},
}


# ---------------------------------------------------------------------------
# Öffentliche API
# ---------------------------------------------------------------------------

def validate_formula(formula: str) -> str | None:
    """
    Prüft Syntax und erlaubte Knoten.
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

    # 2. Erlaubte Knoten prüfen
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return f"Nicht erlaubter Ausdruck: '{type(node).__name__}'"

    # 3. Testlauf mit x=1 und x=0 (fängt offensichtliche Div-by-Zero)
    for test_val, label in ((1.0, "x=1"), (0.0, "x=0")):
        err = _try_eval(formula, test_val)
        if err:
            return f"Auswertungsfehler bei {label}: {err}"

    return None


def apply_formula(formula: str, value: Any) -> Any:
    """
    Wendet die Formel auf *value* an.
    Bei Division durch Null oder anderen Fehlern wird der Originalwert zurückgegeben.
    """
    formula = formula.strip()
    if not formula:
        return value
    try:
        x = float(value)
    except (TypeError, ValueError):
        return value  # Nicht-numerisch → unverändert

    try:
        locals_: dict[str, Any] = {**_SAFE_GLOBALS, "x": x}
        tree  = ast.parse(formula, mode="eval")
        code  = compile(tree, "<formula>", "eval")
        result = eval(code, {"__builtins__": {}}, locals_)   # noqa: S307

        if not isinstance(result, (int, float)):
            logger.warning("Formula '%s' returned non-numeric: %r", formula, result)
            return value
        if math.isnan(result) or math.isinf(result):
            logger.warning("Formula '%s' returned nan/inf for x=%s", formula, x)
            return value
        return result

    except ZeroDivisionError:
        logger.warning("Formula '%s': Division durch Null für x=%s — Originalwert behalten", formula, x)
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
        tree   = ast.parse(formula, mode="eval")
        code   = compile(tree, "<formula>", "eval")
        result = eval(code, {"__builtins__": {}}, locals_)   # noqa: S307
        if isinstance(result, (int, float)) and (math.isnan(result) or math.isinf(result)):
            return "Ergebnis ist nan oder inf"
        return None
    except ZeroDivisionError:
        return "Division durch Null"
    except Exception as exc:
        return str(exc)
