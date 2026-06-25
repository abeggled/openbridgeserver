"""Tests for scripts/check_i18n_guard.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check_i18n_guard.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_i18n_guard", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_module()


class EmptyAllowlist:
    def contains(self, text: str) -> bool:
        return False


def test_raw_translation_call_in_template_text_is_flagged():
    src = """<template>
  <p>
    $t('widgets.info.additionalValues', { max: MAX_EXTRA })
  </p>
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert len(violations) == 1
    assert violations[0].kind == "template-text"
    assert "$t('widgets.info.additionalValues'" in violations[0].snippet


def test_interpolated_translation_call_is_allowed():
    src = """<template>
  <p>{{ $t('widgets.info.additionalValues', { max: MAX_EXTRA }) }}</p>
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert violations == []


def test_dynamic_translation_attribute_is_allowed():
    src = """<template>
  <input
    :placeholder="$t('widgets.info.mainLabelPlaceholder')"
  />
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert violations == []


def test_static_template_text_still_gets_flagged():
    src = """<template>
  <p>Additional values</p>
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert len(violations) == 1
    assert violations[0].snippet == "Additional values"


def test_issue_864_locale_keys_exist():
    gui_en = json.loads((REPO_ROOT / "gui/src/locales/en.json").read_text(encoding="utf-8"))
    gui_de = json.loads((REPO_ROOT / "gui/src/locales/de.json").read_text(encoding="utf-8"))
    frontend_en = json.loads((REPO_ROOT / "frontend/src/locales/en.json").read_text(encoding="utf-8"))
    frontend_de = json.loads((REPO_ROOT / "frontend/src/locales/de.json").read_text(encoding="utf-8"))

    for locale in (gui_en, gui_de):
        assert "enabled" in locale["common"]

    for locale in (frontend_en, frontend_de):
        assert "mainLabelPlaceholder" in locale["widgets"]["info"]
        assert "unitPlaceholder" in locale["widgets"]["info"]
