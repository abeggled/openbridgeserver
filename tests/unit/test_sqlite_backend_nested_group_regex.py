r"""Codex-[P2]-Finding (#919, PR #951): nesting-aware safe-regex-Gate (:285).

Follow-up auf die Runden 19/21/25. Der bisherige nested-quantifier-Detektor
matchte nur eine quantifizierte FLACHE Klammergruppe: ein zusaetzlicher Wrapper
wie ``((a+))+`` passierte ``_assert_safe_regex()``, obwohl es dieselbe
katastrophale nested-repeat-Form ist. In einem Regex-Value-Filter gegen einen
langen NICHT-matchenden Textwert kann CPython ``re.search`` sekundenlang
backtracken (synchroner SQLite-Callback, haelt Worker/GIL).

Fix: geschachtelte Gruppen REKURSIV/nesting-aware erkennen (kleiner Scanner mit
Klammertiefe statt flachem Regex). Eine quantifizierte Gruppe (Quantifier direkt
nach ``)``) wird abgelehnt, wenn sie IRGENDWO – auch hinter zusaetzlichen
Wrapper-Klammern – einen inneren Quantifier oder eine Alternation enthaelt.
Escapte Klammern (``\(``/``\)``) und Zeichenklassen (``[...]``) zaehlen nicht als
Gruppen. Die Detektionen aus Runde 21 (Alternation-Quantifier) und Runde 25
(flat nested quantifier) bleiben subsumiert.
"""

from __future__ import annotations

import pytest

from obs.ringbuffer.store.sqlite_backend import _assert_safe_regex

# ===========================================================================
# Neue nesting-aware Reject-Faelle (Wrapper-Klammern verstecken den Kern)
# ===========================================================================


@pytest.mark.parametrize(
    "pattern",
    [
        "((a+))+b",  # das im Finding genannte Muster (Wrapper um (a+))
        "(((a?)))+",  # doppelter Wrapper, inneres ``?`` + aeusseres ``+``
        "((a|aa){30})+",  # gewrappte quantifizierte Alternation
        "((a+))*",  # Wrapper + inneres ``+`` + aeusseres ``*``
        "((a+))+",  # Wrapper ohne Trailer
        "(((a?)))+b",  # dreifach gewrappt, inneres ``?``
        "((a+){3})+",  # Wrapper um bereits gewrappten counted inner
    ],
)
def test_rejects_nested_quantifiers_behind_wrapper_groups(pattern: str):
    with pytest.raises(ValueError, match="unsafe regex pattern"):
        _assert_safe_regex(pattern)


# ===========================================================================
# Bestehende Runde-21/25-Faelle bleiben reject (Subsumption verifizieren)
# ===========================================================================


@pytest.mark.parametrize(
    "pattern",
    [
        "(a+)+",  # flat nested quantifier (Runde 25)
        "((a+))*",  # gewrappt
        "(a|aa){30}b",  # quantifizierte Alternation, counted (Runde 21)
        "(a?){30}a{30}",  # optionaler innerer Quantifier + counted (Runde 25)
        "(a|b)+",  # lineare Alternation, konservativ abgelehnt (Runde 21)
    ],
)
def test_existing_round21_25_cases_still_rejected(pattern: str):
    with pytest.raises(ValueError, match="unsafe regex pattern"):
        _assert_safe_regex(pattern)


# ===========================================================================
# Benigne Muster bleiben erlaubt (kein innerer Quantifier / keine Alternation)
# ===========================================================================


@pytest.mark.parametrize(
    "pattern",
    [
        "((abc))+",  # Wrapper OHNE inneren Quantifier
        "(abc)+",  # flache Gruppe ohne inneren Quantifier
        "a?",  # einzelnes optionales Zeichen
        "x{2,5}",  # counted-range Quantifier ohne Gruppe
        "foo.*bar",  # ``.*`` ist linear
        "[(]+",  # Klammer als Zeichenklassen-Inhalt, keine Gruppe
        r"\((a)\)+",  # escapte Klammern zaehlen nicht als Gruppe
        "((abc)){2,5}",  # gewrappt counted, ohne inneren Quantifier
        "a{3}",  # counted Quantifier ohne Gruppe
        "(abc){2,5}",  # counted Gruppe ohne inneren Quantifier
    ],
)
def test_allows_benign_nested_groups(pattern: str):
    _assert_safe_regex(pattern)
