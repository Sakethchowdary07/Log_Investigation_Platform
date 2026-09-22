"""Shared pytest fixtures.

Each test run gets a temp SQLite DB and a temp KB path via env vars so tests
never touch the real data directory or the shipped knowledge base.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SAMPLES_DIR = Path(__file__).parent.parent / "samples"
DEFAULT_KB = Path(__file__).parent.parent / "app" / "config" / "knowledge_base.yaml"


@pytest.fixture()
def temp_db_path(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("LOG_DB_PATH", str(db))
    return db


@pytest.fixture()
def temp_kb_path(tmp_path, monkeypatch):
    """Copy the shipped KB to a temp location and point LOG_KB_PATH at it."""
    kb = tmp_path / "kb.yaml"
    shutil.copy(DEFAULT_KB, kb)
    monkeypatch.setenv("LOG_KB_PATH", str(kb))
    return kb


@pytest.fixture()
def client(temp_db_path, temp_kb_path):
    # Import inside the fixture so env vars are set before app modules read them.
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def samples_dir():
    return SAMPLES_DIR
