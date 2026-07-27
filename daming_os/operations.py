"""Host-neutral maintenance: review cadence, health probes, archives and golden paths."""
from __future__ import annotations
import json, shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

class HealthMonitor:
    """Runs portable probes for storage, scheduler, rules, config and security."""
    def __init__(self, report_dir: str): self.report_dir=Path(report_dir)
    def check(self, probes: Dict[str, Callable[[], Any]]) -> Dict[str, Any]:
        checks={}
        for name, probe in probes.items():
            try: checks[name]={"ok":bool(probe())}
            except Exception as exc: checks[name]={"ok":False,"error":str(exc)}
        report={"timestamp":datetime.now(timezone.utc).isoformat(),"healthy":all(v["ok"] for v in checks.values()),"checks":checks}
        self.report_dir.mkdir(parents=True,exist_ok=True)
        (self.report_dir/"latest.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        return report

class ArchiveStore:
    """Moves expired artifacts to a dated archive and supports explicit restore."""
    def __init__(self, root: str): self.root=Path(root)
    def archive(self, source: str) -> Path:
        item=Path(source); target=self.root/datetime.now(timezone.utc).strftime("%Y-%m-%d")/item.name
        target.parent.mkdir(parents=True,exist_ok=True); shutil.move(str(item),str(target)); return target
    def restore(self, archived: str, destination: str) -> Path:
        target=Path(destination); target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(archived,target); return target

class GoldenPathStore:
    """Stores verified successful task procedures for later few-shot retrieval."""
    def __init__(self, directory: str): self.directory=Path(directory)
    def save(self, task: str, steps: Iterable[Dict[str,Any]], evidence: Dict[str,Any]) -> Path:
        self.directory.mkdir(parents=True,exist_ok=True); stamp=datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        path=self.directory/f"golden-{stamp}.json"; path.write_text(json.dumps({"task":task,"steps":list(steps),"evidence":evidence,"verified_at":datetime.now(timezone.utc).isoformat()},ensure_ascii=False,indent=2),encoding="utf-8"); return path
