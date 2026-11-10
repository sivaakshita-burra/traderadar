import json
from decimal import Decimal

import pytest

from traderadar import codecs
from traderadar.batch import iter_records, run_batch
from traderadar.cli import main
from traderadar.copybook.layout import build_layout
from traderadar.copybook.parser import parse_copybook
from traderadar.decoder import RecordDecoder, flatten
from traderadar.encoder import RecordEncoder
from traderadar.errors import DecodeError
from traderadar.samples import SHIPMENT_COPYBOOK, generate_sample

TRADE = """
01 TRADE-REC.
   05 TR-ID          PIC X(6).
   05 TR-TYPE        PIC X.
      88 TR-IMPORT   VALUE 'I'.
      88 TR-EXPORT   VALUE 'E'.
   05 TR-PRIORITY    PIC 9.
      88 TR-URGENT   VALUE 1 THRU 3.
   05 TR-VALUE       PIC S9(7)V99 COMP-3.
   05 TR-QTY         PIC 9(4) COMP.
   05 TR-DATE        PIC 9(8).
   05 TR-DATE-X REDEFINES TR-DATE.
      10 TR-YYYY     PIC 9(4).
      10 TR-MMDD     PIC 9(4).
   05 TR-LINES OCCURS 2 TIMES.
      10 TL-CODE     PIC X(2).
      10 TL-AMT      PIC S9(3)V99 SIGN TRAILING SEPARATE.
   05 FILLER         PIC X(3).
"""


@pytest.fixture()
def layout():
    return build_layout(parse_copybook(TRADE))


def build_good(layout) -> bytes:
    raw = b"".join([
        "TR0001".encode("cp037"),
        "I".encode("cp037"),
        bytes.fromhex("F2"),
        bytes.fromhex("001234567D"),            # TR-VALUE = -12345.67
        (250).to_bytes(2, "big"),               # TR-QTY = 250
        bytes.fromhex("F2F0F2F6F1F1F0F9"),      # 20261109
        "AB".encode("cp037") + bytes.fromhex("F0F1F2F5F0") + bytes.fromhex("60"),  # AB, -12.50
        "CD".encode("cp037") + bytes.fromhex("F0F0F0F9F9") + bytes.fromhex("4E"),  # CD, +0.99
        "   ".encode("cp037"),
    ])
    assert len(raw) == layout.record_length
    return raw


def test_decode_hand_built_record(layout):
    rec = RecordDecoder(layout).decode(build_good(layout))
    assert rec.ok and not rec.warnings
    d = rec.data
    assert d["TR-ID"] == "TR0001"
    assert d["TR-TYPE"] == {"value": "I", "_conditions": {"TR-IMPORT": True, "TR-EXPORT": False}}
    assert d["TR-PRIORITY"]["_conditions"] == {"TR-URGENT": True}
    assert d["TR-VALUE"] == Decimal("-12345.67")
    assert d["TR-QTY"] == Decimal("250")
    assert d["TR-DATE"] == Decimal("20261109")
    assert d["TR-DATE-X"] == {"TR-YYYY": Decimal("2026"), "TR-MMDD": Decimal("1109")}
    assert d["TR-LINES"] == [
        {"TL-CODE": "AB", "TL-AMT": Decimal("-12.50")},
        {"TL-CODE": "CD", "TL-AMT": Decimal("0.99")},
    ]
    assert "FILLER" not in d


def test_flatten_matches_columns(layout):
    decoder = RecordDecoder(layout)
    flat = flatten(decoder.decode(build_good(layout)).data)
    assert list(flat) == decoder.columns()
    assert flat["TR-LINES(2).TL-AMT"] == Decimal("0.99")


def test_bad_field_rejects_but_reports_all_errors(layout):
    raw = bytearray(build_good(layout))
    raw[8:13] = b"\x40" * 5                           # spaces in TR-VALUE (packed)
    raw[38] = 0x99                                    # bad separate sign on TL-AMT(2)
    rec = RecordDecoder(layout).decode(bytes(raw))
    assert not rec.ok
    paths = [e.path for e in rec.errors]
    assert paths == ["TR-VALUE", "TR-LINES(2).TL-AMT"]
    assert rec.errors[0].offset == 8
    assert rec.errors[0].raw_hex == "4040404040"


def test_redefines_failure_is_only_a_warning(layout):
    raw = bytearray(build_good(layout))
    # Make the date non-numeric: the base field fails (error) and so does the redefine (warning).
    raw[15] = 0xC1
    rec = RecordDecoder(layout).decode(bytes(raw))
    assert [e.path for e in rec.errors] == ["TR-DATE"]
    assert [w.path for w in rec.warnings] == ["TR-DATE-X.TR-YYYY"]


def test_wrong_record_length(layout):
    with pytest.raises(DecodeError, match="layout expects"):
        RecordDecoder(layout).decode(b"short")


def test_encoder_round_trip(layout):
    decoder = RecordDecoder(layout)
    original = build_good(layout)
    data = decoder.decode(original).data
    assert RecordEncoder(layout).encode(data) == original


def test_iter_records_handles_chunk_boundaries():
    import io

    data = bytes(range(10)) * 7 + b"xyz"  # 7 records of 10 bytes + 3 trailing
    recs = list(iter_records(io.BytesIO(data), 10, chunk_records=3))
    assert [r.number for r in recs] == list(range(1, 9))
    assert recs[-1].data == b"xyz" and recs[-1].offset == 70
    assert all(len(r.data) == 10 for r in recs[:-1])


def test_sample_end_to_end(tmp_path):
    cpy, dat = generate_sample(tmp_path, records=40, defects=4, seed=7)
    layout = build_layout(parse_copybook(cpy.read_text()))
    out, rej = tmp_path / "out.jsonl", tmp_path / "rej.jsonl"
    summary = run_batch(RecordDecoder(layout), dat, out, rej)
    assert summary.total == 41            # 40 + one truncated fragment
    assert summary.valid == 37
    assert summary.rejected == 4
    assert summary.trailing_bytes == 37
    first = json.loads(out.read_text().splitlines()[0])
    assert first["SHP-ID"] == "SHP0000001"
    assert len(first["SHP-LINE"]) == 3
    rejects = [json.loads(line) for line in rej.read_text().splitlines()]
    assert {r["record"] for r in rejects} >= {41}


def test_cli_layout_and_csv_decode(tmp_path, capsys):
    cpy, dat = generate_sample(tmp_path, records=10, defects=0)
    assert main(["layout", "--copybook", str(cpy)]) == 0
    assert "SHP-LINE" in capsys.readouterr().out
    out = tmp_path / "ships.csv"
    assert main(["decode", "--copybook", str(cpy), "--data", str(dat), "--out", str(out)]) == 0
    header = out.read_text().splitlines()[0].split(",")
    assert header[:3] == ["_record", "SHP-ID", "SHP-STATUS"]
    assert "SHP-LINE(3).LIN-ADJUSTMENT" in header


def test_cli_fail_on_reject_exit_code(tmp_path):
    cpy, dat = generate_sample(tmp_path, records=10, defects=2, seed=3)
    code = main(["decode", "--copybook", str(cpy), "--data", str(dat),
                 "--out", str(tmp_path / "o.jsonl"), "--fail-on-reject"])
    assert code == 2


def test_cli_reports_copybook_errors(tmp_path, capsys):
    bad = tmp_path / "bad.cpy"
    bad.write_text("01 A. 05 B PIC Q.\n")
    assert main(["layout", "--copybook", str(bad)]) == 1
    assert "unrecognised PIC" in capsys.readouterr().err


def test_sample_copybook_layout_is_stable():
    layout = build_layout(parse_copybook(SHIPMENT_COPYBOOK))
    assert layout.record_length == 207
    assert layout.find("SHP-LINE").item_size == 53
    assert codecs.is_ebcdic("cp037")
