"""Portable implementations for the whitepaper's maintenance-side capabilities."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .consolidator import MemoryConsolidator
from .runtime import HotMemoryJournal


class FileTracker:
    def __init__(self, path: str): self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
    def snapshot(self, files: Iterable[str]) -> Dict[str, str]:
        state = {str(Path(item)): hashlib.sha256(Path(item).read_bytes()).hexdigest()
                 for item in files if Path(item).is_file()}
        self.path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return state
    def changed(self, files: Iterable[str]) -> List[str]:
        old = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        current = {str(Path(item)): hashlib.sha256(Path(item).read_bytes()).hexdigest()
                   for item in files if Path(item).is_file()}
        return sorted(key for key, value in current.items() if old.get(key) != value)


class PathScopedRules:
    """Rule matching used by memory retrieval and safe deployment gates."""
    def __init__(self, rules: Optional[Dict[str, List[str]]] = None): self.rules = rules or {}
    def rules_for(self, path: str) -> List[str]:
        normalized = str(Path(path))
        return [rule for prefix, rules in self.rules.items() if normalized.startswith(prefix) for rule in rules]


class FTSRebuilder:
    def __init__(self, db_path: str): self.path = Path(db_path)
    def rebuild(self) -> Dict[str, int]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(item_id, content)")
            connection.execute("DELETE FROM memory_fts")
            has_items = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='items'").fetchone()
            if has_items:
                rows = connection.execute("SELECT item_id, content FROM items").fetchall()
                connection.executemany("INSERT INTO memory_fts(item_id, content) VALUES (?,?)", rows)
            else:
                rows = []
            connection.commit()
            return {"indexed": len(rows)}
        finally:
            connection.close()


class DeepSleepMaintenance:
    """Consolidate, remove expired hot sessions, rebuild FTS and report evidence."""
    def __init__(self, workspace: str, metadata_db: str):
        self.root = Path(workspace); self.journal = HotMemoryJournal(str(self.root / "memory" / "hot"))
        self.fts = FTSRebuilder(metadata_db)
    def run(self) -> Dict[str, Any]:
        promoted = MemoryConsolidator().run_nightly_consolidation()
        indexed = self.fts.rebuild()["indexed"]
        report = {"at": datetime.now(timezone.utc).isoformat(), "promoted": promoted, "fts_indexed": indexed}
        path = self.root / "memory" / "maintenance" / "deep-sleep.json"
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report


class SessionCleaner:
    def __init__(self, directory: str, max_age_days: int = 7): self.directory = Path(directory); self.max_age = max_age_days * 86400
    def run(self) -> Dict[str, int]:
        removed = 0; now = time.time()
        for path in self.directory.glob("*") if self.directory.exists() else []:
            if path.is_file() and now - path.stat().st_mtime > self.max_age:
                path.unlink(); removed += 1
        return {"removed": removed}


class MemoryHealthcheck:
    def __init__(self, workspace: str): self.root = Path(workspace)
    def run(self) -> Dict[str, Any]:
        memory = self.root / "memory"
        report = {"at": datetime.now(timezone.utc).isoformat(), "memory_directory": memory.exists(),
                  "event_log": (memory / "event_logs.jsonl").exists(),
                  "sqlite": (memory / "memory_meta.db").exists(), "wiki": (self.root / "wiki" / "main").exists()}
        report["healthy"] = all(report[key] for key in ("memory_directory", "sqlite"))
        output = memory / "health-reports" / "latest.json"; output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report


class SicaGuard:
    """Security integrity/change audit used before and after autonomous evolution."""
    def __init__(self, path: str): self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
    def snapshot(self, files: Iterable[str], action: str = "check") -> Dict[str, Any]:
        records = {str(Path(item)): hashlib.sha256(Path(item).read_bytes()).hexdigest()
                   for item in files if Path(item).is_file()}
        entry = {"at": datetime.now(timezone.utc).isoformat(), "action": action, "hashes": records}
        with self.path.open("a", encoding="utf-8") as output: output.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry


class SessionWatchdog:
    """Reports stale sessions without requiring an OpenClaw daemon."""
    def __init__(self, directory: str, stale_seconds: int = 3600): self.directory=Path(directory); self.stale_seconds=stale_seconds
    def run(self) -> Dict[str, Any]:
        now=time.time(); stale=[str(path) for path in self.directory.glob("*") if path.is_file() and now-path.stat().st_mtime>self.stale_seconds] if self.directory.exists() else []
        return {"stale": stale, "count": len(stale)}


class VersionManager:
    def __init__(self, path: str): self.path=Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
    def record(self, event: str, **details: Any) -> Dict[str, Any]:
        entry={"at":datetime.now(timezone.utc).isoformat(),"event":event,"details":details}
        with self.path.open("a",encoding="utf-8") as output: output.write(json.dumps(entry,ensure_ascii=False)+"\n")
        return entry
