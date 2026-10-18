"""Architectural guardrails for the Logic node package.

These tests keep the structure described in ``docs/architecture/logic-nodes.md``
from silently collapsing back into monolithic files: they check the dependency
direction, that registry modules stay registration-only, and that every
registered node type is actually reachable from the shared dispatcher.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import re

import pytest

import obs.logic.nodes as nodes_package
from obs.logic import registry as registry_module
from obs.logic.registry import BUILTIN_NODE_CATEGORIES, BUILTIN_NODE_TYPES

NODES_ROOT = pathlib.Path(nodes_package.__file__).parent
REGISTRY_PATH = pathlib.Path(registry_module.__file__)
EXECUTOR_PATH = REGISTRY_PATH.parent / "executor.py"
REPO_ROOT = NODES_ROOT.parents[2]
PALETTE_PATH = REPO_ROOT / "gui/src/components/logic/NodePalette.vue"

# Layers a node module must never reach into. A node may depend on shared models
# and helpers; the API, the manager, the executor and the registry depend on it.
FORBIDDEN_NODE_IMPORTS = ("obs.api", "obs.logic.manager", "obs.logic.executor", "obs.logic.registry")

# Registered but intentionally not executed: purely visual / placeholder blocks.
# They fall through to the dispatcher's ``case _`` no-op branch.
NON_EXECUTING_NODE_TYPES = frozenset({"ai_logic", "comment"})

NODE_MODULE_PATHS = sorted(path for path in NODES_ROOT.glob("*/*.py") if path.name != "__init__.py")
CATEGORY_INIT_PATHS = sorted(NODES_ROOT.glob("*/__init__.py"))


def _module_name(path: pathlib.Path) -> str:
    return f"{nodes_package.__name__}.{path.parent.name}.{path.stem}"


def _parse(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_modules(tree: ast.Module) -> list[str]:
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.append(node.module)
    return imported


def test_node_modules_are_discovered():
    """Guards the guardrails: everything below is scoped to what this finds.

    The layout is exactly two levels — ``nodes/<category>/<block>.py``. A module
    nested any deeper would escape every check in this file, so the discovered
    module count must match the catalogue and no deeper package may exist.
    """
    assert len(NODE_MODULE_PATHS) == len(BUILTIN_NODE_TYPES), "every node module must be discovered and registered exactly once"
    assert {path.parent.name for path in CATEGORY_INIT_PATHS} == set(BUILTIN_NODE_CATEGORIES)

    nested = [path for path in NODES_ROOT.glob("*/*/") if path.is_dir() and not path.name.startswith("__")]
    assert nested == [], f"node categories must stay flat, found nested packages: {nested}"


@pytest.mark.parametrize("path", NODE_MODULE_PATHS, ids=lambda path: f"{path.parent.name}/{path.stem}")
def test_node_module_defines_exactly_one_node_type(path: pathlib.Path):
    tree = _parse(path)
    assignments = [target.id for node in tree.body if isinstance(node, ast.Assign) for target in node.targets if isinstance(target, ast.Name)]

    assert assignments.count("NODE_TYPE") == 1, f"{path} must define exactly one NODE_TYPE"

    module = importlib.import_module(_module_name(path))
    registered = BUILTIN_NODE_CATEGORIES[path.parent.name]
    assert any(node_type is module.NODE_TYPE for node_type in registered), f"{path} is not registered in its category"


@pytest.mark.parametrize("path", NODE_MODULE_PATHS, ids=lambda path: f"{path.parent.name}/{path.stem}")
def test_node_module_declares_the_category_of_its_package(path: pathlib.Path):
    module = importlib.import_module(_module_name(path))

    assert module.NODE_TYPE.category == path.parent.name


@pytest.mark.parametrize(
    "path",
    [*NODE_MODULE_PATHS, *CATEGORY_INIT_PATHS, NODES_ROOT / "base.py", NODES_ROOT / "__init__.py"],
    ids=lambda path: f"{path.parent.name}/{path.stem}",
)
def test_node_package_does_not_depend_on_upper_layers(path: pathlib.Path):
    for imported in _imported_modules(_parse(path)):
        assert not imported.startswith(FORBIDDEN_NODE_IMPORTS), f"{path} must not import {imported}"


def test_shared_node_helpers_do_not_depend_on_concrete_nodes():
    for imported in _imported_modules(_parse(NODES_ROOT / "base.py")):
        assert not imported.startswith("obs.logic.nodes."), f"base.py must not import {imported}"


def test_node_package_root_has_no_import_side_effects():
    """The package root must not import the category packages.

    Otherwise importing one node module would pull in every other node module,
    and a single block could no longer be imported (or reviewed) on its own.
    """
    assert _imported_modules(_parse(NODES_ROOT / "__init__.py")) == []


@pytest.mark.parametrize("path", CATEGORY_INIT_PATHS, ids=lambda path: path.parent.name)
def test_category_registry_contains_registration_only(path: pathlib.Path):
    tree = _parse(path)

    for node in tree.body:
        assert not isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef), f"{path} must not contain business logic"

    exported = [
        target.id
        for node in tree.body
        if isinstance(node, ast.AnnAssign | ast.Assign)
        for target in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
        if isinstance(target, ast.Name)
    ]
    assert exported == ["NODE_TYPES", "__all__"], f"{path} must only export NODE_TYPES"


def test_registry_only_combines_category_registries():
    tree = _parse(REGISTRY_PATH)

    defined = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    assert defined == ["_classify_node_type", "_build_catalogue", "get_node_type", "list_node_types"]

    for imported in _imported_modules(tree):
        if imported.startswith("obs.logic.nodes"):
            assert imported.count(".") == 3, f"registry must import category packages, not node modules: {imported}"
        assert not imported.startswith(("obs.api", "obs.logic.manager", "obs.logic.executor"))


def test_every_registered_category_is_rendered_by_the_palette():
    """A category the palette does not know about is invisible in the editor.

    ``NodePalette.vue`` renders a fixed list of category ids and filters the
    catalogue by it, so a new category package without a matching entry there
    would register blocks that no user can find. Order is deliberately not
    asserted — that is a UI choice and changing it breaks nothing.
    """
    assert PALETTE_PATH.is_file(), f"{PALETTE_PATH} not found — this check needs the frontend sources next to the backend"
    palette_source = PALETTE_PATH.read_text(encoding="utf-8")
    declaration = re.search(r"const CATEGORY_IDS = \[(.*?)\]", palette_source, re.DOTALL)
    assert declaration, f"CATEGORY_IDS not found in {PALETTE_PATH} — update this test if the palette was refactored"

    palette_categories = set(re.findall(r"['\"]([a-z_]+)['\"]", declaration.group(1)))

    assert palette_categories == set(BUILTIN_NODE_CATEGORIES), (
        f"palette categories and registered categories drifted apart: only in palette "
        f"{sorted(palette_categories - set(BUILTIN_NODE_CATEGORIES))}, only registered "
        f"{sorted(set(BUILTIN_NODE_CATEGORIES) - palette_categories)}"
    )


def _dispatcher_node_types() -> set[str]:
    tree = _parse(EXECUTOR_PATH)
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_eval_node")
    match_statement = next(node for node in ast.walk(function) if isinstance(node, ast.Match))

    handled: set[str] = set()
    for case in match_statement.cases:
        patterns = case.pattern.patterns if isinstance(case.pattern, ast.MatchOr) else [case.pattern]
        for pattern in patterns:
            if isinstance(pattern, ast.MatchValue) and isinstance(pattern.value, ast.Constant):
                handled.add(pattern.value.value)
    return handled


def test_every_executable_node_type_has_an_implementation():
    handled = _dispatcher_node_types()
    registered = {node_type.type for node_type in BUILTIN_NODE_TYPES}

    missing = registered - handled - NON_EXECUTING_NODE_TYPES
    assert not missing, f"registered node types without an implementation: {sorted(missing)}"

    stale = handled - registered
    assert not stale, f"dispatcher branches for unregistered node types: {sorted(stale)}"

    assert NON_EXECUTING_NODE_TYPES.isdisjoint(handled)
    assert NON_EXECUTING_NODE_TYPES <= registered
