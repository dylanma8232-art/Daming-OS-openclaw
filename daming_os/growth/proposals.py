"""Durable, host-neutral lifecycle for growth proposals."""
import json
import sqlite3
from contextlib import contextmanager
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
        with self._transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, state TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _transaction(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def create(self, payload: Dict[str, Any], proposal_id: Optional[str] = None) -> str:
        proposal_id = proposal_id or f"proposal-{uuid.uuid4()}"
        now = datetime.now(timezone.utc).isoformat()
        with self._transaction() as conn:
            conn.execute("INSERT INTO proposals VALUES (?, ?, ?, ?, ?)", (proposal_id, "proposed", json.dumps(payload, ensure_ascii=False), now, now))
        return proposal_id

    def transition(self, proposal_id: str, state: str) -> None:
        with self._transaction() as conn:
            row = conn.execute("SELECT state FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            if not row:
                raise KeyError(proposal_id)
            if state not in _TRANSITIONS.get(row[0], set()):
                raise ValueError(f"Invalid proposal transition: {row[0]} -> {state}")
            conn.execute("UPDATE proposals SET state=?, updated_at=? WHERE id=?", (state, datetime.now(timezone.utc).isoformat(), proposal_id))

    def update_payload(self, proposal_id: str, payload: Dict[str, Any]) -> None:
        """Persist an enriched proposal without bypassing its state machine."""
        with self._transaction() as conn:
            if not conn.execute("SELECT 1 FROM proposals WHERE id=?", (proposal_id,)).fetchone():
                raise KeyError(proposal_id)
            conn.execute("UPDATE proposals SET payload=?, updated_at=? WHERE id=?",
                         (json.dumps(payload, ensure_ascii=False), datetime.now(timezone.utc).isoformat(), proposal_id))

    def get(self, proposal_id: str) -> Dict[str, Any]:
        with self._transaction() as conn:
            row = conn.execute("SELECT id, state, payload, created_at, updated_at FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if not row:
            raise KeyError(proposal_id)
        return {"id": row[0], "state": row[1], "payload": json.loads(row[2]), "created_at": row[3], "updated_at": row[4]}

    def pending(self) -> Iterable[Dict[str, Any]]:
        with self._transaction() as conn:
            ids = [row[0] for row in conn.execute("SELECT id FROM proposals WHERE state NOT IN ('verified', 'rolled_back', 'rejected')")]
        return [self.get(proposal_id) for proposal_id in ids]
