import hashlib
import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from ..config import config
from .embeddings import EmbeddingProvider
from .warm import upsert_memory

logger = logging.getLogger("daming_os.memory.consolidator")

class MemoryConsolidator:
    """
    Handles deep sleep background tasks for the 大明记忆系统.
    Consolidates hot memory to warm (LanceDB/SQLite) and cold (Obsidian Markdown) layers.
    """
    def __init__(self, embedding_provider: Optional[EmbeddingProvider] = None):
        root = Path(config.WORKSPACE_ROOT)
        self.root = root
        self.wiki_dir = root / config.WIKI_DIR
        path = Path(config.SQLITE_META_PATH)
        self.db_path = path if path.is_absolute() else root / path
        self.embedding_provider = embedding_provider
        
    def run_nightly_consolidation(self):
        """
        Merge redundant L1/L2 data, update semantic representations,
        and generate physical markdown files for long-term storage.
        """
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path, timeout=30.0)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS items (item_id TEXT PRIMARY KEY, title TEXT, content TEXT, importance REAL DEFAULT 0.5, category TEXT DEFAULT 'memory', created_at TEXT, metadata_json TEXT DEFAULT '{}')")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
            if "metadata_json" not in columns:
                conn.execute("ALTER TABLE items ADD COLUMN metadata_json TEXT DEFAULT '{}'")
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(item_id, content)")
            conn.execute("CREATE TABLE IF NOT EXISTS wiki_edges (source_node TEXT NOT NULL, target_node TEXT NOT NULL, link_type TEXT NOT NULL DEFAULT 'related_to', PRIMARY KEY(source_node, target_node, link_type))")
            conn.execute("CREATE TABLE IF NOT EXISTS incoming_memories (id INTEGER PRIMARY KEY AUTOINCREMENT, session_key TEXT, data_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            rows = conn.execute("SELECT id, session_key, data_json, created_at FROM incoming_memories ORDER BY id").fetchall()
            consolidated = 0
            for row_id, session_key, raw, created_at in rows:
                try:
                    data = json.loads(raw)
                    content = str(data.get("content", "")).strip()
                    metadata = data.get("meta", {})
                    if not content:
                        continue
                    item_id = hashlib.sha256(f"{session_key}:{content}".encode()).hexdigest()
                    exists = conn.execute("SELECT 1 FROM items WHERE item_id=?", (item_id,)).fetchone()
                    if not exists:
                        title = content.replace("\n", " ")[:80]
                        timestamp = created_at or datetime.now(timezone.utc).isoformat()
                        conn.execute(
                            "INSERT OR IGNORE INTO items (item_id, title, content, importance, category, created_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (item_id, title, content, 0.5, "memory", timestamp,
                             json.dumps(metadata, ensure_ascii=False)),
                        )
                        conn.execute("INSERT OR IGNORE INTO memory_fts (item_id, content) VALUES (?, ?)", (item_id, content))
                        category = str(metadata.get("category", "memory"))
                        section = "experiences" if category in {"experience", "memory"} else ("reports" if category == "report" else "concepts")
                        wiki_path = self.wiki_dir / section / f"{item_id}.md"
                        wiki_path.parent.mkdir(parents=True, exist_ok=True)
                        wiki_path.write_text(f"# {title}\n\n{content}\n", encoding="utf-8")

                        # 构建知识图谱语义连线与实体关联 (Triple Linkage)
                        triplets = self._extract_triplets(content)
                        for src, tgt, rel in triplets:
                            conn.execute("INSERT OR IGNORE INTO wiki_edges(source_node, target_node, link_type) VALUES (?, ?, ?)", (src, tgt, rel))

                        previous = conn.execute("SELECT item_id FROM items WHERE item_id<>? ORDER BY created_at DESC LIMIT 1", (item_id,)).fetchone()
                        if previous:
                            conn.execute("INSERT OR IGNORE INTO wiki_edges(source_node,target_node,link_type) VALUES (?,?,?)", (item_id, previous[0], "related_to"))
                        consolidated += 1

                    # Vector promotion is intentionally outside the insert
                    # branch: a memory created while embeddings were offline
                    # must become searchable when a later run has a provider.
                    if self.embedding_provider is not None:
                        try:
                            vector = self.embedding_provider.embed(content)
                            memory_db = Path(config.MEMORY_DB_PATH)
                            vector_db = memory_db if memory_db.is_absolute() else self.root / memory_db
                            upsert_memory(item_id, content, vector, str(vector_db), metadata)
                        except Exception as exc:
                            # SQLite/FTS remains durable when an embedding service is
                            # unavailable; the next maintenance run can retry it.
                            logger.warning("Warm vector promotion failed for %s: %s", item_id, exc)
                    conn.execute("DELETE FROM incoming_memories WHERE id=?", (row_id,))
                except (ValueError, TypeError) as exc:
                    logger.warning("Skipping invalid memory queue item %s: %s", row_id, exc)
            conn.commit()
            logger.info("Memory consolidation complete: %s new items", consolidated)
            return consolidated

    def _extract_triplets(self, content: str) -> list[tuple[str, str, str]]:
        """从记忆文本中正则/启发式智能抽取实体-关系-实体三元组。"""
        import re
        triplets = []
        # 1. 抽取偏好与关联模式 (e.g., User prefers Python)
        pref_matches = re.findall(r"([A-Za-z0-9_\u4e00-\u9fff]+)\s*(?:偏好|喜欢|推荐|使用|采用)\s*([A-Za-z0-9_\u4e00-\u9fff]+)", content)
        for src, tgt in pref_matches:
            if len(src) >= 2 and len(tgt) >= 2:
                triplets.append((src, tgt, "prefers"))

        # 2. 抽取依赖与架构关系 (e.g., Daming Agent depends on FastAPI)
        dep_matches = re.findall(r"([A-Za-z0-9_\u4e00-\u9fff]+)\s*(?:依赖|基于|运行在|部署在)\s*([A-Za-z0-9_\u4e00-\u9fff]+)", content)
        for src, tgt in dep_matches:
            if len(src) >= 2 and len(tgt) >= 2:
                triplets.append((src, tgt, "depends_on"))

        # 3. 抽取包含与配置关系 (e.g., Config contains API_KEY)
        has_matches = re.findall(r"([A-Za-z0-9_\u4e00-\u9fff]+)\s*(?:包含|配置|设置|绑定)\s*([A-Za-z0-9_\u4e00-\u9fff]+)", content)
        for src, tgt in has_matches:
            if len(src) >= 2 and len(tgt) >= 2:
                triplets.append((src, tgt, "configures"))

        return triplets
