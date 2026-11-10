"""Compute the physical byte layout of a parsed record.

Every node gets an ``offset`` (from the start of the record), the size of one
occurrence (``item_size``) and its total size (``item_size * occurs``). OCCURS
children are laid out once at the first occurrence; the decoder shifts them by
``index * item_size`` for later occurrences.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from traderadar.copybook.parser import Condition, Copybook, Field
from traderadar.copybook.pic import PicSpec, Usage, storage_size
from traderadar.errors import CopybookSyntaxError


@dataclass
class LayoutNode:
    name: str
    level: int
    offset: int
    item_size: int
    occurs: int = 1
    usage: Usage = Usage.DISPLAY
    pic: PicSpec | None = None
    sign_leading: bool = False
    sign_separate: bool = False
    redefines: str | None = None
    conditions: list[Condition] = field(default_factory=list)
    children: list["LayoutNode"] = field(default_factory=list)
    line: int | None = None

    @property
    def size(self) -> int:
        return self.item_size * self.occurs

    @property
    def is_group(self) -> bool:
        return bool(self.children)

    @property
    def is_filler(self) -> bool:
        return self.name == "FILLER"

    @property
    def end(self) -> int:
        """Offset one past the last byte (exclusive)."""
        return self.offset + self.size


@dataclass
class Layout:
    root: LayoutNode

    @property
    def record_length(self) -> int:
        return self.root.size

    def walk(self) -> Iterator[tuple[int, LayoutNode]]:
        """Depth-first (depth, node) pairs; OCCURS shown once."""

        def _walk(node: LayoutNode, depth: int) -> Iterator[tuple[int, LayoutNode]]:
            yield depth, node
            for child in node.children:
                yield from _walk(child, depth + 1)

        yield from _walk(self.root, 0)

    def find(self, name: str) -> LayoutNode:
        for _, node in self.walk():
            if node.name == name.upper():
                return node
        raise KeyError(name)

    def describe(self) -> str:
        """Human-readable layout table, like a mainframe file-layout listing."""
        header = f"{'LVL':<4}{'FIELD':<34}{'OFFSET':>7}{'LEN':>6}  {'TYPE'}"
        lines = [header, "-" * len(header)]
        for depth, node in self.walk():
            if node.is_group:
                kind = "GROUP"
            else:
                assert node.pic is not None
                kind = f"PIC {node.pic.source}"
                if node.usage is not Usage.DISPLAY:
                    kind += f" {node.usage.value}"
                if node.sign_separate:
                    kind += " SIGN " + ("LEADING" if node.sign_leading else "TRAILING") + " SEP"
            if node.occurs > 1:
                kind += f" OCCURS {node.occurs} (x{node.item_size})"
            if node.redefines:
                kind += f" REDEFINES {node.redefines}"
            name = ("  " * depth + node.name)[:33]
            lines.append(
                f"{node.level:02d}  {name:<34}{node.offset + 1:>7}{node.size:>6}  {kind}"
            )
        lines.append(f"\nRecord length: {self.record_length} bytes (offsets are 1-based)")
        return "\n".join(lines)


def _effective_usage(fld: Field, inherited: Usage | None) -> Usage | None:
    if fld.usage is not None and inherited is not None and fld.usage is not inherited:
        raise CopybookSyntaxError(
            f"{fld.name} USAGE {fld.usage.value} conflicts with group USAGE {inherited.value}",
            fld.line,
        )
    return fld.usage or inherited


def _build(fld: Field, offset: int, inherited: Usage | None) -> LayoutNode:
    usage = _effective_usage(fld, inherited)

    if not fld.is_group:
        assert fld.pic is not None
        concrete = usage or Usage.DISPLAY
        size = storage_size(fld.pic, concrete, fld.sign_separate, fld.line)
        return LayoutNode(
            name=fld.name,
            level=fld.level,
            offset=offset,
            item_size=size,
            occurs=fld.occurs or 1,
            usage=concrete,
            pic=fld.pic,
            sign_leading=fld.sign_leading,
            sign_separate=fld.sign_separate,
            redefines=fld.redefines,
            conditions=list(fld.conditions),
            line=fld.line,
        )

    children: list[LayoutNode] = []
    cursor = offset
    by_name: dict[str, LayoutNode] = {}
    last_base: LayoutNode | None = None  # most recent non-REDEFINES sibling

    for child in fld.children:
        if child.redefines:
            target = by_name.get(child.redefines)
            if target is None or last_base is None or target is not last_base:
                raise CopybookSyntaxError(
                    f"{child.name} REDEFINES {child.redefines}: target must be the "
                    "preceding item at the same level",
                    child.line,
                )
            if target.occurs > 1:
                raise CopybookSyntaxError(
                    f"{child.name} cannot redefine {target.name}, which has OCCURS", child.line
                )
            node = _build(child, target.offset, usage)
            if node.size > target.size:
                raise CopybookSyntaxError(
                    f"{child.name} ({node.size} bytes) is larger than the item it "
                    f"redefines, {target.name} ({target.size} bytes)",
                    child.line,
                )
        else:
            node = _build(child, cursor, usage)
            cursor += node.size
            last_base = node
        children.append(node)
        if not node.is_filler:
            by_name[node.name] = node

    item_size = cursor - offset
    if item_size == 0:
        raise CopybookSyntaxError(f"group {fld.name} has no storage", fld.line)
    return LayoutNode(
        name=fld.name,
        level=fld.level,
        offset=offset,
        item_size=item_size,
        occurs=fld.occurs or 1,
        usage=usage or Usage.DISPLAY,
        redefines=fld.redefines,
        conditions=list(fld.conditions),
        children=children,
        line=fld.line,
    )


def build_layout(copybook: Copybook, record: str | None = None) -> Layout:
    """Build the layout for one record (default: the first 01 level)."""
    rec = copybook.record(record)
    if rec.occurs:
        raise CopybookSyntaxError("an 01-level record cannot have OCCURS", rec.line)
    return Layout(_build(rec, 0, None))
