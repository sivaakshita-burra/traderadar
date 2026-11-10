"""COBOL copybook parser.

Supported subset (Phase 1):

* Fixed format (sequence area cols 1-6, indicator col 7, code cols 8-72) and
  free format, detected automatically
* Comments: ``*`` or ``/`` in col 7 (fixed), ``*>`` inline (both formats)
* Levels 01-49, 77 and 88 (condition names)
* PIC / PICTURE, USAGE (DISPLAY, COMP, COMP-4, BINARY, COMP-5, COMP-3,
  PACKED-DECIMAL), inherited from group items
* OCCURS n [TIMES] (fixed count), with INDEXED BY / KEY phrases skipped
* REDEFINES, FILLER (named or unnamed)
* SIGN [IS] LEADING|TRAILING [SEPARATE [CHARACTER]]
* VALUE (kept for 88-levels, ignored elsewhere), JUSTIFIED, BLANK WHEN ZERO

Deliberately rejected with :class:`UnsupportedFeatureError` rather than
decoded wrongly: OCCURS DEPENDING ON, SYNCHRONIZED, level 66 RENAMES,
continuation lines, COMP-1/COMP-2, P scaling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from traderadar.copybook.pic import PicSpec, Usage, parse_picture
from traderadar.errors import CopybookSyntaxError, UnsupportedFeatureError

USAGE_WORDS = {
    "DISPLAY", "COMP", "COMPUTATIONAL", "COMP-1", "COMP-2", "COMP-3", "COMP-4",
    "COMP-5", "COMPUTATIONAL-1", "COMPUTATIONAL-2", "COMPUTATIONAL-3",
    "COMPUTATIONAL-4", "COMPUTATIONAL-5", "BINARY", "PACKED-DECIMAL", "POINTER",
    "INDEX", "NATIONAL", "DISPLAY-1",
}
CLAUSE_WORDS = USAGE_WORDS | {
    "PIC", "PICTURE", "USAGE", "OCCURS", "REDEFINES", "VALUE", "VALUES", "SIGN",
    "LEADING", "TRAILING", "SYNC", "SYNCHRONIZED", "JUST", "JUSTIFIED", "BLANK",
    "INDEXED", "ASCENDING", "DESCENDING", "GLOBAL", "EXTERNAL", "RENAMES",
}


@dataclass
class Condition:
    """An 88-level condition name, e.g. ``88 STATUS-OK VALUE 'A'``."""

    name: str
    values: list[str]


@dataclass
class Field:
    """One data description entry and its children."""

    level: int
    name: str
    line: int
    pic: PicSpec | None = None
    usage: Usage | None = None  # explicit usage on this entry only
    occurs: int | None = None
    redefines: str | None = None
    sign_leading: bool = False
    sign_separate: bool = False
    children: list["Field"] = field(default_factory=list)
    conditions: list[Condition] = field(default_factory=list)

    @property
    def is_group(self) -> bool:
        return bool(self.children)

    @property
    def is_filler(self) -> bool:
        return self.name.upper() == "FILLER"


@dataclass
class Copybook:
    """All 01/77-level records found in a copybook."""

    records: list[Field]

    def record(self, name: str | None = None) -> Field:
        if not self.records:
            raise CopybookSyntaxError("copybook contains no 01-level record")
        if name is None:
            return self.records[0]
        for rec in self.records:
            if rec.name.upper() == name.upper():
                return rec
        available = ", ".join(r.name for r in self.records)
        raise CopybookSyntaxError(f"record '{name}' not found (available: {available})")


# --------------------------------------------------------------------------
# Source preprocessing
# --------------------------------------------------------------------------

_FIXED_INDICATORS = set(" *-/D")


def _looks_fixed_format(lines: list[str]) -> bool:
    code_lines = [ln for ln in lines if ln.strip()]
    if not code_lines:
        return False
    for ln in code_lines:
        if len(ln) < 7:
            return False
        if any(not (c.isdigit() or c == " ") for c in ln[:6]):
            return False
        if ln[6] not in _FIXED_INDICATORS:
            return False
    return True


def _source_lines(text: str, fixed: bool | None) -> Iterator[tuple[int, str]]:
    """Yield (line_number, code_text) with comments and margins removed."""
    raw_lines = text.expandtabs(4).splitlines()
    if fixed is None:
        fixed = _looks_fixed_format(raw_lines)
    for number, raw in enumerate(raw_lines, start=1):
        if fixed:
            if len(raw) < 7:
                continue
            indicator = raw[6]
            if indicator in "*/":
                continue
            if indicator == "-":
                raise UnsupportedFeatureError("continuation lines are not supported yet", number)
            if indicator == "D":  # debugging line: treat as comment
                continue
            code = raw[7:72]
        else:
            stripped = raw.lstrip()
            if stripped.startswith("*") and not stripped.startswith("*>"):
                continue
            code = raw
        if "*>" in code:
            code = code[: code.index("*>")]
        yield number, code


# --------------------------------------------------------------------------
# Tokenizer
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Token:
    text: str
    line: int
    is_literal: bool = False


PERIOD = "."


def tokenize(text: str, fixed: bool | None = None) -> list[Token]:
    tokens: list[Token] = []
    for number, code in list(_source_lines(text, fixed)):
        i, n = 0, len(code)
        while i < n:
            ch = code[i]
            if ch.isspace() or ch in ",;":
                i += 1
                continue
            if ch in "'\"":
                end = code.find(ch, i + 1)
                if end == -1:
                    raise CopybookSyntaxError("unterminated literal", number)
                tokens.append(Token(code[i + 1 : end], number, is_literal=True))
                i = end + 1
                continue
            j = i
            while j < n and not code[j].isspace() and code[j] not in "'\"":
                j += 1
            word = code[i:j]
            # A trailing period followed by whitespace/end terminates the entry.
            if word.endswith(".") and len(word) > 1:
                tokens.append(Token(word[:-1], number))
                tokens.append(Token(PERIOD, number))
            elif word == ".":
                tokens.append(Token(PERIOD, number))
            else:
                tokens.append(Token(word, number))
            i = j
    return tokens


def _split_entries(tokens: list[Token]) -> list[list[Token]]:
    entries: list[list[Token]] = []
    current: list[Token] = []
    for tok in tokens:
        if tok.text == PERIOD and not tok.is_literal:
            if current:
                entries.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        raise CopybookSyntaxError("last entry is not terminated by a period", current[-1].line)
    return entries


# --------------------------------------------------------------------------
# Entry parsing
# --------------------------------------------------------------------------

class _Cursor:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self, offset: int = 0) -> Token | None:
        idx = self.pos + offset
        return self.tokens[idx] if idx < len(self.tokens) else None

    def peek_word(self, offset: int = 0) -> str | None:
        tok = self.peek(offset)
        if tok is None or tok.is_literal:
            return None
        return tok.text.upper()

    def next(self) -> Token:
        tok = self.peek()
        if tok is None:
            last = self.tokens[-1].line if self.tokens else None
            raise CopybookSyntaxError("unexpected end of entry", last)
        self.pos += 1
        return tok

    def accept(self, *words: str) -> bool:
        if self.peek_word() in words:
            self.pos += 1
            return True
        return False

    def done(self) -> bool:
        return self.pos >= len(self.tokens)


def _parse_int(tok: Token, what: str) -> int:
    try:
        return int(tok.text)
    except ValueError:
        raise CopybookSyntaxError(f"expected {what}, got '{tok.text}'", tok.line) from None


def _parse_entry(tokens: list[Token]) -> tuple[Field, list[str] | None]:
    """Parse one entry. Returns the field and, for 88-levels, its values."""
    cur = _Cursor(tokens)
    level_tok = cur.next()
    level = _parse_int(level_tok, "level number")
    line = level_tok.line

    if level == 66:
        raise UnsupportedFeatureError("level 66 RENAMES is not supported yet", line)
    if not (1 <= level <= 49 or level in (77, 88)):
        raise CopybookSyntaxError(f"invalid level number {level}", line)

    # Data name is optional (unnamed FILLER) when a clause follows directly.
    name = "FILLER"
    nxt = cur.peek_word()
    if nxt is not None and nxt not in CLAUSE_WORDS:
        name = cur.next().text.upper()

    fld = Field(level=level, name=name, line=line)
    values: list[str] | None = None

    while not cur.done():
        word = cur.peek_word()
        tok = cur.peek()
        assert tok is not None
        if word is None:
            raise CopybookSyntaxError(f"unexpected literal '{tok.text}'", tok.line)

        if word in ("PIC", "PICTURE"):
            cur.next()
            cur.accept("IS")
            pic_tok = cur.next()
            fld.pic = parse_picture(pic_tok.text, pic_tok.line)
        elif word == "USAGE":
            cur.next()
            cur.accept("IS")
            fld.usage = Usage.from_keyword(cur.next().text, line)
        elif word in USAGE_WORDS:
            fld.usage = Usage.from_keyword(cur.next().text, line)
        elif word == "REDEFINES":
            cur.next()
            fld.redefines = cur.next().text.upper()
        elif word == "OCCURS":
            cur.next()
            count = _parse_int(cur.next(), "OCCURS count")
            if cur.accept("TO"):
                raise UnsupportedFeatureError(
                    "OCCURS ... DEPENDING ON (variable length) is not supported yet", line
                )
            cur.accept("TIMES")
            if cur.peek_word() == "DEPENDING":
                raise UnsupportedFeatureError(
                    "OCCURS ... DEPENDING ON (variable length) is not supported yet", line
                )
            if count < 1:
                raise CopybookSyntaxError("OCCURS count must be at least 1", line)
            fld.occurs = count
        elif word in ("ASCENDING", "DESCENDING"):
            cur.next()
            cur.accept("KEY")
            cur.accept("IS")
            while cur.peek_word() not in CLAUSE_WORDS | {None}:
                cur.next()
        elif word == "INDEXED":
            cur.next()
            cur.accept("BY")
            while cur.peek_word() not in CLAUSE_WORDS | {None}:
                cur.next()
        elif word in ("SIGN", "LEADING", "TRAILING"):
            if word == "SIGN":
                cur.next()
                cur.accept("IS")
            position = cur.next().text.upper()
            if position not in ("LEADING", "TRAILING"):
                raise CopybookSyntaxError("SIGN must be LEADING or TRAILING", line)
            fld.sign_leading = position == "LEADING"
            if cur.accept("SEPARATE"):
                fld.sign_separate = True
                cur.accept("CHARACTER")
        elif word in ("SYNC", "SYNCHRONIZED"):
            raise UnsupportedFeatureError(
                "SYNCHRONIZED changes alignment and is not supported yet", line
            )
        elif word in ("JUST", "JUSTIFIED"):
            cur.next()
            cur.accept("RIGHT")
        elif word == "BLANK":
            cur.next()
            cur.accept("WHEN")
            if not cur.accept("ZERO", "ZEROS", "ZEROES"):
                raise CopybookSyntaxError("expected ZERO after BLANK WHEN", line)
        elif word in ("GLOBAL", "EXTERNAL"):
            cur.next()
        elif word in ("VALUE", "VALUES"):
            cur.next()
            cur.accept("IS", "ARE")
            collected: list[str] = []
            while not cur.done() and (
                cur.peek().is_literal or cur.peek_word() not in CLAUSE_WORDS  # type: ignore[union-attr]
            ):
                item = cur.next()
                if not item.is_literal and item.text.upper() in ("THRU", "THROUGH"):
                    collected.append("THRU")
                else:
                    collected.append(item.text)
            if not collected:
                raise CopybookSyntaxError("VALUE clause without a value", line)
            values = collected
        elif word == "RENAMES":
            raise UnsupportedFeatureError("RENAMES is not supported yet", line)
        else:
            raise CopybookSyntaxError(f"unexpected word '{tok.text}'", tok.line)

    if level == 88 and values is None:
        raise CopybookSyntaxError("88-level entry needs a VALUE clause", line)
    return fld, values


# --------------------------------------------------------------------------
# Tree building
# --------------------------------------------------------------------------

def _validate(fld: Field) -> None:
    if fld.is_group:
        if fld.pic is not None:
            raise CopybookSyntaxError(f"group item {fld.name} must not have a PIC", fld.line)
        for child in fld.children:
            _validate(child)
    elif fld.pic is None:
        raise CopybookSyntaxError(f"elementary item {fld.name} needs a PIC clause", fld.line)
    if fld.sign_separate and fld.pic is not None and not fld.pic.signed:
        raise CopybookSyntaxError(f"SIGN SEPARATE on unsigned field {fld.name}", fld.line)


def parse_copybook(text: str, fixed: bool | None = None) -> Copybook:
    """Parse copybook source text.

    ``fixed=None`` auto-detects fixed vs free format.
    """
    entries = _split_entries(tokenize(text, fixed))
    records: list[Field] = []
    stack: list[Field] = []

    for entry in entries:
        fld, values = _parse_entry(entry)

        if fld.level == 88:
            if not stack:
                raise CopybookSyntaxError("88-level entry has no parent item", fld.line)
            stack[-1].conditions.append(Condition(fld.name, values or []))
            continue

        if fld.level in (1, 77):
            if fld.level == 77 and fld.pic is None:
                raise CopybookSyntaxError("77-level item needs a PIC clause", fld.line)
            records.append(fld)
            stack = [fld]
            continue

        if not stack:
            raise CopybookSyntaxError(
                f"level {fld.level:02d} item {fld.name} appears before any 01 level", fld.line
            )
        while stack and stack[-1].level >= fld.level:
            stack.pop()
        if not stack:
            raise CopybookSyntaxError(f"cannot place level {fld.level:02d} item {fld.name}", fld.line)
        parent = stack[-1]
        if parent.level == 77:
            raise CopybookSyntaxError("77-level items cannot have children", fld.line)
        if parent.pic is not None:
            raise CopybookSyntaxError(
                f"{parent.name} has a PIC so it cannot contain {fld.name}", fld.line
            )
        parent.children.append(fld)
        stack.append(fld)

    for rec in records:
        _validate(rec)
    return Copybook(records)
