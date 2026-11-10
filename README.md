# TradeRadar

**Decode legacy mainframe trade records and compute import landed costs (UK first).**

A lot of trade and shipment data still lives in fixed-length mainframe files
described by COBOL copybooks: EBCDIC text, zoned decimals, COMP-3 packed
decimals and binary fields. Ordinary tools can't read these files. TradeRadar
reads them using the copybook as the schema, turns them into clean JSON or CSV,
and quarantines bad records with enough detail to fix them.

Phase 2 adds a landed-cost engine (customs value, duty, import VAT, FX), with a
general core and country rules as plug-ins, starting with the UK.

> All sample data in this repository is synthetic. It is not based on any real
> company's systems, layouts or records.

## Features (Phase 1)

- **Copybook parser.** Handles fixed format (sequence area, indicator column,
  ID area) and free format, and works out which one it has.
- **Supported COBOL:** levels 01–49, 77, 88; `PIC`; `USAGE` (DISPLAY, COMP,
  COMP-4, BINARY, COMP-5, COMP-3, PACKED-DECIMAL), including usage inherited
  from groups; `OCCURS n`; `REDEFINES`; `FILLER`;
  `SIGN LEADING/TRAILING [SEPARATE]`; 88-level `VALUE`/`THRU`.
- **Byte-exact decoders:**
  - EBCDIC text (cp037, cp500, cp1140, …)
  - zoned decimal, including ASCII overpunch for files that were converted as text
  - packed decimal
  - big-endian binary with the PIC truncation rule
- **Strict validation.** Bad data raises a clear error and is never guessed:
  spaces or low-values in numeric fields, invalid sign nibbles, digits beyond
  the PIC, negative values in unsigned fields.
- **Batch processing.** Reads the file as a stream, writes valid records and
  rejects separately (record number, byte offset, raw hex, every field error),
  produces a summary report, and has a `--fail-on-reject` exit code for batch
  jobs.
- **Unsupported features fail loudly.** `OCCURS DEPENDING ON`, `SYNC`,
  `RENAMES`, `COMP-1/2` and `P` scaling raise `UnsupportedFeatureError`
  instead of producing wrong output.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

traderadar sample --out-dir examples          # synthetic copybook + data file
traderadar layout --copybook examples/shiprec.cpy
traderadar decode --copybook examples/shiprec.cpy \
                  --data examples/shipments.dat --out out/shipments.jsonl
traderadar decode --copybook examples/shiprec.cpy \
                  --data examples/shipments.dat --out out/shipments.csv
pytest
```

Example layout listing:

```
LVL FIELD                              OFFSET   LEN  TYPE
05    SHP-FREIGHT-AMT                      28     5  PIC S9(7)V99 COMP-3
05    SHP-LINE                             44   159  GROUP OCCURS 3 (x53)
10      LIN-QUANTITY                       74     4  PIC S9(7) COMP-3
```

Example reject record:

```json
{"record": 37, "offset": 7452,
 "errors": [{"field": "SHP-FREIGHT-AMT", "offset": 27,
             "reason": "packed field contains EBCDIC spaces (uninitialised?)",
             "raw": "4040404040"}]}
```

Numbers are written to JSON as strings (`"2362.08"`) so decimal precision is
never lost to floating point.

## Design

```
copybook text ──► parser ──► Field tree ──► layout (offsets/sizes)
                                                 │
data file ──► iter_records ──► RecordDecoder ────┴──► valid JSONL/CSV
                                     │
                                     └──► rejects JSONL + summary
```

| Module | Responsibility |
|---|---|
| `copybook/pic.py` | PIC parsing, USAGE, storage sizes |
| `copybook/parser.py` | tokenizer and data-description parser |
| `copybook/layout.py` | offsets, REDEFINES overlays, OCCURS |
| `codecs.py` | byte-level encode/decode for each field type |
| `decoder.py` / `encoder.py` | record ⇄ Python dict |
| `batch.py` | streaming, rejects, summary |
| `cli.py` | command-line interface |

Decoder and trade logic are separate modules so each can be tested
independently.

## Roadmap

**Phase 1b: decoder depth**
- [ ] `OCCURS DEPENDING ON` (variable-length tables)
- [ ] Variable-length files with RDW (RECFM=VB)
- [ ] `SYNC` alignment, `COMP-1/COMP-2` (IBM hex float), `P` scaling
- [ ] Rules for choosing a REDEFINES view (e.g. by record type code)
- [ ] Multi-record-type files (header / detail / trailer) with trailer-count reconciliation
- [ ] cp1047 code page; continuation lines
- [ ] JSON → binary encoding from the CLI

**Phase 2: landed-cost engine (UK first)**
- [ ] Customs valuation by Incoterm (CIF basis)
- [ ] Duty expressions: ad valorem, specific, compound, MIN/MAX
- [ ] Import VAT calculation
- [ ] FX conversion with dated rates
- [ ] Effective-dated, versioned tariff rules with source provenance
- [ ] Per-shipment explanations of every calculation step
- [ ] Country rules as plug-ins: EU, India, US after the UK

**Phase 3: enterprise batch**
- [ ] Checkpoint/restart for large files
- [ ] Duplicate detection, configurable validation rules
- [ ] Performance benchmarks, parallel decoding
- [ ] Monitoring dashboard

## License

MIT. Not affiliated with or endorsed by HMRC; any landed-cost figures are estimates, not customs advice.
