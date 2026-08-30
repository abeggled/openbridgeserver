#!/usr/bin/env python3

"""Fail CI when a documentation-required UI surface has no resolvable help_id.

Third completeness gate next to ``check_authz_contract.py`` (every live v1
route must be declared) and ``check_i18n_guard.py`` (every user-facing string
must be a translation key): every UI surface that users can land on must have
help content, or an explicitly justified opt-out.

The gate introspects the real surfaces from the registries that already exist
in the code, derives the help_id each surface is expected to carry, and then
requires that id to resolve in *every* locale of the generated help index:

    surface type   enumerated from                        expected help_id
    ------------   ------------------------------------   -----------------------
    route          gui/src/router/index.js (``routes[]``)  ``route.meta.helpId``
    widget         frontend/src/widgets/*/index.ts         ``widget-<kebab type>``
    logic block    obs.logic.registry.BUILTIN_NODE_TYPES   ``logic-block-<kebab type>``
    skin           <skins repo>/packages/skins/*/          ``skin-<kebab name>``

Routes carry their id explicitly because the Admin-GUI reads it at runtime
(``TopBar.vue`` renders the page-level help button from ``route.meta.helpId``),
so the field is live wiring rather than gate-only metadata. The other three
follow a convention: ``NodePalette.vue`` derives a block's help_id from its
node type the same way this gate does, and widgets and skins have no runtime
consumer for such a field yet.

Logic blocks that are ``hidden_from_palette`` are excluded by rule, not by
allowlist: the palette is the only place a user can pick a block from, so a
block it never offers is not a surface anyone can land on (today those are the
two legacy notification blocks).

The scans read JavaScript textually, and where that cannot be done with
confidence they **fail closed** rather than skip: a route whose ``name``, a
``children`` array, or a widget registration's ``type`` is not something the
scan can resolve to a literal aborts the run with the file and line. Silently
passing over such a declaration is the one outcome a coverage gate must never
produce — the surface ships, and the gate reports success.

On top of coverage the gate closes the resolvability hole the help generator
leaves open: every help_id literally referenced from GUI/Visu sources must
exist, and — unlike ``generate-help-index.mjs``, which only warns — a help_id
missing in one locale fails the build.

Deliberately undocumented surfaces belong in ``tools/help-contract-allowlist.txt``
with a reason, mirroring ``tools/i18n-allowlist.txt``. The allowlist is checked
back against reality: an entry for a surface that no longer exists, or for one
that meanwhile *is* documented, fails too, so the list cannot rot into a
blanket exemption.

Usage:
    python tools/check_help_contract.py [--skins-dir PATH]

The skins live in the separate ``obs-visu-skins`` repository. Without
``--skins-dir`` (or ``OBS_VISU_SKINS_DIR``) that surface is reported as not
checked instead of silently passing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Same shape as generate-help-index.mjs's HEADING_RE capture group: a help_id
# is a Markdown heading anchor, so anything that cannot be one is not an id.
_HELP_ID_RE = re.compile(r"^[A-Za-z][\w-]*$")

_SURFACE_KINDS = ("route", "widget", "logic-block", "skin")

# NodePalette.vue derives a block's help_id as `logic-block-${type.replaceAll('_', '-')}`.
# That is identical to kebab() only while the node type is lowercase snake_case,
# so the gate enforces the shape instead of letting the two silently disagree
# on a camelCase type and check an id the GUI never asks for.
_LOGIC_NODE_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Kinds whose help_id is derived from the surface's own name. Two of them
# landing on the same id means the derivation cannot tell them apart, so one
# surface's "documented" status is really borrowed from the other. A route, by
# contrast, declares its id by hand, and pointing two routes at one page (a
# detail route at its list page, say) is a legitimate authoring choice — both
# buttons resolve, which is all the contract asks for.
_DERIVED_ID_KINDS = frozenset({"widget", "logic-block", "skin"})

# `:help-id="expr"` / `v-bind:help-id="expr"` is a dynamic binding whose value
# is only known at runtime — the leading colon is what distinguishes it from
# the static attribute this gate can resolve. Both quote styles are valid Vue
# template syntax and both must be seen: an unread reference is a dead help
# button the gate would wave through.
_STATIC_HELP_ATTR_RE = re.compile(r"(?<![:\w-])help-id\s*=\s*(['\"])(.*?)\1")


# Object-literal form, used by route meta and by prop defaults. Deliberately
# narrow: it matches a property literally named `helpId`, so a lookup table
# keyed by something else (`and: 'logic-block-and'`) is NOT collected here.
# Such tables are covered by a surface rule instead — see the logic-block row
# in the module docstring — because a static scan cannot tell an arbitrary
# string constant from a help_id. A non-literal value (`helpId: props.helpId`)
# has no quotes and is skipped by construction.
def _js_property(name: str, value: str) -> re.Pattern:
    """A JS object property, with the key optionally quoted (`name:`, `'name':`)."""
    return re.compile(rf"(?:\b{name}|['\"]{name}['\"])\s*:\s*{value}")


_HELP_ID_LITERAL_RE = _js_property("helpId", r"['\"]([^'\"]*)['\"]")

# Comments and string text neither render nor execute, so neither may be read
# as code — and code must never be mistaken for either. One scanner classifies
# the source once; both views below are built from its regions.
_STRING_DELIMITERS = frozenset("\"'`")


def _scan_regions(source: str, start: int = 0, end: int | None = None) -> list[tuple[str, int, int]]:
    """Classify ``source[start:end]`` into ``code`` / ``string`` / ``comment`` spans.

    A template literal's ``${...}`` is executable JavaScript, so it is scanned
    as code rather than swallowed with the surrounding text — a real
    ``helpId`` declaration can live in there.
    """
    end = len(source) if end is None else end
    regions: list[tuple[str, int, int]] = []
    index = start
    while index < end:
        char = source[index]
        if char in _STRING_DELIMITERS:
            index = _scan_string(source, index, end, regions)
            continue
        opener = next((o for o in ("//", "/*", "<!--") if source.startswith(o, index)), None)
        if opener is None:
            index += 1
            continue
        closer = {"//": "\n", "/*": "*/", "<!--": "-->"}[opener]
        stop = source.find(closer, index + len(opener))
        stop = end if stop < 0 or stop >= end else (stop if opener == "//" else stop + len(closer))
        regions.append(("comment", index, stop))
        index = stop
    return regions


def _scan_string(source: str, start: int, end: int, regions: list[tuple[str, int, int]]) -> int:
    """Append the regions of the string literal at ``start``; return its end."""
    quote = source[start]
    index = start + 1
    text_from = index
    while index < end:
        char = source[index]
        if char == "\\" and index + 1 < end:
            index += 2
            continue
        if quote == "`" and source.startswith("${", index):
            if text_from < index:
                regions.append(("string", text_from, index))
            depth = 0
            cursor = index + 1
            while cursor < end:
                if source[cursor] == "{":
                    depth += 1
                elif source[cursor] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                cursor += 1
            regions.extend(_scan_regions(source, index + 2, min(cursor, end)))
            index = min(cursor + 1, end)
            text_from = index
            continue
        if char == quote:
            if text_from < index:
                regions.append(("string", text_from, index))
            return index + 1
        index += 1
    if text_from < end:
        regions.append(("string", text_from, end))
    return end


def _blank_span(source: str, start: int, end: int) -> str:
    """The span with every character replaced by a space, newlines kept."""
    return "".join("\n" if char == "\n" else " " for char in source[start:end])


def _blank_comments(source: str) -> str:
    """Blank out comments, preserving newlines so reported lines stay right."""
    out = list(source)
    for kind, start, end in _scan_regions(source):
        if kind == "comment":
            out[start:end] = _blank_span(source, start, end)
    return "".join(out)


# Both frontends can hold either dialect — a reference moved into a helper
# module must not become invisible just because of its extension.
_SOURCE_SUFFIXES = (".vue", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_REFERENCE_DIRS = (
    ("gui/src", _SOURCE_SUFFIXES),
    ("frontend/src", _SOURCE_SUFFIXES),
)

# Vue/JS string properties are written with either quote style, and a route or
# widget declared with the other one must not fall out of the enumeration —
# an unseen surface is an undocumented surface the gate would never notice.
_JS_NAME_RE = _js_property("name", r"(['\"])(.*?)\1")
_JS_TYPE_RE = _js_property("type", r"(['\"])(.*?)\1")
_META_RE = _js_property("meta", r"(?=\{)")
_CHILDREN_RE = _js_property("children", r"")
_ANY_NAME_PROPERTY_RE = _js_property("name", r"")
# ES6 shorthand: `{ path, name }` gives the route a runtime name with no colon
# in sight, so the colon-based pattern above never sees it.
_SHORTHAND_NAME_RE = re.compile(r"(?<![\w$.])name\s*(?=[,}])")
# `WidgetRegistry . register (` is the same call after a formatter touches it.
_WIDGET_REGISTER_RE = re.compile(r"\bWidgetRegistry\s*\.\s*register\s*\(")

# Split before an uppercase letter that starts a new word, so acronyms stay
# whole: ValueDisplay -> value-display, QrCode -> qr-code, RTR -> rtr,
# IFrame -> iframe (the second alternative needs two leading capitals, so a
# single-letter prefix is not mistaken for a word of its own).
_KEBAB_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z][A-Z])(?=[A-Z][a-z])")


def kebab(name: str) -> str:
    """Return ``name`` as the lower-case kebab form used in expected help_ids."""
    return _KEBAB_BOUNDARY_RE.sub("-", name).replace("_", "-").replace(" ", "-").lower()


@dataclass(frozen=True)
class Surface:
    """One documentation-required UI surface."""

    kind: str
    name: str
    #: help_id the surface is expected to resolve to; ``None`` when the surface
    #: declares no id at all (an undeclared route, i.e. the surface itself is
    #: the finding).
    help_id: str | None
    #: Where the surface was enumerated from, for the error message.
    origin: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name}"


@dataclass(frozen=True)
class Reference:
    """A help_id literally referenced from GUI/Visu source."""

    help_id: str
    location: str


@dataclass(frozen=True)
class AllowlistEntry:
    key: str
    reason: str
    line: int


class _UnreadableRoute(Exception):
    """A route declaration the scan cannot read, with the offset it starts at."""

    def __init__(self, offset: int, problem: str) -> None:
        super().__init__(problem)
        self.offset = offset
        self.problem = problem


_SPREAD_RE = re.compile(r"\.\.\.")


def _strip_nested(text: str) -> str:
    """Blank everything inside brackets, keeping this level's text and offsets."""
    depth = 0
    out: list[str] = []
    for char in text:
        if char in "{[(":
            depth += 1
            out.append(char if depth == 1 else " ")
            continue
        if char in "}])":
            depth -= 1
            out.append(char if depth == 0 else " ")
            continue
        out.append(char if depth == 0 else ("\n" if char == "\n" else " "))
    return "".join(out)


def _top_level_object_literals(text: str) -> list[tuple[str, int]]:
    """Return the ``{...}`` literals sitting directly in an array body, with offsets."""
    items: list[tuple[str, int]] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char in "{[":
            if depth == 0 and char == "{":
                start = index
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0 and char == "}":
                items.append((text[start : index + 1], start))
    return items


def _iter_route_records(array_body: str, base_offset: int = 0):
    """Yield each route record in ``array_body``, descending through ``children``.

    Only ``children`` is followed. Recursing into every nested object would
    turn an ordinary static object that happens to carry a ``name`` — a
    ``props`` bag, say — into a fictitious route and fail the gate over a page
    that does not exist.
    """
    spread = _SPREAD_RE.search(_strip_nested(array_body))
    if spread is not None:
        # `[...EXTRA_ROUTES, { … }]` can contribute named, reachable routes the
        # scan never sees. Enumerating only what is visible would report the
        # rest as covered.
        raise _UnreadableRoute(base_offset + spread.start(), "spreads a route array the gate cannot read; list the routes inline")
    for record, offset in _top_level_object_literals(array_body):
        yield record, base_offset + offset
        children = next(_direct_property_matches(record, _CHILDREN_RE), None)
        if children is None:
            continue
        bracket = record.find("[", children.end())
        nested = _balanced_array_after(record, children.end())
        if nested is None:
            # A `children` that is not an array literal can hide named routes,
            # and the gate cannot see into it — say so instead of passing.
            # Raised as an offset so parse_routes, which holds the source, can
            # turn it into a line number.
            raise _UnreadableRoute(base_offset + offset, "declares 'children' the gate cannot read; give it an inline array literal")
        yield from _iter_route_records(nested, base_offset + offset + bracket + 1)


def _direct_property_matches(obj: str, pattern: re.Pattern, source: str | None = None):
    """Yield ``pattern`` matches that sit directly in ``obj``, not in a nested one.

    A parent route record contains its children's text, so an undirected
    search would read a child's ``name``/``meta`` as the parent's own.

    ``obj`` is the masked view (see ``_blank_string_contents``), so a key can
    never be matched inside string text. When ``source`` is given — the same
    span, unmasked — the match is re-run there so the *value* is read from the
    real literal; both have identical offsets.
    """
    depths = [0] * (len(obj) + 1)
    depth = 0
    for index, char in enumerate(obj):
        if char in "{[":
            depth += 1
        depths[index] = depth
        if char in "}]":
            depth -= 1
            depths[index] = depth
    for match in pattern.finditer(obj):
        if depths[match.start()] != 1:
            continue
        if source is None:
            yield match
            continue
        real = pattern.match(source, match.start())
        if real is not None:
            yield real


def _blank_string_contents(source: str) -> str:
    """Blank the *inside* of every string literal, keeping quotes and offsets.

    Structure — braces, brackets, property keys — must be read from a view
    where string text cannot pose as code: `meta: { note: "helpId: 'x'" }`
    declares nothing, and a `{` inside a string would otherwise unbalance the
    brace matching. Values are still read from the untouched source at the
    same offsets, so this view only ever *locates* things.

    A string used as a key keeps its content: `'name': 'Dashboard'` is a valid
    declaration and the key has to stay findable.
    """
    out = list(source)
    for kind, start, end in _scan_regions(source):
        if kind == "string" and not _is_key_string(source, start, end):
            out[start:end] = _blank_span(source, start, end)
    return "".join(out)


# A quoted key can only follow `{` or `,`. Anywhere else — after `?`, `(`, `=`
# — the string is a value, and reading `true ? 'helpId' : 'x'` as a property
# would fail the gate over an expression that declares nothing.
_KEY_PREDECESSORS = frozenset("{,")


def _is_key_string(source: str, start: int, end: int) -> bool:
    """Is the string whose text spans ``start:end`` used as a property key?"""
    before = start - 2  # step back over the opening quote
    while before >= 0 and source[before].isspace():
        before -= 1
    if before >= 0 and source[before] not in _KEY_PREDECESSORS:
        return False
    after = end + 1  # step past the closing quote
    while after < len(source) and source[after].isspace():
        after += 1
    return after < len(source) and source[after] == ":"


def _literal_start(text: str, start: int, bracket: str) -> int:
    """Index of ``bracket`` at ``start`` (whitespace skipped), or -1."""
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    return index if index < len(text) and text[index] == bracket else -1


def _balanced_object_after(text: str, start: int) -> str | None:
    """Return the ``{...}`` literal beginning at ``start``, or ``None``.

    The bracket must be the value itself, not merely the next one somewhere
    ahead: searching forward would happily borrow an unrelated literal from a
    later property (or a later statement) and describe it as this value.
    """
    open_brace = _literal_start(text, start, "{")
    if open_brace < 0:
        return None
    depth = 0
    for index in range(open_brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace : index + 1]
    return None


def _balanced_array_after(text: str, start: int) -> str | None:
    """Return the body of the ``[...]`` literal beginning at ``start``, or ``None``.

    Anchored for the same reason as ``_balanced_object_after``: a
    ``children: someVar`` next to a sibling property holding an array would
    otherwise be read as that sibling's array, and the routes the real
    ``children`` hides would never be seen.
    """
    open_bracket = _literal_start(text, start, "[")
    if open_bracket < 0:
        return None
    depth = 0
    for index in range(open_bracket, len(text)):
        if text[index] == "[":
            depth += 1
        elif text[index] == "]":
            depth -= 1
            if depth == 0:
                return text[open_bracket + 1 : index]
    return None


def _array_body(source: str, declaration: str) -> tuple[str, int]:
    """Return ``declaration``'s array-literal body and the offset it starts at."""
    start = source.index(declaration) + len(declaration)
    open_bracket = source.index("[", start)
    depth = 0
    for index in range(open_bracket, len(source)):
        if source[index] == "[":
            depth += 1
        elif source[index] == "]":
            depth -= 1
            if depth == 0:
                return source[open_bracket + 1 : index], open_bracket + 1
    raise ValueError(f"unterminated array literal after {declaration!r}")


def parse_routes(source: str, origin: str = "gui/src/router/index.js") -> list[Surface]:
    """Enumerate the named Admin-GUI routes and the help_id each declares.

    The catch-all redirect has no ``name`` and is not a surface a user can be
    sent to for help, so it is skipped rather than allowlisted.

    Comments are blanked first: a route left behind in a comment is not a
    surface the app can route to, so enumerating it would fail the gate over a
    page that cannot exist.
    """
    surfaces: list[Surface] = []
    source = _blank_comments(source)
    # Structure is read from the masked view so string text cannot pose as a
    # property (or unbalance the braces); values come from `source` at the
    # same offsets.
    masked = _blank_string_contents(source)
    body, body_offset = _array_body(masked, "const routes")
    try:
        records = list(_iter_route_records(body, body_offset))
    except _UnreadableRoute as unreadable:
        line = source.count("\n", 0, unreadable.offset) + 1
        raise SystemExit(f"help contract: {origin}:{line} {unreadable.problem}") from None
    for item, offset in records:
        real_item = source[offset : offset + len(item)]
        name_match = next(_direct_property_matches(item, _JS_NAME_RE, real_item), None)
        if not name_match:
            unreadable_name = next(_direct_property_matches(item, _ANY_NAME_PROPERTY_RE), None) or next(
                _direct_property_matches(item, _SHORTHAND_NAME_RE), None
            )
            if unreadable_name is not None:
                # The route is named and reachable; only the gate cannot tell
                # what it is called. Skipping would let it ship undocumented,
                # so it fails closed instead.
                line = source.count("\n", 0, offset) + 1
                raise SystemExit(
                    f"help contract: {origin}:{line} declares a route whose 'name' is not a string "
                    f"literal — the gate cannot tell which route this is; give 'name' a literal value"
                )
            continue
        # Scoped to the route's own `meta` object, because that is the only
        # place TopBar.vue reads: a `helpId` sitting in `props` or any other
        # property would otherwise be accepted as the declaration while the
        # page in fact renders no help button.
        meta_match = next(_direct_property_matches(item, _META_RE), None)
        meta = _balanced_object_after(item, meta_match.end()) if meta_match else None
        help_id_match = None
        if meta is not None:
            # Direct properties only: TopBar reads `route.meta.helpId`, so a
            # helpId parked in an object nested inside meta declares nothing.
            meta_start = _literal_start(item, meta_match.end(), "{")
            real_meta = real_item[meta_start : meta_start + len(meta)]
            help_id_match = next(_direct_property_matches(meta, _HELP_ID_LITERAL_RE, real_meta), None)
        surfaces.append(
            Surface(
                kind="route",
                name=name_match.group(2),
                help_id=help_id_match.group(1) if help_id_match else None,
                origin=origin,
            )
        )
    return surfaces


def parse_widget_types(widgets_dir: Path, repo_root: Path | None = None) -> list[Surface]:
    """Enumerate the Visu widget types from their self-registration modules.

    Comments are blanked first: a commented-out ``WidgetRegistry.register``
    call registers nothing at runtime, so counting it would fail the gate over
    a widget that does not exist.
    """
    root = repo_root or _REPO_ROOT
    surfaces: list[Surface] = []
    for index_file in sorted(widgets_dir.glob("*/index.ts")):
        source = _blank_comments(index_file.read_text(encoding="utf-8"))
        # Same two views as parse_routes: locate on the masked one so string
        # text cannot pose as a `type` property, read the value from the real
        # source at the same offset.
        masked = _blank_string_contents(source)
        try:
            origin = index_file.relative_to(root).as_posix()
        except ValueError:
            origin = index_file.as_posix()
        # Every registration in the module, not just the first: a second
        # `WidgetRegistry.register` call produces a second buildable,
        # palette-visible widget type, and missing it would let that type ship
        # undocumented and unexempted.
        for registration in _WIDGET_REGISTER_RE.finditer(masked):
            body = _balanced_object_after(masked, registration.end())
            # Direct property only: `defaultConfig: { type: 'Slider' }` is the
            # widget's own config, not the type it registers under.
            type_match = None
            if body is not None:
                body_start = _literal_start(masked, registration.end(), "{")
                real_body = source[body_start : body_start + len(body)]
                type_match = next(_direct_property_matches(body, _JS_TYPE_RE, real_body), None)
            if type_match is None:
                # Silently skipping would under-enumerate: the widget is built
                # and offered in the palette, the gate just cannot tell which
                # one it is — so it says so instead of passing.
                line = source.count("\n", 0, registration.start()) + 1
                raise SystemExit(
                    f"help contract: {origin}:{line} registers a widget whose 'type' is not a string "
                    f"literal — the gate cannot tell which widget this is; give 'type' a literal value"
                )
            widget_type = type_match.group(2)
            surfaces.append(
                Surface(
                    kind="widget",
                    name=widget_type,
                    help_id=f"widget-{kebab(widget_type)}",
                    origin=origin,
                )
            )
    return surfaces


def parse_skins(skins_root: Path) -> list[Surface]:
    """Enumerate skins from ``packages/skins/*/manifest.json`` of the skins repo.

    A directory without ``packages/skins/`` is not an obs-visu-skins checkout.
    Reporting zero skins for it would be the worst outcome: the run would look
    like the skin surface had been checked and found complete, when in truth
    nothing was looked at.
    """
    package_root = skins_root / "packages" / "skins"
    if not package_root.is_dir():
        raise SystemExit(f"help contract: {skins_root} is not an obs-visu-skins checkout ({package_root.relative_to(skins_root)}/ is missing)")
    surfaces: list[Surface] = []
    for manifest_file in sorted(package_root.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SystemExit(f"help contract: cannot read {manifest_file}: {error}") from error
        name = manifest.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SystemExit(f"help contract: {manifest_file} has no usable 'name'")
        surfaces.append(
            Surface(
                kind="skin",
                name=name,
                help_id=f"skin-{kebab(name)}",
                origin=manifest_file.as_posix(),
            )
        )
    return surfaces


def builtin_logic_node_types():
    """Return the backend's built-in Logic node catalogue.

    Imported lazily, with the repo root put on the path only here, so the rest
    of this module — and its tests — stay usable without the backend's
    dependency tree installed and without an import-time sys.path side effect.
    """
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from obs.logic.registry import BUILTIN_NODE_TYPES

    return BUILTIN_NODE_TYPES


def parse_logic_block_types(node_types, origin: str = "obs.logic.registry.BUILTIN_NODE_TYPES") -> list[Surface]:
    """Enumerate the Logic function blocks the palette can offer.

    ``hidden_from_palette`` blocks are skipped: NodePalette.vue filters them
    out, so there is no place a user could meet one and press a help button.
    """
    return [
        Surface(
            kind="logic-block",
            name=node_type.type,
            help_id=f"logic-block-{kebab(node_type.type)}",
            origin=origin,
        )
        for node_type in node_types
        if not getattr(node_type, "hidden_from_palette", False)
    ]


def collect_help_references(repo_root: Path, dirs=_REFERENCE_DIRS) -> list[Reference]:
    """Collect every statically resolvable help_id reference in the frontends."""
    references: list[Reference] = []
    for relative_dir, suffixes in dirs:
        base = repo_root / relative_dir
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            if "node_modules" in path.parts:
                continue
            source = _blank_comments(path.read_text(encoding="utf-8"))
            # Prose inside a string is neither rendered nor executed: a
            # sentence containing `helpId: '…'` must not fail the gate. Keys
            # keep their content, so a real `helpId: 'x'` is still seen.
            masked = _blank_string_contents(source)
            location = path.relative_to(repo_root).as_posix()
            for pattern, group in ((_STATIC_HELP_ATTR_RE, 2), (_HELP_ID_LITERAL_RE, 1)):
                for located in pattern.finditer(masked):
                    real = pattern.match(source, located.start())
                    if real is None:
                        continue
                    line = source.count("\n", 0, located.start()) + 1
                    references.append(Reference(real.group(group), f"{location}:{line}"))
    return references


def load_allowlist(text: str) -> tuple[list[AllowlistEntry], list[str]]:
    """Parse the allowlist file. Returns ``(entries, errors)``.

    An entry without a reason is an error: the point of the list is that every
    exemption stays reviewable, so a bare key would defeat it.
    """
    entries: list[AllowlistEntry] = []
    errors: list[str] = []
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, reason = line.partition("#")
        key = key.strip()
        reason = reason.strip()
        if not separator or not reason:
            errors.append(f"allowlist line {number}: {key!r} has no reason — add '# <why this surface has no help>'")
            continue
        kind = key.partition(":")[0]
        if kind not in _SURFACE_KINDS or ":" not in key or not key.partition(":")[2]:
            errors.append(f"allowlist line {number}: {key!r} is not a '<{'|'.join(_SURFACE_KINDS)}>:<name>' entry")
            continue
        if key in seen:
            errors.append(f"allowlist line {number}: duplicate entry {key!r}")
            continue
        seen.add(key)
        entries.append(AllowlistEntry(key=key, reason=reason, line=number))
    return entries, errors


def build_help_index(help_root: Path) -> dict:
    """Return the help index as ``generate-help-index.mjs`` computes it.

    Shelling out to the generator keeps a single source of truth for how a
    ``## Heading {#id}`` anchor becomes a help_id; a second Python
    implementation of that scan would be free to drift from the one the
    published help site is actually built with.
    """
    script = help_root / "scripts" / "generate-help-index.mjs"
    if shutil.which("node") is None:
        raise SystemExit("help contract: node is required to build the help index (see help/package.json)")
    result = subprocess.run(
        ["node", str(script), "--print"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"help contract: generate-help-index.mjs failed:\n{result.stderr.strip()}")
    return json.loads(result.stdout)


def validate(
    surfaces: list[Surface],
    references: list[Reference],
    index: dict,
    allowlist: list[AllowlistEntry],
    *,
    skins_checked: bool = True,
) -> list[str]:
    """Compare the real surfaces against the help index. Returns error lines."""
    errors: list[str] = []
    help_ids: dict = index.get("helpIds") or {}
    allowed = {entry.key for entry in allowlist}

    for duplicate in index.get("duplicates") or []:
        errors.append(f"help index: {duplicate}")
    # generate-help-index.mjs only warns here; a help_id that resolves in one
    # locale and not the other is exactly the silent gap this gate exists for.
    for incomplete in index.get("incomplete") or []:
        missing = ", ".join(incomplete.get("missing", []))
        errors.append(f"help index: help_id {incomplete.get('id')!r} is missing in locale(s): {missing}")

    for reference in sorted(set(references), key=lambda ref: (ref.help_id, ref.location)):
        if not _HELP_ID_RE.fullmatch(reference.help_id):
            errors.append(f"{reference.location}: {reference.help_id!r} is not a valid help_id")
        elif reference.help_id not in help_ids:
            errors.append(f"{reference.location}: help_id {reference.help_id!r} does not resolve in the help index")

    documented: set[str] = set()
    seen_ids: dict[str, str] = {}
    for surface in sorted(surfaces, key=lambda item: (item.kind, item.name)):
        if surface.kind == "logic-block" and not _LOGIC_NODE_TYPE_RE.fullmatch(surface.name):
            errors.append(
                f"{surface.key}: logic node type must be lowercase snake_case so NodePalette's "
                f"derived help_id matches the one checked here ({surface.origin})"
            )
        if surface.help_id is None:
            if surface.key not in allowed:
                errors.append(f"{surface.key}: no help_id declared in {surface.origin} — add one, or allowlist it with a reason")
            continue
        if not _HELP_ID_RE.fullmatch(surface.help_id):
            errors.append(f"{surface.key}: {surface.help_id!r} is not a valid help_id ({surface.origin})")
            continue
        if surface.kind in _DERIVED_ID_KINDS:
            previous = seen_ids.get(surface.help_id)
            if previous is not None:
                errors.append(f"{surface.key}: derived help_id {surface.help_id!r} collides with {previous}")
            seen_ids[surface.help_id] = surface.key
        if surface.help_id in help_ids:
            documented.add(surface.key)
            continue
        if surface.key not in allowed:
            errors.append(
                f"{surface.key}: help_id {surface.help_id!r} has no help page — "
                f"add a '{{#{surface.help_id}}}' heading anchor under help/<locale>/, or allowlist it with a reason"
            )

    existing = {surface.key for surface in surfaces}
    for entry in sorted(allowlist, key=lambda item: item.line):
        if entry.key.startswith("skin:") and not skins_checked:
            continue
        if entry.key not in existing:
            errors.append(f"allowlist line {entry.line}: {entry.key!r} is not a surface that exists — remove it")
        elif entry.key in documented:
            errors.append(f"allowlist line {entry.line}: {entry.key!r} is documented — remove the exemption")
    return errors


def collect_surfaces(repo_root: Path, skins_dir: Path | None) -> list[Surface]:
    surfaces = parse_routes((repo_root / "gui" / "src" / "router" / "index.js").read_text(encoding="utf-8"))
    surfaces += parse_widget_types(repo_root / "frontend" / "src" / "widgets", repo_root)
    surfaces += parse_logic_block_types(builtin_logic_node_types())
    if skins_dir is not None:
        surfaces += parse_skins(skins_dir)
    return surfaces


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--skins-dir",
        default=os.environ.get("OBS_VISU_SKINS_DIR"),
        help="checkout of the obs-visu-skins repository; without it the skin surface is not checked",
    )
    args = parser.parse_args(argv)
    skins_dir = Path(args.skins_dir).resolve() if args.skins_dir else None
    if skins_dir is not None and not skins_dir.is_dir():
        print(f"Help contract check failed:\n  - --skins-dir {skins_dir} does not exist")
        return 1

    surfaces = collect_surfaces(_REPO_ROOT, skins_dir)
    references = collect_help_references(_REPO_ROOT)
    index = build_help_index(_REPO_ROOT / "help")
    allowlist, errors = load_allowlist((_REPO_ROOT / "tools" / "help-contract-allowlist.txt").read_text(encoding="utf-8"))
    errors += validate(surfaces, references, index, allowlist, skins_checked=skins_dir is not None)

    if errors:
        print("Help contract check failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    counts = {kind: sum(1 for surface in surfaces if surface.kind == kind) for kind in _SURFACE_KINDS}
    exempt = len(allowlist)
    print(
        f"Help contract check passed: {counts['route']} routes, {counts['widget']} widgets, "
        f"{counts['logic-block']} logic blocks, {counts['skin']} skins ({exempt} allowlisted), "
        f"{len(set(references))} help_id references resolved"
    )
    if skins_dir is None:
        print("  note: skins not checked — pass --skins-dir/OBS_VISU_SKINS_DIR with an obs-visu-skins checkout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
