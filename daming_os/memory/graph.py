"""SQLite-backed, host-neutral knowledge graph for memory relationships."""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Optional


class KnowledgeGraph:
    def __init__(self, db_path: str):
        self.path=Path(db_path); self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._connect() as c:
            c.execute("CREATE TABLE IF NOT EXISTS knowledge_edges (source TEXT NOT NULL,target TEXT NOT NULL,relation TEXT NOT NULL,weight REAL NOT NULL DEFAULT .5,PRIMARY KEY(source,target,relation))")
    def _connect(self): return sqlite3.connect(self.path)
    def link(self, source: str, target: str, relation: str="related_to", weight: float=.5) -> None:
        with self._connect() as c: c.execute("INSERT OR REPLACE INTO knowledge_edges VALUES (?,?,?,?)",(source,target,relation,max(0.,min(1.,weight))))
    def neighbors(self, node: str, depth: int=1) -> List[Dict[str,object]]:
        frontier={node}; seen={node}; result=[]
        with self._connect() as c:
            for _ in range(depth):
                if not frontier: break
                marks=','.join('?'*len(frontier)); rows=c.execute(f"SELECT source,target,relation,weight FROM knowledge_edges WHERE source IN ({marks}) OR target IN ({marks})",tuple(frontier)*2).fetchall()
                frontier=set()
                for source,target,relation,weight in rows:
                    other=target if source in seen else source
                    if other not in seen: seen.add(other); frontier.add(other); result.append({"id":other,"relation":relation,"weight":weight})
        return result
