"""Durable, host-neutral lifecycle for growth proposals."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from ..config import config

_TRANSITIONS = {
    "proposed": {"validated", "rejected"},
    "validated": {"approved", "rejected"},
    "approved": {"deployed", "rejected"},
    "deployed": {"verified", "rolled_back"},
    "verified": set(), "rolled_back": set(), "rejected": set(),
}

class ProposalStore:
    def __init__(self, db_path: Optional[str] = None):
        raw = Path(db_path or config.GROWTH_DB_PATH)
        self.path = raw if raw.is_absolute() else Path(config.WORKSPACE_ROOT) / raw
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, state TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self.path)

    def create(self, payload: Dict[str, Any], proposal_id: Optional[str] = None) -> str:
        proposal_id = proposal_id or f"proposal-{uuid.uuid4()}"
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("INSERT INTO proposals VALUES (?, ?, ?, ?, ?)", (proposal_id, "proposed", json.dumps(payload, ensure_ascii=False), now, now))
        return proposal_id

    def transition(self, proposal_id: str, state: str) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT state FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            if not row:
                raise KeyError(proposal_id)
            if state not in _TRANSITIONS.get(row[0], set()):
                raise ValueError(f"Invalid proposal transition: {row[0]} -> {state}")
            conn.execute("UPDATE proposals SET state=?, updated_at=? WHERE id=?", (state, datetime.now(timezone.utc).isoformat(), proposal_id))

    def get(self, proposal_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT id, state, payload, created_at, updated_at FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if not row:
            raise KeyError(proposal_id)
        return {"id": row[0], "state": row[1], "payload": json.loads(row[2]), "created_at": row[3], "updated_at": row[4]}

    def pending(self) -> Iterable[Dict[str, Any]]:
        with self._connect() as conn:
            ids = [row[0] for row in conn.execute("SELECT id FROM proposals WHERE state NOT IN ('verified', 'rolled_back', 'rejected')")]
        return [self.get(proposal_id) for proposal_id in ids]
