"""Unit tests for obs.knxproj.csv_parser.

Tests cover:
- _dpt_from_csv: all DPT string variants
- _decode_csv: UTF-8, UTF-8-BOM, cp1252
- parse_ga_csv: happy path, folder rows skipped, missing columns, empty file
"""

from __future__ import annotations

import pytest

from obs.knxproj.csv_parser import _decode_csv, _dpt_from_csv, parse_ga_csv


# ---------------------------------------------------------------------------
# _dpt_from_csv
# ---------------------------------------------------------------------------


class TestDptFromCsv:
    def test_none_returns_none(self):
        assert _dpt_from_csv(None) is None

    def test_empty_returns_none(self):
        assert _dpt_from_csv("") is None

    def test_whitespace_returns_none(self):
        assert _dpt_from_csv("   ") is None

    def test_dpst_with_sub(self):
        assert _dpt_from_csv("DPST-9-4") == "DPT9.004"

    def test_dpst_sub_zero_padded(self):
        assert _dpt_from_csv("DPST-1-1") == "DPT1.001"

    def test_dpst_large_sub(self):
        assert _dpt_from_csv("DPST-14-54") == "DPT14.054"

    def test_dpt_main_with_known_default(self):
        assert _dpt_from_csv("DPT-9") == "DPT9.001"

    def test_dpt_main_1(self):
        assert _dpt_from_csv("DPT-1") == "DPT1.001"

    def test_dpt_main_5(self):
        assert _dpt_from_csv("DPT-5") == "DPT5.001"

    def test_dpt_main_16(self):
        assert _dpt_from_csv("DPT-16") == "DPT16.000"

    def test_dpt_main_unknown_falls_back_to_001(self):
        assert _dpt_from_csv("DPT-99") == "DPT99.001"

    def test_unrecognised_string_returns_none(self):
        assert _dpt_from_csv("something-else") is None

    def test_whitespace_stripped(self):
        assert _dpt_from_csv("  DPST-9-1  ") == "DPT9.001"


# ---------------------------------------------------------------------------
# _decode_csv
# ---------------------------------------------------------------------------


class TestDecodeCsv:
    def test_utf8_plain(self):
        text = "Köche;1/2/3\n"
        assert _decode_csv(text.encode("utf-8")) == text

    def test_utf8_bom(self):
        text = "Köche;1/2/3\n"
        bom_bytes = text.encode("utf-8-sig")
        # utf-8-sig decoding strips BOM
        assert _decode_csv(bom_bytes) == text

    def test_cp1252_fallback(self):
        text = "Küche;1/2/3\n"
        cp_bytes = text.encode("cp1252")
        assert _decode_csv(cp_bytes) == text


# ---------------------------------------------------------------------------
# parse_ga_csv — helpers
# ---------------------------------------------------------------------------

_HEADER = '"Group name";"Address";"Central";"Unfiltered";"Description";"DatapointType";"Security"\n'


def _make_csv(*rows: str) -> bytes:
    """Build a UTF-8 CSV with the standard ETS header."""
    return (_HEADER + "\n".join(rows) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# parse_ga_csv — happy path
# ---------------------------------------------------------------------------


class TestParseGaCsv:
    def test_single_ga_row(self):
        csv_bytes = _make_csv('"Licht";"1/2/3";"False";"False";"Lichtschalter";"DPST-1-1";"Auto"')
        records = parse_ga_csv(csv_bytes)
        assert len(records) == 1
        r = records[0]
        assert r.address == "1/2/3"
        assert r.name == "Licht"
        assert r.description == "Lichtschalter"
        assert r.dpt == "DPT1.001"

    def test_multiple_rows(self):
        csv_bytes = _make_csv(
            '"Licht";"1/2/3";"False";"False";"";"DPST-1-1";"Auto"',
            '"Temperatur";"2/1/0";"False";"False";"";"DPST-9-1";"Auto"',
        )
        records = parse_ga_csv(csv_bytes)
        assert len(records) == 2
        assert records[0].address == "1/2/3"
        assert records[1].address == "2/1/0"
        assert records[1].dpt == "DPT9.001"

    def test_folder_rows_skipped(self):
        """Rows like "0/-/-" and "1/2/-" must be skipped."""
        csv_bytes = _make_csv(
            '"Hauptgruppe";"0/-/-";"False";"False";"";"";""',
            '"Mittelgruppe";"0/1/-";"False";"False";"";"";""',
            '"Echte GA";"0/1/0";"False";"False";"";"DPST-1-1";""',
        )
        records = parse_ga_csv(csv_bytes)
        assert len(records) == 1
        assert records[0].address == "0/1/0"

    def test_empty_dpt_yields_none(self):
        csv_bytes = _make_csv('"Licht";"1/2/3";"False";"False";"";"";"Auto"')
        records = parse_ga_csv(csv_bytes)
        assert records[0].dpt is None

    def test_description_empty(self):
        csv_bytes = _make_csv('"Licht";"1/2/3";"False";"False";"";"DPST-1-1";"Auto"')
        records = parse_ga_csv(csv_bytes)
        assert records[0].description == ""

    def test_utf8_bom_file(self):
        """ETS sometimes exports UTF-8 with BOM."""
        row = '"Küche";"3/4/5";"False";"False";"Küchenlampe";"DPST-1-1";"Auto"'
        csv_bytes = (_HEADER + row + "\n").encode("utf-8-sig")
        records = parse_ga_csv(csv_bytes)
        assert len(records) == 1
        assert records[0].name == "Küche"
        assert records[0].description == "Küchenlampe"

    def test_cp1252_encoded_file(self):
        """ETS on Windows may export cp1252."""
        row = '"Wohnzimmer";"5/6/7";"False";"False";"Wohnzimmerlampe";"DPST-1-1";"Auto"'
        csv_bytes = (_HEADER + row + "\n").encode("cp1252")
        records = parse_ga_csv(csv_bytes)
        assert records[0].name == "Wohnzimmer"

    def test_quoted_fields_stripped(self):
        """Fields with surrounding quotes should be handled correctly."""
        csv_bytes = _make_csv('"Licht EG";"1/0/1";"False";"False";"Flur";"DPT-1";"Auto"')
        records = parse_ga_csv(csv_bytes)
        assert records[0].name == "Licht EG"
        assert records[0].dpt == "DPT1.001"


# ---------------------------------------------------------------------------
# parse_ga_csv — error cases
# ---------------------------------------------------------------------------


class TestParseGaCsvErrors:
    def test_empty_file_raises(self):
        with pytest.raises(ValueError, match="leer|Header"):
            parse_ga_csv(b"")

    def test_missing_required_column_raises(self):
        bad_header = '"Group name";"Address";"Central"\n'
        csv_bytes = (bad_header + '"Licht";"1/2/3";"False"\n').encode("utf-8")
        with pytest.raises(ValueError, match="Unbekanntes CSV-Format"):
            parse_ga_csv(csv_bytes)

    def test_undecodable_bytes_raises(self):
        """Bytes that cannot be decoded by either UTF-8 or cp1252 raise ValueError.

        cp1252 leaves byte 0x81 undefined, so it triggers a UnicodeDecodeError
        which parse_ga_csv wraps in a ValueError.
        """
        row = b'"L\x81cht";"1/2/3";"False";"False";"";"DPST-1-1";"Auto"\n'
        csv_bytes = _HEADER.encode("utf-8") + row
        with pytest.raises(ValueError, match="dekodiert"):
            parse_ga_csv(csv_bytes)
