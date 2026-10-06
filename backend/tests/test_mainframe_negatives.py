"""Negative / false-positive tests for mainframe support.

These pin the corrected behavior after the review:

* An ordinary application log that merely mentions a mainframe-ish token in
  prose (e.g. "the new ASRA module shipped") must NOT raise a detection,
  because such a log routes to the plain-text parser and the single-critical
  pass only scans entries surfaced by the mainframe parser.
* The SQLCODE rules must match the exact code only: ``SQLCODE -9110`` must not
  match the ``-911`` rule, nor ``SQLCODE -2040`` the ``-204`` rule.
* A time-separated later occurrence of an abend already reported in a spike
  still produces its own CRITICAL MESSAGE detection (window-scoped dedup).
"""

from app.detection import analyze_text
from app.knowledge_base import KnowledgeBase
from app.parsers import parse_logs


# --- prose mentioning mainframe tokens must not fire -----------------------

def test_prose_mentioning_asra_yields_no_detection(temp_kb_path):
    text = (
        "2024-06-01 10:00:00 INFO starting the release pipeline\n"
        "2024-06-01 10:00:01 INFO the new ASRA module shipped today\n"
        "2024-06-01 10:00:02 INFO deployment finished cleanly\n"
    )
    entries = parse_logs(text)
    # The log routes to the plain-text parser, not the mainframe parser.
    assert all(e.source_format == "text" for e in entries)
    result = analyze_text(entries, kb=KnowledgeBase.load())
    assert result.detections == []


def test_prose_mentioning_abend_and_cics_tokens_yields_no_detection(temp_kb_path):
    text = (
        "2024-06-01 10:00:00 INFO S806 is the exit number on the highway\n"
        "2024-06-01 10:00:01 INFO please follow the AEIX naming convention\n"
        "2024-06-01 10:00:02 INFO call the CAEIO9 helper routine\n"
    )
    entries = parse_logs(text)
    assert all(e.source_format == "text" for e in entries)
    result = analyze_text(entries, kb=KnowledgeBase.load())
    assert result.detections == []


# --- SQLCODE exact-code matching (no prefix mis-match) ---------------------

def test_sqlcode_9110_does_not_match_911_rule(temp_kb_path):
    kb = KnowledgeBase.load()
    # Direct rule-matching: the longer code must not match the -911 rule.
    matched = {r.id for r in kb.match("SQLCODE -9110")}
    assert "db2_sqlcode_deadlock_timeout" not in matched

    # End-to-end through the mainframe parser: a lone -9110 line (an undefined
    # code to our KB) should raise no critical detection for the -911 rule.
    text = "2024-06-01 10:42:05 DSNT408I SQLCODE = -9110, some custom app sqlcode"
    entries = parse_logs(text, fmt="mainframe")
    result = analyze_text(entries, kb=kb)
    assert all(
        d.matched_signature != "db2_sqlcode_deadlock_timeout"
        for d in result.detections
    )


def test_sqlcode_2040_does_not_match_204_rule(temp_kb_path):
    kb = KnowledgeBase.load()
    matched = {r.id for r in kb.match("SQLCODE -2040")}
    assert "db2_sqlcode_object_not_defined" not in matched


# --- window-scoped dedup keeps a later distinct occurrence -----------------

def test_later_abend_after_spike_still_detected(temp_kb_path):
    """A burst of S0C7 at 10:00 and a lone S0C7 at 18:00 both surface."""
    lines = [f"10.00.0{i} JOB1 IEF450I MYJOB STEP01 - ABEND=S0C7" for i in range(5)]
    lines.append("18.00.00 JOB1 IEF450I MYJOB STEP02 - ABEND=S0C7")
    text = "\n".join(lines)
    entries = parse_logs(text, fmt="mainframe")
    result = analyze_text(entries, kb=KnowledgeBase.load())

    spike = [
        d
        for d in result.detections
        if d.type == "ERROR SPIKE" and d.matched_signature == "zos_abend_s0c7"
    ]
    assert len(spike) == 1
    assert spike[0].error_count == 5

    # The 18:00 occurrence is distinct from the 10:00 spike window, so it still
    # produces its own CRITICAL MESSAGE rather than being silently dropped.
    later = [
        d
        for d in result.detections
        if d.type == "CRITICAL MESSAGE" and d.matched_signature == "zos_abend_s0c7"
    ]
    assert len(later) == 1
    assert later[0].error_count == 1
    assert later[0].peak_time is not None
    assert later[0].peak_time.hour == 18
