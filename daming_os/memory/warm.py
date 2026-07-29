import logging
import json
import math
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger("daming_os.memory.warm")

def vector_search(query: str, query_vector: List[float], db_path: str, top_k: int = 20) -> List[Dict[str, Any]]:
    """LanceDB Dense Vector Search. Layer 2 (Warm)."""
    try:
        import lancedb
        db = lancedb.connect(db_path)
        tables = db.list_tables()
        if hasattr(tables, 'tables'):
            table_names = list(tables.tables)
        else:
            table_names = list(tables) if isinstance(tables, (list, tuple)) else []

        if 'learnings' in table_names:
            table = db.open_table('learnings')
            results = table.search(query_vector).limit(top_k).to_list()
            return [{"id": r.get("item_id", r.get("id", "")), "score": 1.0 - r.get("_distance", 0), "source": "lancedb"} for r in results]
    except ModuleNotFoundError:
        # LanceDB is an optional accelerator; the local vector fallback is the
        # supported zero-dependency path.
        pass
    except Exception as e:
        logger.warning(f"LanceDB search failed: {e}")
    fallback = Path(db_path) / "fallback-vectors.json"
    if fallback.exists():
        try:
            records = json.loads(fallback.read_text(encoding="utf-8"))
            def similarity(record: Dict[str, Any]) -> float:
                vector = record.get("vector", [])
                dot = sum(a * b for a, b in zip(query_vector, vector))
                norm = math.sqrt(sum(a*a for a in query_vector)) * math.sqrt(sum(b*b for b in vector))
                return dot / norm if norm else 0.0
            ranked = sorted(records, key=similarity, reverse=True)[:top_k]
            return [{"id": record["item_id"], "score": similarity(record), "source": "local-vector"} for record in ranked]
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Local vector fallback failed: %s", exc)
    return []


def upsert_memory(item_id: str, content: str, vector: List[float], db_path: str,
                  metadata: Optional[Dict[str, Any]] = None) -> bool:
    """Persist a consolidated memory into the Warm vector layer.

    The original OpenClaw deployment received this write from its memory plugin.
    Standalone Daming OS must own it, otherwise its LanceDB query path is empty.
    """
    if not vector:
        return False
    try:
        import lancedb
        db = lancedb.connect(db_path)
        tables = db.list_tables()
        names = list(tables.tables) if hasattr(tables, "tables") else list(tables)
        record = {
            "item_id": item_id,
            "text": content,
            "vector": vector,
            "importance": float((metadata or {}).get("importance", .5)),
            "category": str((metadata or {}).get("category", "memory")),
            "q_value": float((metadata or {}).get("q_value", .5)),
        }
        if "learnings" in names:
            table = db.open_table("learnings")
            # item_id is SHA-256 in the standard consolidator and therefore safe
            # to interpolate after the defensive quote replacement.
            table.delete("item_id = '" + item_id.replace("'", "''") + "'")
            table.add([record])
        else:
            db.create_table("learnings", data=[record])
        return True
    except ModuleNotFoundError:
        logger.debug("LanceDB is not installed; writing the local vector fallback")
        try:
            directory = Path(db_path); directory.mkdir(parents=True, exist_ok=True)
            path = directory / "fallback-vectors.json"
            rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            rows = [row for row in rows if row.get("item_id") != item_id]
            rows.append({"item_id": item_id, "text": content, "vector": vector, **(metadata or {})})
            path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            return True
        except (OSError, ValueError, TypeError) as fallback_exc:
            logger.warning("Local vector fallback upsert failed: %s", fallback_exc)
            return False
    except Exception as exc:
        logger.warning("LanceDB upsert failed: %s", exc)
        try:
            directory = Path(db_path); directory.mkdir(parents=True, exist_ok=True)
            path = directory / "fallback-vectors.json"
            rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            rows = [row for row in rows if row.get("item_id") != item_id]
            rows.append({"item_id": item_id, "text": content, "vector": vector, **(metadata or {})})
            path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            return True
        except (OSError, ValueError, TypeError) as fallback_exc:
            logger.warning("Local vector fallback upsert failed: %s", fallback_exc)
            return False
