"""TradeRadar: decode legacy mainframe trade records and compute import landed costs.

Phase 1 (this package): a copybook-driven decoder for fixed-length mainframe
records (EBCDIC text, zoned decimal, COMP-3 packed decimal, COMP binary).
"""

from traderadar.copybook.parser import parse_copybook
from traderadar.copybook.layout import Layout, build_layout
from traderadar.decoder import RecordDecoder
from traderadar.errors import (
    CopybookSyntaxError,
    DecodeError,
    TradeRadarError,
    UnsupportedFeatureError,
)

__version__ = "0.1.0"

__all__ = [
    "parse_copybook",
    "build_layout",
    "Layout",
    "RecordDecoder",
    "TradeRadarError",
    "CopybookSyntaxError",
    "UnsupportedFeatureError",
    "DecodeError",
]
