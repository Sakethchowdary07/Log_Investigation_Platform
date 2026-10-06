"""API-level test for mainframe log upload.

Confirms end-to-end wiring: uploading a mainframe sample via POST /api/upload
returns a CRITICAL MESSAGE detection with the mainframe cause from the KB.
Uses the existing `client` fixture (temp DB + temp KB copy of the shipped YAML).
"""


def test_upload_db2_sample_returns_mainframe_detection(client, samples_dir):
    with open(samples_dir / "db2-sqlcode.log", "rb") as fh:
        r = client.post(
            "/api/upload",
            files={"file": ("db2-sqlcode.log", fh, "text/plain")},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["analysis_id"] is not None
    assert data["filename"] == "db2-sqlcode.log"
    assert len(data["detections"]) >= 1

    sigs = {d["matched_signature"]: d for d in data["detections"]}
    assert "db2_sqlcode_deadlock_timeout" in sigs
    det = sigs["db2_sqlcode_deadlock_timeout"]
    assert det["type"] == "CRITICAL MESSAGE"
    assert det["possible_cause"] == (
        "DB2 detected a deadlock or lock timeout; the current unit of work was "
        "rolled back (-911) or not rolled back (-913)."
    )
    assert det["suggested_steps"][0] == (
        "Identify the contending threads/transactions holding incompatible "
        "locks around the failure time."
    )


def test_upload_cics_sample_returns_asra_detection(client, samples_dir):
    with open(samples_dir / "cics-abend.log", "rb") as fh:
        r = client.post(
            "/api/upload",
            files={"file": ("cics-abend.log", fh, "text/plain")},
        )
    assert r.status_code == 200
    data = r.json()
    sigs = {d["matched_signature"] for d in data["detections"]}
    assert "cics_abend_asra" in sigs
