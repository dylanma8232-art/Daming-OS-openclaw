import sqlite3
import json
import logging
from typing import Dict, List, Any, Optional

from ..config import config

logger = logging.getLogger("daming_os.memory.db")

class HardenedSQLiteManager:
    """
    Manager for interacting with the main memory metadata SQLite database.
    """
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or config.SQLITE_META_PATH

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_adjacency_list(self) -> Dict[str, List[Dict[str, Any]]]:
        """从 SQLite wiki_edges 加载邻接表，并根据 link_type 进行多维语义扩散加权"""
        adj_list = {}
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='wiki_edges'")
                if not cursor.fetchone():
                    return adj_list

                # 3.0 DDL 列名变更为 source_node, target_node, 包含 link_type 分类
                cursor.execute("SELECT source_node, target_node, link_type FROM wiki_edges")
                for row in cursor.fetchall():
                    src = row['source_node']
                    target = row['target_node']
                    link_type = row['link_type']
                    
                    # 根据语义分类 link_type 赋予动态扩散能量权重
                    if link_type == "depends_on":
                        weight = 1.0
                    elif link_type == "causes":
                        weight = 0.9
                    elif link_type == "extends":
                        weight = 0.8
                    else:
                        weight = 0.5
                        
                    if src not in adj_list:
                        adj_list[src] = []
                    adj_list[src].append({"target_node": target, "weight": weight})
        except Exception as e:
            logger.warning(f"Failed to get adjacency list: {e}")
        return adj_list

    def get_top_items(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Fallback method to get top items if vector search fails"""
        items = []
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='items'")
                if not cursor.fetchone():
                    return items

                cursor.execute(
                    "SELECT item_id, title, created_at FROM items ORDER BY created_at DESC LIMIT ?", 
                    (limit,)
                )
                for row in cursor.fetchall():
                    items.append({
                        "item_id": row["item_id"],
                        "title": row["title"],
                        "created_at": row["created_at"]
                    })
        except Exception as e:
            logger.warning(f"Failed to get top items: {e}")
        return items

    def get_item_meta(self, item_id: str) -> Optional[Dict[str, Any]]:
        """获取节点的元数据 (importance, category 等)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='items'")
                if not cursor.fetchone():
                    return None

                cursor.execute(
                    "SELECT importance, category FROM items WHERE item_id = ?", 
                    (item_id,)
                )
                row = cursor.fetchone()
                if row:
                    return {"importance": row["importance"], "category": row["category"]}
        except Exception as e:
            logger.warning(f"Failed to get item meta for {item_id}: {e}")
        return None

def fts5_search(query: str, db_path: Optional[str] = None, top_k: int = 10) -> List[Dict[str, Any]]:
    """SQLite FTS5 稀疏关键字检索"""
    db_path = db_path or config.SQLITE_META_PATH
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 确保 FTS5 表存在
        cursor.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                item_id, content
            )
        ''')
        
        # 清洗查询文本，转换为 FTS5 MATCH 语法
        clean_query = ''.join(c if c.isalnum() else ' ' for c in query)
        match_query = ' OR '.join(w for w in clean_query.split() if w)
        
        if not match_query:
            return []
            
        cursor.execute('''
            SELECT item_id, rank
            FROM memory_fts
            WHERE memory_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        ''', (match_query, top_k))
        
        results = []
        for row in cursor.fetchall():
            item_id = row[0]
            rank = row[1]
            results.append({"id": item_id, "score": abs(rank), "source": "fts5"})
        return results
    except Exception as e:
        logger.warning(f"FTS5 search failed: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()

def get_markdown_content(item_id: str, sqlite_manager: HardenedSQLiteManager) -> Optional[str]:
    """Retrieves physical content for an item"""
    try:
        with sqlite_manager._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='items'")
            if not cursor.fetchone():
                return None
            
            cursor.execute("SELECT content FROM items WHERE item_id = ?", (item_id,))
            row = cursor.fetchone()
            if row:
                return row["content"]
    except Exception as e:
        logger.warning(f"Failed to fetch content for {item_id}: {e}")
    return None

def queue_incoming_memory(session_key: str, memory_data: Dict[str, Any], db_path: Optional[str] = None) -> bool:
    """无锁单向写入: 将新记忆存入 incoming_memories 队列"""
    db_path = db_path or config.SQLITE_META_PATH
    try:
        conn = sqlite3.connect(db_path)
        conn.execute('PRAGMA journal_mode=WAL')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS incoming_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_key TEXT,
                data_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute(
            'INSERT INTO incoming_memories (session_key, data_json) VALUES (?, ?)',
            (session_key, json.dumps(memory_data, ensure_ascii=False))
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to queue memory: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()
