"""Five-dimension proactive inspection without a scheduler or chat dependency."""
from __future__ import annotations
from typing import Any, Callable, Dict

class GrowthHealthInspector:
    DIMENSIONS=("scheduler","experience_freshness","rule_compliance","path_integrity","security")
    def inspect(self, probes: Dict[str,Callable[[],Any]]) -> Dict[str,Any]:
        results={}
        for name in self.DIMENSIONS:
            probe=probes.get(name)
            if probe is None: results[name]={"ok":False,"reason":"probe_not_configured"}; continue
            try:
                value=probe(); results[name]={"ok":bool(value),"detail":value if not isinstance(value,bool) else ""}
            except Exception as exc: results[name]={"ok":False,"reason":str(exc)}
        return {"healthy":all(x["ok"] for x in results.values()),"dimensions":results}
