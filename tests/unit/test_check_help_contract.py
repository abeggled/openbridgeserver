"""Tests for tools/check_help_contract.py."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "tools" / "check_help_contract.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_help_contract", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_module()


def _index(*help_ids: str, duplicates=None, incomplete=None) -> dict:
    return {
        "helpIds": {help_id: {"de": f"/help/de/x.html#{help_id}", "en": f"/help/en/x.html#{help_id}"} for help_id in help_ids},
        "duplicates": duplicates or [],
        "incomplete": incomplete or [],
    }


def _route(name: str, help_id: str | None) -> gate.Surface:
    return gate.Surface(kind="route", name=name, help_id=help_id, origin="gui/src/router/index.js")


def _widget(name: str, help_id: str) -> gate.Surface:
    return gate.Surface(kind="widget", name=name, help_id=help_id, origin=f"frontend/src/widgets/{name}/index.ts")


def _logic_block(name: str) -> gate.Surface:
    return gate.Surface(
        kind="logic-block",
        name=name,
        help_id=f"logic-block-{gate.kebab(name)}",
        origin="obs.logic.registry.BUILTIN_NODE_TYPES",
    )


class _NodeType:
    """Stand-in for obs.logic.models.NodeTypeDef — only what the gate reads."""

    def __init__(self, type: str, hidden_from_palette: bool = False) -> None:
        self.type = type
        self.hidden_from_palette = hidden_from_palette


def _allow(key: str, reason: str = "because", line: int = 1) -> gate.AllowlistEntry:
    return gate.AllowlistEntry(key=key, reason=reason, line=line)


# ── kebab ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("ValueDisplay", "value-display"),
        ("HorizontalBar", "horizontal-bar"),
        ("QrCode", "qr-code"),
        ("RTR", "rtr"),
        ("IFrame", "iframe"),
        ("HTMLElement", "html-element"),
        ("Zeitschaltuhr", "zeitschaltuhr"),
        ("dark_mode skin", "dark-mode-skin"),
    ],
)
def test_kebab_keeps_acronyms_whole(name, expected):
    assert gate.kebab(name) == expected


# ── route enumeration ────────────────────────────────────────────────────────


ROUTER_SOURCE = """import { createRouter } from 'vue-router'

const routes = [
  { path: '/login', name: 'Login', component: () => import('@/views/LoginView.vue'), meta: { public: true } },
  { path: '/', name: 'Dashboard', component: () => import('@/views/DashboardView.vue'), meta: { helpId: 'dashboard' } },
  { path: '/logs', name: 'Logs', component: () => import('@/views/LogView.vue'), meta: { helpId: "logs" } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export default createRouter({ routes })
"""


def test_parse_routes_reads_name_and_help_id():
    routes = gate.parse_routes(ROUTER_SOURCE)

    assert [(route.name, route.help_id) for route in routes] == [
        ("Login", None),
        ("Dashboard", "dashboard"),
        ("Logs", "logs"),
    ]


def test_parse_routes_reads_double_quoted_names_and_ids():
    """A route written with double quotes must not fall out of the enumeration."""
    source = 'const routes = [\n  { path: "/new", name: "NewView", meta: { helpId: "new-view" } },\n]\n'

    routes = gate.parse_routes(source)

    assert [(route.name, route.help_id) for route in routes] == [("NewView", "new-view")]


def test_parse_routes_ignores_a_help_id_outside_the_meta_object():
    """TopBar reads route.meta.helpId — a helpId elsewhere declares nothing."""
    source = "const routes = [\n  { path: '/', name: 'Dashboard', props: { helpId: 'dashboard' }, meta: {} },\n]\n"

    assert [route.help_id for route in gate.parse_routes(source)] == [None]


def test_parse_routes_reads_a_multi_property_meta_object():
    source = "const routes = [\n  { path: '/', name: 'Dashboard', meta: { public: false, helpId: 'dashboard' } },\n]\n"

    assert [route.help_id for route in gate.parse_routes(source)] == ["dashboard"]


def test_parse_routes_handles_a_nested_meta_object():
    source = "const routes = [\n  { path: '/', name: 'X', meta: { nested: { a: 1 }, helpId: 'x' } },\n]\n"

    assert [route.help_id for route in gate.parse_routes(source)] == ["x"]


@pytest.mark.parametrize("comment", ["//", "/*", "<!--"])
def test_parse_routes_ignores_a_commented_out_route(comment):
    """A route left in a comment is not one the app can route to."""
    closer = {"//": "", "/*": " */", "<!--": " -->"}[comment]
    source = f"const routes = [\n  {comment} {{ path: '/old', name: 'RemovedView' }},{closer}\n  {{ path: '/', name: 'Dashboard', meta: {{ helpId: 'dashboard' }} }},\n]\n"

    assert [route.name for route in gate.parse_routes(source)] == ["Dashboard"]


@pytest.mark.parametrize("key", ["name", "'name'", '"name"'])
def test_parse_routes_accepts_a_quoted_property_key(key):
    source = f"const routes = [\n  {{ path: '/', {key}: 'Dashboard', meta: {{ helpId: 'dashboard' }} }},\n]\n"

    assert [(r.name, r.help_id) for r in gate.parse_routes(source)] == [("Dashboard", "dashboard")]


def test_parse_routes_ignores_a_name_in_a_non_route_object():
    """A static `props` bag carrying a `name` is not a route."""
    source = "const routes = [\n  { path: '/', name: 'Dashboard', props: { name: 'not-a-route' }, meta: { helpId: 'dashboard' } },\n]\n"

    assert [r.name for r in gate.parse_routes(source)] == ["Dashboard"]


def test_parse_routes_ignores_a_help_id_that_is_only_string_text():
    """`note: "helpId: 'x'"` is text; route.meta.helpId stays undefined."""
    source = "const routes = [\n  { path: '/', name: 'Dashboard', meta: { note: \"helpId: 'dashboard'\" } },\n]\n"

    assert [route.help_id for route in gate.parse_routes(source)] == [None]


def test_parse_routes_survives_a_brace_inside_a_string():
    """A `{` in string text must not unbalance the record matching."""
    source = "const routes = [\n  { path: '/', name: 'Dashboard', meta: { title: 'a { b', helpId: 'dashboard' } },\n]\n"

    assert [(r.name, r.help_id) for r in gate.parse_routes(source)] == [("Dashboard", "dashboard")]


def test_parse_routes_ignores_a_route_name_that_is_only_string_text():
    source = "const routes = [\n  { path: '/', note: \"name: 'Fake'\", meta: { helpId: 'dashboard' } },\n]\n"

    assert gate.parse_routes(source) == []


def test_parse_routes_ignores_a_help_id_nested_inside_meta():
    """TopBar reads meta.helpId directly; a nested one declares nothing."""
    source = "const routes = [\n  { path: '/', name: 'Dashboard', meta: { analytics: { helpId: 'dashboard' } } },\n]\n"

    assert [r.help_id for r in gate.parse_routes(source)] == [None]


def test_parse_routes_enumerates_nested_children():
    """vue-router nests routes; each reachable one needs its own help_id."""
    source = (
        "const routes = [\n"
        "  { path: '/group', children: [\n"
        "    { path: 'one', name: 'NestedOne', meta: { helpId: 'one' } },\n"
        "    { path: 'two', name: 'NestedTwo' },\n"
        "  ] },\n"
        "]\n"
    )

    routes = gate.parse_routes(source)

    assert sorted((route.name, route.help_id) for route in routes) == [("NestedOne", "one"), ("NestedTwo", None)]


def test_parse_routes_does_not_read_a_childs_name_as_the_parents():
    source = "const routes = [\n  { path: '/group', children: [{ path: 'one', name: 'NestedOne', meta: { helpId: 'one' } }] },\n]\n"

    assert [route.name for route in gate.parse_routes(source)] == ["NestedOne"]


def test_parse_routes_does_not_read_a_nested_meta_as_the_parents():
    """A parent without its own meta must not inherit a child's helpId."""
    source = "const routes = [\n  { path: '/group', name: 'Group', children: [{ path: 'one', name: 'One', meta: { helpId: 'one' } }] },\n]\n"

    routes = {route.name: route.help_id for route in gate.parse_routes(source)}

    assert routes == {"Group": None, "One": "one"}


def test_parse_routes_skips_the_unnamed_catch_all():
    assert all(route.name for route in gate.parse_routes(ROUTER_SOURCE))


def test_parse_routes_fails_closed_on_a_non_literal_name():
    """The route is reachable; only the gate cannot tell what it is called."""
    source = "const routes = [\n  { path: '/c', name: EXTRA_ROUTE_NAME, component: X },\n]\n"

    with pytest.raises(SystemExit, match="'name' is not a string literal"):
        gate.parse_routes(source)


def test_parse_routes_fails_closed_on_a_shorthand_name():
    """`{ path, name }` names the route at runtime; the scan cannot read it."""
    source = "const routes = [\n  { path: '/s', name, component: c },\n]\n"

    with pytest.raises(SystemExit, match="'name' is not a string literal"):
        gate.parse_routes(source)


def test_parse_routes_is_not_confused_by_a_property_ending_in_name():
    """`componentName: X` is not a shorthand `name`."""
    source = "const routes = [\n  { path: '/', componentName: X, name: 'A', meta: { helpId: 'a' } },\n]\n"

    assert [(r.name, r.help_id) for r in gate.parse_routes(source)] == [("A", "a")]


def test_parse_routes_fails_closed_on_a_spread_route_array():
    """A spread can contribute named routes the scan never sees."""
    source = "const routes = [\n  ...EXTRA_ROUTES,\n  { path: '/', name: 'A', meta: { helpId: 'a' } },\n]\n"

    with pytest.raises(SystemExit, match="spreads a route array"):
        gate.parse_routes(source)


def test_parse_routes_allows_a_spread_inside_a_route_property():
    """Only a spread *of routes* is unreadable; one inside a value is fine."""
    source = "const routes = [\n  { path: '/', name: 'A', meta: { ...base, helpId: 'a' } },\n]\n"

    assert [(r.name, r.help_id) for r in gate.parse_routes(source)] == [("A", "a")]


def test_parse_routes_fails_closed_on_children_it_cannot_read():
    """A non-literal children array can hide named routes from the scan."""
    source = "const routes = [\n  { path: '/group', children: EXTERNAL_ROUTES },\n]\n"

    with pytest.raises(SystemExit, match="'children' the gate cannot read"):
        gate.parse_routes(source)


def test_parse_routes_does_not_borrow_a_sibling_array_as_children():
    """`children: someVar` must not be read as the next array on the record."""
    source = "const routes = [\n  { path: '/g', children: EXTERNAL, props: ['a', 'b'] },\n]\n"

    with pytest.raises(SystemExit, match="'children' the gate cannot read"):
        gate.parse_routes(source)


def test_parse_routes_names_the_line_of_an_unreadable_declaration():
    source = "const routes = [\n  { path: '/', name: 'A', meta: { helpId: 'a' } },\n  { path: '/c', name: CONST },\n]\n"

    with pytest.raises(SystemExit, match=r"index\.js:3"):
        gate.parse_routes(source)


def test_parse_routes_still_accepts_an_unnamed_record():
    """The catch-all redirect and a pure layout group carry no name."""
    source = "const routes = [\n  { path: '/:pathMatch(.*)*', redirect: '/' },\n  { path: '/g', children: [{ path: 'a', name: 'A', meta: { helpId: 'a' } }] },\n]\n"

    assert [route.name for route in gate.parse_routes(source)] == ["A"]


def test_parse_routes_rejects_an_unterminated_array():
    with pytest.raises(ValueError, match="unterminated array literal"):
        gate.parse_routes("const routes = [ { name: 'X' },\n")


def test_parse_routes_matches_the_real_router():
    source = (REPO_ROOT / "gui" / "src" / "router" / "index.js").read_text(encoding="utf-8")

    names = {route.name for route in gate.parse_routes(source)}

    assert {"Dashboard", "Settings", "Logic"} <= names


# ── widget enumeration ───────────────────────────────────────────────────────


def _write_widget(widgets_dir: Path, name: str, body: str) -> None:
    (widgets_dir / name).mkdir(parents=True)
    (widgets_dir / name / "index.ts").write_text(body, encoding="utf-8")


def test_parse_widget_types_derives_the_expected_help_id(tmp_path):
    widgets = tmp_path / "widgets"
    _write_widget(widgets, "ValueDisplay", "WidgetRegistry.register({\n  type: 'ValueDisplay',\n  label: 'x',\n})\n")

    surfaces = gate.parse_widget_types(widgets, tmp_path)

    assert [(surface.name, surface.help_id) for surface in surfaces] == [("ValueDisplay", "widget-value-display")]
    assert surfaces[0].origin == "widgets/ValueDisplay/index.ts"


def test_parse_widget_types_reads_a_double_quoted_type(tmp_path):
    widgets = tmp_path / "widgets"
    _write_widget(widgets, "Toggle", 'WidgetRegistry.register({\n  type: "Toggle",\n})\n')

    assert [surface.help_id for surface in gate.parse_widget_types(widgets, tmp_path)] == ["widget-toggle"]


def test_parse_widget_types_does_not_borrow_a_later_registrations_object(tmp_path):
    """`register(definition)` must not adopt the next `{...}` in the file."""
    widgets = tmp_path / "widgets"
    _write_widget(widgets, "Slider", "WidgetRegistry.register(definition)\nWidgetRegistry.register({ type: 'Slider' })\n")

    with pytest.raises(SystemExit, match="not a string literal"):
        gate.parse_widget_types(widgets, tmp_path)


def test_parse_widget_types_ignores_a_type_that_is_only_string_text(tmp_path):
    widgets = tmp_path / "widgets"
    _write_widget(widgets, "Slider", "WidgetRegistry.register({ label: \"type: 'Fake'\" })\n")

    with pytest.raises(SystemExit, match="not a string literal"):
        gate.parse_widget_types(widgets, tmp_path)


def test_parse_widget_types_survives_a_brace_inside_a_string(tmp_path):
    widgets = tmp_path / "widgets"
    _write_widget(widgets, "Slider", "WidgetRegistry.register({ label: 'a { b', type: 'Slider' })\n")

    assert [surface.name for surface in gate.parse_widget_types(widgets, tmp_path)] == ["Slider"]


def test_parse_widget_types_reads_the_registrations_own_type_not_a_nested_one(tmp_path):
    """`defaultConfig: { type: … }` is the widget's config, not its type."""
    widgets = tmp_path / "widgets"
    _write_widget(widgets, "Slider", "WidgetRegistry.register({ type: EXTRA, defaultConfig: { type: 'Slider' } })\n")

    with pytest.raises(SystemExit, match="not a string literal"):
        gate.parse_widget_types(widgets, tmp_path)


@pytest.mark.parametrize("call", ["WidgetRegistry.register(", "WidgetRegistry .register (", "WidgetRegistry\n  .register("])
def test_parse_widget_types_tolerates_whitespace_in_the_call(tmp_path, call):
    widgets = tmp_path / "widgets"
    _write_widget(widgets, "Slider", f"{call}{{ type: 'Slider' }})\n")

    assert [surface.name for surface in gate.parse_widget_types(widgets, tmp_path)] == ["Slider"]


def test_parse_widget_types_enumerates_every_registration_in_one_module(tmp_path):
    """A second register() call is a second shippable widget type."""
    widgets = tmp_path / "widgets"
    _write_widget(
        widgets,
        "Slider",
        "WidgetRegistry.register({ type: 'Slider', defaultConfig: { min: 0 } })\nWidgetRegistry.register({ type: 'SliderPro' })\n",
    )

    surfaces = gate.parse_widget_types(widgets, tmp_path)

    assert [surface.name for surface in surfaces] == ["Slider", "SliderPro"]


def test_parse_widget_types_fails_on_an_unterminated_registration(tmp_path):
    widgets = tmp_path / "widgets"
    _write_widget(widgets, "Broken", "WidgetRegistry.register({ type: 'Broken'\n")

    with pytest.raises(SystemExit, match="not a string literal"):
        gate.parse_widget_types(widgets, tmp_path)


def test_parse_widget_types_ignores_a_commented_out_registration(tmp_path):
    """A commented-out register() call registers nothing at runtime."""
    widgets = tmp_path / "widgets"
    _write_widget(
        widgets,
        "Slider",
        "// WidgetRegistry.register({ type: 'CommentedWidget' })\n"
        "/* WidgetRegistry.register({ type: 'BlockCommented' }) */\n"
        "WidgetRegistry.register({ type: 'Slider' })\n",
    )

    assert [surface.name for surface in gate.parse_widget_types(widgets, tmp_path)] == ["Slider"]


def test_parse_widget_types_ignores_a_module_without_registration(tmp_path):
    widgets = tmp_path / "widgets"
    _write_widget(widgets, "Helper", "export const x = { type: 'Helper' }\n")

    assert gate.parse_widget_types(widgets, tmp_path) == []


@pytest.mark.parametrize(
    "body",
    [
        "WidgetRegistry.register({\n  label: 'x',\n})\n",  # no type at all
        "WidgetRegistry.register({ type: EXTRA_TYPE })\n",  # type via a constant
        "WidgetRegistry.register(definition)\n",  # not an object literal
    ],
)
def test_parse_widget_types_fails_on_a_registration_it_cannot_read(tmp_path, body):
    """Skipping silently would under-enumerate a widget that does get built."""
    widgets = tmp_path / "widgets"
    _write_widget(widgets, "Broken", body)

    with pytest.raises(SystemExit, match="not a string literal"):
        gate.parse_widget_types(widgets, tmp_path)


def test_parse_widget_types_falls_back_to_an_absolute_origin_outside_the_repo(tmp_path):
    widgets = tmp_path / "widgets"
    _write_widget(widgets, "Toggle", "WidgetRegistry.register({ type: 'Toggle' })\n")

    surfaces = gate.parse_widget_types(widgets, REPO_ROOT)

    assert surfaces[0].origin == (widgets / "Toggle" / "index.ts").as_posix()


def test_parse_widget_types_covers_the_real_visu_widgets():
    surfaces = gate.parse_widget_types(REPO_ROOT / "frontend" / "src" / "widgets", REPO_ROOT)

    assert {"Toggle", "Slider", "Zeitschaltuhr"} <= {surface.name for surface in surfaces}


# ── skin enumeration ─────────────────────────────────────────────────────────


def _write_skin(skins_root: Path, directory: str, manifest: str) -> None:
    package = skins_root / "packages" / "skins" / directory
    package.mkdir(parents=True)
    (package / "manifest.json").write_text(manifest, encoding="utf-8")


def test_parse_skins_derives_the_expected_help_id(tmp_path):
    _write_skin(tmp_path, "glass", json.dumps({"name": "Glass Dark"}))

    surfaces = gate.parse_skins(tmp_path)

    assert [(surface.kind, surface.name, surface.help_id) for surface in surfaces] == [("skin", "Glass Dark", "skin-glass-dark")]


def test_parse_skins_reports_an_unreadable_manifest(tmp_path):
    _write_skin(tmp_path, "broken", "{not json")

    with pytest.raises(SystemExit, match="cannot read"):
        gate.parse_skins(tmp_path)


@pytest.mark.parametrize("manifest", ['{"name": ""}', '{"name": 42}', "{}"])
def test_parse_skins_requires_a_usable_name(tmp_path, manifest):
    _write_skin(tmp_path, "nameless", manifest)

    with pytest.raises(SystemExit, match="no usable 'name'"):
        gate.parse_skins(tmp_path)


def test_parse_skins_rejects_a_directory_that_is_not_a_skins_checkout(tmp_path):
    """Reporting zero skins here would look like a completed check of nothing."""
    with pytest.raises(SystemExit, match="is not an obs-visu-skins checkout"):
        gate.parse_skins(tmp_path)


def test_parse_skins_is_empty_for_a_checkout_without_skins(tmp_path):
    (tmp_path / "packages" / "skins").mkdir(parents=True)

    assert gate.parse_skins(tmp_path) == []


# ── logic block enumeration ──────────────────────────────────────────────────


def test_parse_logic_block_types_derives_the_expected_help_id():
    surfaces = gate.parse_logic_block_types([_NodeType("edge_detect"), _NodeType("and")])

    assert [(surface.kind, surface.name, surface.help_id) for surface in surfaces] == [
        ("logic-block", "edge_detect", "logic-block-edge-detect"),
        ("logic-block", "and", "logic-block-and"),
    ]


def test_parse_logic_block_types_skips_blocks_hidden_from_the_palette():
    """A block the palette never offers is not a surface a user can land on."""
    surfaces = gate.parse_logic_block_types([_NodeType("and"), _NodeType("notify_sms", hidden_from_palette=True)])

    assert [surface.name for surface in surfaces] == ["and"]


def test_parse_logic_block_types_tolerates_a_node_type_without_the_attribute():
    class Bare:
        type = "and"

    assert [surface.name for surface in gate.parse_logic_block_types([Bare()])] == ["and"]


def test_parse_logic_block_types_matches_the_real_registry():
    surfaces = gate.parse_logic_block_types(gate.builtin_logic_node_types())

    names = {surface.name for surface in surfaces}
    assert {"and", "edge_detect", "python_script"} <= names
    assert "notify_sms" not in names  # hidden_from_palette, legacy


def test_the_derived_help_id_matches_what_nodepalette_derives():
    """NodePalette.vue builds `logic-block-${type.replaceAll('_', '-')}`.

    The gate would happily check an id the GUI never asks for if the two
    transformations disagreed, so pin them against each other here.
    """
    for surface in gate.parse_logic_block_types(gate.builtin_logic_node_types()):
        assert surface.help_id == "logic-block-" + surface.name.replace("_", "-")


def test_validate_rejects_a_logic_node_type_that_is_not_snake_case():
    surface = gate.Surface(
        kind="logic-block",
        name="edgeDetect",
        help_id="logic-block-edge-detect",
        origin="obs.logic.registry.BUILTIN_NODE_TYPES",
    )

    errors = gate.validate([surface], [], _index("logic-block-edge-detect"), [])

    assert len(errors) == 1
    assert "must be lowercase snake_case" in errors[0]


def test_validate_reports_an_undocumented_logic_block():
    errors = gate.validate([_logic_block("edge_detect")], [], _index(), [])

    assert len(errors) == 1
    assert "logic-block:edge_detect: help_id 'logic-block-edge-detect' has no help page" in errors[0]


def test_validate_accepts_a_documented_logic_block():
    assert gate.validate([_logic_block("edge_detect")], [], _index("logic-block-edge-detect"), []) == []


# ── reference collection ─────────────────────────────────────────────────────


def test_collect_help_references_finds_static_attributes_and_literals(tmp_path):
    source = tmp_path / "gui" / "src"
    (source / "nested").mkdir(parents=True)
    (source / "View.vue").write_text(
        '<template>\n  <HelpButton help-id="logs-level" />\n  <StatCard :help-id="helpId" />\n</template>\n',
        encoding="utf-8",
    )
    (source / "nested" / "map.js").write_text("const m = { helpId: 'logic-block-and' }\n", encoding="utf-8")
    (source / "ignored.txt").write_text('help-id="nope"\n', encoding="utf-8")

    references = gate.collect_help_references(tmp_path, (("gui/src", (".vue", ".js")),))

    assert sorted((reference.help_id, reference.location) for reference in references) == [
        ("logic-block-and", "gui/src/nested/map.js:1"),
        ("logs-level", "gui/src/View.vue:2"),
    ]


def test_collect_help_references_reads_a_single_quoted_static_attribute(tmp_path):
    """`help-id='…'` is valid Vue syntax — an unread reference is a dead button."""
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "View.vue").write_text("<template>\n  <HelpButton help-id='logs-level' />\n</template>\n", encoding="utf-8")

    references = gate.collect_help_references(tmp_path, (("gui/src", (".vue",)),))

    assert [reference.help_id for reference in references] == ["logs-level"]


def test_collect_help_references_accepts_whitespace_around_the_equals_sign(tmp_path):
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "View.vue").write_text('<template>\n  <HelpButton help-id = "logs-level" />\n</template>\n', encoding="utf-8")

    assert [ref.help_id for ref in gate.collect_help_references(tmp_path, (("gui/src", (".vue",)),))] == ["logs-level"]


@pytest.mark.parametrize(
    "commented",
    [
        "// Removed example: { helpId: 'gone' }",
        "/* helpId: 'gone' */",
        "/*\n  helpId: 'gone'\n*/",
        '<!-- <HelpButton help-id="gone" /> -->',
        '<!--\n  <HelpButton help-id="gone" />\n-->',
    ],
)
def test_collect_help_references_ignores_a_commented_out_reference(tmp_path, commented):
    """The build ignores it, so it cannot be a dead button — nor a gate failure."""
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "View.vue").write_text(f"<template>\n  {commented}\n</template>\n", encoding="utf-8")

    assert gate.collect_help_references(tmp_path, (("gui/src", (".vue",)),)) == []


@pytest.mark.parametrize(
    "line",
    [
        "const removed = 'old'// { helpId: 'gone' }",
        "const removed = 'old' // { helpId: 'gone' }",
        "const removed = \"old\"/* { helpId: 'gone' } */",
    ],
)
def test_collect_help_references_sees_a_comment_that_starts_right_after_a_string(tmp_path, line):
    """`'old'// note` is a comment; only string state tells it from a URL."""
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "router.js").write_text(line + "\n", encoding="utf-8")

    assert gate.collect_help_references(tmp_path, (("gui/src", (".js",)),)) == []


def test_collect_help_references_ignores_prose_inside_a_string(tmp_path):
    """A sentence mentioning `helpId: '…'` neither renders nor executes."""
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "msg.js").write_text("const msg = \"set helpId: 'gone' in meta\"\n", encoding="utf-8")

    assert gate.collect_help_references(tmp_path, (("gui/src", gate._SOURCE_SUFFIXES),)) == []


@pytest.mark.parametrize("suffix", [".vue", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"])
def test_collect_help_references_scans_every_supported_extension(tmp_path, suffix):
    """A reference moved into a helper module must stay visible."""
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    body = "const m = { helpId: 'logs-level' }\n"
    # An SFC keeps its script in a <script> block; that is where the property
    # scan looks (see _blank_outside_script).
    (source / f"helpMap{suffix}").write_text(f"<script setup>\n{body}</script>\n" if suffix == ".vue" else body, encoding="utf-8")

    references = gate.collect_help_references(tmp_path, (("gui/src", gate._SOURCE_SUFFIXES),))

    assert [ref.help_id for ref in references] == ["logs-level"]


@pytest.mark.parametrize("end_tag", ["</script>", "</script >", "</script\t\n bar>", "</script/>"])
def test_collect_help_references_reads_script_with_any_valid_end_tag(tmp_path, end_tag):
    """A missed end tag means the block is not recognised and its declarations vanish."""
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "View.vue").write_text(f"<script setup>\nconst m = {{ helpId: 'logs-level' }}\n{end_tag}\n", encoding="utf-8")

    assert [ref.help_id for ref in gate.collect_help_references(tmp_path, (("gui/src", (".vue",)),))] == ["logs-level"]


def test_collect_help_references_does_not_treat_a_similar_tag_as_the_end(tmp_path):
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "View.vue").write_text("<script>const m = { helpId: 'gone' }</scriptx>\n", encoding="utf-8")

    assert gate.collect_help_references(tmp_path, (("gui/src", (".vue",)),)) == []


def test_collect_help_references_ignores_a_css_custom_property(tmp_path):
    """`--panel-helpId: '…'` is a style declaration, not a help reference."""
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "View.vue").write_text(
        "<template><div class=\"panel\" /></template>\n<style>\n.panel { --panel-helpId: 'gone'; }\n</style>\n", encoding="utf-8"
    )

    assert gate.collect_help_references(tmp_path, (("gui/src", (".vue",)),)) == []


def test_collect_help_references_ignores_property_shaped_template_prose(tmp_path):
    """Rendered text mentioning a property declares nothing."""
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "View.vue").write_text("<template>\n  <p>Set helpId: 'gone' in meta</p>\n</template>\n", encoding="utf-8")

    assert gate.collect_help_references(tmp_path, (("gui/src", (".vue",)),)) == []


def test_collect_help_references_still_reads_a_help_attribute_in_the_template(tmp_path):
    """Restricting the property scan must not hide the template attribute."""
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "View.vue").write_text('<template>\n  <HelpButton help-id="logs-level" />\n</template>\n', encoding="utf-8")

    assert [ref.help_id for ref in gate.collect_help_references(tmp_path, (("gui/src", (".vue",)),))] == ["logs-level"]


def test_both_frontends_are_scanned_with_the_same_extensions():
    assert {suffixes for _, suffixes in gate._REFERENCE_DIRS} == {gate._SOURCE_SUFFIXES}


def test_collect_help_references_keeps_a_help_id_inside_a_template_literal(tmp_path):
    """A template literal is a string, not a comment host."""
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "map.js").write_text("const url = `a//b`\nconst m = { helpId: 'logs-level' }\n", encoding="utf-8")

    assert [ref.help_id for ref in gate.collect_help_references(tmp_path, (("gui/src", (".js",)),))] == ["logs-level"]


def test_collect_help_references_reads_a_template_expression(tmp_path):
    """`${…}` is executable JavaScript and can hold a real declaration."""
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "tpl.js").write_text("const t = `a ${ { helpId: 'logs-level' } } b`\n", encoding="utf-8")

    assert [ref.help_id for ref in gate.collect_help_references(tmp_path, (("gui/src", (".js",)),))] == ["logs-level"]


def test_collect_help_references_ignores_template_literal_text(tmp_path):
    """The text around `${…}` is still string content, not code."""
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "tpl.js").write_text("const t = `write helpId: 'gone' here`\n", encoding="utf-8")

    assert gate.collect_help_references(tmp_path, (("gui/src", (".js",)),)) == []


def test_collect_help_references_survives_a_regex_containing_a_quote(tmp_path):
    """`/['\"]/` must not open a string and swallow the declaration after it."""
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "re.js").write_text("const q = /['\"]/\nconst m = { helpId: 'logs-level' }\n", encoding="utf-8")

    assert [ref.help_id for ref in gate.collect_help_references(tmp_path, (("gui/src", (".js",)),))] == ["logs-level"]


def test_collect_help_references_ignores_a_help_id_inside_a_regex(tmp_path):
    """A regex is opaque: it cannot carry a declaration."""
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "re.js").write_text("const q = /helpId: 'gone'/\n", encoding="utf-8")

    assert gate.collect_help_references(tmp_path, (("gui/src", (".js",)),)) == []


def test_collect_help_references_treats_division_as_division(tmp_path):
    """`a / b` is not a regex — the declaration after it must still be read."""
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "div.js").write_text("const ratio = width / height\nconst m = { helpId: 'logs-level' }\n", encoding="utf-8")

    assert [ref.help_id for ref in gate.collect_help_references(tmp_path, (("gui/src", (".js",)),))] == ["logs-level"]


def test_collect_help_references_ignores_a_ternary_that_looks_like_a_property(tmp_path):
    """`true ? 'helpId' : 'x'` declares nothing — the colon is not a key's."""
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "pick.js").write_text("const pick = true ? 'helpId' : 'missing-ternary-help'\n", encoding="utf-8")

    assert gate.collect_help_references(tmp_path, (("gui/src", (".js",)),)) == []


def test_collect_help_references_keeps_a_reference_next_to_a_url(tmp_path):
    """`//` in a URL must not swallow the rest of the line and hide a reference."""
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "View.vue").write_text(
        '<template>\n  <a href="https://example.com/x"><HelpButton help-id="logs-level" /></a>\n'
        '  <a href="//cdn.example.com/y"><HelpButton help-id="logs-table" /></a>\n</template>\n',
        encoding="utf-8",
    )

    references = gate.collect_help_references(tmp_path, (("gui/src", (".vue",)),))

    assert sorted(ref.help_id for ref in references) == ["logs-level", "logs-table"]


def test_collect_help_references_reports_the_line_after_blanking_comments(tmp_path):
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "View.vue").write_text('<template>\n  <!--\n    old\n  -->\n  <HelpButton help-id="logs-level" />\n</template>\n', encoding="utf-8")

    assert [ref.location for ref in gate.collect_help_references(tmp_path, (("gui/src", (".vue",)),))] == ["gui/src/View.vue:5"]


def test_collect_help_references_skips_a_bound_attribute_and_a_non_literal_prop(tmp_path):
    source = tmp_path / "gui" / "src"
    source.mkdir(parents=True)
    (source / "StatCard.vue").write_text(
        '<template>\n  <HelpButton v-bind:help-id="helpId" />\n</template>\n'
        "<script setup>\nconst props = defineProps({ helpId: { type: String, default: null } })\n</script>\n",
        encoding="utf-8",
    )

    assert gate.collect_help_references(tmp_path, (("gui/src", (".vue",)),)) == []


def test_collect_help_references_skips_a_missing_directory(tmp_path):
    assert gate.collect_help_references(tmp_path, (("does/not/exist", (".vue",)),)) == []


def test_collect_help_references_skips_node_modules(tmp_path):
    vendored = tmp_path / "gui" / "src" / "node_modules" / "pkg"
    vendored.mkdir(parents=True)
    (vendored / "index.js").write_text("const m = { helpId: 'vendored' }\n", encoding="utf-8")

    assert gate.collect_help_references(tmp_path, (("gui/src", (".js",)),)) == []


# ── allowlist parsing ────────────────────────────────────────────────────────


def test_load_allowlist_parses_entries_and_ignores_comments():
    entries, errors = gate.load_allowlist("# header\n\nroute:Login  # public screen\nwidget:Uhr # not written yet\n")

    assert errors == []
    assert [(entry.key, entry.reason, entry.line) for entry in entries] == [
        ("route:Login", "public screen", 3),
        ("widget:Uhr", "not written yet", 4),
    ]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("route:Login", "has no reason"),
        ("route:Login  #", "has no reason"),
        ("page:Login  # wrong kind", "is not a"),
        ("route  # no name", "is not a"),
        ("route:  # empty name", "is not a"),
    ],
)
def test_load_allowlist_rejects_malformed_entries(line, expected):
    entries, errors = gate.load_allowlist(line + "\n")

    assert entries == []
    assert expected in errors[0]


def test_load_allowlist_rejects_a_duplicate_entry():
    entries, errors = gate.load_allowlist("route:Login  # a\nroute:Login  # b\n")

    assert len(entries) == 1
    assert "duplicate entry" in errors[0]


def test_the_real_allowlist_is_well_formed():
    text = (REPO_ROOT / "tools" / "help-contract-allowlist.txt").read_text(encoding="utf-8")

    entries, errors = gate.load_allowlist(text)

    assert errors == []
    assert any(entry.key == "route:Login" for entry in entries)


# ── validation ───────────────────────────────────────────────────────────────


def test_validate_accepts_a_documented_surface():
    errors = gate.validate([_route("Logs", "logs")], [gate.Reference("logs", "gui/src/views/LogView.vue:1")], _index("logs"), [])

    assert errors == []


def test_validate_reports_a_route_without_a_help_id():
    errors = gate.validate([_route("BrandNew", None)], [], _index(), [])

    assert errors == ["route:BrandNew: no help_id declared in gui/src/router/index.js — add one, or allowlist it with a reason"]


def test_validate_accepts_an_allowlisted_route_without_a_help_id():
    assert gate.validate([_route("Login", None)], [], _index(), [_allow("route:Login")]) == []


def test_validate_reports_an_undocumented_widget():
    errors = gate.validate([_widget("Uhr", "widget-uhr")], [], _index(), [])

    assert len(errors) == 1
    assert "widget:Uhr: help_id 'widget-uhr' has no help page" in errors[0]
    assert "{#widget-uhr}" in errors[0]


def test_validate_accepts_an_allowlisted_widget():
    assert gate.validate([_widget("Uhr", "widget-uhr")], [], _index(), [_allow("widget:Uhr")]) == []


def test_validate_reports_an_unresolvable_reference():
    reference = gate.Reference("logs-typo", "gui/src/views/LogView.vue:21")

    errors = gate.validate([], [reference], _index("logs-level"), [])

    assert errors == ["gui/src/views/LogView.vue:21: help_id 'logs-typo' does not resolve in the help index"]


def test_validate_deduplicates_repeated_references():
    reference = gate.Reference("logs-typo", "gui/src/views/LogView.vue:21")

    errors = gate.validate([], [reference, reference], _index(), [])

    assert len(errors) == 1


@pytest.mark.parametrize("bad_id", ["", "1invalid", "has space", "{{ dynamic }}"])
def test_validate_rejects_a_syntactically_invalid_reference(bad_id):
    errors = gate.validate([], [gate.Reference(bad_id, "gui/src/View.vue:3")], _index(), [])

    assert errors == [f"gui/src/View.vue:3: {bad_id!r} is not a valid help_id"]


def test_validate_rejects_a_syntactically_invalid_surface_help_id():
    errors = gate.validate([_route("Logs", "1nope")], [], _index(), [])

    assert errors == ["route:Logs: '1nope' is not a valid help_id (gui/src/router/index.js)"]


def test_validate_allows_two_routes_to_point_at_the_same_page():
    """Both buttons resolve — that is all the contract asks of a route."""
    assert gate.validate([_route("Logs", "logs"), _route("LogsAlias", "logs")], [], _index("logs"), []) == []


def test_validate_reports_a_collision_between_two_derived_help_ids():
    """A derived id shared by two surfaces cannot tell them apart."""
    first = gate.Surface(kind="widget", name="QrCode", help_id="widget-qr-code", origin="a")
    second = gate.Surface(kind="widget", name="Qr_code", help_id="widget-qr-code", origin="b")

    errors = gate.validate([first, second], [], _index("widget-qr-code"), [])

    assert errors == ["widget:Qr_code: derived help_id 'widget-qr-code' collides with widget:QrCode"]


def test_validate_reports_help_index_duplicates():
    errors = gate.validate([], [], _index(duplicates=['duplicate help_id "logs" in locale "de" (de/logs.md)']), [])

    assert errors == ['help index: duplicate help_id "logs" in locale "de" (de/logs.md)']


def test_validate_reports_a_help_id_missing_in_one_locale():
    """generate-help-index.mjs only warns about this; the gate must fail."""
    errors = gate.validate([], [], _index(incomplete=[{"id": "logs", "missing": ["en"]}]), [])

    assert errors == ["help index: help_id 'logs' is missing in locale(s): en"]


def test_validate_reports_a_stale_allowlist_entry():
    errors = gate.validate([], [], _index(), [_allow("widget:Gone", line=7)])

    assert errors == ["allowlist line 7: 'widget:Gone' is not a surface that exists — remove it"]


def test_validate_reports_a_redundant_allowlist_entry():
    errors = gate.validate([_route("Logs", "logs")], [], _index("logs"), [_allow("route:Logs", line=4)])

    assert errors == ["allowlist line 4: 'route:Logs' is documented — remove the exemption"]


def test_validate_keeps_skin_exemptions_when_skins_are_not_checked():
    assert gate.validate([], [], _index(), [_allow("skin:glass")], skins_checked=False) == []


def test_validate_checks_skin_exemptions_when_skins_are_checked():
    errors = gate.validate([], [], _index(), [_allow("skin:glass", line=9)], skins_checked=True)

    assert errors == ["allowlist line 9: 'skin:glass' is not a surface that exists — remove it"]


def test_validate_tolerates_an_index_without_optional_keys():
    assert gate.validate([], [], {}, []) == []


# ── end to end ───────────────────────────────────────────────────────────────


def test_collect_surfaces_without_a_skins_checkout():
    surfaces = gate.collect_surfaces(REPO_ROOT, None)

    assert {surface.kind for surface in surfaces} == {"route", "widget", "logic-block"}


def test_collect_surfaces_with_a_skins_checkout(tmp_path):
    _write_skin(tmp_path, "glass", json.dumps({"name": "glass"}))

    surfaces = gate.collect_surfaces(REPO_ROOT, tmp_path)

    assert [surface.key for surface in surfaces if surface.kind == "skin"] == ["skin:glass"]


def test_main_rejects_a_missing_skins_dir(tmp_path, capsys):
    assert gate.main(["--skins-dir", str(tmp_path / "absent")]) == 1
    assert "does not exist" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("node") is None, reason="the help index is built by generate-help-index.mjs")
def test_the_repository_satisfies_its_own_help_contract(capsys):
    assert gate.main([]) == 0
    assert "Help contract check passed" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("node") is None, reason="the help index is built by generate-help-index.mjs")
def test_build_help_index_matches_the_generated_index():
    index = gate.build_help_index(REPO_ROOT / "help")

    assert index["duplicates"] == []
    assert set(index["helpIds"]["settings"]) == {"de", "en"}


def test_build_help_index_requires_node(monkeypatch):
    monkeypatch.setattr(gate.shutil, "which", lambda _: None)

    with pytest.raises(SystemExit, match="node is required"):
        gate.build_help_index(REPO_ROOT / "help")


def test_build_help_index_surfaces_a_generator_failure(monkeypatch):
    monkeypatch.setattr(gate.shutil, "which", lambda _: "/usr/bin/node")
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, stdout="", stderr="duplicate help_id\n"),
    )

    with pytest.raises(SystemExit, match="duplicate help_id"):
        gate.build_help_index(REPO_ROOT / "help")
