"""Stream a fixed-length record file through the decoder.

Valid records go to JSON Lines or CSV; rejected records go to a separate
rejects file with record number, byte offset, raw hex and every field error,
so a support engineer can find and fix the bad data. Files are read in
chunks, so input size is not limited by memory.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import IO, Any, Iterator

from traderadar.decoder import RecordDecoder, flatten


@dataclass
class RawRecord:
    number: int  # 1-based
    offset: int  # byte offset in the file
    data: bytes


@dataclass
class BatchSummary:
    input_path: str
    record_length: int
    total: int = 0
    valid: int = 0
    rejected: int = 0
    with_warnings: int = 0
    trailing_bytes: int = 0
    reject_reasons: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "input": self.input_path,
            "record_length": self.record_length,
            "records_read": self.total,
            "records_valid": self.valid,
            "records_rejected": self.rejected,
            "records_with_warnings": self.with_warnings,
            "trailing_bytes": self.trailing_bytes,
            "reject_reasons": dict(sorted(self.reject_reasons.items(), key=lambda kv: -kv[1])),
        }

    def report(self) -> str:
        lines = [
            "TRADERADAR DECODE SUMMARY",
            "=" * 40,
            f"Input file        : {self.input_path}",
            f"Record length     : {self.record_length}",
            f"Records read      : {self.total}",
            f"Records valid     : {self.valid}",
            f"Records rejected  : {self.rejected}",
            f"With warnings     : {self.with_warnings}",
        ]
        if self.trailing_bytes:
            lines.append(f"Trailing bytes    : {self.trailing_bytes} (incomplete last record)")
        if self.reject_reasons:
            lines.append("Reject reasons:")
            for reason, count in self.as_dict()["reject_reasons"].items():
                lines.append(f"  {count:>6}  {reason}")
        return "\n".join(lines)


def iter_records(stream: IO[bytes], record_length: int, chunk_records: int = 4096) -> Iterator[RawRecord]:
    """Yield fixed-length records. A short final fragment is yielded as-is;
    callers detect it by ``len(record.data) < record_length``."""
    if record_length <= 0:
        raise ValueError("record_length must be positive")
    buffer = b""
    number = 0
    offset = 0
    chunk_size = record_length * chunk_records
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        buffer += chunk
        usable = len(buffer) - len(buffer) % record_length
        for start in range(0, usable, record_length):
            number += 1
            yield RawRecord(number, offset, buffer[start : start + record_length])
            offset += record_length
        buffer = buffer[usable:]
    if buffer:
        number += 1
        yield RawRecord(number, offset, buffer)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"cannot serialise {type(obj).__name__}")


def to_json(obj: Any) -> str:
    return json.dumps(obj, default=_json_default, ensure_ascii=False)


def run_batch(
    decoder: RecordDecoder,
    input_path: str | Path,
    output_path: str | Path,
    rejects_path: str | Path,
    output_format: str = "jsonl",
    limit: int | None = None,
) -> BatchSummary:
    if output_format not in ("jsonl", "csv"):
        raise ValueError("output_format must be 'jsonl' or 'csv'")
    input_path = Path(input_path)
    summary = BatchSummary(str(input_path), decoder.record_length)
    columns = decoder.columns()
    for target in (output_path, rejects_path):
        Path(target).parent.mkdir(parents=True, exist_ok=True)

    with (
        input_path.open("rb") as src,
        Path(output_path).open("w", encoding="utf-8", newline="") as out,
        Path(rejects_path).open("w", encoding="utf-8") as rej,
    ):
        writer = None
        if output_format == "csv":
            writer = csv.DictWriter(out, fieldnames=["_record"] + columns, extrasaction="ignore")
            writer.writeheader()

        for raw in iter_records(src, decoder.record_length):
            if limit is not None and summary.total >= limit:
                break
            if len(raw.data) < decoder.record_length:
                summary.trailing_bytes = len(raw.data)
                rej.write(to_json({
                    "record": raw.number, "offset": raw.offset,
                    "errors": [{"reason": f"incomplete record: {len(raw.data)} of "
                                f"{decoder.record_length} bytes"}],
                    "raw": raw.data.hex().upper(),
                }) + "\n")
                summary.rejected += 1
                summary.reject_reasons["incomplete record"] = (
                    summary.reject_reasons.get("incomplete record", 0) + 1
                )
                summary.total += 1
                continue

            summary.total += 1
            decoded = decoder.decode(raw.data)
            if decoded.warnings:
                summary.with_warnings += 1

            if not decoded.ok:
                summary.rejected += 1
                for issue in decoded.errors:
                    key = f"{issue.path.split('(')[0]}: {issue.reason}"
                    summary.reject_reasons[key] = summary.reject_reasons.get(key, 0) + 1
                rej.write(to_json({
                    "record": raw.number,
                    "offset": raw.offset,
                    "errors": [e.as_dict() for e in decoded.errors],
                    "raw": raw.data.hex().upper(),
                }) + "\n")
                continue

            summary.valid += 1
            if writer is not None:
                row = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in flatten(decoded.data).items()}
                row["_record"] = raw.number
                writer.writerow(row)
            else:
                payload: dict[str, Any] = {"_record": raw.number, **decoded.data}
                if decoded.warnings:
                    payload["_warnings"] = [w.as_dict() for w in decoded.warnings]
                out.write(to_json(payload) + "\n")

    return summary
