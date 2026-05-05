"""KNX Project File Parser (.knxproj)

Verwendet xknxproject (Home Assistant's KNX library) für robustes Parsing:
- ETS4, ETS5, ETS6
- Passwortgeschützte Projekte (AES)
- Alle Namespaces und Formate

https://github.com/XKNX/xknxproject
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class GroupAddressRecord:
    address: str  # "1/2/3"
    name: str
    description: str
    dpt: str | None          # "DPT9.001" oder None
    main_group_name: str = ""  # ETS-Name der Hauptgruppe (z.B. "Lichtsteuerung")
    mid_group_name: str = ""   # ETS-Name der Mittelgruppe (z.B. "Erdgeschoss")


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
