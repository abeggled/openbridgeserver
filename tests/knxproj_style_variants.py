"""Test-time variants of the demo .knxproj in every ETS group address style (#1296).

The demo project in ``tools/`` is three-level and contains group addresses only.
These helpers derive, at test time and without checked-in binaries:

- a **two-level** project: ``GroupAddressStyle="TwoLevel"`` *and* no middle-group
  ranges — the group addresses of each middle range move up into its main range,
  the way ETS lays out a two-level project;
- a **free** project: ``GroupAddressStyle="Free"``; free style allows arbitrary
  nested ranges, so the existing range nesting stays as it is;
- in every variant one device (PA ``1.1.5``) whose two communication objects link
  group addresses, and one function in the building that references a third one.

Group addresses are stored as raw numbers in ETS XML, so all variants carry the
same addresses; only the style (and with it xknxproject's formatting) differs.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.etree import ElementTree

DEMO_KNXPROJ = Path(__file__).parent.parent / "tools" / "Demo-Test-Projekt-2026-04-06-17-18.knxproj"

STYLES = ("ThreeLevel", "TwoLevel", "Free")

DEVICE_PA = "1.1.5"
# Raw addresses linked below, with their internal (three-level) notation.
CO_SWITCH_RAW = 2305  # 1/1/1  "Licht EG Schalten"
CO_STATUS_RAW = 2306  # 1/1/2  "Licht OG Schalten"
FUNCTION_RAW = 2307  # 1/1/3  "Licht Keller Schalten"
STATE_RAW = 2308  # 1/1/4  "Licht Garten Schalten", linked by nothing
INTERNAL = {CO_SWITCH_RAW: "1/1/1", CO_STATUS_RAW: "1/1/2", FUNCTION_RAW: "1/1/3", STATE_RAW: "1/1/4"}
NOTATION = {
    "ThreeLevel": dict(INTERNAL),
    "TwoLevel": {CO_SWITCH_RAW: "1/257", CO_STATUS_RAW: "1/258", FUNCTION_RAW: "1/259", STATE_RAW: "1/260"},
    "Free": {raw: str(raw) for raw in INTERNAL},
}

_NS = "http://knx.org/xml/project/23"
_PROJECT = "P-065E"


def _q(tag: str) -> str:
    return f"{{{_NS}}}{tag}"


def _ga_id(root: ElementTree.Element, raw: int) -> str:
    for ga in root.iter(_q("GroupAddress")):
        if ga.get("Address") == str(raw):
            return ga.get("Id", "")
    raise LookupError(raw)


def _flatten_middle_ranges(root: ElementTree.Element) -> None:
    """Turn main/middle/address into main/address, as in a two-level ETS project."""
    ranges = root.find(f".//{_q('GroupAddresses')}/{_q('GroupRanges')}")
    for main_range in ranges.findall(_q("GroupRange")):
        for middle_range in main_range.findall(_q("GroupRange")):
            main_range.remove(middle_range)
            main_range.extend(middle_range.findall(_q("GroupAddress")))


def _add_device_and_function(root: ElementTree.Element) -> None:
    switch_id, status_id, function_ga_id = (_ga_id(root, raw) for raw in (CO_SWITCH_RAW, CO_STATUS_RAW, FUNCTION_RAW))
    segment = root.find(f".//{_q('Line')}[@Id='{_PROJECT}-0_L-3']/{_q('Segment')}")
    device = ElementTree.SubElement(
        segment,
        _q("DeviceInstance"),
        Id=f"{_PROJECT}-0_DI-1",
        Address="5",
        Name="Testaktor",
        ProductRefId="M-0083_H-1-1_P-1",
        Hardware2ProgramRefId="M-0083_H-1-1_HP-1",
        Puid="900",
    )
    refs = ElementTree.SubElement(device, _q("ComObjectInstanceRefs"))
    # ETS 5.7+ links group addresses by their project-local id ("0_GA-1").
    for number, (text, ga_id) in enumerate((("Schalten", switch_id), ("Status", status_id)), start=1):
        ElementTree.SubElement(
            refs,
            _q("ComObjectInstanceRef"),
            RefId=f"O-{number}_R-{number}",
            Text=text,
            DatapointType="DPST-1-1",
            Links=ga_id.split("_", 1)[1],
        )

    building = root.find(f".//{_q('Locations')}/{_q('Space')}")
    function = ElementTree.SubElement(building, _q("Function"), Id=f"{_PROJECT}-0_F-1", Name="Licht Keller", Type="SwitchableLight", Puid="901")
    ElementTree.SubElement(
        function,
        _q("GroupAddressRef"),
        Id=f"{_PROJECT}-0_F-1_GR-1",
        RefId=function_ga_id,
        Name="Schalten",
        Role="SwitchOnOff",
        Puid="902",
    )
    ElementTree.SubElement(building, _q("DeviceInstanceRef"), RefId=f"{_PROJECT}-0_DI-1")


def knxproj_in_style(style: str) -> bytes:
    """Return the demo project as .knxproj bytes in ``style``, with device and function."""
    if style not in STYLES:
        raise ValueError(style)
    ElementTree.register_namespace("", _NS)
    ElementTree.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    ElementTree.register_namespace("xsd", "http://www.w3.org/2001/XMLSchema")
    out = io.BytesIO()
    with zipfile.ZipFile(DEMO_KNXPROJ) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == f"{_PROJECT}/project.xml":
                data = data.replace(b'GroupAddressStyle="ThreeLevel"', f'GroupAddressStyle="{style}"'.encode())
            elif info.filename == f"{_PROJECT}/0.xml":
                root = ElementTree.fromstring(data)
                if style == "TwoLevel":
                    _flatten_middle_ranges(root)
                _add_device_and_function(root)
                data = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
            zout.writestr(info, data)
    return out.getvalue()
