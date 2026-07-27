"""SQLite-backed, host-neutral knowledge graph for memory relationships."""
from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterable, List, Optional


class KnowledgeGraph:
    """Compatibility writer for the same ``wiki_edges`` graph read by recall.

    Older code called this a separate dynamic graph and wrote ``knowledge_edges``;
    that made graph construction invisible to spreading activation.  There is
    now one graph store and one retrieval path.
    """
    def __init__(self, db_path: str):
        self.path=Path(db_path); self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._transaction() as c:
            c.execute("CREATE TABLE IF NOT EXISTS wiki_edges (source_node TEXT NOT NULL,target_node TEXT NOT NULL,link_type TEXT NOT NULL DEFAULT 'related_to',PRIMARY KEY(source_node,target_node,link_type))")
    def _connect(self): return sqlite3.connect(self.path)
    @contextmanager
    def _transaction(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()
    def link(self, source: str, target: str, relation: str="related_to", weight: float=.5) -> None:
        # Weight is represented by the relation type in the common traverser;
        # preserve strong links as dependencies and all others as related.
        link_type = "depends_on" if weight >= .95 else relation
        with self._transaction() as c:
            c.execute("INSERT OR REPLACE INTO wiki_edges(source_node,target_node,link_type) VALUES (?,?,?)",(source,target,link_type))
    def neighbors(self, node: str, depth: int=1) -> List[Dict[str,object]]:
        frontier={node}; seen={node}; result=[]
        with self._transaction() as c:
            for _ in range(depth):
                if not frontier: break
                marks=','.join('?'*len(frontier)); rows=c.execute(f"SELECT source_node,target_node,link_type FROM wiki_edges WHERE source_node IN ({marks}) OR target_node IN ({marks})",tuple(frontier)*2).fetchall()
                frontier=set()
                for source,target,relation in rows:
                    other=target if source in seen else source
                    if other not in seen: seen.add(other); frontier.add(other); result.append({"id":other,"relation":relation,"weight":1.0 if relation == "depends_on" else .5})
        return result
