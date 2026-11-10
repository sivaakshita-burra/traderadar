"""Decode fixed-length records using a :class:`Layout`.

Error policy:

* A bad field in the main view rejects the record (``DecodedRecord.ok`` is
  False) but decoding continues, so every problem is reported at once.
* REDEFINES views are alternative interpretations of the same bytes, so a
  failure there is recorded as a warning, not a rejection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterator

from traderadar import codecs
from traderadar.copybook.layout import Layout, LayoutNode
from traderadar.copybook.parser import Condition
from traderadar.copybook.pic import Category, Usage
from traderadar.errors import DecodeError


@dataclass
class FieldIssue:
    path: str
    offset: int  # 0-based offset within the record
    reason: str
    raw_hex: str

    def as_dict(self) -> dict[str, Any]:
        return {"field": self.path, "offset": self.offset, "reason": self.reason, "raw": self.raw_hex}


@dataclass
class DecodedRecord:
    data: dict[str, Any]
    errors: list[FieldIssue] = field(default_factory=list)
    warnings: list[FieldIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _occ_suffix(index: int | None) -> str:
    return f"({index})" if index is not None else ""


class RecordDecoder:
    def __init__(
        self,
        layout: Layout,
        codepage: str = "cp037",
        trim_text: bool = True,
        include_conditions: bool = True,
    ) -> None:
        codecs.check_codepage(codepage)
        self.layout = layout
        self.codepage = codepage
        self.trim_text = trim_text
        self.include_conditions = include_conditions

    @property
    def record_length(self) -> int:
        return self.layout.record_length

    # ------------------------------------------------------------------ API

    def decode(self, record: bytes) -> DecodedRecord:
        if len(record) != self.record_length:
            raise DecodeError(
                f"record is {len(record)} bytes, layout expects {self.record_length}"
            )
        result = DecodedRecord(data={})
        root = self.layout.root
        if root.is_group:
            result.data = self._decode_children(root, record, 0, "", result, in_redefine=False)
        else:
            value = self._decode_elementary(root, record, 0, root.name, result, False)
            result.data = {root.name: value}
        return result

    def columns(self) -> list[str]:
        """Flat column names in record order (FILLER excluded)."""
        return list(self._column_paths(self.layout.root, prefix="", top=True))

    # ------------------------------------------------------------ internals

    def _column_paths(self, node: LayoutNode, prefix: str, top: bool) -> Iterator[str]:
        if node.is_filler:
            return
        indices = [None] if node.occurs == 1 else list(range(1, node.occurs + 1))
        for idx in indices:
            name = "" if top else f"{prefix}{node.name}{_occ_suffix(idx)}"
            if node.is_group:
                child_prefix = f"{name}." if name else ""
                for child in node.children:
                    yield from self._column_paths(child, child_prefix, top=False)
            else:
                yield name or node.name

    def _decode_children(
        self,
        group: LayoutNode,
        record: bytes,
        shift: int,
        prefix: str,
        result: DecodedRecord,
        in_redefine: bool,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for child in group.children:
            if child.is_filler:
                continue
            redefine = in_redefine or child.redefines is not None
            path = f"{prefix}{child.name}"
            if child.occurs == 1:
                out[child.name] = self._decode_node(child, record, shift, path, result, redefine)
            else:
                out[child.name] = [
                    self._decode_node(
                        child, record, shift + i * child.item_size,
                        f"{path}({i + 1})", result, redefine,
                    )
                    for i in range(child.occurs)
                ]
        return out

    def _decode_node(
        self,
        node: LayoutNode,
        record: bytes,
        shift: int,
        path: str,
        result: DecodedRecord,
        in_redefine: bool,
    ) -> Any:
        if node.is_group:
            value = self._decode_children(node, record, shift, f"{path}.", result, in_redefine)
            if self.include_conditions and node.conditions:
                start = node.offset + shift
                text = codecs.decode_text(record[start : start + node.item_size], self.codepage, False)
                value["_conditions"] = self._conditions(node.conditions, text, None)
            return value
        return self._decode_elementary(node, record, shift, path, result, in_redefine)

    def _decode_elementary(
        self,
        node: LayoutNode,
        record: bytes,
        shift: int,
        path: str,
        result: DecodedRecord,
        in_redefine: bool,
    ) -> Any:
        start = node.offset + shift
        raw = record[start : start + node.item_size]
        try:
            value = self._decode_value(node, raw)
        except DecodeError as exc:
            issue = FieldIssue(path, start, exc.reason, raw.hex().upper())
            (result.warnings if in_redefine else result.errors).append(issue)
            return None
        except UnicodeDecodeError as exc:
            issue = FieldIssue(path, start, f"cannot decode text: {exc.reason}", raw.hex().upper())
            (result.warnings if in_redefine else result.errors).append(issue)
            return None

        if self.include_conditions and node.conditions:
            return {"value": value, "_conditions": self._conditions(node.conditions, value, node)}
        return value

    def _decode_value(self, node: LayoutNode, raw: bytes) -> Any:
        pic = node.pic
        assert pic is not None
        if node.usage is Usage.PACKED:
            return codecs.decode_packed(raw, pic.digits, pic.scale, pic.signed)
        if node.usage in (Usage.BINARY, Usage.NATIVE_BINARY):
            return codecs.decode_binary(
                raw, pic.digits, pic.scale, pic.signed,
                truncate_to_pic=node.usage is Usage.BINARY,
            )
        if pic.category is Category.NUMERIC:
            return codecs.decode_zoned(
                raw, pic.digits, pic.scale, pic.signed,
                node.sign_leading, node.sign_separate, self.codepage,
            )
        return codecs.decode_text(raw, self.codepage, self.trim_text)

    @staticmethod
    def _conditions(conditions: list[Condition], value: Any, node: LayoutNode | None) -> dict[str, bool]:
        numeric = node is not None and node.pic is not None and node.pic.is_numeric
        flags: dict[str, bool] = {}
        for cond in conditions:
            flags[cond.name] = _matches(cond.values, value, numeric)
        return flags


_FIGURATIVE = {
    "SPACE": "", "SPACES": "", "ZERO": "0", "ZEROS": "0", "ZEROES": "0",
    "LOW-VALUE": "\x00", "LOW-VALUES": "\x00", "HIGH-VALUE": "\xff", "HIGH-VALUES": "\xff",
}


def _normalise(literal: str, numeric: bool) -> Any:
    literal = _FIGURATIVE.get(literal.upper(), literal)
    if numeric:
        try:
            return Decimal(literal)
        except InvalidOperation:
            return None
    return literal.rstrip(" ")


def _matches(values: list[str], value: Any, numeric: bool) -> bool:
    if value is None:
        return False
    actual = value if numeric else str(value).rstrip(" ")
    i = 0
    while i < len(values):
        low = _normalise(values[i], numeric)
        if i + 2 < len(values) and values[i + 1] == "THRU":
            high = _normalise(values[i + 2], numeric)
            if low is not None and high is not None and low <= actual <= high:
                return True
            i += 3
            continue
        if low is not None and actual == low:
            return True
        i += 1
    return False


def flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten nested decoded data to ``GROUP.FIELD(2)`` style keys.

    88-level flags are dropped; a field carrying conditions contributes its
    ``value``.
    """
    flat: dict[str, Any] = {}
    for key, value in data.items():
        if key == "_conditions":
            continue
        path = f"{prefix}{key}"
        if isinstance(value, dict) and "value" in value and set(value) <= {"value", "_conditions"}:
            flat[path] = value["value"]
        elif isinstance(value, dict):
            flat.update(flatten(value, f"{path}."))
        elif isinstance(value, list):
            for i, item in enumerate(value, start=1):
                item_path = f"{path}({i})"
                if isinstance(item, dict) and not ("value" in item and set(item) <= {"value", "_conditions"}):
                    flat.update(flatten(item, f"{item_path}."))
                elif isinstance(item, dict):
                    flat[item_path] = item["value"]
                else:
                    flat[item_path] = item
        else:
            flat[path] = value
    return flat
