from app.detection import SpikeDetector, analyze_text
from app.knowledge_base import KnowledgeBase
from app.parsers import parse_logs


def _load_sample(samples_dir, name):
    return (samples_dir / name).read_text(encoding="utf-8")


def test_spike_detected_for_plain_text_sample(temp_kb_path, samples_dir):
    text = _load_sample(samples_dir, "app.log")
    entries = parse_logs(text)
    kb = KnowledgeBase.load()
    result = analyze_text(entries, filename="app.log", kb=kb)

    assert result.total_entries > 0
    assert result.error_count >= 5
    assert len(result.detections) >= 1

    det = result.detections[0]
    assert det.type == "ERROR SPIKE"
    assert det.peak_time is not None
    assert det.peak_time.hour == 10 and det.peak_time.minute == 42
    assert det.possible_cause == "Database connection pool exhaustion"
    assert "DB timeout" in det.related_errors
    assert "Connection refused" in det.related_errors
    assert "HTTP 503" in det.related_errors
    assert det.suggested_steps[:3] == [
        "Check DB connections",
        "Check CPU",
        "Check connection pool",
    ]


def test_spike_detected_for_json_sample(temp_kb_path, samples_dir):
    text = _load_sample(samples_dir, "app.jsonl")
    entries = parse_logs(text)
    kb = KnowledgeBase.load()
    result = analyze_text(entries, filename="app.jsonl", kb=kb)
    assert len(result.detections) >= 1
    assert result.detections[0].possible_cause == "Database connection pool exhaustion"


def test_below_threshold_no_detection(temp_kb_path):
    text = (
        "2024-06-01 10:42:03 ERROR connection pool exhausted\n"
        "2024-06-01 10:42:05 ERROR DB timeout\n"
    )
    entries = parse_logs(text)
    kb = KnowledgeBase.load()
    result = analyze_text(entries, kb=kb)
    assert result.detections == []


def test_count_based_fallback_without_timestamps(temp_kb_path):
    lines = "\n".join("ERROR connection pool exhausted" for _ in range(6))
    entries = parse_logs(lines)
    kb = KnowledgeBase.load()
    result = analyze_text(entries, kb=kb)
    assert len(result.detections) == 1
    det = result.detections[0]
    assert det.window_start is None
    assert det.possible_cause == "Database connection pool exhaustion"


def test_spike_straddling_minute_boundary_is_detected(temp_kb_path):
    """Regression: a burst split across a fixed 60s bucket boundary must still
    be detected. Seven errors span 10:42:40-10:43:15 (four before the minute
    boundary, three after); with a fixed-bucket scheme neither half reaches the
    default threshold of 5, but the sliding window catches the burst.
    """
    text = "\n".join(
        [
            "2024-06-01 10:42:40 ERROR DB timeout while acquiring connection from pool",
            "2024-06-01 10:42:47 ERROR connection pool exhausted",
            "2024-06-01 10:42:53 ERROR Connection refused by database host",
            "2024-06-01 10:42:59 ERROR DB timeout: could not get connection",
            "2024-06-01 10:43:04 ERROR connection pool exhausted",
            "2024-06-01 10:43:09 ERROR Upstream returned HTTP 503",
            "2024-06-01 10:43:15 ERROR 503 Service Unavailable returned to client",
        ]
    )
    entries = parse_logs(text)
    kb = KnowledgeBase.load()
    result = analyze_text(entries, kb=kb)

    assert len(result.detections) == 1
    det = result.detections[0]
    # All seven errors are attributed to the single merged spike.
    assert det.error_count == 7
    assert det.possible_cause == "Database connection pool exhaustion"


def test_peak_time_breaks_ties_toward_onset(temp_kb_path):
    """Regression: when every second holds one error, the peak time should be
    the onset (earliest second), not the tail of the spike.
    """
    text = "\n".join(
        [
            "2024-06-01 10:42:03 ERROR DB timeout while acquiring connection",
            "2024-06-01 10:42:07 ERROR connection pool exhausted",
            "2024-06-01 10:42:11 ERROR Connection refused by database host",
            "2024-06-01 10:42:18 CRITICAL connection pool exhausted",
            "2024-06-01 10:42:24 ERROR Upstream returned HTTP 503",
            "2024-06-01 10:42:52 ERROR 503 Service Unavailable returned to client",
        ]
    )
    entries = parse_logs(text)
    kb = KnowledgeBase.load()
    result = analyze_text(entries, kb=kb)
    assert len(result.detections) == 1
    det = result.detections[0]
    assert det.peak_time is not None
    # Earliest error is 10:42:03; tie-break must pick the onset, not 10:42:52.
    assert (det.peak_time.hour, det.peak_time.minute, det.peak_time.second) == (10, 42, 3)


def test_deployment_correlations_default_empty(temp_kb_path, samples_dir):
    entries = parse_logs(_load_sample(samples_dir, "app.log"))
    kb = KnowledgeBase.load()
    result = analyze_text(entries, kb=kb)
    assert result.deployment_correlations == []
    assert result.detections[0].deployment_correlations == []
