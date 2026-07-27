"""A portable quality gate for tasks that must be reviewed before delivery."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .config import config


class QualityGate:
    """Persists task risk and review state; no chat platform or scheduler required."""
    def __init__(self, db_path: Optional[str] = None):
        raw = Path(db_path or config.GROWTH_DB_PATH)
        self.path = raw if raw.is_absolute() else Path(config.WORKSPACE_ROOT) / raw
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS quality_tasks (task_id TEXT PRIMARY KEY, risk TEXT NOT NULL, state TEXT NOT NULL, review_note TEXT, updated_at TEXT NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self.path)

    def register(self, task_id: str, risk: str = "normal") -> None:
        if risk not in {"normal", "high"}:
            raise ValueError("risk must be normal or high")
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO quality_tasks (task_id, risk, state, review_note, updated_at) VALUES (?, ?, COALESCE((SELECT state FROM quality_tasks WHERE task_id=?), 'open'), COALESCE((SELECT review_note FROM quality_tasks WHERE task_id=?), NULL), ?)", (task_id, risk, task_id, task_id, self._now()))

    def complete(self, task_id: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE quality_tasks SET state='completed', updated_at=? WHERE task_id=?", (self._now(), task_id))

    def review(self, task_id: str, passed: bool, note: str = "") -> None:
        with self._connect() as conn:
            conn.execute("UPDATE quality_tasks SET state=?, review_note=?, updated_at=? WHERE task_id=?", ("reviewed" if passed else "rejected", note, self._now(), task_id))

    def blocked(self) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT task_id FROM quality_tasks WHERE risk='high' AND state IN ('open', 'completed') ORDER BY updated_at").fetchall()
        return [row[0] for row in rows]

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
