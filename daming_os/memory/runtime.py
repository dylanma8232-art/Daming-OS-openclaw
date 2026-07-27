"""Runtime memory primitives derived from the production memory loop.

They deliberately use only JSONL and the Python standard library, so any agent
host can call them after a turn without adopting a particular scheduler or
chat platform.
"""
from __future__ import annotations

import fcntl
import json
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional


def estimate_tokens(text: str) -> int:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    remainder = re.sub(r"[\u4e00-\u9fff]", "", text)
    return max(1, int(cjk + max(len(remainder.split()) * 1.3, len(remainder) / 4))) if text else 0


@dataclass(frozen=True)
class TurnRecord:
    turn_id: str
    timestamp: float
    summary: str
    tool_calls: List[str]
    state_diff: Dict[str, Any]
    token_count: int


class HotMemoryJournal:
    """Append-only, process-safe hot memory instead of a single overwritten file."""
    def __init__(self, directory: str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def append(self, session_id: str, summary: str, *, tool_calls: Optional[Iterable[str]] = None,
               state_diff: Optional[Dict[str, Any]] = None, token_count: Optional[int] = None,
               turn_id: Optional[str] = None) -> TurnRecord:
        record = TurnRecord(
            turn_id=turn_id or str(time.time_ns()), timestamp=time.time(), summary=str(summary),
            tool_calls=list(tool_calls or []), state_diff=dict(state_diff or {}),
            token_count=token_count if token_count is not None else estimate_tokens(str(summary)),
        )
        path = self._path(session_id)
        with path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
                handle.flush()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return record

    def read(self, session_id: str, limit: Optional[int] = None) -> List[TurnRecord]:
        path = self._path(session_id)
        if not path.exists():
            return []
        records = []
        with path.open(encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            try:
                for line in handle:
                    try:
                        raw = json.loads(line)
                        records.append(TurnRecord(**raw))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return records[-limit:] if limit else records

    def context_window(self, session_id: str, max_tokens: int = 2_000, keep_turns: int = 10,
                       summarizer: Optional[Callable[[List[TurnRecord]], str]] = None) -> List[Dict[str, Any]]:
        records = self.read(session_id)
        recent = records[-keep_turns:]
        overflow = records[:-keep_turns]
        messages: List[Dict[str, Any]] = []
        if overflow:
            snapshot = summarizer(overflow) if summarizer else self._deterministic_snapshot(overflow)
            messages.append({"role": "system", "content": "[COMPACTED HISTORY]\n" + snapshot})
        for record in recent:
            messages.append({"role": "system", "content": record.summary, "turn_id": record.turn_id,
                             "tool_calls": record.tool_calls, "state_diff": record.state_diff})
        while messages and sum(estimate_tokens(str(message.get("content", ""))) for message in messages) > max_tokens:
            messages.pop(1 if len(messages) > 1 and messages[0].get("content", "").startswith("[COMPACTED") else 0)
        return messages

    def _path(self, session_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)
        return self.directory / f"hot_memory_{safe}.jsonl"

    @staticmethod
    def _deterministic_snapshot(records: List[TurnRecord]) -> str:
        tools = sorted({tool for record in records for tool in record.tool_calls})
        changes = [record.state_diff for record in records if record.state_diff]
        summaries = "; ".join(record.summary[:160] for record in records[-3:])
        return f"turns={len(records)}; tools={','.join(tools)}; latest={summaries}; changes={json.dumps(changes[-3:], ensure_ascii=False)}"
