"""Portable scheduler contracts, heartbeat orchestration and configuration drift checks."""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Protocol

class Scheduler(Protocol):
    def schedule(self, name: str, expression: str, callback: Callable[[],Any]) -> None: ...

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
