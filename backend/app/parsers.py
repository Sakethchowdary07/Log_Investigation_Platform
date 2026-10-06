"""Log parsers.

``parse_logs`` auto-detects the input format and normalizes each line into a
:class:`~app.models.LogEntry`. Three formats are recognized, tried in this
order: JSON-lines, z/OS mainframe logs (SYSLOG / OPERLOG / JES job logs), and
plain text (the default). All parsers are robust to mixed and garbage lines: an
unparseable line still produces an entry (defaulting to level ``INFO`` and no
timestamp) so nothing is silently dropped.

The mainframe parser surfaces the significant codes (z/OS message IDs,
abend/completion codes, CICS transaction abend codes, and DB2 SQLCODEs/reason
codes) as plain tokens inside ``LogEntry.message`` so the case-insensitive
knowledge-base regexes can match them (for example ``S0C7``, ``U4038``,
``ASRA``, ``IEF450I``, ``SQLCODE -911``).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, time as dtime, timedelta
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


# --------------------------------------------------------------------------
# Mainframe (z/OS) parsing
# --------------------------------------------------------------------------
#
# These patterns recognize the significant tokens found in z/OS SYSLOG /
# OPERLOG / JES job-log lines and surface them into ``LogEntry.message`` so the
# knowledge base can match them.

# z/OS message ID: 3-4 uppercase letters, digits, trailing action-code letter.
# Examples: IEF450I, IEC141I, ICH408I, IRA200E, IEA995I.
_MF_MSGID_RE = re.compile(r"\b([A-Z]{3,4}\d{2,5}[A-Z])\b")

# z/OS abend / system completion code: System (Snnn, 3 hex digits) or User
# (Unnnn, up to 4 decimal digits). Examples: S0C7, S806, SB37, U4038, U0778.
# The code must contain at least one DIGIT so a plain English word following
# "User"/"System" (e.g. "User ABCD") is not misread as a completion code; real
# z/OS completion codes always include digits.
_MF_ABEND_RE = re.compile(
    r"\b([SU])(?:ystem|ser)?[= ]?((?=[0-9A-F]{3,4}\b)[0-9A-F]*\d[0-9A-F]*)\b",
    re.IGNORECASE,
)
# Simpler form that catches the common inline token Snnn / Unnnn directly.
_MF_ABEND_TOKEN_RE = re.compile(r"\b(S[0-9A-F]{3}|U\d{3,4})\b")

# CICS 4-character transaction abend code, typically following ABCODE= or the
# word "abend". Examples: ASRA, ASRB, AICA, AKEA, AEY9, AEID, AEXR.
_MF_CICS_RE = re.compile(r"\b(?:ABCODE[=: ]+|abend(?:ed)?(?:\s+(?:code|with))?\s+)([A-Z][A-Z0-9]{3})\b", re.IGNORECASE)

# DB2 SQLCODE: the keyword followed by a (usually negative) integer. We keep
# the sign so "SQLCODE -911" is surfaced verbatim.
_MF_SQLCODE_RE = re.compile(r"\bSQLCODE[=: ]*(-?\d+)\b", re.IGNORECASE)

# DB2 / z/OS reason code family: 8 hex chars, often shown as 00Cxxxxx or
# 00Dxxxxx. Surfaced verbatim; we do NOT interpret specific sub-values.
_MF_REASON_RE = re.compile(r"\b(00[CD][0-9A-F]{5})\b", re.IGNORECASE)

# SYSLOG-style leading jobname/control token: the hardcopy log prefixes a line
# with a record type and a jobname, e.g. "N 0000000 SYSNAME ...". A simple
# jobname-ish leading token helps the heuristic without false-matching prose.
_MF_SYSLOG_PREFIX_RE = re.compile(r"^[NMWEX]\s+\d{7}\b")

# Timestamp forms peculiar to mainframe logs.
# SYSLOG hardcopy time: HH.MM.SS (dot separated). Also accept HH:MM:SS.
_MF_TIME_DOT_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{2})\b")
# Julian date: yyyy.ddd (year dot day-of-year).
_MF_JULIAN_RE = re.compile(r"\b(\d{4})\.(\d{3})\b")
# Bare HHMM (4 digits) with no separators, e.g. a leading "1042" column.
_MF_HHMM_RE = re.compile(r"(?<!\d)(\d{2})(\d{2})(?!\d)")

# Message-ID action-code suffix letters that denote a severity worth flagging.
# A = immediate action, D = decision, E = eventual action, W = wait.
_MF_ACTION_SUFFIXES = {"A", "D", "E", "W"}


def _parse_mainframe_timestamp(text: str) -> Optional[datetime]:
    """Best-effort timestamp extraction using mainframe conventions.

    Tries, in order: HH.MM.SS, a Julian date (yyyy.ddd) optionally paired with
    an HH.MM.SS/HH:MM:SS time, the shared ``_parse_timestamp`` (HH:MM:SS, ISO),
    and finally a bare HHMM column. Returns None when nothing is found.
    """
    # Julian date yyyy.ddd (+ optional time) -> real date where possible.
    jm = _MF_JULIAN_RE.search(text)
    if jm:
        year, doy = int(jm.group(1)), int(jm.group(2))
        hh = mm = ss = 0
        tm = _MF_TIME_DOT_RE.search(text) or re.search(r"\b(\d{2}):(\d{2}):(\d{2})\b", text)
        if tm:
            hh, mm, ss = int(tm.group(1)), int(tm.group(2)), int(tm.group(3))
        try:
            base = datetime(year, 1, 1, hh, mm, ss)
            return base.replace(month=1, day=1) + timedelta(days=doy - 1)
        except (ValueError, OverflowError):
            pass

    # HH.MM.SS hardcopy time.
    tm = _MF_TIME_DOT_RE.search(text)
    if tm:
        try:
            return datetime.strptime(f"{tm.group(1)}:{tm.group(2)}:{tm.group(3)}", "%H:%M:%S")
        except ValueError:
            pass

    # Fall back to the shared parser (handles HH:MM:SS and ISO forms).
    shared = _parse_timestamp(text)
    if shared is not None:
        return shared

    # Last resort: a bare leading HHMM column (e.g. "1042 ...").
    hm = _MF_HHMM_RE.match(text.strip())
    if hm:
        hh, mm = int(hm.group(1)), int(hm.group(2))
        if hh < 24 and mm < 60:
            try:
                return datetime.combine(datetime.min.date(), dtime(hh, mm))
            except ValueError:
                pass
    return None


def _normalize_abend(raw_code: str) -> str:
    """Normalize an abend code to its canonical token (e.g. 'S0C7', 'U4038').

    System codes keep 3 hex digits; user codes are shown with up to 4 digits.
    """
    kind = raw_code[0].upper()
    digits = raw_code[1:]
    if kind == "S":
        return f"S{digits.upper()[-3:].zfill(3)}"
    return f"U{digits[-4:].zfill(4)}"


def _mainframe_tokens(line: str) -> list[str]:
    """Extract the significant mainframe codes from a line as plain tokens."""
    tokens: list[str] = []

    for m in _MF_MSGID_RE.finditer(line):
        tokens.append(m.group(1).upper())

    # Explicit abend tokens (Snnn/Unnnn) and "System xxx" / "ABEND=Snnn" forms.
    for m in _MF_ABEND_TOKEN_RE.finditer(line):
        tokens.append(_normalize_abend(m.group(1)))
    for m in _MF_ABEND_RE.finditer(line):
        tokens.append(_normalize_abend(f"{m.group(1)}{m.group(2)}"))

    for m in _MF_CICS_RE.finditer(line):
        tokens.append(m.group(1).upper())

    sql = _MF_SQLCODE_RE.search(line)
    if sql:
        # Surface verbatim so rules like "SQLCODE -911" match directly.
        tokens.append(f"SQLCODE {sql.group(1)}")

    for m in _MF_REASON_RE.finditer(line):
        tokens.append(m.group(1).upper())

    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def _mainframe_level(line: str, tokens: list[str]) -> str:
    """Decide a level for a mainframe line.

    ERROR/CRITICAL when the line carries an abend code, a CICS abend code, a
    negative SQLCODE, or a message ID with an action/eventual-action/wait
    severity suffix; otherwise fall back to any explicit level token, else INFO.
    """
    for t in tokens:
        if _MF_ABEND_TOKEN_RE.fullmatch(t) or re.fullmatch(r"[SU][0-9A-F]{3,4}", t):
            return "CRITICAL"
    # Negative SQLCODE is an error condition.
    sql = _MF_SQLCODE_RE.search(line)
    if sql and sql.group(1).startswith("-"):
        return "ERROR"
    # CICS 4-char abend token present (from an abend/ABCODE context).
    if _MF_CICS_RE.search(line):
        return "ERROR"
    # Message ID with an action-code severity suffix.
    for m in _MF_MSGID_RE.finditer(line):
        if m.group(1)[-1].upper() in _MF_ACTION_SUFFIXES:
            return "ERROR"
    # Otherwise honor any explicit level token on the line.
    return _extract_level(line)


def _line_has_mainframe_marker(line: str) -> bool:
    """True if a single line carries a recognizable mainframe marker."""
    return bool(
        _MF_MSGID_RE.search(line)
        or _MF_ABEND_TOKEN_RE.search(line)
        or _MF_CICS_RE.search(line)
        or _MF_SQLCODE_RE.search(line)
        or _MF_SYSLOG_PREFIX_RE.search(line)
    )


def _looks_like_mainframe(text: str) -> bool:
    """Heuristic: do the majority of non-empty lines carry mainframe markers?

    Kept conservative so plain-text and JSON samples keep routing to their
    current parsers. Requires at least half of a sample of lines to contain a
    z/OS message ID, abend token, CICS abend, SQLCODE, or SYSLOG prefix.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    sample = lines[:50]
    marked = sum(1 for ln in sample if _line_has_mainframe_marker(ln))
    return marked >= max(1, (len(sample) + 1) // 2)


def _is_mainframe_continuation(line: str) -> bool:
    """True if a line looks like a continuation of a preceding mainframe message.

    Continuation lines are indented and do not begin their own message (no
    leading message ID, abend token, SYSLOG prefix, or timestamp).
    """
    if not line or not line[0].isspace():
        return False
    stripped = line.strip()
    if not stripped:
        return False
    if _MF_MSGID_RE.match(stripped) or _MF_ABEND_TOKEN_RE.match(stripped):
        return False
    if _MF_SYSLOG_PREFIX_RE.match(stripped):
        return False
    if _parse_mainframe_timestamp(stripped) is not None and _MF_TIME_DOT_RE.match(stripped):
        return False
    return True


def parse_mainframe_line(line: str, source_line: int) -> LogEntry:
    """Parse a single mainframe log line into a :class:`LogEntry`.

    Significant codes are surfaced into ``message`` as plain tokens; the full
    original line is preserved in ``raw``.
    """
    raw = line.rstrip("\n")
    tokens = _mainframe_tokens(raw)
    ts = _parse_mainframe_timestamp(raw)
    level = _mainframe_level(raw, tokens)

    # Build a message that carries the significant tokens up front (so KB
    # regexes match) followed by the stripped original text for context.
    stripped = raw.strip()
    if tokens:
        prefix = " ".join(tokens)
        message = f"{prefix} | {stripped}" if stripped else prefix
    else:
        message = stripped

    return LogEntry(
        timestamp=ts,
        level=level,
        message=message,
        raw=raw,
        source_line=source_line,
        source_format="mainframe",
    )


def parse_mainframe(text: str) -> list[LogEntry]:
    """Parse a full mainframe log document into normalized entries.

    Continuation lines are appended to the preceding entry's message (and raw)
    rather than producing bare entries. Best effort: single-line parsing is
    never broken by this.
    """
    entries: list[LogEntry] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if entries and _is_mainframe_continuation(line):
            prev = entries[-1]
            cont = line.strip()
            prev.message = f"{prev.message} {cont}".strip()
            prev.raw = f"{prev.raw}\n{line.rstrip(chr(10))}"
            # A continuation may carry codes the opening line lacked; surface
            # any new tokens so the KB can still match them. Use an exact-token
            # comparison (not a substring test) so a genuinely-new token is not
            # skipped merely because it is a substring of existing text.
            existing_tokens = set(_mainframe_tokens(prev.message))
            new_tokens = [t for t in _mainframe_tokens(cont) if t not in existing_tokens]
            if new_tokens:
                prev.message = f"{' '.join(new_tokens)} | {prev.message}"
            continue
        entries.append(parse_mainframe_line(line, idx))
    return entries


def parse_logs(text: str, fmt: Optional[str] = None) -> list[LogEntry]:
    """Parse a full log document into normalized entries.

    Auto-detects the format when ``fmt`` is None: JSON-lines first, then
    mainframe, then plain text (the default). Pass ``fmt`` as ``'json'``,
    ``'mainframe'``, or ``'text'`` to force a parser. Blank lines are skipped.
    """
    if fmt is None:
        if _looks_like_jsonl(text):
            fmt = "json"
        elif _looks_like_mainframe(text):
            fmt = "mainframe"
        else:
            fmt = "text"

    if fmt == "mainframe":
        return parse_mainframe(text)

    entries: list[LogEntry] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if fmt == "json":
            entries.append(parse_json_line(line, idx))
        else:
            entries.append(parse_text_line(line, idx))
    return entries
