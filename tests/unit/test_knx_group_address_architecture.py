"""AST guardrail for KNX group address texts (#1296) — the weakest of three layers.

The contract lives in ``docs/architecture/knx-group-addresses.md``. Storage is
enforced by database triggers (migration V56) and by the data invariant in
``tests/knx_group_address_invariant.py``, which the integration and upgrade
tests check after driving every entrance. This file only covers what a syntax
check recognizes reliably: a raw group address text used as a key or in a
comparison.

**Sources** (raw text): ``str(<x>.destination_address)``; ``.get``/``[...]``
of ``group_address``/``state_group_address``; FastAPI route parameters named
``ga``/``group_address``/``state_group_address``; and local names assigned from
these (also via ``str()``, ``.strip()``, ``.lower()``, ``.upper()``, ``or``,
tuple/list/set displays and list/set/generator comprehensions). A normalizer
call makes a text clean; a name assigned from a normalizer anywhere in the
function counts as normalized (``ga = normalize_ga(ga)``).

**Sinks**: ``==``, ``!=``, ``in``, ``not in`` (except against literals such as
``""`` or ``(None, "")``); subscript indices; dict keys; set comprehension
elements; the first argument of ``get``/``setdefault``/``pop``/``add``/
``discard``/``remove``; and arguments passed one call deep, within the same
module, to a parameter that reaches one of these.

Every Pydantic field named ``group_address``/``state_group_address`` must have a
``field_validator`` calling ``normalize_ga``/``try_normalize_ga``.

Not seen here: SQL, storing, flows across modules, other field names.
"""

from __future__ import annotations

import ast
import pathlib
import textwrap
from dataclasses import dataclass, field

import pytest

import obs

OBS_ROOT = pathlib.Path(obs.__file__).parent
SOURCE_PATHS = sorted(path for path in OBS_ROOT.rglob("*.py") if "__pycache__" not in path.parts)

RAW_CONFIG_KEYS = frozenset({"group_address", "state_group_address"})
ROUTE_GA_PARAMS = frozenset({"ga", "group_address", "state_group_address"})
NORMALIZERS = frozenset({"normalize_ga", "try_normalize_ga", "format_ga"})
TRANSPARENT_METHODS = frozenset({"strip", "lower", "upper"})
KEY_METHODS = frozenset({"get", "setdefault", "pop", "add", "discard", "remove"})
ROUTE_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
COMPARE_OPS = (ast.Eq, ast.NotEq, ast.In, ast.NotIn)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_raw(node: ast.AST | None, tainted: frozenset[str]) -> bool:
    """Whether ``node`` evaluates to a group address text that skipped normalization."""
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id in tainted
    if isinstance(node, ast.Call):
        name = _call_name(node)
        if name in NORMALIZERS:
            return False
        if isinstance(node.func, ast.Name) and node.func.id == "str" and node.args:
            arg = node.args[0]
            return (isinstance(arg, ast.Attribute) and arg.attr == "destination_address") or _is_raw(arg, tainted)
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in TRANSPARENT_METHODS:
                return _is_raw(node.func.value, tainted)
            if node.func.attr == "get" and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in RAW_CONFIG_KEYS:
                return True
        return False
    if isinstance(node, ast.Subscript):
        return isinstance(node.slice, ast.Constant) and node.slice.value in RAW_CONFIG_KEYS
    if isinstance(node, ast.BoolOp):
        return any(_is_raw(value, tainted) for value in node.values)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return any(_is_raw(element, tainted) for element in node.elts)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return _is_raw(node.elt, tainted)
    return False


def _is_constant(node: ast.AST) -> bool:
    """Literal like ``""``/``None`` or a display of literals: an emptiness check, not an address comparison."""
    if isinstance(node, ast.Constant):
        return True
    return isinstance(node, (ast.Tuple, ast.List, ast.Set)) and all(isinstance(element, ast.Constant) for element in node.elts)


def _is_route(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr in ROUTE_METHODS
        for decorator in func.decorator_list
    )


def _params(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    return [arg.arg for arg in (*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs)]


def _assignments(func: ast.AST) -> list[tuple[ast.expr, ast.expr]]:
    pairs: list[tuple[ast.expr, ast.expr]] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple) and len(target.elts) == len(node.value.elts):
                    pairs.extend(zip(target.elts, node.value.elts, strict=True))
                else:
                    pairs.append((target, node.value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            pairs.append((node.target, node.value))
    return [(target, value) for target, value in pairs if isinstance(target, ast.Name)]


def _tainted_names(func: ast.AST, seeds: frozenset[str]) -> frozenset[str]:
    pairs = _assignments(func)
    normalized = {target.id for target, value in pairs if isinstance(value, ast.Call) and _call_name(value) in NORMALIZERS}
    tainted = set(seeds)
    changed = True
    while changed:
        changed = False
        for target, value in pairs:
            if target.id not in tainted and _is_raw(value, frozenset(tainted)):
                tainted.add(target.id)
                changed = True
    return frozenset(tainted - normalized)


@dataclass
class _Module:
    path: str
    tree: ast.Module
    scopes: list[ast.FunctionDef | ast.AsyncFunctionDef] = field(default_factory=list)
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = field(default_factory=dict)
    sink_params: dict[str, set[int]] = field(default_factory=dict)


def _is_sink(node: ast.AST, tainted: frozenset[str]) -> bool:
    """Whether ``node`` uses a raw group address text as a key or in a comparison."""
    if isinstance(node, ast.Compare):
        operands = (node.left, *node.comparators)
        return (
            any(isinstance(op, COMPARE_OPS) for op in node.ops)
            and not any(_is_constant(operand) for operand in operands)
            and any(_is_raw(operand, tainted) for operand in operands)
        )
    if isinstance(node, ast.Subscript):
        return _is_raw(node.slice, tainted)
    if isinstance(node, ast.Dict):
        return any(_is_raw(key, tainted) for key in node.keys)
    if isinstance(node, ast.DictComp):
        return _is_raw(node.key, tainted)
    if isinstance(node, ast.SetComp):
        return _is_raw(node.elt, tainted)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in KEY_METHODS and node.args:
        return _is_raw(node.args[0], tainted)
    return False


def _direct_sinks(func: ast.AST, tainted: frozenset[str]) -> list[ast.AST]:
    return [node for node in ast.walk(func) if _is_sink(node, tainted)]


def _load(source: str, path: str) -> _Module:
    module = _Module(path=path, tree=ast.parse(source, filename=path))
    for node in ast.walk(module.tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            module.scopes.append(node)
            module.functions.setdefault(node.name, node)
    for name, func in module.functions.items():
        params = _params(func)
        offset = 1 if params[:1] in (["self"], ["cls"]) else 0
        module.sink_params[name] = {
            index - offset for index, param in enumerate(params) if index >= offset and _direct_sinks(func, _tainted_names(func, frozenset({param})))
        }
    return module


def _call_sinks(module: _Module, func: ast.AST, tainted: frozenset[str]) -> list[ast.AST]:
    hits: list[ast.AST] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        callee = None
        if isinstance(node.func, ast.Name):
            callee = node.func.id
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in ("self", "cls"):
            callee = node.func.attr
        sink_positions = module.sink_params.get(callee or "", set())
        if any(index in sink_positions and _is_raw(arg, tainted) for index, arg in enumerate(node.args)):
            hits.append(node)
    return hits


def find_violations(source: str, path: str = "<snippet>") -> list[str]:
    """Return ``path:line`` for every raw group address text used as key or in a comparison."""
    module = _load(source, path)
    found: set[tuple[int, str]] = set()
    for scope in module.scopes:
        seeds = frozenset()
        if _is_route(scope):
            seeds = frozenset(param for param in _params(scope) if param in ROUTE_GA_PARAMS)
        tainted = _tainted_names(scope, seeds)
        for node in (*_direct_sinks(scope, tainted), *_call_sinks(module, scope, tainted)):
            found.add((node.lineno, ast.unparse(node)[:100]))
    return [f"{path}:{line}: {code}" for line, code in sorted(found)]


def find_unnormalized_model_fields(source: str, path: str = "<snippet>") -> list[str]:
    """Return ``path:Class.field`` for GA model fields without a normalizing validator."""
    missing: list[str] = []
    for node in ast.walk(ast.parse(source, filename=path)):
        if not isinstance(node, ast.ClassDef):
            continue
        fields = [
            stmt.target.id
            for stmt in node.body
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id in RAW_CONFIG_KEYS
        ]
        normalized: set[str] = set()
        for stmt in node.body:
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls_normalizer = any(isinstance(call, ast.Call) and _call_name(call) in ("normalize_ga", "try_normalize_ga") for call in ast.walk(stmt))
            for decorator in stmt.decorator_list:
                if isinstance(decorator, ast.Call) and _call_name(decorator) == "field_validator" and calls_normalizer:
                    normalized.update(arg.value for arg in decorator.args if isinstance(arg, ast.Constant))
        missing.extend(f"{path}:{node.name}.{name}" for name in fields if name not in normalized)
    return missing


def _relative(path: pathlib.Path) -> str:
    return str(path.relative_to(OBS_ROOT.parent))


# ---------------------------------------------------------------------------
# The guardrail on the real code base
# ---------------------------------------------------------------------------


def test_sources_are_discovered():
    """Guards the guardrail: the checks below only see what this finds."""
    names = {_relative(path) for path in SOURCE_PATHS}
    assert {"obs/adapters/knx/adapter.py", "obs/api/v1/knxproj.py", "obs/knxproj/parser.py"} <= names


def test_no_raw_group_address_text_is_used_as_key_or_compared():
    violations = [violation for path in SOURCE_PATHS for violation in find_violations(path.read_text(encoding="utf-8"), _relative(path))]
    assert violations == [], "Gruppenadress-Text ohne normalize_ga() als Schlüssel/Vergleich benutzt:\n" + "\n".join(violations)


def test_group_address_model_fields_are_normalized():
    missing = [entry for path in SOURCE_PATHS for entry in find_unnormalized_model_fields(path.read_text(encoding="utf-8"), _relative(path))]
    assert missing == [], "GA-Felder ohne field_validator mit normalize_ga():\n" + "\n".join(missing)


def test_there_is_a_single_normalization():
    """No second implementation: only group_address.py defines the normalizers."""
    definitions = [
        _relative(path)
        for path in SOURCE_PATHS
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in NORMALIZERS
    ]
    assert sorted(definitions) == ["obs/adapters/knx/group_address.py"] * len(NORMALIZERS)


# ---------------------------------------------------------------------------
# The guardrail's own detection — keeps it effective
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "snippet",
    [
        # adapter before #1296: telegram text as lookup key
        """
        def on_telegram(self, telegram):
            ga = str(telegram.destination_address)
            return self._ga_source_map.get(ga)
        """,
        # raw binding text as table key
        """
        def reload(self, binding):
            self._ga_source_map.setdefault(binding.config["group_address"], []).append(binding)
        """,
        """
        def build(rows):
            existing = {}
            for row in rows:
                ga = row_config(row).get("group_address")
                existing[ga] = row
        """,
        # raw binding text compared with an address
        """
        def matches(config, ga):
            return config.get("state_group_address") == ga
        """,
        """
        def matches(config, ga):
            state_ga = config.get("state_group_address") or ""
            return ga in {state_ga.strip()}
        """,
        # raw text handed one call deep into a comparison
        """
        class A:
            def transmitted(self, telegram):
                self._activate(telegram, str(telegram.destination_address))

            def _activate(self, telegram, ga):
                return ga != self._command_ga
        """,
        # route parameter used before normalization
        """
        @router.get("/group-addresses/{ga:path}/devices")
        async def devices(ga: str):
            if ga not in allowed:
                return []
        """,
        # raw text as dict / set key
        """
        def index(config, dp):
            return {config.get("group_address"): dp}
        """,
        # list built from raw texts, then a membership test
        """
        def bound(configs, ga):
            gas = [c.get("group_address") for c in configs]
            return ga in gas
        """,
    ],
)
def test_detects_raw_group_address_use(snippet):
    assert find_violations(textwrap.dedent(snippet)) != []


@pytest.mark.parametrize(
    "snippet",
    [
        # normalized at the source
        """
        def on_telegram(self, telegram):
            ga = normalize_ga(str(telegram.destination_address))
            return self._ga_source_map.get(ga)
        """,
        """
        def matches(config, ga):
            return try_normalize_ga(config.get("state_group_address")) == ga
        """,
        # route parameter normalized in place
        """
        @router.get("/group-addresses/{ga:path}/devices")
        async def devices(ga: str):
            ga = normalize_ga(ga)
            if ga not in allowed:
                return []
        """,
        # raw text only logged or stored as a value — not a key, not compared
        """
        def process(self, telegram):
            ga = str(telegram.destination_address)
            logger.info("KNX sniffer.process: GA=%s", ga)
        """,
        """
        def snapshot(config):
            return {"group_address": str(config.get("group_address") or "").strip()}
        """,
        # a non-route parameter is the caller's responsibility
        """
        def handle(self, ga):
            return self._ga_respond_map.get(ga)
        """,
        # telegram address objects compare by raw value
        """
        def same(telegram, other):
            return telegram.destination_address == other
        """,
        # emptiness checks against literals
        """
        def missing(config):
            return config.get("group_address") == "" or config.get("state_group_address") not in (None, "")
        """,
    ],
)
def test_ignores_normalized_or_harmless_use(snippet):
    assert find_violations(textwrap.dedent(snippet)) == []


def test_detects_a_model_field_without_normalizing_validator():
    model = """
    class Binding(BaseModel):
        group_address: str
        state_group_address: str | None = None

        @field_validator("group_address")
        @classmethod
        def _norm(cls, value):
            return normalize_ga(value)
    """
    assert find_unnormalized_model_fields(textwrap.dedent(model)) == ["<snippet>:Binding.state_group_address"]
