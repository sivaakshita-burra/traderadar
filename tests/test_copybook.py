import pytest

from traderadar.copybook.layout import build_layout
from traderadar.copybook.parser import parse_copybook
from traderadar.copybook.pic import Category, Usage, parse_picture, storage_size
from traderadar.errors import CopybookSyntaxError, UnsupportedFeatureError

# ------------------------------------------------------------------ PIC


@pytest.mark.parametrize(
    "pic, category, length, digits, scale, signed",
    [
        ("X(10)", Category.ALPHANUMERIC, 10, 0, 0, False),
        ("XXX", Category.ALPHANUMERIC, 3, 0, 0, False),
        ("A(4)", Category.ALPHABETIC, 4, 0, 0, False),
        ("9(5)", Category.NUMERIC, 5, 5, 0, False),
        ("S9(7)V99", Category.NUMERIC, 9, 9, 2, True),
        ("s9(3)v9(4)", Category.NUMERIC, 7, 7, 4, True),
        ("V99", Category.NUMERIC, 2, 2, 2, False),
        ("ZZ,ZZ9.99-", Category.NUMERIC_EDITED, 10, 0, 0, False),
    ],
)
def test_parse_picture(pic, category, length, digits, scale, signed):
    spec = parse_picture(pic)
    assert (spec.category, spec.display_length, spec.digits, spec.scale, spec.signed) == (
        category, length, digits, scale, signed,
    )


@pytest.mark.parametrize("pic", ["9S9", "9V9V9", "Q(3)", "9(0)", "9(3"])
def test_bad_pictures(pic):
    with pytest.raises(CopybookSyntaxError):
        parse_picture(pic)


def test_p_scaling_unsupported():
    with pytest.raises(UnsupportedFeatureError):
        parse_picture("99PPP")


@pytest.mark.parametrize(
    "pic, usage, separate, size",
    [
        ("S9(7)V99", Usage.DISPLAY, False, 9),
        ("S9(7)V99", Usage.DISPLAY, True, 10),
        ("S9(7)V99", Usage.PACKED, False, 5),
        ("S9(4)", Usage.PACKED, False, 3),
        ("9(4)", Usage.BINARY, False, 2),
        ("9(5)", Usage.BINARY, False, 4),
        ("S9(9)", Usage.BINARY, False, 4),
        ("S9(10)", Usage.NATIVE_BINARY, False, 8),
        ("X(12)", Usage.DISPLAY, False, 12),
    ],
)
def test_storage_size(pic, usage, separate, size):
    assert storage_size(parse_picture(pic), usage, separate) == size


def test_comp3_needs_numeric_pic():
    with pytest.raises(CopybookSyntaxError, match="numeric PIC"):
        storage_size(parse_picture("X(3)"), Usage.PACKED)


# --------------------------------------------------------------- parser

FREE = """
01 CUSTOMER-REC.
   05 CUST-ID        PIC 9(6).
   05 CUST-NAME      PIC X(20).
   05 CUST-STATUS    PIC X.
      88 ACTIVE      VALUE 'A'.
      88 CLOSED      VALUES ARE 'C' 'X'.
   05 BALANCE        PIC S9(7)V99 USAGE IS COMP-3.
   05 FILLER         PIC X(4).
   05 PIC X(2).  *> unnamed filler
"""


def test_parse_free_format():
    cb = parse_copybook(FREE)
    rec = cb.record()
    assert rec.name == "CUSTOMER-REC"
    names = [c.name for c in rec.children]
    assert names == ["CUST-ID", "CUST-NAME", "CUST-STATUS", "BALANCE", "FILLER", "FILLER"]
    status = rec.children[2]
    assert [c.name for c in status.conditions] == ["ACTIVE", "CLOSED"]
    assert status.conditions[1].values == ["C", "X"]
    assert rec.children[3].usage is Usage.PACKED


FIXED = (
    "000100 01  ORDER-REC.\n"
    "000200*    THIS IS A COMMENT LINE\n"
    + "000300     05  ORD-NO          PIC X(8).".ljust(72) + "ORD00001\n"
    "000400     05  ORD-AMT         PIC S9(5)V99\n"
    "000500                         COMP-3.\n"
    "000600/    PAGE EJECT IS ALSO A COMMENT\n"
    "000700     05  ORD-QTY         PIC 9(4) COMP.\n"
)


def test_parse_fixed_format_ignores_sequence_and_id_areas():
    layout = build_layout(parse_copybook(FIXED))
    assert [n.name for _, n in layout.walk()] == ["ORDER-REC", "ORD-NO", "ORD-AMT", "ORD-QTY"]
    assert layout.record_length == 8 + 4 + 2


def test_continuation_line_unsupported():
    text = "       01  A.\n       05  B  PIC X(10) VALUE 'ABC\n      -    'DEF'.\n"
    with pytest.raises(UnsupportedFeatureError, match="continuation"):
        parse_copybook(text)


@pytest.mark.parametrize(
    "text, error, message",
    [
        ("01 A. 05 B PIC X(2) OCCURS 1 TO 5 DEPENDING ON C.", UnsupportedFeatureError, "DEPENDING"),
        ("01 A. 05 B PIC 9(4) COMP SYNC.", UnsupportedFeatureError, "SYNCHRONIZED"),
        ("01 A. 05 B PIC X(2). 66 C RENAMES B.", UnsupportedFeatureError, "66"),
        ("01 A. 05 B COMP-1.", UnsupportedFeatureError, "COMP-1"),
        ("01 A. 05 B PIC X(2)", CopybookSyntaxError, "period"),
        ("01 A. 05 B.", CopybookSyntaxError, "needs a PIC"),
        ("05 B PIC X.", CopybookSyntaxError, "before any 01"),
        ("01 A. 05 B PIC X(2). 10 C PIC X.", CopybookSyntaxError, "has a PIC"),
        ("01 A. 05 B PIC X FOO.", CopybookSyntaxError, "unexpected word"),
        ("01 A. 05 B PIC 9 SIGN LEADING SEPARATE.", CopybookSyntaxError, "unsigned"),
        ("01 A. 88 X.", CopybookSyntaxError, "VALUE"),
    ],
)
def test_parse_errors(text, error, message):
    with pytest.raises(error, match=message):
        build_layout(parse_copybook(text))


def test_error_reports_line_number():
    with pytest.raises(CopybookSyntaxError) as exc:
        parse_copybook("01 A.\n05 B PIC X.\n05 C PIC Q.\n")
    assert exc.value.line == 3


# --------------------------------------------------------------- layout

LAYOUT = """
01 REC.
   05 KEY-FIELD       PIC X(4).
   05 AMOUNTS COMP-3.
      10 AMT-1        PIC S9(5)V99.
      10 AMT-2        PIC S9(3).
   05 DATE-NUM        PIC 9(8).
   05 DATE-PARTS REDEFINES DATE-NUM.
      10 YYYY         PIC 9(4).
      10 MM           PIC 99.
      10 DD           PIC 99.
   05 ITEMS OCCURS 3 TIMES INDEXED BY ITEM-IX.
      10 ITEM-CODE    PIC X(3).
      10 ITEM-QTY     PIC S9(4) COMP.
   05 TRAILER         PIC X(2).
"""


def test_layout_offsets_redefines_occurs_and_group_usage():
    layout = build_layout(parse_copybook(LAYOUT))
    off = {n.name: (n.offset, n.size) for _, n in layout.walk()}
    assert off["KEY-FIELD"] == (0, 4)
    assert off["AMOUNTS"] == (4, 6)          # usage inherited: 4 + 2 bytes packed
    assert off["AMT-1"] == (4, 4)
    assert off["AMT-2"] == (8, 2)
    assert off["DATE-NUM"] == (10, 8)
    assert off["DATE-PARTS"] == (10, 8)      # overlays DATE-NUM
    assert off["MM"] == (14, 2)
    assert off["ITEMS"] == (18, 15)          # 3 x (3 + 2)
    assert off["TRAILER"] == (33, 2)
    assert layout.record_length == 35
    assert "REDEFINES DATE-NUM" in layout.describe()


@pytest.mark.parametrize(
    "text, message",
    [
        ("01 R. 05 A PIC X(2). 05 B PIC X(5) REDEFINES A.", "larger than"),
        ("01 R. 05 A PIC X(2). 05 C PIC X. 05 B PIC X REDEFINES A.", "preceding item"),
        ("01 R. 05 A PIC X OCCURS 2. 05 B PIC XX REDEFINES A.", "OCCURS"),
        ("01 R COMP-3. 05 A PIC 9(3) COMP.", "conflicts"),
    ],
)
def test_layout_errors(text, message):
    with pytest.raises(CopybookSyntaxError, match=message):
        build_layout(parse_copybook(text))


def test_multiple_redefines_of_same_item():
    text = "01 R. 05 A PIC X(4). 05 B REDEFINES A PIC 9(4). 05 C REDEFINES A PIC S9(7) COMP-3."
    layout = build_layout(parse_copybook(text))
    assert layout.record_length == 4
    assert layout.find("C").offset == 0


def test_select_record_by_name():
    text = "01 HDR. 05 H-TYPE PIC X. 05 H-DATE PIC 9(8). 01 DTL. 05 D-TYPE PIC X. 05 D-AMT PIC 9(5)."
    cb = parse_copybook(text)
    assert build_layout(cb, "DTL").record_length == 6
    with pytest.raises(CopybookSyntaxError, match="not found"):
        cb.record("TRL")
