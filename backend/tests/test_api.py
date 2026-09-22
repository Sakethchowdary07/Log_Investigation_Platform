def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_knowledge_base_endpoint(client):
    r = client.get("/api/knowledge-base")
    assert r.status_code == 200
    rules = r.json()["rules"]
    ids = {rule["id"] for rule in rules}
    assert "db_connection_pool_exhaustion" in ids


def test_upload_plain_text_returns_detection(client, samples_dir):
    with open(samples_dir / "app.log", "rb") as fh:
        r = client.post("/api/upload", files={"file": ("app.log", fh, "text/plain")})
    assert r.status_code == 200
    data = r.json()
    assert data["analysis_id"] is not None
    assert data["filename"] == "app.log"
    assert len(data["detections"]) >= 1

    det = data["detections"][0]
    assert det["type"] == "ERROR SPIKE"
    assert det["peak_time"] is not None
    assert det["possible_cause"] == "Database connection pool exhaustion"
    assert "DB timeout" in det["related_errors"]
    assert "Connection refused" in det["related_errors"]
    assert "HTTP 503" in det["related_errors"]
    assert det["suggested_steps"][:3] == [
        "Check DB connections",
        "Check CPU",
        "Check connection pool",
    ]
    assert data["deployment_correlations"] == []


def test_upload_json_returns_detection(client, samples_dir):
    with open(samples_dir / "app.jsonl", "rb") as fh:
        r = client.post("/api/upload", files={"file": ("app.jsonl", fh, "application/json")})
    assert r.status_code == 200
    data = r.json()
    assert len(data["detections"]) >= 1
    assert data["detections"][0]["possible_cause"] == "Database connection pool exhaustion"


def test_list_and_get_roundtrip(client, samples_dir):
    with open(samples_dir / "app.log", "rb") as fh:
        upload = client.post("/api/upload", files={"file": ("app.log", fh, "text/plain")})
    analysis_id = upload.json()["analysis_id"]

    listed = client.get("/api/analyses")
    assert listed.status_code == 200
    ids = [a["analysis_id"] for a in listed.json()]
    assert analysis_id in ids

    fetched = client.get(f"/api/analyses/{analysis_id}")
    assert fetched.status_code == 200
    assert fetched.json()["analysis_id"] == analysis_id


def test_get_missing_analysis_404(client):
    r = client.get("/api/analyses/999999")
    assert r.status_code == 404
