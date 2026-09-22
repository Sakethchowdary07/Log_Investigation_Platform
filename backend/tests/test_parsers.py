from datetime import datetime

from app.parsers import parse_logs, parse_text_line, parse_json_line


def test_plain_text_parsing_levels_and_timestamp():
    text = (
        "2024-06-01 10:42:03 ERROR DB timeout while acquiring connection\n"
        "2024-06-01 10:42:10 INFO all good\n"
    )
    entries = parse_logs(text)
    assert len(entries) == 2
    assert entries[0].level == "ERROR"
    assert entries[0].timestamp == datetime(2024, 6, 1, 10, 42, 3)
    assert "DB timeout" in entries[0].message
    assert entries[1].level == "INFO"


def test_level_defaults_to_info_when_absent():
    entry = parse_text_line("just some text without a level", 1)
    assert entry.level == "INFO"
    assert entry.timestamp is None


def test_hh_mm_ampm_timestamp():
    entry = parse_text_line("10:42 AM ERROR something failed", 1)
    assert entry.timestamp is not None
    assert entry.timestamp.hour == 10 and entry.timestamp.minute == 42
    assert entry.level == "ERROR"


def test_json_lines_parsing():
    text = (
        '{"timestamp": "2024-06-01T10:42:03", "level": "error", "message": "pool exhausted"}\n'
        '{"time": "2024-06-01T10:42:10", "severity": "INFO", "msg": "ok"}\n'
    )
    entries = parse_logs(text)
    assert len(entries) == 2
    assert entries[0].level == "ERROR"
    assert entries[0].message == "pool exhausted"
    assert entries[0].timestamp == datetime(2024, 6, 1, 10, 42, 3)
    assert entries[1].level == "INFO"
    assert entries[1].message == "ok"


def test_json_epoch_timestamp():
    entry = parse_json_line('{"ts": 1717238523, "level": "ERROR", "msg": "boom"}', 1)
    assert entry.timestamp is not None
    assert entry.level == "ERROR"


def test_robust_to_garbage_lines():
    text = (
        "!!!! not a log line ###\n"
        "\n"
        "2024-06-01 10:42:03 ERROR real error\n"
        "{broken json here\n"
    )
    entries = parse_logs(text)
    # blank line skipped, three real lines kept
    assert len(entries) == 3
    levels = [e.level for e in entries]
    assert "ERROR" in levels


def test_mixed_json_and_text_treated_as_text():
    text = (
        '{"level":"ERROR","msg":"a"}\n'
        "2024-06-01 10:42:03 ERROR plain line\n"
    )
    entries = parse_logs(text)
    assert len(entries) == 2
