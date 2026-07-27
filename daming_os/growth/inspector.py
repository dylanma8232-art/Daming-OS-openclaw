"""Event recurrence inspection and durable growth-signal generation."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..config import config
from .proposals import ProposalStore


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w\u4e00-\u9fff]+", text.lower()))


def _similarity(first: set[str], second: set[str]) -> float:
    return len(first & second) / max(1, len(first | second))


class ProactiveInspector:
    """Clusters recurring failures/discoveries in the durable JSONL event stream."""
    def __init__(self, event_log_path: Optional[str] = None, proposals: Optional[ProposalStore] = None):
        raw = Path(event_log_path or config.EVENT_LOG_PATH)
        self.event_log_path = raw if raw.is_absolute() else Path(config.WORKSPACE_ROOT) / raw
        self.proposals = proposals or ProposalStore()

    def inspect(self, days: int = 7, min_occurrences: int = 2, threshold: float = 0.45) -> List[str]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        clusters: List[List[Dict[str, Any]]] = []
        for event in self._events_after(cutoff):
            event_type = event.get("log_type", event.get("event_type", ""))
            if event_type not in {"task_failure", "rule_violation", "discovery"}:
                continue
            terms = _tokens(str(event.get("content", "")))
            for cluster in clusters:
                cluster_terms = _tokens(" ".join(str(item.get("content", "")) for item in cluster))
                if _similarity(terms, cluster_terms) >= threshold:
                    cluster.append(event)
                    break
            else:
                clusters.append([event])
        proposals = []
        for cluster in clusters:
            if len(cluster) < min_occurrences:
                continue
            headline = str(cluster[-1].get("content", ""))[:240]
            proposals.append(self.proposals.create({"kind": "recurring-signal", "occurrences": len(cluster),
                                                    "pattern": headline, "source_events": cluster}))
        return proposals

    def _events_after(self, cutoff: datetime) -> Iterable[Dict[str, Any]]:
        if not self.event_log_path.exists():
            return []
        events = []
        for line in self.event_log_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
                timestamp = datetime.fromisoformat(event.get("timestamp", "").replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                if timestamp >= cutoff:
                    events.append(event)
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
        return events
