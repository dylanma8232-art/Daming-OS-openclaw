"""Portable scheduler contracts, heartbeat orchestration and configuration drift checks."""
from __future__ import annotations
import hashlib, json, threading, time
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Tuple

class Scheduler(Protocol):
    def schedule(self, name: str, expression: str, callback: Callable[[],Any]) -> None: ...


class DurableScheduler:
    """Small persistent scheduler for runtimes that do not provide Cron.

    It accepts ``every:<seconds>``, ``daily@HH:MM`` and
    ``weekly@Mon@HH:MM`` expressions.  A host may call ``run_due`` from any
    lifecycle hook, or start the optional background loop in a long-running
    agent process.  Last-run state is persisted across restarts.
    """
    def __init__(self, state_path: str):
        self.path = Path(state_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.jobs: Dict[str, Tuple[str, Callable[[], Any]]] = {}
        self._stop = threading.Event()

    def schedule(self, name: str, expression: str, callback: Callable[[], Any]) -> None:
        if expression.startswith("every:"):
            interval = float(expression.split(":", 1)[1])
            if interval <= 0:
                raise ValueError("Scheduler interval must be positive")
        elif expression.startswith("daily@"):
            self._parse_clock(expression.split("@", 1)[1])
        elif expression.startswith("weekly@"):
            parts = expression.split("@")
            if len(parts) != 3 or parts[1].lower()[:3] not in {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}:
                raise ValueError("Use weekly@Mon@HH:MM")
            self._parse_clock(parts[2])
        else:
            raise ValueError("Use every:<seconds>, daily@HH:MM, or weekly@Mon@HH:MM")
        self.jobs[name] = (expression, callback)

    def run_due(self, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        state = self._read_state()
        results: Dict[str, Any] = {}
        for name, (expression, callback) in self.jobs.items():
            if not self._is_due(expression, float(state.get(name, 0)), now):
                continue
            try:
                results[name] = {"ok": True, "result": callback()}
                state[name] = now
            except Exception as exc:
                results[name] = {"ok": False, "error": str(exc)}
        self._write_state(state)
        return results

    @staticmethod
    def _parse_clock(value: str) -> Tuple[int, int]:
        hour, minute = (int(part) for part in value.split(":"))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("clock must be HH:MM")
        return hour, minute

    def _is_due(self, expression: str, last_run: float, now: float) -> bool:
        if expression.startswith("every:"):
            return now - last_run >= float(expression.split(":", 1)[1])
        current, previous = datetime.fromtimestamp(now), datetime.fromtimestamp(last_run) if last_run else None
        if expression.startswith("daily@"):
            hour, minute = self._parse_clock(expression.split("@", 1)[1])
            return ((current.hour, current.minute) >= (hour, minute)
                    and (previous is None or previous.date() != current.date()))
        _, weekday, clock = expression.split("@")
        hour, minute = self._parse_clock(clock)
        target = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}[weekday.lower()[:3]]
        return (current.weekday() == target and (current.hour, current.minute) >= (hour, minute)
                and (previous is None or previous.isocalendar()[:2] != current.isocalendar()[:2]))

    def start(self, poll_seconds: float = 30) -> threading.Thread:
        def run() -> None:
            while not self._stop.wait(poll_seconds):
                self.run_due()
        worker = threading.Thread(target=run, daemon=True, name="daming-os-scheduler")
        worker.start()
        return worker

    def stop(self) -> None:
        self._stop.set()

    def _read_state(self) -> Dict[str, float]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def _write_state(self, state: Dict[str, float]) -> None:
        self.path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

@dataclass
class Heartbeat:
    name: str
    check: Callable[[],Any]

class HeartbeatRunner:
    def __init__(self, heartbeats: Iterable[Heartbeat]): self.heartbeats=list(heartbeats)
    def run(self) -> Dict[str,Any]:
        result={}
        for item in self.heartbeats:
            try: result[item.name]={"ok":bool(item.check())}
            except Exception as exc: result[item.name]={"ok":False,"error":str(exc)}
        return result

class ConfigGuard:
    def __init__(self, state_path: str): self.path=Path(state_path); self.path.parent.mkdir(parents=True,exist_ok=True)
    def check(self, config: Dict[str,Any]) -> bool:
        digest=hashlib.sha256(json.dumps(config,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        previous=self.path.read_text().strip() if self.path.exists() else ""
        self.path.write_text(digest+"\n"); return bool(previous) and previous != digest
