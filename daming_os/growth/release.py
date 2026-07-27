"""Deployment verification, rollback records and portable version audit."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict

class ReleaseLedger:
    def __init__(self, path: str): self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
    def record(self, version: str, proposal_id: str, status: str, evidence: Dict[str,Any]) -> Dict[str,Any]:
        entry={"timestamp":datetime.now(timezone.utc).isoformat(),"version":version,"proposal_id":proposal_id,"status":status,"evidence":evidence}
        with self.path.open("a",encoding="utf-8") as f: f.write(json.dumps(entry,ensure_ascii=False)+"\n")
        return entry

class VerifiedDeployment:
    """Calls injected deploy/verify/rollback functions and never hides a failed verify."""
    def __init__(self, deploy: Callable[[],Any], verify: Callable[[],bool], rollback: Callable[[],Any], ledger: ReleaseLedger):
        self.deploy,self.verify,self.rollback,self.ledger=deploy,verify,rollback,ledger
    def run(self, version: str, proposal_id: str) -> bool:
        self.deploy()
        if self.verify(): self.ledger.record(version,proposal_id,"verified",{}); return True
        self.rollback(); self.ledger.record(version,proposal_id,"rolled_back",{}); return False
