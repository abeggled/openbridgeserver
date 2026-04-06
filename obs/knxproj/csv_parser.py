"""
ETS Gruppen-Adressen CSV-Import

ETS exportiert je nach Konfiguration in UTF-8 (mit oder ohne BOM)
oder Windows-1252 (ANSI). Diese Funktion erkennt die Kodierung automatisch.

CSV-Format (Semikolon-getrennt):
  "Group name";"Address";"Central";"Unfiltered";"Description";"DatapointType";"Security"
"""
from __future__ import annotations

import csv
import io
import logging
import re

from obs.knxproj.parser import GroupAddressRecord

logger = logging.getLogger(__name__)

# Adressmuster für echte Gruppenadressen (nur numerische Segmente: 1/2/3)
_GA_PATTERN = re.compile(r"^\d+/\d+/\d+$")


def _dpt_from_csv(dpt_str: str | None) -> str | None:
    """ETS CSV DPT-String → open bridge server DPT-ID.

    ETS liefert: "DPST-9-4" oder "DPT-9" oder leer.
    open bridge server erwartet: "DPT9.004"
    """
    if not dpt_str:
        return None
    dpt_str = dpt_str.strip()

    # DPST-<main>-<sub>
    m = re.match(r"DPST-(\d+)-(\d+)$", dpt_str)
    if m:
        main, sub = int(m.group(1)), int(m.group(2))
        return f"DPT{main}.{str(sub).zfill(3)}"

    # DPT-<main>
    m = re.match(r"DPT-(\d+)$", dpt_str)
    if m:
        main = int(m.group(1))
        defaults = {
            1: "DPT1.001",  2: "DPT2.001",  3: "DPT3.007",
            5: "DPT5.001",  6: "DPT6.010",  7: "DPT7.001",
            8: "DPT8.001",  9: "DPT9.001", 10: "DPT10.001",
            11: "DPT11.001", 12: "DPT12.001", 13: "DPT13.001",
            14: "DPT14.054", 16: "DPT16.000", 18: "DPT18.001",
            19: "DPT19.001", 20: "DPT20.102",
        }
        return defaults.get(main, f"DPT{main}.001")

    return None


def _decode_csv(content: bytes) -> str:
    """CSV-Bytes dekodieren: UTF-8 (mit/ohne BOM) → cp1252 Fallback.

    ETS unter Windows exportiert oft Windows-1252 (ANSI). Neuere Versionen
    können auch UTF-8 mit BOM ausgeben. utf-8-sig erkennt beides korrekt;
    bei UnicodeDecodeError wird cp1252 als Fallback verwendet.
    """
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        logger.debug("CSV nicht UTF-8, versuche cp1252 (Windows-ANSI)")
        return content.decode("cp1252")


def parse_ga_csv(content: bytes) -> list[GroupAddressRecord]:
    """ETS Gruppen-Adressen CSV parsen.

    Args:
        content: Rohe Bytes der CSV-Datei

    Returns:
        Liste von GroupAddressRecord (nur echte GAs, keine Gruppen-Ordner)

    Raises:
        ValueError: wenn das CSV-Format nicht erkannt wird
    """
    try:
        text = _decode_csv(content)
    except Exception as e:
        raise ValueError(f"CSV-Datei konnte nicht dekodiert werden: {e}") from e

    reader = csv.DictReader(io.StringIO(text), delimiter=";")

    # Spaltennamen normalisieren (Anführungszeichen, Leerzeichen)
    expected = {"Group name", "Address", "Description", "DatapointType"}

    if reader.fieldnames is None:
        raise ValueError("CSV-Datei ist leer oder hat keinen Header")

    normalized = {f.strip('" ') for f in reader.fieldnames}
    missing = expected - normalized
    if missing:
        raise ValueError(
            f"Unbekanntes CSV-Format. Fehlende Spalten: {missing}. "
            "Bitte ETS GA-Export als CSV (Semikolon-getrennt) verwenden."
        )

    # Feldnamen-Mapping (falls mit Anführungszeichen)
    field_map: dict[str, str] = {}
    for raw in reader.fieldnames:
        clean = raw.strip('" ')
        field_map[clean] = raw

    def get(row: dict, key: str) -> str:
        raw_key = field_map.get(key, key)
        return (row.get(raw_key) or row.get(key) or "").strip().strip('"')

    records: list[GroupAddressRecord] = []
    skipped = 0

    for row in reader:
        address = get(row, "Address")

        # Gruppen-Ordner-Zeilen überspringen (z.B. "0/-/-", "1/2/-")
        if not _GA_PATTERN.match(address):
            skipped += 1
            continue

        name        = get(row, "Group name")
        description = get(row, "Description")
        dpt_raw     = get(row, "DatapointType") or None

        records.append(GroupAddressRecord(
            address=     address,
            name=        name,
            description= description,
            dpt=         _dpt_from_csv(dpt_raw),
        ))

    logger.info(
        "CSV-Parser: %d Gruppenadressen gelesen, %d Ordner-Zeilen übersprungen",
        len(records), skipped,
    )
    return records
