"""Structured, model-agnostic reflection records consumable by any Agent host."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

class ReflectionStore:
    def __init__(self, path: str): self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
    def record(self, event_type: str, context: str, *, root_cause: str="", improvement: str="", action_plan: str="", assessment: str="") -> Dict[str,str]:
        entry={"timestamp":datetime.now(timezone.utc).isoformat(),"event_type":event_type,"context":context,"root_cause":root_cause,"improvement":improvement,"action_plan":action_plan,"assessment":assessment}
        with self.path.open("a",encoding="utf-8") as f: f.write(json.dumps(entry,ensure_ascii=False)+"\n")
        return entry
