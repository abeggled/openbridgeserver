"""
KNX Project File Parser (.knxproj)

Unterstützt:
- Standard ZIP (ETS4 / ETS5)
- AES-verschlüsselte ZIP (ETS6, via pyzipper)
- Passwortgeschützte Projekte
- Alle ETS-XML-Namespaces (http://knx.org/xml/project/*)

Gibt eine Liste von GroupAddressRecord-Objekten zurück:
  address     — "1/2/3"
  name        — Beschreibung aus ETS
  description — Kommentarfeld
  dpt         — normalisiert auf OpenTWS-Format ("DPT9.001") oder None
"""
from __future__ import annotations

import io
import logging
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass

logger = logging.getLogger(__name__)

try:
    import pyzipper
    _HAS_PYZIPPER = True
except ImportError:
    _HAS_PYZIPPER = False
    logger.warning("pyzipper nicht installiert — AES-verschlüsselte .knxproj Dateien werden nicht unterstützt")


# ---------------------------------------------------------------------------
# DPT Mapping: ETS-Format → OpenTWS-Format
# ---------------------------------------------------------------------------

# Wenn nur der Haupttyp angegeben ist (kein Subtyp), wird dieser Default verwendet
_DPT_MAIN_DEFAULTS: dict[str, str] = {
    "1":  "DPT1.001",
    "2":  "DPT2.001",
    "3":  "DPT3.007",
    "4":  "DPT4.001",
    "5":  "DPT5.001",
    "6":  "DPT6.010",
    "7":  "DPT7.001",
    "8":  "DPT8.001",
    "9":  "DPT9.001",
    "10": "DPT10.001",
    "11": "DPT11.001",
    "12": "DPT12.001",
    "13": "DPT13.001",
    "14": "DPT14.054",
    "16": "DPT16.000",
    "17": "DPT17.001",
    "18": "DPT18.001",
    "19": "DPT19.001",
    "20": "DPT20.001",
}


def _normalize_dpt(raw: str | None) -> str | None:
    """ETS DPT-String → OpenTWS DPT-ID.

    Eingabeformate:
      "DPST-9-1"   → "DPT9.001"
      "DPT-9"      → "DPT9.001"  (Default-Subtyp)
      "9.001"      → "DPT9.001"
      ""           → None
    """
    if not raw or not raw.strip():
        return None
    raw = raw.strip()

    # Format: "DPST-9-1" → DPT9.001
    if raw.startswith("DPST-"):
        parts = raw[5:].split("-")
        if len(parts) >= 2:
            main = parts[0]
            sub  = parts[1].zfill(3)
            return f"DPT{main}.{sub}"
        elif len(parts) == 1:
            return _DPT_MAIN_DEFAULTS.get(parts[0])

    # Format: "DPT-9" → default subtype
    if raw.startswith("DPT-"):
        main = raw[4:]
        return _DPT_MAIN_DEFAULTS.get(main)

    # Format: "9.001" → DPT9.001
    if "." in raw and not raw.startswith("DPT"):
        parts = raw.split(".")
        if len(parts) == 2 and parts[0].isdigit():
            return f"DPT{parts[0]}.{parts[1].zfill(3)}"

    # Already in DPT9.001 format
    if raw.startswith("DPT") and "." in raw:
        return raw

    return None


# ---------------------------------------------------------------------------
# Address conversion
# ---------------------------------------------------------------------------

def _addr_to_str(addr_int: int) -> str:
    """Ganzzahl-GA → "Hauptgruppe/Mittelgruppe/Untergruppe"."""
    main   = (addr_int >> 11) & 0x1F
    middle = (addr_int >>  8) & 0x07
    sub    =  addr_int        & 0xFF
    return f"{main}/{middle}/{sub}"


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class GroupAddressRecord:
    address:     str         # "1/2/3"
    name:        str
    description: str
    dpt:         str | None  # "DPT9.001" oder None


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------

def _parse_installation_xml(xml_bytes: bytes) -> list[GroupAddressRecord]:
    """Alle GroupAddress-Elemente aus einer ETS-Installations-XML extrahieren."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        logger.warning("XML-Parse-Fehler: %s", e)
        return []

    results: list[GroupAddressRecord] = []

    # Namespace-agnostische Suche: '{*}GroupAddress' funktioniert ab Python 3.8
    for elem in root.iter():
        # Tag ohne Namespace vergleichen
        local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if local != "GroupAddress":
            continue

        addr_raw = elem.get("Address")
        if addr_raw is None:
            continue

        try:
            addr_int = int(addr_raw)
        except ValueError:
            continue

        # DPT: "DPTs" ist das Standardattribut, "DatapointType" ein Alias
        dpt_raw = elem.get("DPTs") or elem.get("DatapointType") or elem.get("Dpt")

        results.append(GroupAddressRecord(
            address=     _addr_to_str(addr_int),
            name=        elem.get("Name", "").strip(),
            description= elem.get("Description", "").strip(),
            dpt=         _normalize_dpt(dpt_raw),
        ))

    return results


# ---------------------------------------------------------------------------
# ZIP opening (mit Fallback auf pyzipper für AES)
# ---------------------------------------------------------------------------

def _open_zip(file_bytes: bytes, password: str | None):
    """Gibt ein geöffnetes ZipFile-artiges Objekt zurück (Standard oder AES)."""
    pwd_bytes = password.encode("utf-8") if password else None

    # 1. Versuch: Standard zipfile (kein Passwort oder Standard-ZIP-Verschlüsselung)
    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
        if pwd_bytes:
            zf.setpassword(pwd_bytes)
        # Probe-Lesen um sicherzustellen dass das Passwort korrekt ist
        names = zf.namelist()
        if names:
            try:
                zf.read(names[0], pwd=pwd_bytes)
            except (RuntimeError, zipfile.BadZipFile):
                zf.close()
                raise
        return zf
    except (RuntimeError, zipfile.BadZipFile, Exception):
        pass

    # 2. Versuch: pyzipper für AES-verschlüsselte ZIP (ETS6)
    if _HAS_PYZIPPER:
        try:
            zf = pyzipper.AESZipFile(io.BytesIO(file_bytes))
            if pwd_bytes:
                zf.setpassword(pwd_bytes)
            return zf
        except Exception as e:
            raise ValueError(f"Konnte .knxproj nicht öffnen (auch mit pyzipper): {e}") from e
    else:
        raise ValueError(
            "Datei konnte nicht geöffnet werden. "
            "Für AES-verschlüsselte ETS6-Projekte ist 'pyzipper' erforderlich."
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_knxproj(file_bytes: bytes, password: str | None = None) -> list[GroupAddressRecord]:
    """
    .knxproj Datei parsen und alle Gruppenadressen zurückgeben.

    Args:
        file_bytes: Rohe Bytes der .knxproj Datei
        password:   Projektpasswort (falls vorhanden)

    Returns:
        Liste von GroupAddressRecord

    Raises:
        ValueError: wenn die Datei nicht geöffnet oder geparst werden kann
    """
    try:
        zf = _open_zip(file_bytes, password)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Ungültige .knxproj Datei: {e}") from e

    with zf:
        names = zf.namelist()
        logger.debug("knxproj Inhalt: %s", names)

        # Projektordner erkennen: P-XXXX/ Verzeichnisse
        project_folders = sorted({
            n.split("/")[0] for n in names
            if n.startswith("P-") and "/" in n
        })
        if project_folders:
            logger.info("knxproj: Projektordner gefunden: %s", project_folders)
        else:
            logger.warning(
                "knxproj: Keine Projektordner (P-XXXX/) gefunden. "
                "Vorhandene Top-Level-Ordner: %s — "
                "Bitte das ETS-Projekt exportieren (Datei > Speichern unter / Projekt exportieren), "
                "nicht die Produktdatenbank.",
                sorted({n.split("/")[0] for n in names if "/" in n})[:10],
            )

        # Installationsdateien suchen: P-*/0.xml, */0.xml oder 0.xml (Priorität)
        install_files = [
            n for n in names
            if n.endswith("/0.xml") or n == "0.xml"
        ]

        if not install_files:
            # Fallback: alle XML-Dateien ausser reinen Katalog-/Hardware-Dateien
            install_files = [
                n for n in names
                if n.endswith(".xml")
                and not any(x in n for x in ("Catalog.xml", "Hardware.xml", "Baggages.xml", "knx_master.xml"))
            ]
            if install_files:
                logger.warning("Keine 0.xml gefunden, versuche: %s", install_files)
            else:
                # Letzter Fallback: alles
                install_files = [n for n in names if n.endswith(".xml")]
                logger.warning("Keine Projekt-XMLs gefunden, versuche alle XMLs (%d Dateien)", len(install_files))

        all_records: list[GroupAddressRecord] = []
        pwd_bytes = password.encode("utf-8") if password else None

        for fname in install_files:
            try:
                xml_bytes = zf.read(fname, pwd=pwd_bytes) if pwd_bytes else zf.read(fname)
                records = _parse_installation_xml(xml_bytes)
                logger.info("knxproj: %d GA aus '%s' gelesen", len(records), fname)
                all_records.extend(records)
            except Exception as e:
                logger.warning("Fehler beim Lesen von '%s': %s", fname, e)
                continue

    # Duplikate entfernen (gleiche GA-Adresse)
    seen: set[str] = set()
    unique: list[GroupAddressRecord] = []
    for r in all_records:
        if r.address not in seen:
            seen.add(r.address)
            unique.append(r)

    logger.info("knxproj: %d eindeutige Gruppenadressen importiert", len(unique))
    return unique
