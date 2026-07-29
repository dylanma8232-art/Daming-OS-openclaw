"""Portable scheduler contracts, heartbeat orchestration and configuration drift checks."""
from __future__ import annotations
import hashlib, json, os, threading, time
from datetime import datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

class Scheduler(Protocol):
    def schedule(self, name: str, expression: str, callback: Callable[[],Any]) -> None: ...


class DurableScheduler:
    """Small persistent scheduler for runtimes that do not provide Cron.

    It accepts ``every:<seconds>``, ``daily@HH:MM`` and
    ``weekly@Mon@HH:MM`` expressions.  A host may call ``run_due`` from any
    lifecycle hook, or start the optional background loop in a long-running
    agent process.  Last-run state is persisted across restarts.
    """
    STATE_VERSION = 2

    def __init__(self, state_path: str, timezone_name: str = "local",
                 retry_base_seconds: float = 60, retry_max_seconds: float = 3600):
        self.path = Path(state_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.jobs: Dict[str, Tuple[str, Callable[[], Any]]] = {}
        self.timezone_name = timezone_name
        self.timezone = self._resolve_timezone(timezone_name)
        self.retry_base_seconds = max(1.0, float(retry_base_seconds))
        self.retry_max_seconds = max(self.retry_base_seconds, float(retry_max_seconds))
        self._stop = threading.Event()
        self._run_lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._process_lock_handle: Any = None

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

    def defer_new_jobs(self, now: Optional[float] = None) -> None:
        """Seed newly registered jobs so a fresh install does not run heavy work on its first turn."""
        timestamp = time.time() if now is None else now
        state = self._read_state()
        changed = False
        records = state["jobs"]
        for name, (expression, _) in self.jobs.items():
            if name not in records:
                records[name] = self._new_job_state(
                    expression, next_due_at=self._next_scheduled_after(expression, timestamp)
                )
                changed = True
            elif records[name].get("next_due_at") is None:
                baseline = float(records[name].get("last_success_at") or timestamp)
                records[name]["next_due_at"] = self._next_scheduled_after(expression, baseline)
                records[name]["expression"] = expression
                changed = True
        if changed:
            self._write_state(state)

    def run_due(self, now: Optional[float] = None) -> Dict[str, Any]:
        # A host may call tick() while an optional daemon is polling.  Run at
        # most one batch per runtime so due work is never duplicated in-process.
        if not self._run_lock.acquire(blocking=False):
            return {}
        if not self._acquire_process_lock():
            self._run_lock.release()
            return {}
        try:
            return self._run_due(now)
        finally:
            self._release_process_lock()
            self._run_lock.release()

    def _acquire_process_lock(self) -> bool:
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, OSError):
            handle.close()
            return False
        self._process_lock_handle = handle
        return True

    def _release_process_lock(self) -> None:
        handle = self._process_lock_handle
        self._process_lock_handle = None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def _run_due(self, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        state = self._read_state()
        records = state["jobs"]
        results: Dict[str, Any] = {}
        for name, (expression, callback) in self.jobs.items():
            record = records.setdefault(name, self._new_job_state(expression, next_due_at=0.0))
            record["expression"] = expression
            if now < float(record.get("next_due_at") or 0):
                continue
            record["last_attempt_at"] = now
            try:
                value = callback()
                callback_errors = value.get("errors", {}) if isinstance(value, dict) else {}
                if callback_errors:
                    error = json.dumps(callback_errors, ensure_ascii=False, default=str)
                    self._record_failure(record, now, error)
                    results[name] = {
                        "ok": False,
                        "result": value,
                        "error": error,
                        "next_retry_at": record["next_due_at"],
                    }
                else:
                    record.update({
                        "last_success_at": now,
                        "failure_count": 0,
                        "last_error": None,
                        "next_due_at": self._next_scheduled_after(expression, now),
                    })
                    results[name] = {
                        "ok": True,
                        "result": value,
                        "next_due_at": record["next_due_at"],
                    }
            except Exception as exc:
                self._record_failure(record, now, str(exc))
                results[name] = {
                    "ok": False,
                    "error": str(exc),
                    "next_retry_at": record["next_due_at"],
                }
        self._write_state(state)
        return results

    def _record_failure(self, record: Dict[str, Any], now: float, error: str) -> None:
        failures = int(record.get("failure_count", 0)) + 1
        delay = min(self.retry_max_seconds, self.retry_base_seconds * (2 ** min(failures - 1, 10)))
        record.update({
            "failure_count": failures,
            "last_error": error,
            "next_due_at": now + delay,
        })

    @staticmethod
    def _resolve_timezone(value: str):
        if value == "local":
            return datetime.now().astimezone().tzinfo
        try:
            return ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown scheduler timezone: {value}") from exc

    @staticmethod
    def _parse_clock(value: str) -> Tuple[int, int]:
        hour, minute = (int(part) for part in value.split(":"))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("clock must be HH:MM")
        return hour, minute

    def _next_scheduled_after(self, expression: str, after: float) -> float:
        if expression.startswith("every:"):
            return after + float(expression.split(":", 1)[1])
        current = datetime.fromtimestamp(after, tz=self.timezone)
        if expression.startswith("daily@"):
            hour, minute = self._parse_clock(expression.split("@", 1)[1])
            candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate.timestamp() <= after:
                candidate += timedelta(days=1)
            return candidate.timestamp()
        _, weekday, clock = expression.split("@")
        hour, minute = self._parse_clock(clock)
        target = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}[weekday.lower()[:3]]
        days = (target - current.weekday()) % 7
        candidate = (current + timedelta(days=days)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate.timestamp() <= after:
            candidate += timedelta(days=7)
        return candidate.timestamp()

    @staticmethod
    def _new_job_state(expression: str, next_due_at: float) -> Dict[str, Any]:
        return {
            "expression": expression,
            "last_success_at": None,
            "last_attempt_at": None,
            "next_due_at": float(next_due_at),
            "failure_count": 0,
            "last_error": None,
        }

    def start(self, poll_seconds: float = 30) -> threading.Thread:
        if self._worker is not None and self._worker.is_alive():
            return self._worker
        self._stop.clear()
        def run() -> None:
            while not self._stop.wait(poll_seconds):
                self.run_due()
        worker = threading.Thread(target=run, daemon=True, name="daming-os-scheduler")
        worker.start()
        self._worker = worker
        return worker

    def stop(self) -> None:
        self._stop.set()
        worker = self._worker
        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=2)
        self._worker = None

    def _read_state(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"version": self.STATE_VERSION, "timezone": self.timezone_name, "jobs": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {"version": self.STATE_VERSION, "timezone": self.timezone_name, "jobs": {}}
        if isinstance(value, dict) and value.get("version") == self.STATE_VERSION and isinstance(value.get("jobs"), dict):
            value["timezone"] = self.timezone_name
            return value
        # v1 stored ``{job_name: last_run_timestamp}``.  Preserve the timestamp
        # and calculate the first v2 due time once the expression is registered.
        jobs: Dict[str, Dict[str, Any]] = {}
        if isinstance(value, dict):
            for name, timestamp in value.items():
                try:
                    previous = float(timestamp)
                except (TypeError, ValueError):
                    continue
                jobs[str(name)] = {
                    **self._new_job_state("", next_due_at=0),
                    "last_success_at": previous,
                    "next_due_at": None,
                }
        return {"version": self.STATE_VERSION, "timezone": self.timezone_name, "jobs": jobs}

    def _write_state(self, state: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)

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
        temporary=self.path.with_suffix(self.path.suffix+".tmp")
        temporary.write_text(digest+"\n")
        os.replace(temporary,self.path)
        return bool(previous) and previous != digest
