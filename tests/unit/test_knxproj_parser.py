"""Unit tests for obs.knxproj.parser.

Covers:
- _dpt_from_xknxproject: all variants
- _extract_group_names: dict and object-style project shapes
- _collect_fi_to_fn: XML extraction
- _walk_trade_el: recursive traversal with nested Trades
- parse_knxproj_trades: round-trip via minimal ZIP + real demo file
- parse_knxproj / parse_knxproj_locations: real demo .knxproj file (requires xknxproject)
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from obs.knxproj.parser import (
    FunctionRecord,
    GroupAddressRecord,
    LocationRecord,
    TradeRecord,
    _collect_fi_to_fn,
    _dpt_from_xknxproject,
    _extract_group_names,
    _walk_trade_el,
    parse_knxproj,
    parse_knxproj_locations,
    parse_knxproj_trades,
)

# Path to the checked-in demo project (no Docker required)
_DEMO_KNXPROJ = Path(__file__).parent.parent.parent / "tools" / "Demo-Test-Projekt-2026-04-06-17-18.knxproj"
_HAS_DEMO = _DEMO_KNXPROJ.exists()
_DEMO_BYTES = _DEMO_KNXPROJ.read_bytes() if _HAS_DEMO else b""


# ---------------------------------------------------------------------------
# _dpt_from_xknxproject
# ---------------------------------------------------------------------------

class TestDptFromXknxproject:
    def test_none_returns_none(self):
        assert _dpt_from_xknxproject(None) is None

    def test_empty_dict_returns_none(self):
        assert _dpt_from_xknxproject({}) is None

    def test_main_and_sub(self):
        assert _dpt_from_xknxproject({"main": 9, "sub": 1}) == "DPT9.001"

    def test_sub_zero_padded(self):
        assert _dpt_from_xknxproject({"main": 9, "sub": 4}) == "DPT9.004"

    def test_main_only_known(self):
        assert _dpt_from_xknxproject({"main": 1}) == "DPT1.001"

    def test_main_only_unknown_falls_back(self):
        assert _dpt_from_xknxproject({"main": 99}) == "DPT99.001"

    def test_main_16_default(self):
        assert _dpt_from_xknxproject({"main": 16}) == "DPT16.000"

    def test_main_missing_key(self):
        assert _dpt_from_xknxproject({"sub": 1}) is None


# ---------------------------------------------------------------------------
# _extract_group_names
# ---------------------------------------------------------------------------

class TestExtractGroupNames:
    def _make_dict_project(self):
        return {
            "group_ranges": {
                "1": {
                    "name": "Lichtsteuerung",
                    "group_ranges": {
                        "1/0": {"name": "Erdgeschoss"},
                        "1/1": {"name": "Obergeschoss"},
                    },
                },
                "2": {
                    "name": "Heizung",
                    "group_ranges": {},
                },
            }
        }

    def test_dict_project_main_names(self):
        main, mid = _extract_group_names(self._make_dict_project())
        assert main["1"] == "Lichtsteuerung"
        assert main["2"] == "Heizung"

    def test_dict_project_mid_names(self):
        _, mid = _extract_group_names(self._make_dict_project())
        assert mid["1/0"] == "Erdgeschoss"
        assert mid["1/1"] == "Obergeschoss"

    def test_empty_project(self):
        main, mid = _extract_group_names({})
        assert main == {}
        assert mid == {}

    def test_object_style_project(self):
        """xknxproject may return objects instead of dicts — SimpleNamespace simulates this."""
        mid_range = SimpleNamespace(name="EG")
        main_range = SimpleNamespace(name="Licht", group_ranges={"0/0": mid_range})
        project = SimpleNamespace(group_ranges={"0": main_range})
        main, mid = _extract_group_names(project)
        assert main["0"] == "Licht"
        assert mid["0/0"] == "EG"

    def test_no_group_ranges_key(self):
        main, mid = _extract_group_names({"other_key": 42})
        assert main == {}
        assert mid == {}


# ---------------------------------------------------------------------------
# _collect_fi_to_fn
# ---------------------------------------------------------------------------

class TestCollectFiToFn:
    def _xml_root(self, xml: str):
        return ElementTree.fromstring(xml)

    def test_basic_extraction(self):
        xml = """<Root xmlns="http://knx.org/xml/project/20">
            <Topology>
                <DeviceInstance>
                    <FunctionInstance Id="FI-1" RefId="FN-A"/>
                    <FunctionInstance Id="FI-2" RefId="FN-B"/>
                </DeviceInstance>
            </Topology>
        </Root>"""
        root = self._xml_root(xml)
        mapping = _collect_fi_to_fn(root)
        assert mapping == {"FI-1": "FN-A", "FI-2": "FN-B"}

    def test_no_function_instances(self):
        xml = "<Root><Topology><DeviceInstance/></Topology></Root>"
        root = self._xml_root(xml)
        assert _collect_fi_to_fn(root) == {}

    def test_ignores_incomplete_elements(self):
        xml = """<Root>
            <FunctionInstance Id="" RefId="FN-A"/>
            <FunctionInstance Id="FI-1" RefId=""/>
            <FunctionInstance Id="FI-2" RefId="FN-B"/>
        </Root>"""
        root = self._xml_root(xml)
        mapping = _collect_fi_to_fn(root)
        # Only the complete one is included
        assert mapping == {"FI-2": "FN-B"}


# ---------------------------------------------------------------------------
# _walk_trade_el
# ---------------------------------------------------------------------------

class TestWalkTradeEl:
    def _make_trade_el(self, xml: str):
        return ElementTree.fromstring(xml)

    def test_simple_trade(self):
        xml = '<Trade Id="T-1" Name="Elektro"/>'
        el = self._make_trade_el(xml)
        records: list[TradeRecord] = []
        counter = [0]
        _walk_trade_el(el, None, records, counter, {})
        assert len(records) == 1
        assert records[0].identifier == "T-1"
        assert records[0].name == "Elektro"
        assert records[0].parent_id is None

    def test_trade_with_parent(self):
        xml = '<Trade Id="T-2" Name="Sub"/>'
        el = self._make_trade_el(xml)
        records: list[TradeRecord] = []
        _walk_trade_el(el, "T-1", records, [0], {})
        assert records[0].parent_id == "T-1"

    def test_nested_trades(self):
        xml = """<Trade Id="T-1" Name="Parent">
            <Trade Id="T-2" Name="Child"/>
        </Trade>"""
        el = self._make_trade_el(xml)
        records: list[TradeRecord] = []
        _walk_trade_el(el, None, records, [0], {})
        assert len(records) == 2
        assert records[1].parent_id == "T-1"

    def test_device_instance_ref_links(self):
        xml = """<Trade Id="T-1" Name="Heizung">
            <DeviceInstanceRef Links="FI-1 FI-2"/>
        </Trade>"""
        el = self._make_trade_el(xml)
        records: list[TradeRecord] = []
        fi_to_fn = {"FI-1": "FN-A", "FI-2": "FN-B"}
        _walk_trade_el(el, None, records, [0], fi_to_fn)
        assert records[0].function_ids == ["FN-A", "FN-B"]

    def test_device_instance_ref_passthrough_when_no_mapping(self):
        xml = """<Trade Id="T-1" Name="X">
            <DeviceInstanceRef Links="FI-99"/>
        </Trade>"""
        el = self._make_trade_el(xml)
        records: list[TradeRecord] = []
        _walk_trade_el(el, None, records, [0], {})
        assert records[0].function_ids == ["FI-99"]

    def test_non_trade_element_ignored(self):
        xml = '<DeviceInstance Id="D-1"/>'
        el = self._make_trade_el(xml)
        records: list[TradeRecord] = []
        _walk_trade_el(el, None, records, [0], {})
        assert records == []

    def test_trade_without_id_ignored(self):
        xml = '<Trade Name="NoId"/>'
        el = self._make_trade_el(xml)
        records: list[TradeRecord] = []
        _walk_trade_el(el, None, records, [0], {})
        assert records == []

    def test_sort_order_increments(self):
        xml = """<Trade Id="T-1" Name="A">
            <Trade Id="T-2" Name="B"/>
        </Trade>"""
        el = self._make_trade_el(xml)
        records: list[TradeRecord] = []
        counter = [0]
        _walk_trade_el(el, None, records, counter, {})
        assert records[0].sort_order == 1
        assert records[1].sort_order == 2


# ---------------------------------------------------------------------------
# parse_knxproj_trades — minimal ZIP
# ---------------------------------------------------------------------------

def _make_zip_with_xml(xml_content: str) -> bytes:
    """Build a minimal .knxproj ZIP containing a 0.xml at a known path."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("P-001/0.xml", xml_content)
    return buf.getvalue()


class TestParseKnxprojTrades:
    def test_empty_zip_returns_empty(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w"):
            pass
        records = parse_knxproj_trades(buf.getvalue())
        assert records == []

    def test_single_trade(self):
        xml = """<KNX xmlns="http://knx.org/xml/project/20">
            <Project>
                <Installations>
                    <Trades>
                        <Trade Id="T-1" Name="Elektro"/>
                    </Trades>
                </Installations>
            </Project>
        </KNX>"""
        records = parse_knxproj_trades(_make_zip_with_xml(xml))
        assert len(records) == 1
        assert records[0].identifier == "T-1"
        assert records[0].name == "Elektro"

    def test_nested_trades(self):
        xml = """<KNX>
            <Trades>
                <Trade Id="T-1" Name="Parent">
                    <Trade Id="T-2" Name="Child"/>
                </Trade>
            </Trades>
        </KNX>"""
        records = parse_knxproj_trades(_make_zip_with_xml(xml))
        assert len(records) == 2
        assert records[0].name == "Parent"
        assert records[1].name == "Child"
        assert records[1].parent_id == "T-1"

    def test_fi_to_fn_resolution(self):
        xml = """<KNX>
            <Topology>
                <DeviceInstance>
                    <FunctionInstance Id="FI-1" RefId="FN-A"/>
                </DeviceInstance>
            </Topology>
            <Trades>
                <Trade Id="T-1" Name="X">
                    <DeviceInstanceRef Links="FI-1"/>
                </Trade>
            </Trades>
        </KNX>"""
        records = parse_knxproj_trades(_make_zip_with_xml(xml))
        assert records[0].function_ids == ["FN-A"]

    def test_invalid_zip_returns_empty(self):
        records = parse_knxproj_trades(b"not a zip")
        assert records == []


# ---------------------------------------------------------------------------
# parse_knxproj + parse_knxproj_locations — real demo file
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_DEMO, reason="Demo .knxproj not found")
class TestParseKnxprojReal:
    def test_returns_list_of_records(self):
        records = parse_knxproj(_DEMO_BYTES)
        assert isinstance(records, list)
        assert len(records) > 0

    def test_records_have_required_fields(self):
        records = parse_knxproj(_DEMO_BYTES)
        for r in records:
            assert isinstance(r, GroupAddressRecord)
            # address must match x/y/z pattern
            parts = r.address.split("/")
            assert len(parts) == 3
            assert all(p.isdigit() for p in parts)
            # name must be a string
            assert isinstance(r.name, str)

    def test_dpt_is_string_or_none(self):
        records = parse_knxproj(_DEMO_BYTES)
        for r in records:
            assert r.dpt is None or isinstance(r.dpt, str)
            if r.dpt:
                assert r.dpt.startswith("DPT")

    def test_group_names_populated(self):
        records = parse_knxproj(_DEMO_BYTES)
        # At least some records should have main/mid group names from the demo
        has_main = any(r.main_group_name for r in records)
        assert has_main, "Expected at least some records to have main_group_name"

    def test_invalid_file_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_knxproj(b"not a knxproj file")


@pytest.mark.skipif(not _HAS_DEMO, reason="Demo .knxproj not found")
class TestParseKnxprojLocationsReal:
    def test_returns_tuple(self):
        locs, fns = parse_knxproj_locations(_DEMO_BYTES)
        assert isinstance(locs, list)
        assert isinstance(fns, list)

    def test_locations_have_required_fields(self):
        locs, _ = parse_knxproj_locations(_DEMO_BYTES)
        for loc in locs:
            assert isinstance(loc, LocationRecord)
            assert loc.identifier
            assert isinstance(loc.name, str)
            assert isinstance(loc.space_type, str)

    def test_functions_have_required_fields(self):
        _, fns = parse_knxproj_locations(_DEMO_BYTES)
        for fn in fns:
            assert isinstance(fn, FunctionRecord)
            assert fn.identifier
            assert fn.space_id

    def test_invalid_file_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_knxproj_locations(b"garbage")

