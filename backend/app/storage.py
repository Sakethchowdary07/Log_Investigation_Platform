"""SQLite persistence for uploads and analyses.

Uses the stdlib :mod:`sqlite3`. The database path is configurable via the
``LOG_DB_PATH`` environment variable (default ``backend/data/logsleuth.db``)
and the file/parent directory are created on startup.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import AnalysisResult

_DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "logsleuth.db"


def default_db_path() -> Path:
    env = os.environ.get("LOG_DB_PATH")
    if env:
        return Path(env)
    return _DEFAULT_DB_PATH


class Storage:
    def __init__(self, db_path: Optional[os.PathLike | str] = None):
        self.db_path = Path(db_path) if db_path is not None else default_db_path()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS uploads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    uploaded_at TEXT NOT NULL,
                    entry_count INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    upload_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    FOREIGN KEY (upload_id) REFERENCES uploads (id)
                )
                """
            )
            conn.commit()

    def save_analysis(self, result: AnalysisResult) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO uploads (filename, uploaded_at, entry_count) VALUES (?, ?, ?)",
                (result.filename, now, result.total_entries),
            )
            upload_id = int(cur.lastrowid)
            result.analysis_id = None  # set below once we know the analyses id
            payload = json.dumps(result.model_dump(mode="json"))
            cur = conn.execute(
                "INSERT INTO analyses (upload_id, created_at, result_json) VALUES (?, ?, ?)",
                (upload_id, now, payload),
            )
            analysis_id = int(cur.lastrowid)
            # Persist the assigned analysis_id inside the stored JSON too.
            result.analysis_id = analysis_id
            conn.execute(
                "UPDATE analyses SET result_json = ? WHERE id = ?",
                (json.dumps(result.model_dump(mode="json")), analysis_id),
            )
            conn.commit()
        return analysis_id

    def get_analysis(self, analysis_id: int) -> Optional[AnalysisResult]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM analyses WHERE id = ?", (analysis_id,)
            ).fetchone()
        if row is None:
            return None
        return AnalysisResult.model_validate_json(row["result_json"])

    def list_analyses(self) -> list[AnalysisResult]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT result_json FROM analyses ORDER BY id DESC"
            ).fetchall()
        return [AnalysisResult.model_validate_json(r["result_json"]) for r in rows]
