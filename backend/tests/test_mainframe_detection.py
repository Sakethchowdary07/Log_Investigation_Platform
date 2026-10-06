"""Detection tests for the single-critical mainframe mode.

These parse the REAL sample files and assert concrete matched cause, related
errors, and the FULL ordered suggested_steps for the SQLCODE -911, S0C7, and
ASRA cases, so the tests fail if the implementation or the KB regresses. They
mirror test_detection.py: load samples via samples_dir, parse_logs(), then
analyze_text(entries, kb=KnowledgeBase.load()) under the temp_kb_path fixture
(which copies the shipped YAML, now including the mainframe rules).
"""

from app.detection import analyze_text
from app.knowledge_base import KnowledgeBase
from app.parsers import parse_logs


def _load(samples_dir, name):
    return (samples_dir / name).read_text(encoding="utf-8")


def _detection_for(result, signature):
    for d in result.detections:
        if d.matched_signature == signature:
            return d
    raise AssertionError(f"no detection with signature {signature!r}")


# --- SQLCODE -911 (DB2 deadlock/timeout) ----------------------------------

def test_db2_sqlcode_911_single_critical(temp_kb_path, samples_dir):
    text = _load(samples_dir, "db2-sqlcode.log")
    entries = parse_logs(text)
    kb = KnowledgeBase.load()
    result = analyze_text(entries, filename="db2-sqlcode.log", kb=kb)

    det = _detection_for(result, "db2_sqlcode_deadlock_timeout")
    assert det.type == "CRITICAL MESSAGE"
    assert det.confidence == 1.0
    assert det.peak_time is not None
    assert det.possible_cause == (
        "DB2 detected a deadlock or lock timeout; the current unit of work was "
        "rolled back (-911) or not rolled back (-913)."
    )
    assert det.related_errors == [
        "Deadlock",
        "Lock timeout",
        "Unit of work rolled back",
    ]
    assert det.suggested_steps == [
        "Identify the contending threads/transactions holding incompatible locks around the failure time.",
        "Review commit frequency and lock scope; shorten units of work and commit more often.",
        "Check access paths and indexing so rows are locked consistently and briefly.",
        "Retry the transaction after rollback where the application design allows it.",
    ]


# --- S0C7 (z/OS data exception) -------------------------------------------

def test_zos_abend_s0c7_detection(temp_kb_path, samples_dir):
    text = _load(samples_dir, "mainframe-joblog.log")
    entries = parse_logs(text)
    kb = KnowledgeBase.load()
    result = analyze_text(entries, filename="mainframe-joblog.log", kb=kb)

    det = _detection_for(result, "zos_abend_s0c7")
    assert det.type == "CRITICAL MESSAGE"
    assert det.confidence == 1.0
    assert det.possible_cause == (
        "A data exception: a decimal/arithmetic operation ran on fields "
        "containing invalid (non-numeric or improperly initialized) "
        "packed/zoned data."
    )
    assert det.related_errors == [
        "Data exception",
        "Invalid numeric data",
        "Uninitialized field",
    ]
    assert det.suggested_steps == [
        "Identify the failing instruction and the numeric field it operated on using the dump offset.",
        "Check that the field is initialized and contains valid packed/zoned numeric data before arithmetic.",
        "Validate input data and move layouts; a bad file record or redefinition is a common source.",
    ]


def test_joblog_also_flags_ich408i(temp_kb_path, samples_dir):
    """The RACF access line is INFO level yet still raises a critical detection."""
    text = _load(samples_dir, "mainframe-joblog.log")
    entries = parse_logs(text)
    kb = KnowledgeBase.load()
    result = analyze_text(entries, filename="mainframe-joblog.log", kb=kb)

    det = _detection_for(result, "zos_msg_ich408i")
    assert det.type == "CRITICAL MESSAGE"
    assert det.possible_cause.startswith(
        "The security product (RACF) denied access to a protected resource"
    )


# --- ASRA (CICS program check) --------------------------------------------

def test_cics_abend_asra_detection(temp_kb_path, samples_dir):
    text = _load(samples_dir, "cics-abend.log")
    entries = parse_logs(text)
    kb = KnowledgeBase.load()
    result = analyze_text(entries, filename="cics-abend.log", kb=kb)

    det = _detection_for(result, "cics_abend_asra")
    assert det.type == "CRITICAL MESSAGE"
    assert det.confidence == 1.0
    # Two lines carry ASRA in this sample.
    assert det.error_count == 2
    assert det.possible_cause == (
        "A program check (hardware program interrupt) occurred in a CICS "
        "transaction, equivalent to an S0Cx in a program running under CICS "
        "(data, protection, or addressing exception)."
    )
    assert det.related_errors == [
        "Program check",
        "ASRA",
        "Transaction abend",
    ]
    assert det.suggested_steps == [
        "Use the CICS transaction dump to find the failing program, offset, and interrupt type.",
        "Map the program-interrupt code to the underlying cause (e.g. data vs protection exception) and inspect that logic.",
        "Check for uninitialized or invalid data, bad pointers, or storage overlays in the failing program.",
    ]


# --- single lone critical message below the spike threshold ----------------

def test_lone_sqlcode_911_below_threshold(temp_kb_path):
    """A single SQLCODE -911 line (no burst) still triggers a detection."""
    text = "2024-06-01 10:42:05 DSNT408I SQLCODE = -911, deadlock, unit of work rolled back"
    entries = parse_logs(text)
    kb = KnowledgeBase.load()
    result = analyze_text(entries, kb=kb)

    # Below the spike threshold of 5, there is no ERROR SPIKE...
    assert all(d.type != "ERROR SPIKE" for d in result.detections)
    # ...but the single-critical pass raises exactly one detection.
    assert len(result.detections) == 1
    det = result.detections[0]
    assert det.type == "CRITICAL MESSAGE"
    assert det.matched_signature == "db2_sqlcode_deadlock_timeout"
    assert det.error_count == 1
    assert det.confidence == 1.0


def test_lone_s0c7_below_threshold(temp_kb_path):
    """A single S0C7 abend line (no burst) still triggers a detection."""
    text = "IEF450I MYJOB STEP01 - ABEND=S0C7"
    entries = parse_logs(text)
    kb = KnowledgeBase.load()
    result = analyze_text(entries, kb=kb)

    assert len(result.detections) == 1
    det = result.detections[0]
    assert det.type == "CRITICAL MESSAGE"
    assert det.matched_signature == "zos_abend_s0c7"
    assert det.error_count == 1
