"""Portable Growth 2.0 governance: scoring, review, approvals and audit trail."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..config import config


@dataclass(frozen=True)
class GEPPolicy:
    threshold: float = 3.0
    half_life_hours: float = 4.0
    window_hours: float = 24.0
    dedupe_seconds: int = 300
    category_cap: float = 4.0
    weights: Dict[str, float] = field(default_factory=lambda: {
        "discovery": 1.5, "rule_violation": .4, "system_error": .3,
        "user_feedback": .3, "task_failure": 1.0,
    })

    def score(self, events: Iterable[Dict[str, Any]], now: Optional[datetime] = None) -> float:
        now = now or datetime.now(timezone.utc)
        totals: Dict[str, float] = {}
        seen = set()
        for event in events:
            content = str(event.get("content", ""))
            event_type = str(event.get("log_type", event.get("type", "")))
            digest = hashlib.sha256((event_type + content).encode()).hexdigest()
            timestamp = datetime.fromisoformat(str(event.get("timestamp", now.isoformat())).replace("Z", "+00:00"))
            if timestamp.tzinfo is None: timestamp = timestamp.replace(tzinfo=timezone.utc)
            age = (now - timestamp).total_seconds()
            if digest in seen or age < 0 or age > self.window_hours * 3600: continue
            seen.add(digest)
            raw = self.weights.get(event_type, 0.0) * (0.5 ** (age / (self.half_life_hours * 3600)))
            totals[event_type] = min(self.category_cap, totals.get(event_type, 0.0) + raw)
        return sum(totals.values())


class GrowthLedger:
    """Persistent review/approval queue with OTP hash, lockout and audit history."""
    def __init__(self, db_path: Optional[str] = None):
        raw = Path(db_path or config.GROWTH_DB_PATH)
        self.path = raw if raw.is_absolute() else Path(config.WORKSPACE_ROOT) / raw
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as c:
            c.execute("CREATE TABLE IF NOT EXISTS growth_governance (proposal_id TEXT PRIMARY KEY, score REAL, review_rounds INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL, otp_hash TEXT, otp_expires_at TEXT, failed_attempts INTEGER NOT NULL DEFAULT 0, deadline_at TEXT, audit_json TEXT NOT NULL DEFAULT '[]')")
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
    def queue(self, proposal_id: str, deadline_hours: int = 4) -> None:
        with self._transaction() as c:
            c.execute("INSERT OR REPLACE INTO growth_governance (proposal_id,state,deadline_at) VALUES (?,?,?)", (proposal_id,"pending_review",(datetime.now(timezone.utc)+timedelta(hours=deadline_hours)).isoformat()))
    def record_review(self, proposal_id: str, builder: str, reviewer: str, score: float) -> bool:
        with self._transaction() as c:
            row=c.execute("SELECT review_rounds,audit_json FROM growth_governance WHERE proposal_id=?",(proposal_id,)).fetchone()
            if not row: raise KeyError(proposal_id)
            rounds=row[0]+1; audit=json.loads(row[1]); audit.append({"at":datetime.now(timezone.utc).isoformat(),"builder":builder,"reviewer":reviewer,"score":score})
            state="awaiting_approval" if score>=85 else ("rejected" if rounds>=3 else "pending_review")
            c.execute("UPDATE growth_governance SET score=?,review_rounds=?,state=?,audit_json=? WHERE proposal_id=?",(score,rounds,state,json.dumps(audit,ensure_ascii=False),proposal_id))
            return state=="awaiting_approval"
    def issue_otp(self, proposal_id: str, ttl_minutes: int = 10) -> str:
        token=f"{secrets.randbelow(1_000_000):06d}"; digest=hashlib.sha256(token.encode()).hexdigest()
        with self._transaction() as c:
            row = c.execute("SELECT state FROM growth_governance WHERE proposal_id=?", (proposal_id,)).fetchone()
            if not row:
                raise KeyError(proposal_id)
            if row[0] != "awaiting_approval":
                raise ValueError(f"proposal is not awaiting approval: {row[0]}")
            c.execute("UPDATE growth_governance SET otp_hash=?,otp_expires_at=?,failed_attempts=0 WHERE proposal_id=?",
                      (digest,(datetime.now(timezone.utc)+timedelta(minutes=ttl_minutes)).isoformat(),proposal_id))
        return token
    def approve(self, proposal_id: str, otp: str) -> bool:
        with self._transaction() as c:
            row=c.execute("SELECT state,otp_hash,otp_expires_at,failed_attempts FROM growth_governance WHERE proposal_id=?",(proposal_id,)).fetchone()
            if not row or row[0]!="awaiting_approval" or row[3]>=3: return False
            valid=row[1] and hmac.compare_digest(row[1],hashlib.sha256(otp.encode()).hexdigest()) and datetime.fromisoformat(row[2])>datetime.now(timezone.utc)
            c.execute("UPDATE growth_governance SET state=?,otp_hash=NULL,failed_attempts=? WHERE proposal_id=?",("approved" if valid else row[0],0 if valid else row[3]+1,proposal_id)); return bool(valid)
    def reject(self, proposal_id: str, reason: str = "rejected by user") -> None:
        with self._transaction() as c:
            row = c.execute("SELECT audit_json FROM growth_governance WHERE proposal_id=?", (proposal_id,)).fetchone()
            if not row:
                raise KeyError(proposal_id)
            audit = json.loads(row[0])
            audit.append({"at": datetime.now(timezone.utc).isoformat(), "action": "rejected",
                          "reason": reason})
            c.execute("UPDATE growth_governance SET state='rejected',otp_hash=NULL,otp_expires_at=NULL,audit_json=? WHERE proposal_id=?",
                      (json.dumps(audit, ensure_ascii=False), proposal_id))
    def records(self) -> List[Dict[str, Any]]:
        with self._transaction() as c:
            rows = c.execute(
                "SELECT proposal_id,score,review_rounds,state,otp_expires_at,failed_attempts,deadline_at,audit_json "
                "FROM growth_governance ORDER BY deadline_at DESC"
            ).fetchall()
        return [{
            "proposal_id": row[0], "score": row[1], "review_rounds": row[2],
            "state": row[3], "otp_expires_at": row[4], "failed_attempts": row[5],
            "deadline_at": row[6], "audit": json.loads(row[7]),
        } for row in rows]
    def state(self, proposal_id: str) -> Optional[str]:
        with self._transaction() as c:
            row = c.execute("SELECT state FROM growth_governance WHERE proposal_id=?", (proposal_id,)).fetchone()
        return row[0] if row else None
    def overdue(self) -> List[str]:
        with self._transaction() as c: rows=c.execute("SELECT proposal_id FROM growth_governance WHERE state IN ('pending_review','awaiting_approval') AND deadline_at<?",(datetime.now(timezone.utc).isoformat(),)).fetchall()
        return [r[0] for r in rows]
