"""Log parsers.

``parse_logs`` auto-detects whether the input is JSON-lines or plain text and
normalizes each line into a :class:`~app.models.LogEntry`. Both parsers are
robust to mixed and garbage lines: an unparseable line still produces an entry
(defaulting to level ``INFO`` and no timestamp) so nothing is silently dropped.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, time as dtime
from typing import Optional

from .models import LogEntry

# Levels we recognize, ordered so longer / more specific tokens win.
_LEVELS = ["CRITICAL", "FATAL", "ERROR", "WARNING", "WARN", "INFO", "DEBUG", "TRACE"]
_LEVEL_ALIASES = {"WARNING": "WARN"}

_LEVEL_RE = re.compile(
    r"\b(" + "|".join(_LEVELS) + r")\b",
    re.IGNORECASE,
)

# Timestamp patterns, tried in order. Each maps a regex to a strptime format
# (or a callable). We support ISO8601, `YYYY-MM-DD HH:MM:SS`, `HH:MM:SS`, and
# `HH:MM AM/PM`.
_TS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"),
        "iso",
    ),
    (re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"), "%Y-%m-%d %H:%M:%S"),
    (re.compile(r"\b(\d{1,2}:\d{2}:\d{2})\b"), "%H:%M:%S"),
    (re.compile(r"\b(\d{1,2}:\d{2}\s?[AaPp][Mm])\b"), "%I:%M %p"),
]


def _parse_timestamp(text: str) -> Optional[datetime]:
    """Best-effort extraction of a leading/embedded timestamp from a line."""
    for pattern, fmt in _TS_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        value = m.group(1).strip()
        try:
            if fmt == "iso":
                return _parse_iso(value)
            parsed = datetime.strptime(value.upper().replace(" AM", " AM").replace(" PM", " PM"), fmt)
            return parsed
        except ValueError:
            continue
    return None


def _parse_iso(value: str) -> Optional[datetime]:
    v = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def _extract_level(text: str) -> str:
    m = _LEVEL_RE.search(text)
    if not m:
        return "INFO"
    token = m.group(1).upper()
    return _LEVEL_ALIASES.get(token, token)


def _coerce_ts(value: object) -> Optional[datetime]:
    """Convert a JSON timestamp field into a datetime, best effort."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str):
        parsed = _parse_iso(value)
        if parsed is not None:
            return parsed
        return _parse_timestamp(value)
    return None


def _looks_like_jsonl(text: str) -> bool:
    """Heuristic: are most non-empty lines JSON objects?"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    json_like = 0
    sample = lines[:50]
    for ln in sample:
        if ln.startswith("{") and ln.endswith("}"):
            try:
                json.loads(ln)
                json_like += 1
            except json.JSONDecodeError:
                pass
    return json_like >= max(1, len(sample) // 2)


def parse_json_line(line: str, source_line: int) -> LogEntry:
    raw = line.rstrip("\n")
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        # Fall back to plain-text handling for a stray non-JSON line.
        return parse_text_line(line, source_line)
    if not isinstance(obj, dict):
        return parse_text_line(line, source_line)

    ts = None
    for key in ("timestamp", "time", "@timestamp", "ts", "datetime"):
        if key in obj:
            ts = _coerce_ts(obj[key])
            if ts is not None:
                break

    level = None
    for key in ("level", "severity", "levelname", "loglevel"):
        if key in obj and obj[key]:
            level = str(obj[key]).upper()
            break
    level = _LEVEL_ALIASES.get(level or "", level) or "INFO"

    message = ""
    for key in ("message", "msg", "text", "log"):
        if key in obj and obj[key] is not None:
            message = str(obj[key])
            break
    if not message:
        # Fall back to the whole object as the message.
        message = raw

    return LogEntry(
        timestamp=ts,
        level=level,
        message=message,
        raw=raw,
        source_line=source_line,
    )


def parse_text_line(line: str, source_line: int) -> LogEntry:
    raw = line.rstrip("\n")
    ts = _parse_timestamp(raw)
    level = _extract_level(raw)
    return LogEntry(
        timestamp=ts,
        level=level,
        message=raw.strip(),
        raw=raw,
        source_line=source_line,
    )


def parse_logs(text: str) -> list[LogEntry]:
    """Parse a full log document into normalized entries.

    Auto-detects JSON-lines vs plain text. Blank lines are skipped.
    """
    entries: list[LogEntry] = []
    use_json = _looks_like_jsonl(text)
    for idx, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if use_json:
            entries.append(parse_json_line(line, idx))
        else:
            entries.append(parse_text_line(line, idx))
    return entries
