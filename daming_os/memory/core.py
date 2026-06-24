import logging
import collections
from typing import List, Dict, Any, Optional

from ..config import config
from ..events import bus, EvolutionCompletedEvent
from .cache import HardenedSemanticCache
from .db import HardenedSQLiteManager, get_markdown_content
from .hot import SessionStateMachine
from .warm import vector_search
from .cold import sparse_search

logger = logging.getLogger("daming_os.memory.core")

def rrf_fusion(ranked_lists: List[List[Dict[str, Any]]], k: int = 10) -> List[Dict[str, Any]]:
    """Reciprocal Rank Fusion (RRF)"""
    scores: Dict[str, float] = {}

    for ranked_list in ranked_lists:
        for rank, item in enumerate(ranked_list):
            item_id = item.get("id", "")
            if not item_id:
                continue
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)

    sorted_ids = sorted(scores.keys(), key=lambda x: -scores[x])
    return [
        {"id": mid, "rrf_score": round(scores[mid], 6)}
        for mid in sorted_ids
    ]

class SpreadingActivationTraverser:
    """Spreading Activation Traverser for Graph Search"""
    def traverse(self, seed_ids: List[str], db_path: str) -> List[Dict[str, Any]]:
        manager = HardenedSQLiteManager(db_path)
        adj_list = manager.get_adjacency_list()
        
        beta = 0.5
        gamma = 0.1
        max_depth = 2
        
        results = {}
        queue = collections.deque([(sid, 1.0, 0) for sid in seed_ids])
        visited = set(seed_ids)
        
        for sid in seed_ids:
            results[sid] = 1.0
            
        while queue:
            curr_id, activation, depth = queue.popleft()
            if depth >= max_depth:
                continue
                
            edges = adj_list.get(curr_id, [])
            for edge in edges:
                target = edge.get("target_node")
                weight = edge.get("weight", 0.5)
                
                if target:
                    new_activation = activation * weight * beta - gamma
                    if new_activation > 0:
                        results[target] = results.get(target, 0.0) + new_activation
                        if target not in visited:
                            visited.add(target)
                            queue.append((target, new_activation, depth + 1))
        
        return sorted([{"id": k, "score": v, "source": "graph"} for k, v in results.items() if k not in seed_ids], key=lambda x: -x["score"])

class MemorySystem:
    """
    Core Memory Engine implementing 3-Way RRF (Reciprocal Rank Fusion).
    Coordinates Layer 1 (Hot), Layer 2 (Warm), and Layer 3 (Cold).
    """
    def __init__(self):
        self.cache = HardenedSemanticCache()
        self.sqlite_manager = HardenedSQLiteManager()
        self.traverser = SpreadingActivationTraverser()
        self.hot_state = SessionStateMachine(session_dir="/tmp/daming_os_sessions")
        
        # Subscribe to Growth OS events to handle cache clearing neutrally
        bus.subscribe(EvolutionCompletedEvent, self._on_evolution_completed)
        logger.info("大明记忆系统 initialized. Subscribed to EvolutionCompletedEvent.")

    def _on_evolution_completed(self, event: EvolutionCompletedEvent):
        """Event Listener: Flush L1/L2 semantic cache when a new capability is evolved."""
        logger.info(f"Received EvolutionCompletedEvent for {event.proposal_id}. Flushing semantic cache.")
        self.cache.clear()

    def query(self, text: str, query_vector: Optional[List[float]] = None, top_k: int = 3, max_chars: int = 1000) -> List[Dict[str, Any]]:
        """
        Primary interface for agent retrieval.
        Attempts L1/L2 cache first, then performs 3-Way RRF fallback.
        """
        cached_result = self.cache.get(text, query_vector)
        if cached_result is not None:
            logger.debug("Cache hit for query.")
            return cached_result[:top_k]
            
        logger.debug("Cache miss. Performing 3-Way RRF retrieval.")
        
        if query_vector is None:
            # Create a dummy vector if missing
            query_vector = [0.0] * 1536
        
        # 1. Warm Layer (LanceDB Dense Vector Search)
        warm_results = vector_search(text, query_vector, db_path=config.MEMORY_DB_PATH, top_k=20)
        
        # Fallback if LanceDB has no results
        if not warm_results:
            fallback_items = self.sqlite_manager.get_top_items(limit=20)
            warm_results = [{"id": item["item_id"], "score": 0.8, "source": "vector_fallback"} for item in fallback_items]

        seed_ids = [r["id"] for r in warm_results[:5]]

        # 2. Cold Layer (SQLite FTS5 Sparse Text Search)
        cold_results = sparse_search(text, db_path=config.SQLITE_META_PATH, top_k=10)
        
        for r in cold_results[:3]:
            if r["id"] not in seed_ids:
                seed_ids.append(r["id"])

        # 3. Spreading Activation (Graph Search)
        graph_results = self.traverser.traverse(seed_ids, db_path=config.SQLITE_META_PATH)
        
        # Combine via 3-Way RRF
        fused = rrf_fusion([warm_results, graph_results, cold_results], k=10)
        
        # Hydrate with markdown content
        for res in fused:
            content = get_markdown_content(res["id"], self.sqlite_manager)
            if content:
                if len(content) > max_chars:
                    res["content"] = content[:max_chars] + "\n\n... [Content Truncated due to length limit]"
                else:
                    res["content"] = content
                
        # Apply safety/Q-value ranking & Message Compactor math
        final_results = self._apply_safety_and_qvalue(fused)[:10]

        # Store back to cache
        self.cache.set(text, final_results, query_vector)
        return final_results[:top_k]

    def _apply_safety_and_qvalue(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        bypass = []
        normal = []

        for item in results:
            item_id = item.get("id", "")
            meta = self.sqlite_manager.get_item_meta(item_id)
            if meta:
                importance = meta.get("importance") or 0.0
                category = meta.get("category") or ""
                if importance >= 0.9 or category == "security_redline":
                    item["bypass"] = True
                    bypass.append(item)
                    continue
            normal.append(item)

        for item in normal:
            similarity = item.get("rrf_score", item.get("score", 0.0))
            q_value = item.get("q_value", 0.5)
            recency = item.get("recency", 0.5)
            final_score = 0.6 * similarity + 0.3 * q_value + 0.1 * recency
            
            # Message Compactor math: ID(i) = final_score / len(text)
            content_length = len(item.get("content", item.get("id", "")))
            compactor_id = final_score / max(1, content_length)
            
            item["final_score"] = final_score
            item["information_density"] = compactor_id

        # Sort by Information Density (Message Compactor math)
        normal.sort(key=lambda x: x.get("information_density", 0), reverse=True)
        return bypass + normal

    def store(self, content: str, metadata: dict = None, session_key: str = "current") -> bool:
        """
        Store short-term memory to Layer 1 (Hot) which will eventually 
        be flushed to LanceDB and FTS5.
        """
        logger.info(f"Stored context into 大明记忆系统. Meta: {metadata}")
        return self.hot_state.write_memory(session_key, {"content": content, "meta": metadata})
