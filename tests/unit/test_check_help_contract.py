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


def test_validate_accepts_the_same_widget_type_registered_twice():
    """WidgetRegistry keeps one definition per type and replaces it."""
    twice = [
        gate.Surface(kind="widget", name="Slider", help_id="widget-slider", origin="a:1"),
        gate.Surface(kind="widget", name="Slider", help_id="widget-slider", origin="b:2"),
    ]

    assert gate.validate(twice, [], _index("widget-slider"), []) == []


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


# ── index vs. what the site really renders ───────────────────────────────────


def _index_of(help_id: str, page: str) -> dict:
    return {"helpIds": {help_id: {"de": f"/help/de/{page}#{help_id}", "en": f"/help/en/{page}#{help_id}"}}}


def test_index_matches_render_accepts_an_id_the_page_renders():
    rendered = {"de/x.html": ["a"], "en/x.html": ["a"]}

    assert gate.index_matches_render(_index_of("a", "x.html"), rendered) == []


def test_index_matches_render_reports_an_id_no_element_owns():
    """This is what a Markdown construct the index scan misreads looks like."""
    rendered = {"de/x.html": [], "en/x.html": []}

    errors = gate.index_matches_render(_index_of("a", "x.html"), rendered)

    assert len(errors) == 2
    assert "renders no element with that id" in errors[0]


def test_index_matches_render_reports_a_page_the_build_does_not_produce():
    errors = gate.index_matches_render(_index_of("a", "gone.html"), {"de/x.html": ["a"], "en/x.html": ["a"]})

    assert all("which the help build does not produce" in error for error in errors)


def test_index_matches_render_resolves_a_locale_root_to_its_index_page():
    """`/help/de/` is served by de/index.html."""
    rendered = {"de/index.html": ["a"], "en/index.html": ["a"]}

    assert gate.index_matches_render(_index_of("a", ""), rendered) == []


def test_render_covers_index_reports_a_rendered_anchor_the_index_missed():
    """The other direction: a heading form the index scan does not recognise."""
    errors = gate.render_covers_index({"helpIds": {}}, {"de/index.html": ["setext"]}, {"de/index.html": {"setext"}})

    assert errors == ["help index: 'setext' is written as an anchor and rendered in de/index.html, but the index does not list it"]


def test_render_covers_index_ignores_an_automatic_slug():
    """Only an id written as `{#id}` on that same page is a deliberate anchor."""
    assert gate.render_covers_index({"helpIds": {}}, {"de/index.html": ["some-heading-text"]}, {"de/index.html": {"setext"}}) == []


def test_render_covers_index_does_not_pair_pages_with_each_other():
    """An anchor written on one page must not meet a slug rendered on another."""
    rendered = {"de/adapters.html": ["review-cross-page"]}
    written = {"de/dashboard.html": {"review-cross-page"}}

    assert gate.render_covers_index({"helpIds": {}}, rendered, written) == []


def test_render_covers_index_accepts_an_indexed_anchor():
    assert gate.render_covers_index({"helpIds": {"setext": {}}}, {"de/index.html": ["setext"]}, {"de/index.html": {"setext"}}) == []


def test_render_covers_index_reports_each_id_once():
    rendered = {"de/index.html": ["setext"], "en/index.html": ["setext"]}
    written = {"de/index.html": {"setext"}, "en/index.html": {"setext"}}

    assert len(gate.render_covers_index({"helpIds": {}}, rendered, written)) == 1


def test_written_anchor_ids_are_keyed_by_their_rendered_page():
    sources = {"de/a.html": "## A {#written-a}\n\nB {#written-b}\n===\n", "de/b.html": "## C {#written-c}\n"}

    assert gate.written_anchor_ids(sources) == {"de/a.html": {"written-a", "written-b"}, "de/b.html": {"written-c"}}


def test_written_anchor_ids_ignores_an_anchor_shaped_string_in_prose():
    """A paragraph is not a heading; markdown-it may slug an unrelated one alike."""
    assert gate.written_anchor_ids({"de/a.html": "Ein Absatz mit {#prose} als Text.\n\n## Prose\n"}) == {"de/a.html": set()}


def test_written_anchor_ids_reads_a_closing_hash_heading():
    """CommonMark allows a closing hash sequence, and VitePress keeps the id."""
    assert gate.written_anchor_ids({"de/a.html": "## A {#written-a} ##\n"}) == {"de/a.html": {"written-a"}}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("> ## Quoted {#q}\n", {"q"}),
        ("- ## Listed {#l}\n", {"l"}),
        ("1. ## Numbered {#n}\n", {"n"}),
        ("> Setext {#s}\n> =======\n", {"s"}),
    ],
)
def test_written_anchor_ids_reads_headings_inside_containers(source, expected):
    """VitePress renders these with their id — verified against a real build."""
    assert gate.written_anchor_ids({"de/a.html": source}) == {"de/a.html": expected}


def test_written_anchor_ids_relies_on_the_caller_to_strip_unrendered_regions():
    """`--stripped` blanks fenced code, so nothing here has to know about it."""
    assert gate.written_anchor_ids({"de/a.html": "\n\n## Fenced {#f}\n\n"}) == {"de/a.html": {"f"}}


@pytest.mark.skipif(shutil.which("node") is None, reason="the strippers are a Node helper")
def test_written_anchor_ids_covers_the_real_help_sources():
    written = gate.written_anchor_ids(gate.stripped_help_sources(REPO_ROOT))

    assert "dashboard" in written["de/dashboard/overview.html"]
    assert "logic-block-edge-detect" in written["en/logic/blocks-logic.html"]


def test_index_matches_render_tolerates_an_empty_index():
    assert gate.index_matches_render({}, {}) == []


# ── end to end ───────────────────────────────────────────────────────────────


_SCAN = {
    "routes": [{"name": "Dashboard", "helpId": "dashboard", "file": "gui/src/router/index.js", "line": 9}],
    "widgets": [{"type": "Toggle", "file": "frontend/src/widgets/Toggle/index.ts", "line": 5}],
    "references": [{"helpId": "dashboard", "file": "gui/src/views/DashboardView.vue", "line": 3}],
    "unreadable": [],
}


def test_routes_and_widgets_become_surfaces():
    surfaces = gate.routes_from(_SCAN) + gate.widgets_from(_SCAN)

    assert [(s.kind, s.name, s.help_id, s.origin) for s in surfaces] == [
        ("route", "Dashboard", "dashboard", "gui/src/router/index.js:9"),
        ("widget", "Toggle", "widget-toggle", "frontend/src/widgets/Toggle/index.ts:5"),
    ]


def test_references_carry_their_location():
    assert [(r.help_id, r.location) for r in gate.references_from(_SCAN)] == [("dashboard", "gui/src/views/DashboardView.vue:3")]


def test_an_unreadable_declaration_becomes_an_error():
    """Never skipped: the surface ships either way, only the checker guesses."""
    scan = {**_SCAN, "unreadable": [{"kind": "route", "file": "gui/src/router/index.js", "line": 22, "problem": "declares X"}]}

    assert gate.unreadable_errors(scan) == ["gui/src/router/index.js:22 declares X"]


def test_collect_surfaces_without_a_skins_checkout():
    surfaces = gate.collect_surfaces(_SCAN, None)

    assert {surface.kind for surface in surfaces} == {"route", "widget", "logic-block"}


def test_collect_surfaces_with_a_skins_checkout(tmp_path):
    _write_skin(tmp_path, "glass", json.dumps({"name": "glass"}))

    surfaces = gate.collect_surfaces(_SCAN, tmp_path)

    assert [surface.key for surface in surfaces if surface.kind == "skin"] == ["skin:glass"]


@pytest.mark.skipif(shutil.which("node") is None, reason="the scan is a Node helper")
def test_the_scanner_reads_the_repository_itself():
    scan = gate.scan_declarations(REPO_ROOT)

    assert scan["unreadable"] == []
    assert {route["name"] for route in scan["routes"]} >= {"Dashboard", "Settings"}
    assert {widget["type"] for widget in scan["widgets"]} >= {"Toggle", "Zeitschaltuhr"}


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
