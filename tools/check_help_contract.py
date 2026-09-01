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
# An unquoted value is valid HTML/Vue when it holds none of the forbidden
# characters, and it renders a live button — so it has to be read too.
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


# The frontends are read by tools/scan-help-declarations.mjs, which parses them
# with @babel/parser and @vue/compiler-sfc. This checker used to scan them with
# regexes and a hand-written tokenizer; review after review found the next
# language construct it misread — quoted keys, a comment after a closing quote,
# a regex holding a quote, `${...}` expressions, spreads, shorthand properties,
# a CSS custom property shaped like a JS one. None of those are special cases
# for a parser, which simply sees the syntax tree the runtime sees.
_SCANNER = _REPO_ROOT / "tools" / "scan-help-declarations.mjs"


def scan_declarations(repo_root: Path | None = None) -> dict:
    """Return the parsed routes, widget types, references and unreadable spots."""
    root = repo_root or _REPO_ROOT
    return _run_node(root / "tools" / "scan-help-declarations.mjs", root)


def _run_node(script: Path, cwd: Path, *args: str) -> dict:
    """Run a Node helper that prints JSON on stdout."""
    if shutil.which("node") is None:
        raise SystemExit(f"help contract: node is required to run {script.name}")
    result = subprocess.run(
        ["node", str(script), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(cwd),
    )
    if result.returncode != 0:
        raise SystemExit(f"help contract: {script.name} failed:\n{result.stderr.strip()}")
    return json.loads(result.stdout)


def routes_from(scan: dict) -> list[Surface]:
    """Turn the scanner's routes into surfaces."""
    return [Surface(kind="route", name=route["name"], help_id=route["helpId"], origin=f"{route['file']}:{route['line']}") for route in scan["routes"]]


def widgets_from(scan: dict) -> list[Surface]:
    """Turn the scanner's widget registrations into surfaces."""
    return [
        Surface(
            kind="widget",
            name=widget["type"],
            help_id=f"widget-{kebab(widget['type'])}",
            origin=f"{widget['file']}:{widget['line']}",
        )
        for widget in scan["widgets"]
    ]


def references_from(scan: dict) -> list[Reference]:
    """Turn the scanner's help_id references into references."""
    return [Reference(reference["helpId"], f"{reference['file']}:{reference['line']}") for reference in scan["references"]]


def unreadable_errors(scan: dict) -> list[str]:
    """Report every declaration the parse could not resolve.

    Never skipped: the surface ships either way, and only the checker is left
    guessing — which is the one outcome a coverage gate must not produce.
    """
    return [f"{spot['file']}:{spot['line']} {spot['problem']}" for spot in scan["unreadable"]]


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


# An end tag closes the block as soon as `script` is followed by whitespace or
# `>`; anything up to the `>` is ignored by parsers but tolerated. Requiring
# `</script\s*>` missed `</script\t\n bar>` (CodeQL js/bad-tag-filter) — and a
# missed end tag means the block is not recognised at all, so declarations
# inside it would go unseen and a dead help button would pass.
# Both tags need a real HTML delimiter after the name: `\b` also matches
# `<script-editor>`, a perfectly ordinary custom element, which would then be
# read as an SFC script block all the way to the next real `</script>`.
_SCRIPT_BLOCK_RE = re.compile(r"<script(?=[\s/>])[^>]*>(.*?)</script(?=[\s/>])[^>]*>", re.DOTALL | re.IGNORECASE)


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
    return _run_node(help_root / "scripts" / "generate-help-index.mjs", help_root.parent, "--print")


def rendered_help_ids(repo_root: Path | None = None) -> dict:
    """Return, per rendered page, the heading ids VitePress actually emitted."""
    root = repo_root or _REPO_ROOT
    return _run_node(root / "tools" / "rendered-help-ids.mjs", root)


# An explicit anchor is written as `{#id}` on a *heading* line; the same text
# in a paragraph is prose, and markdown-it may well slug an unrelated heading
# on that page to the same id. Heading detection here is deliberately loose —
# it only decides whether an id is worth comparing, and the render decides
# whether it is real.
# Mirrors CONTAINER in generate-help-index.mjs: the space after a blockquote
# marker is optional, and a list marker takes one to four spaces — with five or
# more the rest indents a code block rather than the item's content.
_CONTAINER = r" {0,3}(?:>[^\S\r\n]*|(?:[-*+]|\d{1,9}[.)])[^\S\r\n]{1,4}(?![^\S\r\n]))*"
_WRITTEN_ANCHOR_RE = re.compile(
    rf"^(?:{_CONTAINER}|[^\S\r\n]*)#{{1,6}}[^\S\r\n].*?\{{#([A-Za-z][\w-]*)\}}(?:[^\S\r\n]+#*)?[^\S\r\n]*$"
    rf"|^{_CONTAINER}\S.*?\{{#([A-Za-z][\w-]*)\}}[^\S\r\n]*\r?\n(?:{_CONTAINER}|[^\S\r\n]*)(?:=+|-+)[^\S\r\n]*$",
    re.MULTILINE,
)


_CONTAINER_PREFIX_RE = re.compile(rf"^{_CONTAINER}")


def _column_width(text: str) -> int:
    """Width in columns, with a tab advancing to the next multiple of four."""
    column = 0
    for character in text:
        column = column + 4 - (column % 4) if character == "\t" else column + 1
    return column


def _underline_is_indented_legally(matched: str) -> bool:
    """Mirrors generate-help-index.mjs: at most three columns past the content column."""
    heading, _, underline = matched.partition("\n")
    content_column = _column_width(_CONTAINER_PREFIX_RE.match(heading).group(0))
    return _column_width(re.match(r"[^-=]*", underline).group(0)) <= content_column + 3


def written_anchor_ids(sources: dict[str, str]) -> dict[str, set[str]]:
    """The ids written as explicit anchors, per rendered page they belong to.

    ``sources`` are the pages with every non-rendered region already blanked
    (``generate-help-index.mjs --stripped``), so an anchor inside fenced code
    or a comment is not mistaken for a heading here. What counts as a *heading*
    is decided by this module's own pattern rather than the generator's, which
    is what lets the reverse check catch a heading form the generator misses.

    Keyed the way ``rendered-help-ids.mjs`` keys its pages, so the two can be
    compared page by page: a global set would pair an anchor-shaped string on
    one page with an unrelated automatic heading slug on another.
    """
    return {page: _anchors_in(text) for page, text in sources.items()}


def _anchors_in(text: str) -> set[str]:
    """The heading-borne anchors of one page, bounded the way the generator bounds them."""
    columns = _content_column_by_line(text)
    found: set[str] = set()
    for match in _WRITTEN_ANCHOR_RE.finditer(text):
        atx, setext = match.groups()
        if setext:
            if _underline_is_indented_legally(match.group(0)):
                found.add(setext)
            continue
        indent = _column_width(re.match(r"[^\S\r\n]*", match.group(0)).group(0))
        if indent <= columns[text.count("\n", 0, match.start())] + 3:
            found.add(atx)
    return found


def _content_column_by_line(text: str) -> list[int]:
    """Mirrors contentColumnByLine in generate-help-index.mjs."""
    columns: list[int] = []
    open_items: list[int] = []
    for line in text.split("\n"):
        indent = _column_width(re.match(r"[^\S\r\n]*", line).group(0))
        if line.strip():
            while open_items and indent < open_items[-1]:
                open_items.pop()
        columns.append(open_items[-1] if open_items else 0)
        prefix = _CONTAINER_PREFIX_RE.match(line).group(0)
        if prefix.strip():
            open_items.append(_column_width(prefix))
    return columns


def stripped_help_sources(repo_root: Path | None = None) -> dict[str, str]:
    """Each help page with the regions the site does not render blanked out."""
    root = repo_root or _REPO_ROOT
    return _run_node(root / "help" / "scripts" / "generate-help-index.mjs", root, "--stripped")


def render_covers_index(index: dict, rendered: dict, written: dict[str, set[str]]) -> list[str]:
    """Report anchors the site renders that the index never learned about.

    ``index_matches_render`` checks the other direction — that everything
    indexed really resolves. Without this one, a heading form the index scan
    does not recognise stays invisible: the anchor renders, the page works,
    and the gate calls the surface undocumented. An id counts as deliberate
    when it is both written as `{#id}` in the sources and rendered as a
    heading, which needs neither a Markdown parser nor markdown-it's slug
    rules.
    """
    indexed = set(index.get("helpIds") or {})
    missing: dict[str, str] = {}
    for page, ids in sorted(rendered.items()):
        # Only the anchors written on *this* page: an id rendered here is a
        # deliberate anchor only if this page's own source asks for it.
        on_page = written.get(page, set())
        for help_id in ids:
            if help_id in on_page and help_id not in indexed:
                missing.setdefault(help_id, page)
    return [
        f"help index: {help_id!r} is written as an anchor and rendered in {page}, but the index does not list it"
        for help_id, page in sorted(missing.items())
    ]


def index_matches_render(index: dict, rendered: dict) -> list[str]:
    """Check every indexed help_id against the page that really renders it.

    The index comes from a text scan of the Markdown, and every rule that scan
    needs — fenced code, comments, raw HTML blocks, escaped braces,
    frontmatter, indented headings — is a rule about what VitePress renders.
    Comparing against the render is what makes the two provably agree, instead
    of the scan reimplementing a Markdown parser and drifting from it one
    construct at a time.
    """
    errors: list[str] = []
    for help_id, by_locale in sorted((index.get("helpIds") or {}).items()):
        for locale, url in sorted(by_locale.items()):
            page, _, fragment = url.partition("#")
            path = page.removeprefix("/help/")
            if path.endswith("/"):
                path += "index.html"
            ids = rendered.get(path)
            if ids is None:
                errors.append(f"help index: {help_id!r} ({locale}) points at {page}, which the help build does not produce")
            elif fragment not in ids:
                errors.append(f"help index: {help_id!r} ({locale}) is indexed but {page} renders no element with that id")
    return errors


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
            # A repeated registration of the *same* type is not a collision:
            # WidgetRegistry keeps one definition per type and deliberately
            # replaces it, so both lines describe a single surface.
            if previous is not None and previous != surface.key:
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


def collect_surfaces(scan: dict, skins_dir: Path | None) -> list[Surface]:
    surfaces = routes_from(scan) + widgets_from(scan)
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
    parser.add_argument(
        "--skip-render-check",
        action="store_true",
        help="skip comparing the help index against a real VitePress build (needs help/node_modules)",
    )
    args = parser.parse_args(argv)
    skins_dir = Path(args.skins_dir).resolve() if args.skins_dir else None
    if skins_dir is not None and not skins_dir.is_dir():
        print(f"Help contract check failed:\n  - --skins-dir {skins_dir} does not exist")
        return 1

    scan = scan_declarations(_REPO_ROOT)
    surfaces = collect_surfaces(scan, skins_dir)
    references = references_from(scan)
    index = build_help_index(_REPO_ROOT / "help")
    allowlist, errors = load_allowlist((_REPO_ROOT / "tools" / "help-contract-allowlist.txt").read_text(encoding="utf-8"))
    errors += unreadable_errors(scan)
    errors += validate(surfaces, references, index, allowlist, skins_checked=skins_dir is not None)
    if not args.skip_render_check:
        rendered = rendered_help_ids(_REPO_ROOT)
        errors += index_matches_render(index, rendered)
        errors += render_covers_index(index, rendered, written_anchor_ids(stripped_help_sources(_REPO_ROOT)))

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
