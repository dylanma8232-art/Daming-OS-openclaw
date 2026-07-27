"""Portable maintenance jobs for review, consolidation and recovery."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .consolidator import MemoryConsolidator


class MemoryMaintenance:
    """Scheduler-agnostic jobs; a host may call these from cron, a worker or a heartbeat."""
    def __init__(self, workspace: str):
        self.root = Path(workspace)
        self.memory = self.root / "memory"

    def consolidate(self) -> int:
        return MemoryConsolidator().run_nightly_consolidation()

    def review(self, days: int = 1) -> Path:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        events = self._events(cutoff)
        summary = {"generated_at": datetime.now(timezone.utc).isoformat(), "days": days,
                   "event_count": len(events), "by_type": {}}
        for event in events:
            key = event.get("log_type", event.get("event_type", "unknown"))
            summary["by_type"][key] = summary["by_type"].get(key, 0) + 1
        directory = self.memory / "reviews"; directory.mkdir(parents=True, exist_ok=True)
        output = directory / f"review-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return output

    def _events(self, cutoff: datetime) -> List[Dict[str, Any]]:
        path = self.memory / "event_logs.jsonl"
        if not path.exists(): return []
        result=[]
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event=json.loads(line); timestamp=datetime.fromisoformat(event["timestamp"].replace("Z","+00:00"))
                if timestamp.tzinfo is None: timestamp=timestamp.replace(tzinfo=timezone.utc)
                if timestamp >= cutoff: result.append(event)
            except (KeyError, ValueError, json.JSONDecodeError): continue
        return result
