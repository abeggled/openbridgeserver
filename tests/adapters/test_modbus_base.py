"""
Unit tests for obs/adapters/modbus_base.py — pure functions only.
No hardware, no network, no mocks needed.
"""
from __future__ import annotations

import struct
import pytest
from obs.adapters.modbus_base import decode_registers, encode_value, register_count


# ---------------------------------------------------------------------------
# register_count
# ---------------------------------------------------------------------------

class TestRegisterCount:
    def test_uint16_is_1(self):
        assert register_count("uint16") == 1

    def test_int16_is_1(self):
        assert register_count("int16") == 1

    def test_uint32_is_2(self):
        assert register_count("uint32") == 2

    def test_int32_is_2(self):
        assert register_count("int32") == 2

    def test_float32_is_2(self):
        assert register_count("float32") == 2

    def test_uint64_is_4(self):
        assert register_count("uint64") == 4

    def test_int64_is_4(self):
        assert register_count("int64") == 4

    def test_unknown_format_returns_1(self):
        assert register_count("nonexistent") == 1


# ---------------------------------------------------------------------------
# decode_registers — uint16 / int16
# ---------------------------------------------------------------------------

class TestDecodeUint16:
    def test_simple_value(self):
        assert decode_registers([42], "uint16") == 42

    def test_zero(self):
        assert decode_registers([0], "uint16") == 0

    def test_max_value(self):
        assert decode_registers([65535], "uint16") == 65535

    def test_scale_factor(self):
        # 1000 × 0.1 = 100  (returned as int)
        result = decode_registers([1000], "uint16", scale_factor=0.1)
        assert result == int(1000 * 0.1)


class TestDecodeInt16:
    def test_positive(self):
        assert decode_registers([100], "int16") == 100

    def test_negative(self):
        # 0xFFFF → -1 as signed int16 (big-endian)
        raw = struct.unpack(">H", struct.pack(">h", -1))[0]
        assert decode_registers([raw], "int16") == -1

    def test_negative_large(self):
        raw = struct.unpack(">H", struct.pack(">h", -32768))[0]
        assert decode_registers([raw], "int16") == -32768

    def test_scale_factor_float(self):
        result = decode_registers([100], "int16", scale_factor=0.01)
        assert abs(result - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# decode_registers — uint32 / int32 / float32
# ---------------------------------------------------------------------------

class TestDecodeUint32:
    def test_simple(self):
        # 0x00010000 → registers [1, 0] big word order
        regs = list(struct.unpack(">HH", struct.pack(">I", 65536)))
        assert decode_registers(regs, "uint32") == 65536

    def test_max(self):
        regs = list(struct.unpack(">HH", struct.pack(">I", 0xFFFFFFFF)))
        assert decode_registers(regs, "uint32") == 0xFFFFFFFF

    def test_word_order_little(self):
        # value 1 packed big → [0x0000, 0x0001]; little word order reverses → [0x0001, 0x0000]
        regs_big = list(struct.unpack(">HH", struct.pack(">I", 1)))
        regs_little = list(reversed(regs_big))
        result = decode_registers(regs_little, "uint32", word_order="little")
        assert result == 1


class TestDecodeInt32:
    def test_negative(self):
        regs = list(struct.unpack(">HH", struct.pack(">i", -1)))
        assert decode_registers(regs, "int32") == -1

    def test_positive(self):
        regs = list(struct.unpack(">HH", struct.pack(">i", 100_000)))
        assert decode_registers(regs, "int32") == 100_000


class TestDecodeFloat32:
    def test_pi(self):
        import math
        regs = list(struct.unpack(">HH", struct.pack(">f", math.pi)))
        result = decode_registers(regs, "float32")
        assert abs(result - math.pi) < 1e-6

    def test_negative_float(self):
        regs = list(struct.unpack(">HH", struct.pack(">f", -1.5)))
        result = decode_registers(regs, "float32")
        assert abs(result - (-1.5)) < 1e-6

    def test_with_scale_factor(self):
        regs = list(struct.unpack(">HH", struct.pack(">f", 10.0)))
        result = decode_registers(regs, "float32", scale_factor=0.1)
        assert abs(result - 1.0) < 1e-5


# ---------------------------------------------------------------------------
# decode_registers — uint64 / int64
# ---------------------------------------------------------------------------

class TestDecodeUint64:
    def test_large_value(self):
        val = 2**40
        regs = list(struct.unpack(">HHHH", struct.pack(">Q", val)))
        assert decode_registers(regs, "uint64") == val

    def test_zero(self):
        assert decode_registers([0, 0, 0, 0], "uint64") == 0


class TestDecodeInt64:
    def test_negative(self):
        regs = list(struct.unpack(">HHHH", struct.pack(">q", -1)))
        assert decode_registers(regs, "int64") == -1


# ---------------------------------------------------------------------------
# encode_value
# ---------------------------------------------------------------------------

class TestEncodeUint16:
    def test_simple(self):
        assert encode_value(42, "uint16") == [42]

    def test_overflow_masked(self):
        # 65537 & 0xFFFF = 1
        assert encode_value(65537, "uint16") == [1]

    def test_with_scale_factor(self):
        # 10.0 / 0.1 = 100 → [100]
        assert encode_value(10.0, "uint16", scale_factor=0.1) == [100]


class TestEncodeInt16:
    def test_negative(self):
        result = encode_value(-1, "int16")
        assert len(result) == 1
        # Decoding back should give -1
        assert decode_registers(result, "int16") == -1


class TestEncodeUint32:
    def test_round_trip(self):
        val = 123456
        regs = encode_value(val, "uint32")
        assert decode_registers(regs, "uint32") == val

    def test_word_order_little_round_trip(self):
        val = 99999
        regs = encode_value(val, "uint32", word_order="little")
        assert decode_registers(regs, "uint32", word_order="little") == val


class TestEncodeFloat32:
    def test_round_trip(self):
        val = 3.14
        regs = encode_value(val, "float32")
        result = decode_registers(regs, "float32")
        assert abs(result - val) < 1e-5


class TestEncodeUint64:
    def test_round_trip(self):
        val = 10**12
        regs = encode_value(val, "uint64")
        assert decode_registers(regs, "uint64") == val


# ---------------------------------------------------------------------------
# Edge cases — too few registers
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_uint32_too_few_registers_returns_0(self):
        assert decode_registers([1], "uint32") == 0

    def test_uint64_too_few_registers_returns_0(self):
        assert decode_registers([1, 2], "uint64") == 0

    def test_unknown_format_falls_back_to_first_register(self):
        assert decode_registers([99], "mystery_format") == 99
