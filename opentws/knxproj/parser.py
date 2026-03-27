"""
KNX Project File Parser (.knxproj)

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
    address:     str         # "1/2/3"
    name:        str
    description: str
    dpt:         str | None  # "DPT9.001" oder None


def _dpt_from_xknxproject(dpt: dict | None) -> str | None:
    """xknxproject DPT-Dict → OpenTWS DPT-ID.

    xknxproject liefert: {"main": 9, "sub": 1} oder None
    """
    if not dpt:
        return None
    main = dpt.get("main")
    sub  = dpt.get("sub")
    if main is None:
        return None
    if sub is not None:
        return f"DPT{main}.{str(sub).zfill(3)}"
    # Nur Haupttyp → Default-Subtyp
    defaults = {
        1: "DPT1.001", 2: "DPT2.001",  5: "DPT5.001",
        6: "DPT6.010", 7: "DPT7.001",  8: "DPT8.001",
        9: "DPT9.001", 12: "DPT12.001", 13: "DPT13.001",
        14: "DPT14.054", 16: "DPT16.000",
    }
    return defaults.get(main, f"DPT{main}.001")


def parse_knxproj(file_bytes: bytes, password: str | None = None) -> list[GroupAddressRecord]:
    """
    .knxproj Datei parsen und alle Gruppenadressen zurückgeben.

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
        raise ValueError(
            "xknxproject nicht installiert. Bitte 'pip install xknxproject' ausführen."
        ) from e

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

    group_addresses = getattr(project, "group_addresses", {}) or {}

    records: list[GroupAddressRecord] = []
    for addr_str, ga in group_addresses.items():
        records.append(GroupAddressRecord(
            address=     addr_str,
            name=        getattr(ga, "name", "") or "",
            description= getattr(ga, "comment", "") or getattr(ga, "description", "") or "",
            dpt=         _dpt_from_xknxproject(getattr(ga, "dpt", None)),
        ))

    logger.info("xknxproject: %d Gruppenadressen gelesen", len(records))
    return records
