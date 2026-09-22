"""Tests for deployment-timeline correlation (FEAT-003).

These exercise the real parsing + correlation logic (no mocks): the shipped
sample log and sample deployments CSV should correlate the 10:42 spike to the
10:39 deploy and append a "Review deployment at ..." suggested step. Omitting
the CSV must leave detections working with empty correlations.
"""

from __future__ import annotations

from app.correlation import correlate, parse_deployments
from app.detection import analyze_text
from app.knowledge_base import KnowledgeBase
from app.parsers import parse_logs


def _load(samples_dir, name):
    return (samples_dir / name).read_text(encoding="utf-8")


def test_parse_deployments_reads_sample_csv(samples_dir):
    events = parse_deployments(_load(samples_dir, "deployments.csv"))
    assert len(events) == 3
    near = [e for e in events if e.deployed_at.hour == 10 and e.deployed_at.minute == 39]
    assert len(near) == 1
    assert near[0].service == "orders-api"
    assert near[0].version == "v3.8.0"


def test_sample_logs_correlate_with_nearby_deploy(temp_kb_path, samples_dir):
    entries = parse_logs(_load(samples_dir, "app.log"))
    kb = KnowledgeBase.load()
    result = analyze_text(entries, filename="app.log", kb=kb)
    assert len(result.detections) >= 1

    events = parse_deployments(_load(samples_dir, "deployments.csv"))
    correlations = correlate(result.detections, events)

    # The analysis-level flat list should include the nearby deploy.
    assert len(correlations) >= 1

    det = result.detections[0]
    assert len(det.deployment_correlations) >= 1
    # The 10:39 deploy is the closest to the 10:42 spike peak.
    closest = det.deployment_correlations[0]
    assert closest.deployed_at.hour == 10 and closest.deployed_at.minute == 39
    assert closest.service == "orders-api"
    assert closest.delta_seconds is not None and closest.delta_seconds > 0

    # An ordered "Review deployment at ..." step is appended, after the
    # knowledge-base steps.
    review_steps = [s for s in det.suggested_steps if s.startswith("Review deployment at")]
    assert review_steps, det.suggested_steps
    assert "10:39 AM" in review_steps[0]
    assert det.suggested_steps[:3] == [
        "Check DB connections",
        "Check CPU",
        "Check connection pool",
    ]
    assert det.suggested_steps[-1].startswith("Review deployment at 10:39 AM")


def test_no_csv_leaves_correlations_empty(temp_kb_path, samples_dir):
    entries = parse_logs(_load(samples_dir, "app.log"))
    kb = KnowledgeBase.load()
    result = analyze_text(entries, filename="app.log", kb=kb)

    # No deployment events -> no correlations, detections unchanged.
    correlations = correlate(result.detections, [])
    assert correlations == []
    assert result.deployment_correlations == []
    assert result.detections[0].deployment_correlations == []
    assert not any(
        s.startswith("Review deployment at")
        for s in result.detections[0].suggested_steps
    )


def test_time_only_log_correlates_with_dated_deploy_csv(temp_kb_path, samples_dir):
    """Regression: time-only log formats parse to year 1900 while deploy CSVs
    carry a real date. Correlation must reconcile the two onto a common basis
    (time-of-day) instead of computing a ~124-year delta and matching nothing.
    """
    entries = parse_logs(_load(samples_dir, "app-timeonly.log"))
    # Sanity: the log is genuinely time-only (sentinel year 1900).
    error_ts = [e.timestamp for e in entries if e.timestamp is not None]
    assert error_ts and all(ts.year == 1900 for ts in error_ts)

    kb = KnowledgeBase.load()
    result = analyze_text(entries, filename="app-timeonly.log", kb=kb)
    assert len(result.detections) >= 1

    events = parse_deployments(_load(samples_dir, "deployments.csv"))
    correlations = correlate(result.detections, events)

    # The cross-source correlation now succeeds despite the year mismatch.
    assert len(correlations) >= 1

    det = result.detections[0]
    assert len(det.deployment_correlations) >= 1
    closest = det.deployment_correlations[0]
    assert closest.deployed_at.hour == 10 and closest.deployed_at.minute == 39
    assert closest.service == "orders-api"

    review_steps = [s for s in det.suggested_steps if s.startswith("Review deployment at")]
    assert review_steps, det.suggested_steps
    assert "10:39 AM" in review_steps[0]


def test_upload_with_deployments_csv_via_api(client, samples_dir):
    with open(samples_dir / "app.log", "rb") as log_fh, open(
        samples_dir / "deployments.csv", "rb"
    ) as dep_fh:
        r = client.post(
            "/api/upload",
            files={
                "file": ("app.log", log_fh, "text/plain"),
                "deployments": ("deployments.csv", dep_fh, "text/csv"),
            },
        )
    assert r.status_code == 200
    data = r.json()
    assert data["deployment_correlations"], data
    det = data["detections"][0]
    assert det["deployment_correlations"]
    assert any(
        s.startswith("Review deployment at") for s in det["suggested_steps"]
    )


def test_upload_time_only_log_with_csv_via_api(client, samples_dir):
    with open(samples_dir / "app-timeonly.log", "rb") as log_fh, open(
        samples_dir / "deployments.csv", "rb"
    ) as dep_fh:
        r = client.post(
            "/api/upload",
            files={
                "file": ("app-timeonly.log", log_fh, "text/plain"),
                "deployments": ("deployments.csv", dep_fh, "text/csv"),
            },
        )
    assert r.status_code == 200
    data = r.json()
    assert data["deployment_correlations"], data
    det = data["detections"][0]
    assert det["deployment_correlations"]
    assert any(
        s.startswith("Review deployment at 10:39 AM") for s in det["suggested_steps"]
    )


def test_upload_without_deployments_csv_via_api(client, samples_dir):
    with open(samples_dir / "app.log", "rb") as log_fh:
        r = client.post(
            "/api/upload",
            files={"file": ("app.log", log_fh, "text/plain")},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["deployment_correlations"] == []
    assert len(data["detections"]) >= 1
    assert data["detections"][0]["deployment_correlations"] == []
