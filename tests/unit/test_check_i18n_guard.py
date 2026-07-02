"""Tests for tools/check_i18n_guard.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "tools" / "check_i18n_guard.py"


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


def test_raw_composition_translation_call_in_template_text_is_flagged():
    src = """<template>
  <p>
    t('widgets.info.additionalValues')
  </p>
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert len(violations) == 1
    assert violations[0].kind == "template-text"
    assert "t('widgets.info.additionalValues'" in violations[0].snippet


def test_raw_translation_call_before_inline_tag_is_flagged():
    src = """<template>
  <p>
    $t('widgets.info.additionalValues')<br>
  </p>
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert len(violations) == 1
    assert violations[0].kind == "template-text"
    assert "$t('widgets.info.additionalValues')" in violations[0].snippet


def test_raw_translation_call_mixed_with_interpolation_is_flagged():
    src = """<template>
  <p>{{ name }} $t('widgets.info.additionalValues')</p>
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert len(violations) == 1
    assert violations[0].kind == "template-text"
    assert "$t('widgets.info.additionalValues')" in violations[0].snippet


def test_raw_translation_call_after_multiline_opening_tag_closes_is_flagged():
    src = """<template>
  <p
    class="text-sm"> $t('widgets.info.additionalValues')</p>
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert len(violations) == 1
    assert violations[0].kind == "template-text"
    assert "$t('widgets.info.additionalValues')" in violations[0].snippet


def test_interpolated_translation_call_is_allowed():
    src = """<template>
  <p>{{ $t('widgets.info.additionalValues', { max: MAX_EXTRA }) }}</p>
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert violations == []


def test_interpolated_composition_translation_call_is_allowed():
    src = """<template>
  <p>{{ t('widgets.info.additionalValues', { max: MAX_EXTRA }) }}</p>
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert violations == []


def test_spaced_inline_interpolated_translation_call_is_allowed():
    src = """<template>
  <p> {{ $t('widgets.info.additionalValues', { max: MAX_EXTRA }) }}</p>
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert violations == []


def test_multiline_interpolated_translation_call_is_allowed():
    src = """<template>
  <p>
    {{
      $t('widgets.info.additionalValues', { max: MAX_EXTRA })
    }}
  </p>
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


def test_bound_literal_attribute_is_flagged():
    src = """<template>
  <input :placeholder="'Hardcoded label'" />
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert len(violations) == 1
    assert violations[0].kind == "template-bound-attr"
    assert violations[0].snippet == "Hardcoded label"


def test_bound_template_literal_attribute_is_flagged():
    src = """<template>
  <input :title="`Farbe ${i + 1}`" />
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert len(violations) == 1
    assert violations[0].kind == "template-bound-attr"
    assert violations[0].snippet == "Farbe"


def test_bound_translated_template_literal_expression_is_allowed():
    src = """<template>
  <button :title="`${children.length} ${children.length === 1 ? $t('breadcrumb.subpageSingular') : $t('breadcrumb.subpagePlural')}`" />
</template>
"""

    violations = gate.scan_vue("frontend/src/components/Breadcrumb.vue", src, EmptyAllowlist())

    assert violations == []


def test_bound_translated_computed_keys_are_allowed():
    src = """<template>
  <button :title="$t(enabled ? 'common.disable' : 'common.enable')" />
</template>
"""

    violations = gate.scan_vue("frontend/src/components/ToggleButton.vue", src, EmptyAllowlist())

    assert violations == []


def test_bound_template_literal_interpolation_literals_are_flagged():
    src = """<template>
  <button :title="`${enabled ? 'Enabled' : 'Disabled'}`" />
</template>
"""

    violations = gate.scan_vue("frontend/src/components/ToggleButton.vue", src, EmptyAllowlist())

    assert [v.snippet for v in violations] == ["Enabled", "Disabled"]


def test_bound_expression_condition_literals_are_allowed_when_output_is_translated():
    src = """<template>
  <button :title="status === 'error' ? $t('errors.title') : ''" />
</template>
"""

    violations = gate.scan_vue("frontend/src/components/StatusButton.vue", src, EmptyAllowlist())

    assert violations == []


def test_bound_expression_logical_guard_literals_are_allowed_when_output_is_translated():
    src = """<template>
  <button :title="status === 'error' && $t('errors.title')" />
</template>
"""

    violations = gate.scan_vue("frontend/src/components/StatusButton.vue", src, EmptyAllowlist())

    assert violations == []


def test_bound_expression_literal_piece_is_flagged():
    src = """<template>
  <input :placeholder="'Room ' + index" />
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert len(violations) == 1
    assert violations[0].kind == "template-bound-attr"
    assert violations[0].snippet == "Room"


def test_bound_object_expression_literal_piece_is_flagged():
    src = """<template>
  <input :label="{ text: 'Room' }" />
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert len(violations) == 1
    assert violations[0].kind == "template-bound-attr"
    assert violations[0].snippet == "Room"


def test_bound_mixed_translation_attribute_flags_literal_piece_only():
    src = """<template>
  <input :placeholder="'Room ' + $t('widgets.room')" />
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert len(violations) == 1
    assert violations[0].kind == "template-bound-attr"
    assert violations[0].snippet == "Room"


def test_bound_translation_parameter_literals_are_flagged():
    src = """<template>
  <button :title="$t('status.message', { value: 'Hardcoded error' })" />
</template>
"""

    violations = gate.scan_vue("frontend/src/components/StatusButton.vue", src, EmptyAllowlist())

    assert len(violations) == 1
    assert violations[0].kind == "template-bound-attr"
    assert violations[0].snippet == "Hardcoded error"


def test_bound_expression_technical_route_literals_are_allowed():
    src = """<template>
  <RouterLink :title="item.to === '/adapters' ? item.label : ''" />
</template>
"""

    violations = gate.scan_vue("gui/src/components/layout/Sidebar.vue", src, EmptyAllowlist())

    assert violations == []


def test_bound_variable_attribute_is_allowed():
    src = """<template>
  <input :placeholder="placeholderText" />
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert violations == []


def test_multiline_bound_literal_attribute_is_flagged():
    src = """<template>
  <input
    :placeholder="
      'Hardcoded label'
    "
  />
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert len(violations) == 1
    assert violations[0].kind == "template-bound-attr"
    assert violations[0].snippet == "Hardcoded label"


def test_multiline_tag_comparison_does_not_expose_translated_bound_attr_as_text():
    src = """<template>
  <button
    :class="count > 0 ? 'has-items' : ''"
    :title="$t('actions.save')"
  />
</template>
"""

    violations = gate.scan_vue("frontend/src/components/ActionButton.vue", src, EmptyAllowlist())

    assert violations == []


def test_multiline_bound_attribute_translation_calls_are_allowed():
    src = """<template>
  <button
    v-for="m in [
      { value: 'full', label: $t('widgets.zeitschaltuhr.modeFull'), title: $t('widgets.zeitschaltuhr.modeFullTitle') },
      { value: 'minimal', label: t('widgets.zeitschaltuhr.modeMinimal'), title: t('widgets.zeitschaltuhr.modeMinimalTitle') },
    ]"
    :key="m.value"
  >{{ m.label }}</button>
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Zeitschaltuhr/Config.vue", src, EmptyAllowlist())

    assert violations == []


def test_nested_template_blocks_do_not_leak_into_script_scan():
    src = """<script setup>
const state = 'ready'
</script>

<template>
  <template v-if="state === 'ready'">
    <button :title="$t('auth.login')">{{ $t('auth.login') }}</button>
  </template>
</template>
"""

    violations = gate.scan_vue("frontend/src/components/AuthButton.vue", src, EmptyAllowlist())

    assert violations == []


def test_empty_string_assignments_do_not_bleed_into_next_literal():
    src = """const props = {
  label: '',
  extra_datapoints: [{ id: 'dp-1', label: '', unit: '', decimals: 1 }],
}
"""

    violations = gate.scan_script("frontend/src/widgets/Info/Config.test.ts", src, EmptyAllowlist())

    assert violations == []


def test_translated_script_template_literal_is_allowed():
    src = """toast.success(`${t('common.saved')}`)
"""

    violations = gate.scan_script("frontend/src/widgets/Info/Config.test.ts", src, EmptyAllowlist())

    assert violations == []


def test_parameterized_translated_script_template_literal_is_allowed():
    src = """toast.success(`${t('common.saved', { name })} ${name}`)
"""

    violations = gate.scan_script("frontend/src/widgets/Info/Config.test.ts", src, EmptyAllowlist())

    assert violations == []


def test_script_template_literal_interpolation_literals_are_flagged():
    src = """toast.success(`${ok ? 'Saved' : 'Failed'}`)
"""

    violations = gate.scan_script("frontend/src/widgets/Info/Config.test.ts", src, EmptyAllowlist())

    assert [v.snippet for v in violations] == ["Saved", "Failed"]


def test_multiline_template_comment_raw_translation_call_is_ignored():
    src = """<template>
  <!--
    $t('widgets.info.additionalValues')
  -->
  <p>{{ $t('widgets.info.additionalValues') }}</p>
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert violations == []


def test_multiline_template_comment_bound_literal_attr_is_ignored():
    src = """<template>
  <!--
    <input :placeholder="'Hardcoded label'" />
  -->
  <input :placeholder="$t('widgets.info.mainLabelPlaceholder')" />
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert violations == []


def test_multiline_template_comment_preserves_violation_line_numbers():
    src = """<template>
  <!--
    hidden
  -->
  <p>Additional values</p>
</template>
"""

    violations = gate.scan_vue("frontend/src/widgets/Info/Config.vue", src, EmptyAllowlist())

    assert len(violations) == 1
    assert violations[0].line == 5
    assert violations[0].snippet == "Additional values"


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
