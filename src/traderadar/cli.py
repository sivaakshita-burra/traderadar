"""Command-line interface.

    traderadar layout   --copybook shiprec.cpy
    traderadar decode   --copybook shiprec.cpy --data shipments.dat --out out.jsonl
    traderadar sample   --out-dir examples
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from traderadar import __version__
from traderadar.batch import run_batch
from traderadar.copybook.layout import build_layout
from traderadar.copybook.parser import parse_copybook
from traderadar.decoder import RecordDecoder
from traderadar.errors import TradeRadarError
from traderadar.samples import generate_sample


def _load_layout(args: argparse.Namespace):
    text = Path(args.copybook).read_text(encoding="utf-8")
    fixed = {"auto": None, "fixed": True, "free": False}[args.format]
    return build_layout(parse_copybook(text, fixed=fixed), record=args.record)


def cmd_layout(args: argparse.Namespace) -> int:
    print(_load_layout(args).describe())
    return 0


def cmd_decode(args: argparse.Namespace) -> int:
    layout = _load_layout(args)
    decoder = RecordDecoder(layout, codepage=args.codepage)
    out = Path(args.out)
    fmt = args.output_format or ("csv" if out.suffix.lower() == ".csv" else "jsonl")
    rejects = Path(args.rejects) if args.rejects else out.with_name(out.stem + ".rejects.jsonl")
    summary = run_batch(decoder, args.data, out, rejects, fmt, limit=args.limit)
    print(summary.report())
    print(f"\nValid records  -> {out}")
    print(f"Rejected       -> {rejects}")
    if args.summary_json:
        Path(args.summary_json).write_text(json.dumps(summary.as_dict(), indent=2), encoding="utf-8")
    if summary.rejected and args.fail_on_reject:
        return 2
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    cpy, dat = generate_sample(args.out_dir, args.records, args.defects, args.seed, args.codepage)
    print(f"Copybook : {cpy}")
    print(f"Data     : {dat}  ({args.records} records, {args.defects} with defects)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="traderadar",
        description="Decode legacy mainframe trade records and compute import landed costs (UK first).",
    )
    parser.add_argument("--version", action="version", version=f"traderadar {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def copybook_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--copybook", required=True, help="path to the COBOL copybook")
        p.add_argument("--record", help="01-level record name (default: first)")
        p.add_argument("--format", choices=["auto", "fixed", "free"], default="auto",
                       help="copybook source format (default: auto-detect)")

    p_layout = sub.add_parser("layout", help="print field offsets and lengths")
    copybook_args(p_layout)
    p_layout.set_defaults(func=cmd_layout)

    p_decode = sub.add_parser("decode", help="decode a fixed-length data file")
    copybook_args(p_decode)
    p_decode.add_argument("--data", required=True, help="binary data file")
    p_decode.add_argument("--out", required=True, help="output file (.jsonl or .csv)")
    p_decode.add_argument("--output-format", choices=["jsonl", "csv"])
    p_decode.add_argument("--rejects", help="rejects file (default: <out>.rejects.jsonl)")
    p_decode.add_argument("--codepage", default="cp037", help="EBCDIC code page (default cp037)")
    p_decode.add_argument("--limit", type=int, help="stop after N records")
    p_decode.add_argument("--summary-json", help="also write the summary as JSON")
    p_decode.add_argument("--fail-on-reject", action="store_true",
                          help="exit with code 2 if any record is rejected (for batch jobs)")
    p_decode.set_defaults(func=cmd_decode)

    p_sample = sub.add_parser("sample", help="generate synthetic shipment sample data")
    p_sample.add_argument("--out-dir", default="examples")
    p_sample.add_argument("--records", type=int, default=50)
    p_sample.add_argument("--defects", type=int, default=4)
    p_sample.add_argument("--seed", type=int, default=42)
    p_sample.add_argument("--codepage", default="cp037")
    p_sample.set_defaults(func=cmd_sample)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (TradeRadarError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
