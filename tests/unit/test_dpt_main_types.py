"""Seam S5 (#1260): DPT main types without a subtype in the registry.

A ``.knxproj`` may carry only the main type of a group address ("DPT-14"). OBS used
to guess a subtype for it (14.054 with a made-up unit, 5.001 with percent scaling).
Instead the registry offers main-type entries whose codec does not depend on the
subtype: the plain encoding of the main type, without unit and without scaling.
"""

from __future__ import annotations

import datetime
import math

import pytest

from obs.adapters.knx.dpt_registry import DPTRegistry

# main type → (raw bytes, decoded value, data_type)
DECODING = {
    "DPT1": (b"\x01", True, "BOOLEAN"),
    "DPT2": (b"\x03", 3, "INTEGER"),
    "DPT3": (b"\x0b", 11, "INTEGER"),
    "DPT4": (b"A", "A", "STRING"),
    "DPT5": (b"\xff", 255, "INTEGER"),
    "DPT6": (b"\x80", -128, "INTEGER"),
    "DPT7": (b"\x12\x34", 0x1234, "INTEGER"),
    "DPT8": (b"\xff\xfe", -2, "INTEGER"),
    "DPT9": (b"\x0c\x1a", 21.0, "FLOAT"),
    "DPT10": (b"\x0c\x1e\x05", datetime.time(12, 30, 5), "TIME"),
    "DPT11": (b"\x08\x0a\x1a", datetime.date(2026, 10, 8), "DATE"),
    "DPT12": (b"\xff\xff\xff\xff", 0xFFFFFFFF, "INTEGER"),
    "DPT13": (b"\xff\xff\xff\x9c", -100, "INTEGER"),
    "DPT14": (b"\x44\x9a\x50\x00", 1234.5, "FLOAT"),
    "DPT16": (b"OBS" + bytes(11), "OBS", "STRING"),
    "DPT17": (b"\x05", 5, "INTEGER"),
    "DPT18": (b"\x85", -6, "INTEGER"),
    "DPT19": (b"\x7e\x0a\x08\x8c\x1e\x05\x00\x00", "2026-10-08T12:30:05", "STRING"),
    "DPT20": (b"\x07", 7, "INTEGER"),
    "DPT29": (b"\xff" * 7 + b"\x9c", -100, "INTEGER"),
    "DPT219": (b"\x01\x02", 0x0102, "INTEGER"),
    "DPT240": (b"\xff\x00\x03", {"height_pct": 100.0, "slats_pct": 0.0, "valid_height": True, "valid_slats": True}, "STRING"),
}


@pytest.mark.parametrize("main", sorted(DECODING))
def test_main_type_is_registered_without_unit(main):
    dpt = DPTRegistry.get(main)
    assert dpt.dpt_id == main
    assert dpt.unit == ""
    assert dpt.data_type == DECODING[main][2]


@pytest.mark.parametrize("main", sorted(DECODING))
def test_main_type_decodes_and_encodes_the_plain_main_type(main):
    raw, value, _ = DECODING[main]
    dpt = DPTRegistry.get(main)
    decoded = dpt.decoder(raw)
    if isinstance(value, float):
        assert math.isclose(decoded, value, abs_tol=0.01)
    else:
        assert decoded == value
    assert dpt.encoder(decoded) == raw
    assert len(raw) == dpt.size_bytes


def test_dpt5_main_type_is_unscaled_not_percent():
    dpt5 = DPTRegistry.get("DPT5")
    assert dpt5.decoder(b"\x80") == 128
    assert dpt5.encoder(128) == b"\x80"
    # the guessed subtype scaled the same byte to percent
    assert DPTRegistry.get("DPT5.001").decoder(b"\x80") == 50.2


@pytest.mark.parametrize(("main", "subtype"), [("DPT9", "DPT9.001"), ("DPT14", "DPT14.054"), ("DPT20", "DPT20.102"), ("DPT13", "DPT13.001")])
def test_main_type_reads_the_same_number_as_its_subtypes(main, subtype):
    raw = DECODING[main][0]
    assert DPTRegistry.get(main).decoder(raw) == DPTRegistry.get(subtype).decoder(raw)


def test_dpt4_main_type_reads_both_character_sets_and_writes_ascii():
    """4.001 is ASCII, 4.002 ISO 8859-1: read Latin-1 (a superset), write ASCII (valid for both)."""
    dpt4 = DPTRegistry.get("DPT4")
    assert dpt4.decoder(b"A") == "A"
    assert dpt4.decoder(b"\xe4") == "ä"
    assert dpt4.encoder("A") == b"A"
    assert dpt4.encoder("ä") == b"?"


def _families() -> dict[str, list]:
    families: dict[str, list] = {}
    for dpt_id, dpt in DPTRegistry.all().items():
        if "." in dpt_id:
            families.setdefault(dpt_id.split(".")[0], []).append(dpt)
    return families


# Main types whose subtypes encode differently in this registry, and the subtype codec the
# main type takes instead: (encoder of, decoder of). DPT5: the raw byte, no scaling.
# DPT4: write ASCII (valid for 4.001 and 4.002), read ISO 8859-1 (a superset of ASCII).
MIXED_FAMILIES = {"DPT5": ("DPT5.010", "DPT5.010"), "DPT4": ("DPT4.001", "DPT4.002")}


def _main_types() -> list[str]:
    return sorted(dpt_id for dpt_id in DPTRegistry.all() if "." not in dpt_id)


@pytest.mark.parametrize("main", _main_types())
def test_every_main_type_entry_takes_the_codec_its_subtypes_share(main):
    """Derived from the registry: a main type without subtypes here has no codec to share."""
    dpt = DPTRegistry.get(main)
    subtypes = _families().get(main)
    assert subtypes, f"{main} has no subtype in the registry, so no codec can be shared"
    assert {(s.data_type, s.size_bytes) for s in subtypes} >= {(dpt.data_type, dpt.size_bytes)}
    if main in MIXED_FAMILIES:
        enc_of, dec_of = MIXED_FAMILIES[main]
        assert (dpt.encoder, dpt.decoder) == (DPTRegistry.get(enc_of).encoder, DPTRegistry.get(dec_of).decoder)
        assert len({(s.encoder, s.decoder) for s in subtypes}) > 1, f"{main} is no longer mixed; drop it from MIXED_FAMILIES"
    else:
        assert {(s.encoder, s.decoder) for s in subtypes} == {(dpt.encoder, dpt.decoder)}
    assert dpt.unit == ""


def test_every_subtype_family_has_a_main_type_entry():
    assert sorted(_families()) == _main_types()
