"""Parser tests for the z/OS mainframe log path.

These parse the REAL sample files shipped under backend/samples/ (not inline
strings only) and assert the mainframe parser path is taken and surfaces the
significant codes into LogEntry.message. Mirrors the style of test_parsers.py.
"""

from app.parsers import (
    parse_logs,
    parse_mainframe,
    parse_mainframe_line,
    _looks_like_mainframe,
)


def _load(samples_dir, name):
    return (samples_dir / name).read_text(encoding="utf-8")


def test_samples_route_to_mainframe_parser(samples_dir):
    """Each new sample is recognized as mainframe by the auto-detector."""
    for name in ("mainframe-joblog.log", "db2-sqlcode.log", "cics-abend.log"):
        text = _load(samples_dir, name)
        assert _looks_like_mainframe(text), f"{name} should route to the mainframe parser"


def test_joblog_surfaces_codes(samples_dir):
    text = _load(samples_dir, "mainframe-joblog.log")
    entries = parse_logs(text)
    messages = [e.message for e in entries]

    # The abend code is normalized to S0C7 and appears up front with its msgid.
    s0c7 = next(m for m in messages if "S0C7" in m)
    assert "IEF450I" in s0c7
    assert "S0C7" in s0c7
    # The abend line is flagged CRITICAL by the mainframe level logic.
    abend_entry = next(e for e in entries if "S0C7" in e.message)
    assert abend_entry.level == "CRITICAL"

    # Other JES/system message IDs are surfaced as tokens too.
    assert any("ICH408I" in m for m in messages)
    assert any("IEC141I" in m for m in messages)


def test_joblog_extracts_mainframe_dot_timestamp(samples_dir):
    """HH.MM.SS SYSLOG hardcopy time is parsed into a timestamp."""
    text = _load(samples_dir, "mainframe-joblog.log")
    entries = parse_logs(text)
    abend_entry = next(e for e in entries if "S0C7" in e.message)
    assert abend_entry.timestamp is not None
    assert (abend_entry.timestamp.hour, abend_entry.timestamp.minute) == (10, 42)


def test_db2_sample_surfaces_sqlcode_with_single_space(samples_dir):
    text = _load(samples_dir, "db2-sqlcode.log")
    entries = parse_logs(text)
    messages = [e.message for e in entries]

    # The parser normalizes 'SQLCODE = -911' to the token 'SQLCODE -911'.
    assert any("SQLCODE -911" in m for m in messages)
    assert any("SQLCODE -904" in m for m in messages)
    # The reason-code family token is surfaced verbatim (not interpreted).
    assert any("00C90084" in m for m in messages)

    sql_entry = next(e for e in entries if "SQLCODE -911" in e.message)
    assert sql_entry.level == "ERROR"


def test_cics_sample_surfaces_asra(samples_dir):
    text = _load(samples_dir, "cics-abend.log")
    entries = parse_logs(text)
    messages = [e.message for e in entries]
    assert any("ASRA" in m for m in messages)
    asra_entry = next(e for e in entries if "ASRA" in e.message)
    assert asra_entry.level == "ERROR"


def test_parse_mainframe_line_normalizes_abend_token():
    entry = parse_mainframe_line("IEF450I MYJOB STEP01 - ABEND=S0C7", 1)
    assert "IEF450I" in entry.message
    assert "S0C7" in entry.message
    # Tokens appear before the ' | ' separator, original line preserved after.
    assert entry.message.split(" | ")[0] == "IEF450I S0C7"
    assert entry.raw == "IEF450I MYJOB STEP01 - ABEND=S0C7"


def test_abend_regex_does_not_capture_english_word():
    """`User ABCD` must not be read as a user completion code `UABCD`.

    Real z/OS completion codes contain digits, so a plain hex-letter word that
    follows User/System is not surfaced as a token.
    """
    entry = parse_mainframe_line("MYLOG User ABCD abended the session", 1)
    assert "UABCD" not in entry.message
    # A genuine user abend with digits is still surfaced.
    entry2 = parse_mainframe_line("Job hit User 4038 completion code", 1)
    assert "U4038" in entry2.message


def test_mainframe_continuation_is_assembled(samples_dir):
    """A clearly-indented continuation line is appended to the prior entry."""
    text = (
        "IEF450I MYJOB STEP01 - ABEND=S0C7\n"
        "    data exception detail continuation line\n"
    )
    entries = parse_mainframe(text)
    assert len(entries) == 1
    assert "continuation line" in entries[0].message
    assert "S0C7" in entries[0].message
