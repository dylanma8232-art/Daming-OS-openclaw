"""Durable experience lifecycle and host-neutral skill distillation."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..config import config


class ExperienceStore:
    """Tracks extracted learnings, review state and verified application counts."""
    def __init__(self, db_path: Optional[str] = None):
        raw = Path(db_path or config.GROWTH_DB_PATH)
        self.path = raw if raw.is_absolute() else Path(config.WORKSPACE_ROOT) / raw
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS experiences (id TEXT PRIMARY KEY, pattern TEXT NOT NULL, lesson TEXT NOT NULL, action_item TEXT NOT NULL, confidence REAL NOT NULL, status TEXT NOT NULL, source_events TEXT NOT NULL, applied_count INTEGER NOT NULL DEFAULT 0, last_applied_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self.path)

    def create(self, *, pattern: str, lesson: str, action_item: str, confidence: float,
               source_events: Iterable[Dict[str, Any]], status: str = "pending") -> str:
        identifier = "learning-" + uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("INSERT INTO experiences VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)",
                         (identifier, pattern, lesson, action_item, max(0.0, min(1.0, confidence)), status,
                          json.dumps(list(source_events), ensure_ascii=False), now, now))
        return identifier

    def transition(self, identifier: str, status: str) -> None:
        if status not in {"pending", "verified", "deprecated"}:
            raise ValueError(f"Unknown experience status: {status}")
        with self._connect() as conn:
            if not conn.execute("SELECT 1 FROM experiences WHERE id=?", (identifier,)).fetchone():
                raise KeyError(identifier)
            conn.execute("UPDATE experiences SET status=?, updated_at=? WHERE id=?",
                         (status, datetime.now(timezone.utc).isoformat(), identifier))

    def mark_applied(self, identifier: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute("UPDATE experiences SET applied_count=applied_count+1, last_applied_at=?, updated_at=? WHERE id=? AND status='verified'", (now, now, identifier))
            if cursor.rowcount != 1:
                raise ValueError("Experience must exist and be verified before use")

    def candidates(self, min_confidence: float = 0.7) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id, pattern, lesson, action_item, confidence, applied_count FROM experiences WHERE status='verified' AND confidence>=? ORDER BY confidence DESC, applied_count DESC", (min_confidence,)).fetchall()
        return [{"id": row[0], "pattern": row[1], "lesson": row[2], "action_item": row[3], "confidence": row[4], "applied_count": row[5]} for row in rows]


class SkillDistiller:
    """Emits reviewable JSON skill candidates; hosts decide how to install them."""
    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)

    def distill(self, learning: Dict[str, Any]) -> Path:
        if not learning.get("pattern"):
            raise ValueError("A learning pattern is required")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output = self.output_dir / f"{learning['id']}.json"
        payload = {"schema_version": 1, "kind": "agent-skill-candidate", "source_learning_id": learning["id"],
                   "instruction": learning["pattern"], "rationale": learning.get("lesson", ""),
                   "verification": learning.get("action_item", ""), "requires_human_review": True}
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return output
