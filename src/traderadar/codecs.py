"""Byte-level codecs for mainframe field types.

Each ``decode_*`` takes the raw bytes of one field and returns a Python value,
raising :class:`DecodeError` on invalid data instead of guessing. Matching
``encode_*`` functions exist so tests and the sample generator can build
records byte-for-byte.

Sign nibbles (packed and zoned): C, A, E, F = positive; D, B = negative.
F means "unsigned"; unsigned PICs must not carry a negative sign.
"""

from __future__ import annotations

import codecs as _codecs
from decimal import Decimal

from traderadar.errors import DecodeError

POSITIVE_SIGNS = {0xC, 0xA, 0xE, 0xF}
NEGATIVE_SIGNS = {0xD, 0xB}

# After an EBCDIC->ASCII text conversion, zoned "overpunched" sign bytes
# C0-C9 / D0-D9 turn into these characters.
_ASCII_OVERPUNCH = {
    **{c: (d, False) for d, c in enumerate("{ABCDEFGHI")},
    **{c: (d, True) for d, c in enumerate("}JKLMNOPQR")},
}
_ASCII_OVERPUNCH_REV = {v: k for k, v in _ASCII_OVERPUNCH.items()}


def is_ebcdic(codepage: str) -> bool:
    """True for EBCDIC code pages (cp037, cp500, cp1140, cp273, ...)."""
    name = _codecs.lookup(codepage).name
    return name in {"cp037", "cp500", "cp1140", "cp273", "cp1026", "cp875"}


def check_codepage(codepage: str) -> str:
    try:
        return _codecs.lookup(codepage).name
    except LookupError:
        raise ValueError(
            f"unknown code page '{codepage}'. Try cp037 (US/Canada EBCDIC), "
            "cp500 (international EBCDIC), cp1140, or ascii for converted files."
        ) from None


def _scaled(value: int, scale: int) -> Decimal:
    return Decimal(value).scaleb(-scale) if scale else Decimal(value)


def _sign_from_nibble(nibble: int, signed: bool, raw: bytes) -> bool:
    """Return True if negative. Validates the sign nibble."""
    if nibble in NEGATIVE_SIGNS:
        if not signed:
            raise DecodeError("negative sign in an unsigned field", raw=raw)
        return True
    if nibble in POSITIVE_SIGNS:
        return False
    raise DecodeError(f"invalid sign nibble {nibble:X}", raw=raw)


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------

def decode_text(raw: bytes, codepage: str = "cp037", trim: bool = True) -> str:
    text = raw.decode(codepage)
    return text.rstrip(" ") if trim else text


def encode_text(value: str, length: int, codepage: str = "cp037") -> bytes:
    data = value.encode(codepage)
    if len(data) > length:
        raise ValueError(f"'{value}' does not fit in {length} bytes")
    return data + " ".encode(codepage) * (length - len(data))


# --------------------------------------------------------------------------
# Packed decimal (COMP-3)
# --------------------------------------------------------------------------

def decode_packed(raw: bytes, digits: int, scale: int = 0, signed: bool = True) -> Decimal:
    """Decode IBM packed decimal. ``digits`` is the PIC digit count."""
    if not raw:
        raise DecodeError("empty packed field", raw=raw)
    expected = digits // 2 + 1
    if len(raw) != expected:
        raise DecodeError(f"packed field should be {expected} bytes, got {len(raw)}", raw=raw)
    if all(b == 0x40 for b in raw):
        raise DecodeError("packed field contains EBCDIC spaces (uninitialised?)", raw=raw)
    if all(b == 0x00 for b in raw):
        raise DecodeError("packed field contains low-values (X'00')", raw=raw)

    nibbles: list[int] = []
    for byte in raw:
        nibbles.append(byte >> 4)
        nibbles.append(byte & 0x0F)
    sign = nibbles.pop()
    negative = _sign_from_nibble(sign, signed, raw)

    if any(n > 9 for n in nibbles):
        raise DecodeError("invalid digit nibble in packed field", raw=raw)
    # Even digit counts leave one spare high-order nibble that must be zero.
    spare = len(nibbles) - digits
    if spare and any(nibbles[:spare]):
        raise DecodeError("packed value has more digits than its PIC allows", raw=raw)

    value = 0
    for n in nibbles:
        value = value * 10 + n
    return _scaled(-value if negative else value, scale)


def encode_packed(value: Decimal | int, digits: int, scale: int = 0, signed: bool = True) -> bytes:
    unscaled = int(Decimal(value).scaleb(scale).to_integral_exact())
    if unscaled < 0 and not signed:
        raise ValueError("negative value for unsigned packed field")
    magnitude = str(abs(unscaled))
    if len(magnitude) > digits:
        raise ValueError(f"{value} does not fit in {digits} digits")
    size = digits // 2 + 1
    digit_str = magnitude.rjust(size * 2 - 1, "0")
    sign = 0xD if unscaled < 0 else (0xC if signed else 0xF)
    nibbles = [int(c) for c in digit_str] + [sign]
    return bytes((nibbles[i] << 4) | nibbles[i + 1] for i in range(0, len(nibbles), 2))


# --------------------------------------------------------------------------
# Zoned decimal (numeric DISPLAY)
# --------------------------------------------------------------------------

def decode_zoned(
    raw: bytes,
    digits: int,
    scale: int = 0,
    signed: bool = False,
    sign_leading: bool = False,
    sign_separate: bool = False,
    codepage: str = "cp037",
) -> Decimal:
    ebcdic = is_ebcdic(codepage)
    expected = digits + (1 if sign_separate else 0)
    if len(raw) != expected:
        raise DecodeError(f"zoned field should be {expected} bytes, got {len(raw)}", raw=raw)

    space = 0x40 if ebcdic else 0x20
    if all(b == space for b in raw):
        raise DecodeError("numeric field contains only spaces", raw=raw)

    negative = False
    body = bytearray(raw)

    if sign_separate:
        sign_byte = body.pop(0) if sign_leading else body.pop()
        plus, minus = (0x4E, 0x60) if ebcdic else (0x2B, 0x2D)
        if sign_byte == minus:
            negative = True
        elif sign_byte != plus:
            raise DecodeError(f"invalid separate sign byte {sign_byte:02X}", raw=raw)

    digit_values: list[int] = []
    sign_index = 0 if sign_leading else len(body) - 1
    for i, byte in enumerate(body):
        carries_sign = signed and not sign_separate and i == sign_index
        if ebcdic:
            zone, digit = byte >> 4, byte & 0x0F
            if digit > 9:
                raise DecodeError(
                    f"non-numeric byte {byte:02X} at position {i + 1}", raw=raw
                )
            if carries_sign:
                negative = _sign_from_nibble(zone, True, raw)
            elif zone != 0xF:
                if i == sign_index and zone in NEGATIVE_SIGNS:
                    raise DecodeError("negative sign in an unsigned field", raw=raw)
                if not (i == sign_index and zone in POSITIVE_SIGNS):
                    raise DecodeError(
                        f"non-numeric byte {byte:02X} at position {i + 1}", raw=raw
                    )
        else:
            ch = chr(byte)
            if ch.isdigit():
                digit = int(ch)
            elif carries_sign and ch in _ASCII_OVERPUNCH:
                digit, negative = _ASCII_OVERPUNCH[ch]
            else:
                raise DecodeError(f"non-numeric character {ch!r} at position {i + 1}", raw=raw)
        digit_values.append(digit)

    value = 0
    for d in digit_values:
        value = value * 10 + d
    return _scaled(-value if negative else value, scale)


def encode_zoned(
    value: Decimal | int,
    digits: int,
    scale: int = 0,
    signed: bool = False,
    sign_leading: bool = False,
    sign_separate: bool = False,
    codepage: str = "cp037",
) -> bytes:
    ebcdic = is_ebcdic(codepage)
    unscaled = int(Decimal(value).scaleb(scale).to_integral_exact())
    if unscaled < 0 and not signed:
        raise ValueError("negative value for unsigned zoned field")
    magnitude = str(abs(unscaled)).rjust(digits, "0")
    if len(magnitude) > digits:
        raise ValueError(f"{value} does not fit in {digits} digits")
    negative = unscaled < 0

    if ebcdic:
        out = bytearray(0xF0 | int(c) for c in magnitude)
    else:
        out = bytearray(magnitude.encode("ascii"))

    if signed and not sign_separate:
        idx = 0 if sign_leading else len(out) - 1
        digit = int(magnitude[idx])
        if ebcdic:
            out[idx] = ((0xD if negative else 0xC) << 4) | digit
        else:
            out[idx] = ord(_ASCII_OVERPUNCH_REV[(digit, negative)])
    elif sign_separate:
        plus, minus = (0x4E, 0x60) if ebcdic else (0x2B, 0x2D)
        sign_byte = minus if negative else plus
        if sign_leading:
            out.insert(0, sign_byte)
        else:
            out.append(sign_byte)
    return bytes(out)


# --------------------------------------------------------------------------
# Binary (COMP, COMP-4, BINARY, COMP-5)
# --------------------------------------------------------------------------

def decode_binary(
    raw: bytes,
    digits: int,
    scale: int = 0,
    signed: bool = True,
    truncate_to_pic: bool = True,
) -> Decimal:
    """Big-endian binary. With ``truncate_to_pic`` (COMP), values outside the
    PIC's digit range are rejected; COMP-5 uses the full storage range."""
    if len(raw) not in (2, 4, 8):
        raise DecodeError(f"binary field must be 2, 4 or 8 bytes, got {len(raw)}", raw=raw)
    value = int.from_bytes(raw, "big", signed=signed)
    if truncate_to_pic and abs(value) > 10**digits - 1:
        raise DecodeError(f"binary value {value} exceeds PIC limit of {digits} digits", raw=raw)
    return _scaled(value, scale)


def encode_binary(value: Decimal | int, size: int, scale: int = 0, signed: bool = True) -> bytes:
    unscaled = int(Decimal(value).scaleb(scale).to_integral_exact())
    return unscaled.to_bytes(size, "big", signed=signed)
