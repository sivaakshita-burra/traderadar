"""Encode Python values back into fixed-length mainframe records.

The reverse of :mod:`traderadar.decoder`. Only the base view is written
(REDEFINES views are skipped because they share the same bytes). Missing
fields default to spaces (text) or zero (numeric); FILLER is spaces.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from traderadar import codecs
from traderadar.copybook.layout import Layout, LayoutNode
from traderadar.copybook.pic import Category, Usage


class RecordEncoder:
    def __init__(self, layout: Layout, codepage: str = "cp037") -> None:
        codecs.check_codepage(codepage)
        self.layout = layout
        self.codepage = codepage

    def encode(self, data: dict[str, Any]) -> bytes:
        buf = bytearray(" ".encode(self.codepage) * self.layout.record_length)
        root = self.layout.root
        if root.is_group:
            self._write_children(root, data, buf, 0)
        else:
            self._write_elementary(root, data.get(root.name), buf, 0)
        return bytes(buf)

    def _write_children(self, group: LayoutNode, data: dict[str, Any], buf: bytearray, shift: int) -> None:
        for child in group.children:
            if child.redefines or child.is_filler:
                continue
            value = data.get(child.name) if isinstance(data, dict) else None
            for i in range(child.occurs):
                item = value
                if child.occurs > 1:
                    item = value[i] if isinstance(value, list) and i < len(value) else None
                offset = shift + i * child.item_size
                if child.is_group:
                    self._write_children(child, item or {}, buf, offset)
                else:
                    self._write_elementary(child, item, buf, offset)

    def _write_elementary(self, node: LayoutNode, value: Any, buf: bytearray, shift: int) -> None:
        pic = node.pic
        assert pic is not None
        if isinstance(value, dict) and "value" in value:
            value = value["value"]
        start = node.offset + shift
        if node.usage is Usage.PACKED:
            raw = codecs.encode_packed(Decimal(value or 0), pic.digits, pic.scale, pic.signed)
        elif node.usage in (Usage.BINARY, Usage.NATIVE_BINARY):
            raw = codecs.encode_binary(Decimal(value or 0), node.item_size, pic.scale, pic.signed)
        elif pic.category is Category.NUMERIC:
            raw = codecs.encode_zoned(
                Decimal(value or 0), pic.digits, pic.scale, pic.signed,
                node.sign_leading, node.sign_separate, self.codepage,
            )
        else:
            raw = codecs.encode_text("" if value is None else str(value), node.item_size, self.codepage)
        buf[start : start + node.item_size] = raw
