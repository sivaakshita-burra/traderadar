"""Synthetic sample data: an import-shipment copybook and a matching file.

Everything here is invented for testing. It is not based on any real
company's system, layout or data.
"""

from __future__ import annotations

import random
from decimal import Decimal
from pathlib import Path

from traderadar.copybook.layout import build_layout
from traderadar.copybook.parser import parse_copybook
from traderadar.encoder import RecordEncoder

SHIPMENT_COPYBOOK = """\
      *================================================================
      * SHIPREC - SYNTHETIC IMPORT SHIPMENT RECORD (TRADERADAR SAMPLE)
      * ALL DATA IS INVENTED. NOT BASED ON ANY REAL SYSTEM.
      *================================================================
       01  SHIPMENT-RECORD.
           05  SHP-ID                  PIC X(10).
           05  SHP-STATUS              PIC X(01).
               88  SHP-OPEN            VALUE 'O'.
               88  SHP-CLEARED         VALUE 'C'.
               88  SHP-HELD            VALUE 'H'.
           05  SHP-ORIGIN-CTRY         PIC X(02).
           05  SHP-INCOTERM            PIC X(03).
           05  SHP-CURRENCY            PIC X(03).
           05  SHP-ARRIVAL-DATE        PIC 9(08).
           05  SHP-ARRIVAL-PARTS REDEFINES SHP-ARRIVAL-DATE.
               10  SHP-ARR-YYYY        PIC 9(04).
               10  SHP-ARR-MM          PIC 9(02).
               10  SHP-ARR-DD          PIC 9(02).
           05  SHP-FREIGHT-AMT         PIC S9(7)V99 COMP-3.
           05  SHP-INSURANCE-AMT       PIC S9(5)V99 COMP-3.
           05  SHP-FX-RATE             PIC 9(3)V9(6) COMP-3.
           05  SHP-LINE-COUNT          PIC S9(4) COMP.
           05  SHP-LINE OCCURS 3 TIMES.
               10  LIN-HS-CODE         PIC X(10).
               10  LIN-DESCRIPTION     PIC X(20).
               10  LIN-QUANTITY        PIC S9(7) COMP-3.
               10  LIN-NET-WEIGHT-KG   PIC S9(7)V999 COMP-3.
               10  LIN-UNIT-PRICE      PIC S9(7)V99 COMP-3.
               10  LIN-ADJUSTMENT      PIC S9(5)V99
                                       SIGN LEADING SEPARATE.
           05  FILLER                  PIC X(05).
"""

# (HS code, description, typical unit price in source currency, kg per unit)
_PRODUCTS = [
    ("6109100010", "COTTON T-SHIRTS", Decimal("3.20"), Decimal("0.180")),
    ("6204420000", "COTTON DRESSES", Decimal("11.50"), Decimal("0.350")),
    ("4202221000", "LEATHER HANDBAGS", Decimal("24.00"), Decimal("0.900")),
    ("8471300000", "LAPTOP COMPUTERS", Decimal("310.00"), Decimal("2.100")),
    ("0902300010", "BLACK TEA", Decimal("4.10"), Decimal("1.000")),
    ("6403999300", "LEATHER FOOTWEAR", Decimal("18.75"), Decimal("1.100")),
    ("9503007000", "PLASTIC TOYS", Decimal("2.60"), Decimal("0.250")),
]
_ORIGINS = [("IN", "USD"), ("CN", "USD"), ("VN", "USD"), ("BD", "USD"), ("TR", "EUR"), ("US", "USD")]
_INCOTERMS = ["FOB", "CIF", "EXW", "FCA", "CFR"]
_FX_TO_GBP = {"USD": Decimal("0.781250"), "EUR": Decimal("0.852300")}

DEFECTS = ("spaces_in_packed", "bad_sign_nibble", "non_numeric_date", "truncated_last")


def _record(rng: random.Random, n: int) -> dict:
    origin, currency = rng.choice(_ORIGINS)
    line_count = rng.randint(1, 3)
    lines = []
    for _ in range(line_count):
        hs, desc, price, kg = rng.choice(_PRODUCTS)
        qty = rng.randint(10, 5000)
        unit_price = (price * Decimal(rng.uniform(0.85, 1.15))).quantize(Decimal("0.01"))
        lines.append({
            "LIN-HS-CODE": hs,
            "LIN-DESCRIPTION": desc,
            "LIN-QUANTITY": qty,
            "LIN-NET-WEIGHT-KG": (kg * qty).quantize(Decimal("0.001")),
            "LIN-UNIT-PRICE": unit_price,
            "LIN-ADJUSTMENT": Decimal(rng.choice([0, 0, 0, -25, -50, 15])),
        })
    month, day = rng.randint(1, 12), rng.randint(1, 28)
    return {
        "SHP-ID": f"SHP{n:07d}",
        "SHP-STATUS": rng.choice("OOCH"),
        "SHP-ORIGIN-CTRY": origin,
        "SHP-INCOTERM": rng.choice(_INCOTERMS),
        "SHP-CURRENCY": currency,
        "SHP-ARRIVAL-DATE": int(f"2026{month:02d}{day:02d}"),
        "SHP-FREIGHT-AMT": Decimal(rng.randint(15000, 450000)) / 100,
        "SHP-INSURANCE-AMT": Decimal(rng.randint(1000, 30000)) / 100,
        "SHP-FX-RATE": _FX_TO_GBP[currency],
        "SHP-LINE-COUNT": line_count,
        "SHP-LINE": lines,  # unused occurrences are written as blanks/zeros
    }


def generate_sample(
    out_dir: str | Path,
    records: int = 50,
    defects: int = 4,
    seed: int = 42,
    codepage: str = "cp037",
) -> tuple[Path, Path]:
    """Write ``shiprec.cpy`` and ``shipments.dat`` into ``out_dir``.

    ``defects`` corrupts that many records (cycling through :data:`DEFECTS`)
    so the reject handling can be demonstrated.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cpy_path = out / "shiprec.cpy"
    dat_path = out / "shipments.dat"
    cpy_path.write_text(SHIPMENT_COPYBOOK, encoding="utf-8")

    layout = build_layout(parse_copybook(SHIPMENT_COPYBOOK))
    encoder = RecordEncoder(layout, codepage)
    rng = random.Random(seed)

    freight = layout.find("SHP-FREIGHT-AMT")
    insurance = layout.find("SHP-INSURANCE-AMT")
    arrival = layout.find("SHP-ARRIVAL-DATE")

    encoded = [bytearray(encoder.encode(_record(rng, i + 1))) for i in range(records)]

    defect_kinds = [DEFECTS[i % len(DEFECTS)] for i in range(min(defects, records))]
    targets = rng.sample(range(records), len(defect_kinds))
    truncate = False
    for kind, idx in zip(defect_kinds, targets):
        rec = encoded[idx]
        if kind == "spaces_in_packed":
            rec[freight.offset : freight.end] = b"\x40" * freight.size
        elif kind == "bad_sign_nibble":
            last = insurance.end - 1
            rec[last] = (rec[last] & 0xF0) | 0x07
        elif kind == "non_numeric_date":
            rec[arrival.offset : arrival.offset + 2] = "AB".encode(codepage)
        elif kind == "truncated_last":
            truncate = True

    with dat_path.open("wb") as fh:
        for rec in encoded:
            fh.write(rec)
        if truncate:
            fh.write(bytes(encoded[0][:37]))  # a partial record at end of file
    return cpy_path, dat_path
