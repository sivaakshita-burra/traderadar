"""PICTURE clause and USAGE handling.

Turns strings like ``S9(7)V99`` into a :class:`PicSpec` and works out how many
bytes a field occupies for a given USAGE. Storage rules follow IBM Enterprise
COBOL defaults:

* DISPLAY numeric   -> one byte per digit (+1 if SIGN ... SEPARATE)
* COMP-3            -> digits // 2 + 1 bytes (two digits per byte, sign nibble)
* COMP / COMP-4 / BINARY / COMP-5 -> 2, 4 or 8 bytes depending on digit count
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from traderadar.errors import CopybookSyntaxError, UnsupportedFeatureError


class Usage(str, Enum):
    DISPLAY = "DISPLAY"
    BINARY = "COMP"  # COMP, COMP-4, BINARY: value limited by PIC digits
    NATIVE_BINARY = "COMP-5"  # full range of the storage size
    PACKED = "COMP-3"

    @classmethod
    def from_keyword(cls, word: str, line: int | None = None) -> "Usage":
        word = word.upper()
        mapping = {
            "DISPLAY": cls.DISPLAY,
            "COMP": cls.BINARY,
            "COMPUTATIONAL": cls.BINARY,
            "COMP-4": cls.BINARY,
            "COMPUTATIONAL-4": cls.BINARY,
            "BINARY": cls.BINARY,
            "COMP-5": cls.NATIVE_BINARY,
            "COMPUTATIONAL-5": cls.NATIVE_BINARY,
            "COMP-3": cls.PACKED,
            "COMPUTATIONAL-3": cls.PACKED,
            "PACKED-DECIMAL": cls.PACKED,
        }
        if word in mapping:
            return mapping[word]
        if word in {"COMP-1", "COMP-2", "COMPUTATIONAL-1", "COMPUTATIONAL-2"}:
            raise UnsupportedFeatureError(
                f"{word} (hexadecimal floating point) is not supported yet", line
            )
        if word in {"POINTER", "INDEX", "NATIONAL", "DISPLAY-1"}:
            raise UnsupportedFeatureError(f"USAGE {word} is not supported yet", line)
        raise CopybookSyntaxError(f"unknown USAGE '{word}'", line)


class Category(str, Enum):
    ALPHANUMERIC = "alphanumeric"
    ALPHABETIC = "alphabetic"
    NUMERIC = "numeric"
    NUMERIC_EDITED = "numeric_edited"


@dataclass(frozen=True)
class PicSpec:
    """A parsed PICTURE string."""

    source: str
    category: Category
    display_length: int  # characters when stored as DISPLAY (excludes S and V)
    digits: int = 0  # total numeric digits (numeric only)
    scale: int = 0  # digits to the right of the implied decimal point V
    signed: bool = False

    @property
    def is_numeric(self) -> bool:
        return self.category is Category.NUMERIC


_REPEAT = re.compile(r"(.)\((\d+)\)")
_EDIT_SYMBOLS = set("Z*,.B0/+-$")


def expand_picture(pic: str) -> str:
    """Expand repetition counts: ``9(3)V9(2)`` -> ``999V99``."""

    def repl(match: re.Match[str]) -> str:
        count = int(match.group(2))
        if count <= 0:
            raise CopybookSyntaxError(f"invalid repeat count in PIC '{pic}'")
        return match.group(1) * count

    expanded = _REPEAT.sub(repl, pic.upper())
    if "(" in expanded or ")" in expanded:
        raise CopybookSyntaxError(f"malformed PIC '{pic}'")
    return expanded


def parse_picture(pic: str, line: int | None = None) -> PicSpec:
    """Parse a PICTURE string into a :class:`PicSpec`."""
    if not pic:
        raise CopybookSyntaxError("empty PIC clause", line)
    try:
        expanded = expand_picture(pic)
    except CopybookSyntaxError as exc:
        raise CopybookSyntaxError(str(exc), line) from None

    if "P" in expanded:
        raise UnsupportedFeatureError(
            f"scaling position 'P' in PIC '{pic}' is not supported yet", line
        )

    chars = set(expanded)

    # Pure alphanumeric / alphabetic.
    if chars <= {"X", "A", "9"} and ("X" in chars or "A" in chars):
        category = Category.ALPHABETIC if chars == {"A"} else Category.ALPHANUMERIC
        return PicSpec(pic, category, display_length=len(expanded))

    # Pure numeric: optional leading S, digits, at most one V.
    if chars <= {"S", "9", "V"} and "9" in chars:
        if expanded.count("S") > 1 or ("S" in expanded and not expanded.startswith("S")):
            raise CopybookSyntaxError(f"'S' must appear once, first, in PIC '{pic}'", line)
        if expanded.count("V") > 1:
            raise CopybookSyntaxError(f"more than one 'V' in PIC '{pic}'", line)
        body = expanded.lstrip("S")
        int_part, _, frac_part = body.partition("V")
        digits = len(int_part) + len(frac_part)
        if digits > 31:
            raise CopybookSyntaxError(f"PIC '{pic}' has more than 31 digits", line)
        return PicSpec(
            pic,
            Category.NUMERIC,
            display_length=digits,
            digits=digits,
            scale=len(frac_part),
            signed=expanded.startswith("S"),
        )

    # Numeric-edited (report formats like ZZ,ZZ9.99-). Decoded as text.
    cleaned = expanded.replace("CR", "").replace("DB", "")
    if set(cleaned) <= (_EDIT_SYMBOLS | {"9", "V"}):
        length = len(expanded.replace("V", ""))
        return PicSpec(pic, Category.NUMERIC_EDITED, display_length=length)

    raise CopybookSyntaxError(f"unrecognised PIC '{pic}'", line)


def binary_size(digits: int, line: int | None = None) -> int:
    """Byte size of a COMP/COMP-5 field with ``digits`` PIC digits."""
    if 1 <= digits <= 4:
        return 2
    if 5 <= digits <= 9:
        return 4
    if 10 <= digits <= 18:
        return 8
    raise CopybookSyntaxError(f"binary field with {digits} digits is not allowed", line)


def storage_size(
    spec: PicSpec,
    usage: Usage,
    sign_separate: bool = False,
    line: int | None = None,
) -> int:
    """Number of bytes the elementary item occupies in the record."""
    if usage is Usage.DISPLAY:
        if spec.is_numeric:
            return spec.digits + (1 if sign_separate else 0)
        return spec.display_length

    if not spec.is_numeric:
        raise CopybookSyntaxError(
            f"USAGE {usage.value} requires a numeric PIC, got '{spec.source}'", line
        )
    if sign_separate:
        raise CopybookSyntaxError("SIGN SEPARATE is only valid for DISPLAY numerics", line)
    if usage is Usage.PACKED:
        return spec.digits // 2 + 1
    return binary_size(spec.digits, line)
