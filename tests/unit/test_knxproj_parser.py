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
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
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
    _walk_spaces,
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


# ---------------------------------------------------------------------------
# _walk_spaces — object-style and advanced branches
# ---------------------------------------------------------------------------


def _make_fn_ns(name="Fn", usage_text="Schalten", ga_address="1/2/3"):
    """Build a SimpleNamespace function with one GA reference."""
    ga_ref = SimpleNamespace(address=ga_address)
    return SimpleNamespace(
        name=name,
        usage_text=usage_text,
        group_addresses={"ref1": ga_ref},
    )


class TestWalkSpaces:
    def _call(self, spaces, all_functions=None):
        loc_list: list[LocationRecord] = []
        fn_list: list[FunctionRecord] = []
        _walk_spaces(spaces, None, loc_list, fn_list, all_functions or {}, [0])
        return loc_list, fn_list

    # --- dict-style spaces with function processing ---

    def test_dict_space_with_dict_function(self):
        all_fns = {
            "FN-1": {
                "name": "Licht",
                "usage_text": "Schalten",
                "group_addresses": {"r1": {"address": "1/2/3"}},
            }
        }
        spaces = {
            "S-1": {
                "identifier": "S-1",
                "name": "Wohnzimmer",
                "type": "Room",
                "functions": ["FN-1"],
                "spaces": {},
            }
        }
        locs, fns = self._call(spaces, all_fns)
        assert len(locs) == 1
        assert locs[0].identifier == "S-1"
        assert len(fns) == 1
        assert fns[0].identifier == "FN-1"
        assert fns[0].ga_addresses == ["1/2/3"]

    def test_fn_not_in_all_functions_skipped(self):
        spaces = {
            "S-1": {
                "identifier": "S-1",
                "name": "Raum",
                "type": "Room",
                "functions": ["FN-MISSING"],
                "spaces": {},
            }
        }
        locs, fns = self._call(spaces, {})
        assert len(locs) == 1
        assert len(fns) == 0  # FN-MISSING skipped

    def test_dict_space_with_object_function(self):
        """Function stored as object (non-dict) inside all_functions."""
        fn_ns = _make_fn_ns("Dimmer", "Dimmen", "2/3/4")
        spaces = {
            "S-1": {
                "identifier": "S-1",
                "name": "Küche",
                "type": "Room",
                "functions": ["FN-1"],
                "spaces": {},
            }
        }
        locs, fns = self._call(spaces, {"FN-1": fn_ns})
        assert fns[0].name == "Dimmer"
        assert fns[0].usage_text == "Dimmen"
        assert fns[0].ga_addresses == ["2/3/4"]

    def test_ga_ref_object_style(self):
        """GA reference stored as object instead of dict."""
        ga_ref_obj = SimpleNamespace(address="5/6/7")
        fn = {"name": "X", "usage_text": "Y", "group_addresses": {"r": ga_ref_obj}}
        spaces = {
            "S-1": {
                "identifier": "S-1",
                "name": "Z",
                "type": "Room",
                "functions": ["FN-1"],
                "spaces": {},
            }
        }
        locs, fns = self._call(spaces, {"FN-1": fn})
        assert fns[0].ga_addresses == ["5/6/7"]

    def test_empty_ga_address_skipped(self):
        """GA refs with blank address should not be added."""
        fn = {"name": "X", "usage_text": "Y", "group_addresses": {"r": {"address": ""}}}
        spaces = {"S-1": {"identifier": "S-1", "name": "Z", "type": "Room", "functions": ["FN-1"], "spaces": {}}}
        locs, fns = self._call(spaces, {"FN-1": fn})
        assert fns[0].ga_addresses == []

    def test_recursive_sub_spaces(self):
        """Child spaces are walked recursively with parent_id set."""
        spaces = {
            "B-1": {
                "identifier": "B-1",
                "name": "Gebäude",
                "type": "Building",
                "functions": [],
                "spaces": {
                    "F-1": {
                        "identifier": "F-1",
                        "name": "EG",
                        "type": "Floor",
                        "functions": [],
                        "spaces": {},
                    }
                },
            }
        }
        locs, _ = self._call(spaces)
        assert len(locs) == 2
        assert locs[1].identifier == "F-1"
        assert locs[1].parent_id == "B-1"

    # --- object-style space branch (lines 254-258) ---

    def test_object_style_space(self):
        fn_ns = _make_fn_ns()
        space_ns = SimpleNamespace(
            identifier="S-OBJ",
            name="ObjRaum",
            type="Room",
            space_type="",
            functions=["FN-OBJ"],
            spaces={},
        )
        locs, fns = self._call({"S-OBJ": space_ns}, {"FN-OBJ": fn_ns})
        assert locs[0].identifier == "S-OBJ"
        assert locs[0].name == "ObjRaum"
        assert fns[0].ga_addresses == ["1/2/3"]


# ---------------------------------------------------------------------------
# parse_knxproj / parse_knxproj_locations — ImportError paths
# ---------------------------------------------------------------------------


class TestImportErrorPaths:
    def test_parse_knxproj_import_error(self):
        with mock.patch.dict(sys.modules, {"xknxproject": None}):
            with pytest.raises(ValueError, match="xknxproject"):
                parse_knxproj(b"irrelevant")

    def test_parse_knxproj_locations_import_error(self):
        with mock.patch.dict(sys.modules, {"xknxproject": None}):
            with pytest.raises(ValueError, match="xknxproject"):
                parse_knxproj_locations(b"irrelevant")


# ---------------------------------------------------------------------------
# parse_knxproj — password error branch + object-style project
# ---------------------------------------------------------------------------


class TestParseKnxprojPasswordError:
    def test_password_error_raised_as_value_error(self):
        """When xknxproject raises a password-related error it must be re-raised as ValueError."""
        fake_xknx = mock.MagicMock()
        fake_xknx.XKNXProj.return_value.parse.side_effect = Exception("bad password detected")
        with mock.patch.dict(sys.modules, {"xknxproject": fake_xknx}):
            with pytest.raises(ValueError, match="Passwort|verschlüsselt"):
                parse_knxproj(b"fake")

    def test_locations_password_error(self):
        fake_xknx = mock.MagicMock()
        fake_xknx.XKNXProj.return_value.parse.side_effect = Exception("decrypt failed")
        with mock.patch.dict(sys.modules, {"xknxproject": fake_xknx}):
            with pytest.raises(ValueError, match="Passwort|verschlüsselt"):
                parse_knxproj_locations(b"fake")


class TestParseKnxprojObjectStyleProject:
    """Cover the object-style (non-dict) project branches in parse_knxproj and parse_knxproj_locations."""

    def _make_fake_xknx(self, project_obj):
        fake_xknx = mock.MagicMock()
        fake_xknx.XKNXProj.return_value.parse.return_value = project_obj
        return fake_xknx

    def test_parse_knxproj_object_style(self):
        ga = SimpleNamespace(name="Licht", comment="Kommentar", dpt={"main": 1, "sub": 1})
        project = SimpleNamespace(
            group_ranges={},
            group_addresses={"1/2/3": ga},
        )
        fake_xknx = self._make_fake_xknx(project)
        with mock.patch.dict(sys.modules, {"xknxproject": fake_xknx}):
            records = parse_knxproj(b"fake")
        assert len(records) == 1
        assert records[0].address == "1/2/3"
        assert records[0].name == "Licht"
        assert records[0].description == "Kommentar"
        assert records[0].dpt == "DPT1.001"

    def test_parse_knxproj_locations_object_style(self):
        project = SimpleNamespace(locations={}, functions={})
        fake_xknx = self._make_fake_xknx(project)
        with mock.patch.dict(sys.modules, {"xknxproject": fake_xknx}):
            locs, fns = parse_knxproj_locations(b"fake")
        assert locs == []
        assert fns == []
