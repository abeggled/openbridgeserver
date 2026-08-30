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
    ------------   ------------------------------------   ----------------------
    route          gui/src/router/index.js (``routes[]``)  ``route.meta.helpId``
    widget         frontend/src/widgets/*/index.ts         ``widget-<kebab type>``
    skin           <skins repo>/packages/skins/*/          ``skin-<kebab name>``

Routes carry their id explicitly because the Admin-GUI reads it at runtime
(``TopBar.vue`` renders the page-level help button from ``route.meta.helpId``),
so the field is live wiring rather than gate-only metadata. Widgets and skins
have no such runtime consumer yet, so their id follows a convention instead of
a field that nothing would read.

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
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Same shape as generate-help-index.mjs's HEADING_RE capture group: a help_id
# is a Markdown heading anchor, so anything that cannot be one is not an id.
_HELP_ID_RE = re.compile(r"^[A-Za-z][\w-]*$")

_SURFACE_KINDS = ("route", "widget", "skin")

# `:help-id="expr"` / `v-bind:help-id="expr"` is a dynamic binding whose value
# is only known at runtime — the leading colon is what distinguishes it from
# the static attribute this gate can resolve.
_STATIC_HELP_ATTR_RE = re.compile(r"(?<![:\w-])help-id=\"([^\"]*)\"")
# Object-literal form used by lookup tables (NodePalette's NODE_HELP_IDS) and
# by route meta. A non-literal value (`helpId: props.helpId`) has no quotes and
# is skipped by construction.
_HELP_ID_LITERAL_RE = re.compile(r"\bhelpId:\s*['\"]([^'\"]*)['\"]")

_REFERENCE_DIRS = (
    ("gui/src", (".vue", ".js")),
    ("frontend/src", (".vue", ".ts")),
)

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


def _split_object_literals(text: str) -> list[str]:
    """Split the body of a JS array literal into its top-level ``{...}`` items."""
    items: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                items.append(text[start : index + 1])
    return items


def _array_body(source: str, declaration: str) -> str:
    """Return the text between the brackets of ``declaration``'s array literal."""
    start = source.index(declaration) + len(declaration)
    open_bracket = source.index("[", start)
    depth = 0
    for index in range(open_bracket, len(source)):
        if source[index] == "[":
            depth += 1
        elif source[index] == "]":
            depth -= 1
            if depth == 0:
                return source[open_bracket + 1 : index]
    raise ValueError(f"unterminated array literal after {declaration!r}")


def parse_routes(source: str, origin: str = "gui/src/router/index.js") -> list[Surface]:
    """Enumerate the named Admin-GUI routes and the help_id each declares.

    The catch-all redirect has no ``name`` and is not a surface a user can be
    sent to for help, so it is skipped rather than allowlisted.
    """
    surfaces: list[Surface] = []
    for item in _split_object_literals(_array_body(source, "const routes")):
        name_match = re.search(r"\bname:\s*'([^']*)'", item)
        if not name_match:
            continue
        help_id_match = _HELP_ID_LITERAL_RE.search(item)
        surfaces.append(
            Surface(
                kind="route",
                name=name_match.group(1),
                help_id=help_id_match.group(1) if help_id_match else None,
                origin=origin,
            )
        )
    return surfaces


def parse_widget_types(widgets_dir: Path, repo_root: Path | None = None) -> list[Surface]:
    """Enumerate the Visu widget types from their self-registration modules."""
    root = repo_root or _REPO_ROOT
    surfaces: list[Surface] = []
    for index_file in sorted(widgets_dir.glob("*/index.ts")):
        source = index_file.read_text(encoding="utf-8")
        registration = source.find("WidgetRegistry.register(")
        if registration < 0:
            continue
        type_match = re.search(r"\btype:\s*'([^']*)'", source[registration:])
        if not type_match:
            continue
        widget_type = type_match.group(1)
        try:
            origin = index_file.relative_to(root).as_posix()
        except ValueError:
            origin = index_file.as_posix()
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
    """Enumerate skins from ``packages/skins/*/manifest.json`` of the skins repo."""
    surfaces: list[Surface] = []
    for manifest_file in sorted((skins_root / "packages" / "skins").glob("*/manifest.json")):
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
            source = path.read_text(encoding="utf-8")
            location = path.relative_to(repo_root).as_posix()
            for pattern in (_STATIC_HELP_ATTR_RE, _HELP_ID_LITERAL_RE):
                for match in pattern.finditer(source):
                    line = source.count("\n", 0, match.start()) + 1
                    references.append(Reference(match.group(1), f"{location}:{line}"))
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
        if surface.help_id is None:
            if surface.key not in allowed:
                errors.append(f"{surface.key}: no help_id declared in {surface.origin} — add one, or allowlist it with a reason")
            continue
        if not _HELP_ID_RE.fullmatch(surface.help_id):
            errors.append(f"{surface.key}: {surface.help_id!r} is not a valid help_id ({surface.origin})")
            continue
        previous = seen_ids.get(surface.help_id)
        if previous is not None:
            errors.append(f"{surface.key}: help_id {surface.help_id!r} is already used by {previous}")
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
        f"{counts['skin']} skins ({exempt} allowlisted), {len(set(references))} help_id references resolved"
    )
    if skins_dir is None:
        print("  note: skins not checked — pass --skins-dir/OBS_VISU_SKINS_DIR with an obs-visu-skins checkout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
