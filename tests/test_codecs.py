"""Codec tests use hand-written bytes, not the encoder, so they check the
decoders against the IBM formats independently."""

from decimal import Decimal

import pytest

from traderadar import codecs
from traderadar.errors import DecodeError


# ---------------------------------------------------------------- packed

@pytest.mark.parametrize(
    "raw, digits, scale, signed, expected",
    [
        (bytes.fromhex("12345C"), 5, 0, True, Decimal("12345")),
        (bytes.fromhex("12345D"), 5, 0, True, Decimal("-12345")),
        (bytes.fromhex("12345F"), 5, 0, False, Decimal("12345")),
        (bytes.fromhex("0012345C"), 7, 2, True, Decimal("123.45")),   # S9(5)V99
        (bytes.fromhex("0000001D"), 7, 2, True, Decimal("-0.01")),
        (bytes.fromhex("01234C"), 4, 0, True, Decimal("1234")),       # even digits: spare nibble
        (bytes.fromhex("000000000C"), 9, 2, True, Decimal("0.00")),
        (bytes.fromhex("0000000D"), 7, 0, True, Decimal("0")),        # negative zero -> 0
        (bytes.fromhex("12345B"), 5, 0, True, Decimal("-12345")),     # alternate negative sign
        (bytes.fromhex("12345A"), 5, 0, True, Decimal("12345")),      # alternate positive sign
    ],
)
def test_decode_packed(raw, digits, scale, signed, expected):
    assert codecs.decode_packed(raw, digits, scale, signed) == expected


def test_packed_scale_is_preserved():
    assert str(codecs.decode_packed(bytes.fromhex("0012345C"), 7, 2)) == "123.45"


@pytest.mark.parametrize(
    "raw, digits, signed, message",
    [
        (bytes.fromhex("123437"), 5, True, "invalid sign nibble 7"),
        (bytes.fromhex("1A345C"), 5, True, "invalid digit nibble"),
        (bytes.fromhex("12345D"), 5, False, "negative sign in an unsigned field"),
        (bytes.fromhex("11234C"), 4, True, "more digits than its PIC"),
        (b"\x40\x40\x40", 5, True, "EBCDIC spaces"),
        (b"\x00\x00\x00", 5, True, "low-values"),
        (bytes.fromhex("123C"), 5, True, "should be 3 bytes"),
    ],
)
def test_decode_packed_errors(raw, digits, signed, message):
    with pytest.raises(DecodeError, match=message):
        codecs.decode_packed(raw, digits, 0, signed)


@pytest.mark.parametrize(
    "value, digits, scale, signed",
    [
        (Decimal("123.45"), 7, 2, True),
        (Decimal("-99999.99"), 7, 2, True),
        (Decimal("0"), 1, 0, True),
        (Decimal("42"), 4, 0, False),
        (Decimal("123.456789"), 9, 6, False),
    ],
)
def test_packed_round_trip(value, digits, scale, signed):
    raw = codecs.encode_packed(value, digits, scale, signed)
    assert len(raw) == digits // 2 + 1
    assert codecs.decode_packed(raw, digits, scale, signed) == value


def test_encode_packed_overflow():
    with pytest.raises(ValueError):
        codecs.encode_packed(Decimal("100000"), 5)


# ----------------------------------------------------------------- zoned

def test_zoned_unsigned_ebcdic():
    assert codecs.decode_zoned(bytes.fromhex("F0F1F2F3"), 4) == Decimal("123")


def test_zoned_signed_trailing_overpunch():
    # +123.4 and -123.4 as PIC S9(3)V9
    assert codecs.decode_zoned(bytes.fromhex("F1F2F3C4"), 4, 1, True) == Decimal("123.4")
    assert codecs.decode_zoned(bytes.fromhex("F1F2F3D4"), 4, 1, True) == Decimal("-123.4")


def test_zoned_signed_leading_overpunch():
    assert codecs.decode_zoned(bytes.fromhex("D1F2F3"), 3, 0, True, sign_leading=True) == Decimal("-123")


def test_zoned_sign_separate_leading_and_trailing():
    # '-' is 0x60 and '+' is 0x4E in EBCDIC
    assert codecs.decode_zoned(bytes.fromhex("60F1F2F3"), 3, 0, True, True, True) == Decimal("-123")
    assert codecs.decode_zoned(bytes.fromhex("F1F2F34E"), 3, 0, True, False, True) == Decimal("123")


def test_zoned_unsigned_accepts_c_zone_on_last_byte():
    assert codecs.decode_zoned(bytes.fromhex("F1C2"), 2) == Decimal("12")


@pytest.mark.parametrize(
    "raw, signed, message",
    [
        (bytes.fromhex("40404040"), False, "only spaces"),
        (bytes.fromhex("F1C1F2F3"), False, "non-numeric byte C1 at position 2"),
        (bytes.fromhex("F1F2F3D4"), False, "negative sign in an unsigned field"),
        (bytes.fromhex("F1F2F3F4")[:3] + b"\x74", True, "invalid sign nibble 7"),
    ],
)
def test_zoned_errors(raw, signed, message):
    with pytest.raises(DecodeError, match=message):
        codecs.decode_zoned(raw, 4, 0, signed)


def test_zoned_ascii_overpunch():
    # After EBCDIC->ASCII conversion: '}' = -0, 'J' = -1, '{' = +0, 'A' = +1
    assert codecs.decode_zoned(b"123J", 4, 0, True, codepage="ascii") == Decimal("-1231")
    assert codecs.decode_zoned(b"012{", 4, 1, True, codepage="ascii") == Decimal("12.0")


@pytest.mark.parametrize("leading", [False, True])
@pytest.mark.parametrize("separate", [False, True])
@pytest.mark.parametrize("codepage", ["cp037", "ascii"])
@pytest.mark.parametrize("value", [Decimal("0"), Decimal("12.34"), Decimal("-99.99"), Decimal("-0.01")])
def test_zoned_round_trip(value, leading, separate, codepage):
    raw = codecs.encode_zoned(value, 4, 2, True, leading, separate, codepage)
    assert codecs.decode_zoned(raw, 4, 2, True, leading, separate, codepage) == value


# ---------------------------------------------------------------- binary

def test_binary_signed_and_unsigned():
    assert codecs.decode_binary(b"\xff\xfe", 4, signed=True) == Decimal("-2")
    assert codecs.decode_binary(b"\x00\x7b", 4, signed=False) == Decimal("123")
    assert codecs.decode_binary((12345).to_bytes(4, "big"), 9, 2) == Decimal("123.45")


def test_binary_pic_truncation_rule():
    # PIC S9(4) COMP holds at most 9999 even though 2 bytes can hold 32767.
    raw = (10000).to_bytes(2, "big", signed=True)
    with pytest.raises(DecodeError, match="exceeds PIC limit"):
        codecs.decode_binary(raw, 4)
    # COMP-5 allows the full storage range.
    assert codecs.decode_binary(raw, 4, truncate_to_pic=False) == Decimal("10000")


def test_binary_bad_length():
    with pytest.raises(DecodeError):
        codecs.decode_binary(b"\x00\x00\x01", 5)


# ------------------------------------------------------------------ text

def test_text_ebcdic():
    assert codecs.decode_text(bytes.fromhex("C8C5D3D3D64040")) == "HELLO"
    assert codecs.decode_text(bytes.fromhex("C8C5D3D3D64040"), trim=False) == "HELLO  "
    assert codecs.encode_text("HI", 4) == bytes.fromhex("C8C94040")


def test_unknown_codepage():
    with pytest.raises(ValueError, match="unknown code page"):
        codecs.check_codepage("cp9999")
