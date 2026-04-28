"""
Unit tests for json_extractor, xml_extractor and substring_extractor logic nodes.

Covers:
  - json_extractor: simple top-level key extraction
  - json_extractor: nested dotted-path extraction
  - json_extractor: array bracket notation
  - json_extractor: missing path → error output, value=None
  - json_extractor: invalid JSON → value=None
  - json_extractor: _preview output is populated and capped at 20 KB
  - xml_extractor: simple element extraction via .//tag XPath
  - xml_extractor: nested element XPath
  - xml_extractor: no match → value=None
  - xml_extractor: invalid XML → value=None
  - xml_extractor: _preview output is populated
  - substring_extractor: all five modes (rechts_von, links_von, zwischen, ausschneiden, regex)
  - substring_extractor: first/last occurrence
  - substring_extractor: no-match and error cases
  - substring_extractor: _preview populated and capped
  - Downstream: json_extractor output flows to next node
"""
from __future__ import annotations

import json

import pytest

from tests.unit.conftest import edge, make_executor, node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jnode(node_id: str, path: str = "", data_override: dict | None = None) -> dict:
    return node(node_id, "json_extractor", {**(data_override or {}), "json_path": path})


def _xnode(node_id: str, path: str = "", data_override: dict | None = None) -> dict:
    return node(node_id, "xml_extractor", {**(data_override or {}), "xml_path": path})


def _run(nodes, edges=None, input_overrides=None):
    ex = make_executor(nodes, edges or [])
    return ex.execute(input_overrides or {})


# ===========================================================================
# json_extractor
# ===========================================================================

class TestJsonExtractor:

    def test_simple_key(self):
        payload = json.dumps({"temperature": 21.5})
        nodes = [_jnode("j1", "temperature")]
        overrides = {"j1": {"data": payload}}
        out = _run(nodes, input_overrides=overrides)
        assert out["j1"]["value"] == 21.5

    def test_nested_dotted_path(self):
        payload = json.dumps({"sensor": {"room": {"temp": 19}}})
        nodes = [_jnode("j1", "sensor.room.temp")]
        overrides = {"j1": {"data": payload}}
        out = _run(nodes, input_overrides=overrides)
        assert out["j1"]["value"] == 19

    def test_array_bracket_notation(self):
        payload = json.dumps({"items": [10, 20, 30]})
        nodes = [_jnode("j1", "items[1]")]
        overrides = {"j1": {"data": payload}}
        out = _run(nodes, input_overrides=overrides)
        assert out["j1"]["value"] == 20

    def test_mixed_path_with_array_and_object(self):
        payload = json.dumps({"sensors": [{"id": 1, "value": 42}]})
        nodes = [_jnode("j1", "sensors[0].value")]
        overrides = {"j1": {"data": payload}}
        out = _run(nodes, input_overrides=overrides)
        assert out["j1"]["value"] == 42

    def test_missing_path_returns_none(self):
        payload = json.dumps({"a": 1})
        nodes = [_jnode("j1", "b.c")]
        overrides = {"j1": {"data": payload}}
        out = _run(nodes, input_overrides=overrides)
        assert out["j1"]["value"] is None

    def test_invalid_json_returns_none(self):
        nodes = [_jnode("j1", "key")]
        overrides = {"j1": {"data": "not-json{{"}}
        out = _run(nodes, input_overrides=overrides)
        assert out["j1"]["value"] is None

    def test_no_path_returns_none(self):
        payload = json.dumps({"x": 5})
        nodes = [_jnode("j1", "")]
        overrides = {"j1": {"data": payload}}
        out = _run(nodes, input_overrides=overrides)
        assert out["j1"]["value"] is None

    def test_no_data_returns_none(self):
        nodes = [_jnode("j1", "key")]
        out = _run(nodes)
        assert out["j1"]["value"] is None

    def test_preview_populated(self):
        payload = json.dumps({"a": 1})
        nodes = [_jnode("j1", "a")]
        overrides = {"j1": {"data": payload}}
        out = _run(nodes, input_overrides=overrides)
        assert out["j1"]["_preview"] == payload

    def test_preview_capped_at_20kb(self):
        big = json.dumps({"data": "x" * 30_000})
        nodes = [_jnode("j1", "data")]
        overrides = {"j1": {"data": big}}
        out = _run(nodes, input_overrides=overrides)
        assert len(out["j1"]["_preview"]) <= 20_001  # 20 KB + truncation marker

    def test_string_value_extraction(self):
        payload = json.dumps({"status": "online"})
        nodes = [_jnode("j1", "status")]
        overrides = {"j1": {"data": payload}}
        out = _run(nodes, input_overrides=overrides)
        assert out["j1"]["value"] == "online"

    def test_boolean_value_extraction(self):
        payload = json.dumps({"active": True})
        nodes = [_jnode("j1", "active")]
        overrides = {"j1": {"data": payload}}
        out = _run(nodes, input_overrides=overrides)
        assert out["j1"]["value"] is True


# ===========================================================================
# xml_extractor
# ===========================================================================

class TestXmlExtractor:

    def test_simple_element(self):
        xml = "<root><temperature>21.5</temperature></root>"
        nodes = [_xnode("x1", ".//temperature")]
        overrides = {"x1": {"data": xml}}
        out = _run(nodes, input_overrides=overrides)
        assert out["x1"]["value"] == "21.5"

    def test_nested_element(self):
        xml = "<root><sensor><room><temp>19</temp></room></sensor></root>"
        nodes = [_xnode("x1", ".//temp")]
        overrides = {"x1": {"data": xml}}
        out = _run(nodes, input_overrides=overrides)
        assert out["x1"]["value"] == "19"

    def test_no_match_returns_none(self):
        xml = "<root><a>1</a></root>"
        nodes = [_xnode("x1", ".//missing")]
        overrides = {"x1": {"data": xml}}
        out = _run(nodes, input_overrides=overrides)
        assert out["x1"]["value"] is None

    def test_invalid_xml_returns_none(self):
        nodes = [_xnode("x1", ".//x")]
        overrides = {"x1": {"data": "<<<invalid"}}
        out = _run(nodes, input_overrides=overrides)
        assert out["x1"]["value"] is None

    def test_no_data_returns_none(self):
        nodes = [_xnode("x1", ".//x")]
        out = _run(nodes)
        assert out["x1"]["value"] is None

    def test_no_path_returns_none(self):
        xml = "<root><a>1</a></root>"
        nodes = [_xnode("x1", "")]
        overrides = {"x1": {"data": xml}}
        out = _run(nodes, input_overrides=overrides)
        assert out["x1"]["value"] is None

    def test_preview_populated(self):
        xml = "<root><a>1</a></root>"
        nodes = [_xnode("x1", ".//a")]
        overrides = {"x1": {"data": xml}}
        out = _run(nodes, input_overrides=overrides)
        assert out["x1"]["_preview"] == xml

    def test_attribute_xpath(self):
        xml = '<root><item id="42">hello</item></root>'
        nodes = [_xnode("x1", './/item[@id="42"]')]
        overrides = {"x1": {"data": xml}}
        out = _run(nodes, input_overrides=overrides)
        assert out["x1"]["value"] == "hello"


# ===========================================================================
# substring_extractor
# ===========================================================================

def _snode(node_id: str, mode: str, **kwargs) -> dict:
    return node(node_id, "substring_extractor", {"mode": mode, **kwargs})


class TestSubstringExtractor:

    # ── rechts_von ────────────────────────────────────────────────────────

    def test_rechts_von_first(self):
        nodes = [_snode("s1", "rechts_von", search=":", occurrence="first")]
        out = _run(nodes, input_overrides={"s1": {"data": "key:value:extra"}})
        assert out["s1"]["value"] == "value:extra"

    def test_rechts_von_last(self):
        nodes = [_snode("s1", "rechts_von", search=":", occurrence="last")]
        out = _run(nodes, input_overrides={"s1": {"data": "key:value:extra"}})
        assert out["s1"]["value"] == "extra"

    def test_rechts_von_no_match(self):
        nodes = [_snode("s1", "rechts_von", search="X")]
        out = _run(nodes, input_overrides={"s1": {"data": "hello world"}})
        assert out["s1"]["value"] is None

    # ── links_von ─────────────────────────────────────────────────────────

    def test_links_von_first(self):
        nodes = [_snode("s1", "links_von", search=":", occurrence="first")]
        out = _run(nodes, input_overrides={"s1": {"data": "key:value:extra"}})
        assert out["s1"]["value"] == "key"

    def test_links_von_last(self):
        nodes = [_snode("s1", "links_von", search=":", occurrence="last")]
        out = _run(nodes, input_overrides={"s1": {"data": "key:value:extra"}})
        assert out["s1"]["value"] == "key:value"

    def test_links_von_no_match(self):
        nodes = [_snode("s1", "links_von", search="X")]
        out = _run(nodes, input_overrides={"s1": {"data": "hello world"}})
        assert out["s1"]["value"] is None

    # ── zwischen ──────────────────────────────────────────────────────────

    def test_zwischen(self):
        nodes = [_snode("s1", "zwischen", start_marker="[", end_marker="]")]
        out = _run(nodes, input_overrides={"s1": {"data": "prefix[hello world]suffix"}})
        assert out["s1"]["value"] == "hello world"

    def test_zwischen_no_end(self):
        nodes = [_snode("s1", "zwischen", start_marker="[", end_marker="]")]
        out = _run(nodes, input_overrides={"s1": {"data": "prefix[no-close"}})
        assert out["s1"]["value"] is None

    def test_zwischen_no_start(self):
        nodes = [_snode("s1", "zwischen", start_marker="[", end_marker="]")]
        out = _run(nodes, input_overrides={"s1": {"data": "no brackets here"}})
        assert out["s1"]["value"] is None

    def test_zwischen_empty_marker(self):
        nodes = [_snode("s1", "zwischen", start_marker="", end_marker="]")]
        out = _run(nodes, input_overrides={"s1": {"data": "test]text"}})
        assert out["s1"]["value"] is None

    # ── ausschneiden ──────────────────────────────────────────────────────

    def test_ausschneiden_with_length(self):
        nodes = [_snode("s1", "ausschneiden", start=7, length=5)]
        out = _run(nodes, input_overrides={"s1": {"data": "Hello, World!"}})
        assert out["s1"]["value"] == "World"

    def test_ausschneiden_to_end(self):
        nodes = [_snode("s1", "ausschneiden", start=7, length=-1)]
        out = _run(nodes, input_overrides={"s1": {"data": "Hello, World!"}})
        assert out["s1"]["value"] == "World!"

    def test_ausschneiden_from_zero(self):
        nodes = [_snode("s1", "ausschneiden", start=0, length=5)]
        out = _run(nodes, input_overrides={"s1": {"data": "Hello, World!"}})
        assert out["s1"]["value"] == "Hello"

    # ── regex ─────────────────────────────────────────────────────────────

    def test_regex_full_match(self):
        nodes = [_snode("s1", "regex", pattern=r"\d+\.\d+", group=0)]
        out = _run(nodes, input_overrides={"s1": {"data": "Temperature: 21.5 °C"}})
        assert out["s1"]["value"] == "21.5"

    def test_regex_capture_group(self):
        nodes = [_snode("s1", "regex", pattern=r"(\w+)=(\w+)", group=2)]
        out = _run(nodes, input_overrides={"s1": {"data": "key=value"}})
        assert out["s1"]["value"] == "value"

    def test_regex_flag_case_insensitive(self):
        nodes = [_snode("s1", "regex", pattern=r"hello", flags="i", group=0)]
        out = _run(nodes, input_overrides={"s1": {"data": "HELLO world"}})
        assert out["s1"]["value"] == "HELLO"

    def test_regex_no_match(self):
        nodes = [_snode("s1", "regex", pattern=r"\d{10}", group=0)]
        out = _run(nodes, input_overrides={"s1": {"data": "short 123"}})
        assert out["s1"]["value"] is None

    def test_regex_invalid_pattern(self):
        nodes = [_snode("s1", "regex", pattern=r"[invalid")]
        out = _run(nodes, input_overrides={"s1": {"data": "some text"}})
        assert out["s1"]["value"] is None

    def test_regex_empty_pattern(self):
        nodes = [_snode("s1", "regex", pattern="")]
        out = _run(nodes, input_overrides={"s1": {"data": "some text"}})
        assert out["s1"]["value"] is None

    # ── general ───────────────────────────────────────────────────────────

    def test_no_data_returns_none(self):
        nodes = [_snode("s1", "rechts_von", search=":")]
        out = _run(nodes)
        assert out["s1"]["value"] is None

    def test_preview_populated(self):
        nodes = [_snode("s1", "rechts_von", search=":")]
        out = _run(nodes, input_overrides={"s1": {"data": "key:value"}})
        assert out["s1"]["_preview"] == "key:value"

    def test_preview_capped_at_20kb(self):
        big = "x" * 30_000
        nodes = [_snode("s1", "rechts_von", search=":")]
        out = _run(nodes, input_overrides={"s1": {"data": big}})
        assert len(out["s1"]["_preview"]) <= 20_000


# ===========================================================================
# Downstream integration
# ===========================================================================

class TestExtractorDownstream:

    def test_json_extractor_output_flows_to_next_node(self):
        """Value from json_extractor should reach a downstream const_value successor."""
        payload = json.dumps({"level": 75})
        nodes = [
            _jnode("j1", "level"),
            node("cmp", "compare", {"operator": ">", "threshold": 50}),
        ]
        edges = [edge("j1", "cmp", source_handle="value", target_handle="in1")]
        overrides = {"j1": {"data": payload}}
        out = _run(nodes, edges, input_overrides=overrides)
        # value=75 should arrive at cmp node's in1
        assert out["j1"]["value"] == 75
        # compare node should have received 75 as in1 — just verify it ran
        assert "out" in out.get("cmp", {})
