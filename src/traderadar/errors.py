"""Exception hierarchy. Every error carries enough context to debug a bad record."""

from __future__ import annotations


class TradeRadarError(Exception):
    """Base class for all TradeRadar errors."""


class CopybookSyntaxError(TradeRadarError):
    """The copybook text could not be parsed."""

    def __init__(self, message: str, line: int | None = None) -> None:
        self.line = line
        prefix = f"line {line}: " if line is not None else ""
        super().__init__(prefix + message)


class UnsupportedFeatureError(TradeRadarError):
    """Valid COBOL that this version deliberately does not support yet."""

    def __init__(self, message: str, line: int | None = None) -> None:
        self.line = line
        prefix = f"line {line}: " if line is not None else ""
        super().__init__(prefix + message)


class DecodeError(TradeRadarError):
    """A field's bytes are not valid for its declared PIC/USAGE."""

    def __init__(
        self,
        message: str,
        field: str | None = None,
        offset: int | None = None,
        raw: bytes | None = None,
    ) -> None:
        self.field = field
        self.offset = offset
        self.raw = raw
        self.reason = message
        parts = [message]
        if field is not None:
            parts.append(f"field={field}")
        if offset is not None:
            parts.append(f"offset={offset}")
        if raw is not None:
            parts.append(f"raw={raw.hex().upper()}")
        super().__init__(" | ".join(parts))
