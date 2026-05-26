"""KNX Project File Parser (.knxproj)

Verwendet xknxproject (Home Assistant's KNX library) für robustes Parsing:
- ETS4, ETS5, ETS6
- Passwortgeschützte Projekte (AES)
- Alle Namespaces und Formate

https://github.com/XKNX/xknxproject
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import zipfile
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree

logger = logging.getLogger(__name__)


@dataclass
class GroupAddressRecord:
    address: str  # "1/2/3"
    name: str
    description: str
    dpt: str | None  # "DPT9.001" oder None
    main_group_name: str = ""  # ETS-Name der Hauptgruppe (z.B. "Lichtsteuerung")
    mid_group_name: str = ""  # ETS-Name der Mittelgruppe (z.B. "Erdgeschoss")


@dataclass
class LocationRecord:
    identifier: str  # stable ETS ID, e.g. "P-0001-0_B-17"
    parent_id: str | None  # parent identifier or None for roots
    name: str
    space_type: str  # "Building", "Floor", "Room", "Distribution", …
    sort_order: int = 0


@dataclass
class FunctionRecord:
    identifier: str  # stable ETS function ID
    space_id: str  # identifier of the containing Space
    name: str
    usage_text: str  # e.g. "Bewegung", "Heizen/Klima", "Schalten/Dimmen"
    ga_addresses: list[str] = field(default_factory=list)  # ["1/2/3", …]


@dataclass
class TradeRecord:
    identifier: str  # ETS Trade ID, e.g. "P-065E-0_T-1"
    name: str  # e.g. "Bewegung", "Schalten/Dimmen"
    parent_id: str | None = None  # parent trade ID for nested trades
    sort_order: int = 0
    function_ids: list[str] = field(default_factory=list)  # resolved Function IDs


def _extract_group_names(project: Any) -> tuple[dict[str, str], dict[str, str]]:
    """Extracts main- and middle-group names from xknxproject group_ranges.

    xknxproject uses:
      project["group_ranges"]                      → dict keyed by str_address() e.g. "0", "1"
      project["group_ranges"]["0"]["group_ranges"] → nested dict keyed by "0/0", "0/1", …

    Returns:
      main_names["1"]    → "Lichtsteuerung"
      mid_names["1/2"]   → "Erdgeschoss"
    """
    main_names: dict[str, str] = {}
    mid_names: dict[str, str] = {}

    if isinstance(project, dict):
        top_ranges = project.get("group_ranges", {}) or {}
    else:
        top_ranges = getattr(project, "group_ranges", {}) or {}

    for main_key, main_range in top_ranges.items():
        main_str = str(main_key)  # already "0", "1", …
        if isinstance(main_range, dict):
            main_name = str(main_range.get("name", "") or "").strip()
            sub_ranges = main_range.get("group_ranges", {}) or {}
        else:
            main_name = str(getattr(main_range, "name", "") or "").strip()
            sub_ranges = getattr(main_range, "group_ranges", {}) or {}
        main_names[main_str] = main_name

        for mid_key, mid_range in sub_ranges.items():
            # mid_key is already "0/0", "0/1", … from str_address()
            mid_str = str(mid_key)
            if isinstance(mid_range, dict):
                mid_name = str(mid_range.get("name", "") or "").strip()
            else:
                mid_name = str(getattr(mid_range, "name", "") or "").strip()
            mid_names[mid_str] = mid_name

    return main_names, mid_names


def _dpt_from_xknxproject(dpt: dict | None) -> str | None:
    """Xknxproject DPT-Dict → open bridge server DPT-ID.

    xknxproject liefert: {"main": 9, "sub": 1} oder None
    """
    if not dpt:
        return None
    main = dpt.get("main")
    sub = dpt.get("sub")
    if main is None:
        return None
    if sub is not None:
        return f"DPT{main}.{str(sub).zfill(3)}"
    # Nur Haupttyp → Default-Subtyp
    defaults = {
        1: "DPT1.001",
        2: "DPT2.001",
        5: "DPT5.001",
        6: "DPT6.010",
        7: "DPT7.001",
        8: "DPT8.001",
        9: "DPT9.001",
        12: "DPT12.001",
        13: "DPT13.001",
        14: "DPT14.054",
        16: "DPT16.000",
    }
    return defaults.get(main, f"DPT{main}.001")


def _collect_fi_to_fn(root: Any) -> dict[str, str]:
    """Build FunctionInstance-ID → Function-ID mapping from <Topology>.

    In ETS XML a <DeviceInstance> in <Topology> holds <FunctionInstance Id="..." RefId="..."/>
    where RefId points to the <Function> in <Locations>.  Trade references in
    <DeviceInstanceRef Links="..."> use FunctionInstance IDs — this map resolves them to the
    Function IDs that xknxproject stores in knx_functions.id.
    """
    fi_to_fn: dict[str, str] = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "FunctionInstance":
            fi_id = el.get("Id", "").strip()
            fn_ref = el.get("RefId", "").strip()
            if fi_id and fn_ref:
                fi_to_fn[fi_id] = fn_ref
    return fi_to_fn


def _walk_trade_el(
    trade_el: Any,
    parent_id: str | None,
    records: list[TradeRecord],
    sort_counter: list[int],
    fi_to_fn: dict[str, str],
) -> None:
    """Recursively walk a <Trade> element and collect TradeRecords with parent hierarchy."""
    tag = trade_el.tag.split("}")[-1] if "}" in trade_el.tag else trade_el.tag
    if tag != "Trade":
        return

    tid = trade_el.get("Id", "").strip()
    name = trade_el.get("Name", "").strip()
    if not (tid and name):
        return

    # Collect function IDs from DeviceInstanceRef.Links; resolve FunctionInstance → Function
    function_ids: list[str] = []
    for child in trade_el:
        ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if ctag == "DeviceInstanceRef":
            links = child.get("Links", "").strip()
            for link_id in links.split():
                # Resolve via FunctionInstance map if available; fall back to raw ID
                function_ids.append(fi_to_fn.get(link_id, link_id))

    sort_counter[0] += 1
    records.append(
        TradeRecord(
            identifier=tid,
            name=name,
            parent_id=parent_id,
            sort_order=sort_counter[0],
            function_ids=function_ids,
        )
    )

    # Recurse into nested <Trade> children (ETS supports trade sub-categories)
    for child in trade_el:
        ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if ctag == "Trade":
            _walk_trade_el(child, tid, records, sort_counter, fi_to_fn)


def parse_knxproj_trades(file_bytes: bytes) -> list[TradeRecord]:
    """Parse <Trades><Trade .../></Trades> directly from the .knxproj ZIP.

    xknxproject does not expose this section. Supports:
    - Nested <Trade> elements (sub-categories) → parent_id is set accordingly
    - DeviceInstanceRef.Links → resolves FunctionInstance IDs to Function IDs

    Returns: list of TradeRecord in pre-order document order.
    """
    records: list[TradeRecord] = []
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            candidates = [n for n in zf.namelist() if n.endswith("0.xml") and "/" in n]
            if not candidates:
                logger.warning("parse_knxproj_trades: no 0.xml found in archive")
                return records

            xml_bytes = zf.read(candidates[0])
            root = ElementTree.fromstring(xml_bytes)

            fi_to_fn = _collect_fi_to_fn(root)

            # Find top-level <Trades> container and walk direct <Trade> children
            sort_counter = [0]
            for el in root.iter():
                etag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
                if etag == "Trades":
                    for child in el:
                        ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if ctag == "Trade":
                            _walk_trade_el(child, None, records, sort_counter, fi_to_fn)
                    break  # only the first <Trades> element

    except Exception as e:
        logger.warning("parse_knxproj_trades failed (ignored): %s", e)

    logger.info("parse_knxproj_trades: %d Gewerke gefunden", len(records))
    return records


def _walk_spaces(
    spaces: dict,
    parent_id: str | None,
    loc_list: list[LocationRecord],
    fn_list: list[FunctionRecord],
    all_functions: dict,
    sort_counter: list[int],
) -> None:
    """Recursively walk xknxproject Space dict and collect LocationRecords + FunctionRecords."""
    for space_key, space in spaces.items():
        if isinstance(space, dict):
            identifier = str(space.get("identifier") or space_key)
            name = str(space.get("name") or "").strip() or identifier
            space_type = str(space.get("type") or space.get("space_type") or "").strip()
            fn_ids: list = space.get("functions") or []
            sub_spaces: dict = space.get("spaces") or {}
        else:
            identifier = str(getattr(space, "identifier", space_key))
            name = str(getattr(space, "name", "") or "").strip() or identifier
            space_type = str(getattr(space, "type", "") or getattr(space, "space_type", "") or "").strip()
            fn_ids = list(getattr(space, "functions", []) or [])
            sub_spaces = dict(getattr(space, "spaces", {}) or {})

        sort_counter[0] += 1
        loc_list.append(
            LocationRecord(
                identifier=identifier,
                parent_id=parent_id,
                name=name,
                space_type=space_type,
                sort_order=sort_counter[0],
            )
        )

        # Functions linked to this space
        for fn_id in fn_ids:
            fn_id_str = str(fn_id)
            fn = all_functions.get(fn_id_str)
            if fn is None:
                continue
            if isinstance(fn, dict):
                fn_name = str(fn.get("name") or "").strip()
                usage_text = str(fn.get("usage_text") or fn.get("type") or "").strip()
                ga_refs = fn.get("group_addresses") or {}
            else:
                fn_name = str(getattr(fn, "name", "") or "").strip()
                usage_text = str(getattr(fn, "usage_text", "") or getattr(fn, "type", "") or "").strip()
                ga_refs = dict(getattr(fn, "group_addresses", {}) or {})

            ga_addresses: list[str] = []
            for _ref_key, ref in ga_refs.items():
                if isinstance(ref, dict):
                    addr = str(ref.get("address") or "").strip()
                else:
                    addr = str(getattr(ref, "address", "") or "").strip()
                if addr:
                    ga_addresses.append(addr)

            fn_list.append(
                FunctionRecord(
                    identifier=fn_id_str,
                    space_id=identifier,
                    name=fn_name,
                    usage_text=usage_text,
                    ga_addresses=ga_addresses,
                )
            )

        # Recurse
        if sub_spaces:
            _walk_spaces(sub_spaces, identifier, loc_list, fn_list, all_functions, sort_counter)


def parse_knxproj_locations(
    file_bytes: bytes,
    password: str | None = None,
) -> tuple[list[LocationRecord], list[FunctionRecord]]:
    """Parse .knxproj and return building/space hierarchy + function groups.

    Returns:
        (locations, functions) — lists ready for DB insertion.
    """
    try:
        from xknxproject import XKNXProj
    except ImportError as e:
        raise ValueError("xknxproject nicht installiert.") from e

    tmp_path = None
    try:
        import tempfile as _tmp

        with _tmp.NamedTemporaryFile(suffix=".knxproj", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        knxproject = XKNXProj(tmp_path, password=password)
        project = knxproject.parse()
    except Exception as e:
        msg = str(e)
        if "password" in msg.lower() or "decrypt" in msg.lower():
            raise ValueError("Falsches Passwort oder Datei ist verschlüsselt.") from e
        raise ValueError(f"Fehler beim Parsen: {msg}") from e
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    if isinstance(project, dict):
        top_spaces = project.get("locations") or {}
        all_functions = project.get("functions") or {}
    else:
        top_spaces = dict(getattr(project, "locations", {}) or {})
        all_functions = dict(getattr(project, "functions", {}) or {})

    loc_list: list[LocationRecord] = []
    fn_list: list[FunctionRecord] = []
    sort_counter = [0]

    _walk_spaces(top_spaces, None, loc_list, fn_list, all_functions, sort_counter)

    logger.info(
        "parse_knxproj_locations: %d Spaces, %d Functions",
        len(loc_list),
        len(fn_list),
    )
    return loc_list, fn_list


def parse_knxproj(file_bytes: bytes, password: str | None = None) -> list[GroupAddressRecord]:
    """.knxproj Datei parsen und alle Gruppenadressen zurückgeben.

    Args:
        file_bytes: Rohe Bytes der .knxproj Datei
        password:   Projektpasswort (falls vorhanden)

    Returns:
        Liste von GroupAddressRecord

    Raises:
        ValueError: wenn die Datei nicht geparst werden kann

    """
    try:
        from xknxproject import XKNXProj
    except ImportError as e:
        raise ValueError("xknxproject nicht installiert. Bitte 'pip install xknxproject' ausführen.") from e

    # xknxproject benötigt einen Dateipfad → temporäre Datei erstellen
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".knxproj", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        knxproject = XKNXProj(tmp_path, password=password)
        project = knxproject.parse()

    except Exception as e:
        msg = str(e)
        if "password" in msg.lower() or "decrypt" in msg.lower() or "bad password" in msg.lower():
            raise ValueError("Falsches Passwort oder Datei ist verschlüsselt.") from e
        raise ValueError(f"Fehler beim Parsen der .knxproj Datei: {msg}") from e
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    main_names, mid_names = _extract_group_names(project)
    logger.info("group_address_ranges: %d Hauptgruppen, %d Mittelgruppen", len(main_names), len(mid_names))

    # KNXProject ist ein TypedDict → dict-Zugriff, nicht Attribut-Zugriff
    logger.info(
        "parse() Typ: %s, Keys: %s",
        type(project).__name__,
        list(project.keys()) if isinstance(project, dict) else dir(project),
    )

    if isinstance(project, dict):
        group_addresses = project.get("group_addresses", {}) or {}
    else:
        group_addresses = getattr(project, "group_addresses", {}) or {}

    logger.info(
        "group_addresses Typ: %s, Anzahl: %d",
        type(group_addresses).__name__,
        len(group_addresses),
    )

    # Ersten Eintrag zur Diagnose loggen
    if group_addresses:
        first_key = next(iter(group_addresses))
        first_val = group_addresses[first_key]
        logger.info(
            "Beispiel GA: key=%r val_type=%s val=%r",
            first_key,
            type(first_val).__name__,
            first_val,
        )

    records: list[GroupAddressRecord] = []
    for addr_str, ga in group_addresses.items():
        # ga kann dict (TypedDict) oder Objekt sein
        if isinstance(ga, dict):
            name = ga.get("name", "") or ""
            description = ga.get("comment", "") or ga.get("description", "") or ""
            dpt_raw = ga.get("dpt")
        else:
            name = getattr(ga, "name", "") or ""
            description = getattr(ga, "comment", "") or getattr(ga, "description", "") or ""
            dpt_raw = getattr(ga, "dpt", None)

        # Resolve parent group names
        parts = addr_str.split("/")
        main_key = parts[0] if parts else ""
        mid_key = f"{parts[0]}/{parts[1]}" if len(parts) > 1 else ""
        records.append(
            GroupAddressRecord(
                address=addr_str,
                name=name,
                description=description,
                dpt=_dpt_from_xknxproject(dpt_raw),
                main_group_name=main_names.get(main_key, ""),
                mid_group_name=mid_names.get(mid_key, ""),
            ),
        )

    logger.info("xknxproject: %d Gruppenadressen gelesen", len(records))
    return records
