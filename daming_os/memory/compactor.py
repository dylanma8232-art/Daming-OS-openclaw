import logging
import re
from typing import List, Dict, Any, Union, Optional
from ..llm import LLMProvider

logger = logging.getLogger("daming_os.memory.compactor")

class MessageCompactor:
    """
    Message Compactor for Daming OS.
    Provides token estimation, information density calculation,
    and message compaction (both heuristic-based and LLM-assisted).
    """
    def __init__(self, llm_provider: Optional[LLMProvider] = None):
        self.llm_provider = llm_provider or LLMProvider()

    def estimate_tokens(self, text: str) -> int:
        """Estimate the number of tokens in a given text."""
        if not text:
            return 0
        try:
            import tiktoken
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text))
        except ImportError:
            # Fallback estimation
            cjk_count = len(re.findall(r'[\u4e00-\u9fff]', text))
            other_text = re.sub(r'[\u4e00-\u9fff]', '', text)
            words = other_text.split()
            # Heuristic: 1 token per CJK char, 1.3 tokens per English word
            return int(cjk_count + len(words) * 1.3)

    def calculate_info_density(self, text: str, score: float) -> float:
        """Calculate the information density of a text given a relevance score."""
        tokens = self.estimate_tokens(text)
        if tokens <= 0:
            return 0.0
        return score / tokens

    def compact_sync(self, messages: Union[str, List[Dict[str, Any]]], max_tokens: int) -> Union[str, List[Dict[str, Any]]]:
        """
        Compact messages or text to fit within max_tokens.
        If a string is passed, it uses LLM to summarize/compress it.
        If a list of message dicts is passed, it filters them based on information density,
        or truncates them, or uses LLM to compress them.
        """
        if isinstance(messages, str):
            curr_tokens = self.estimate_tokens(messages)
            if curr_tokens <= max_tokens:
                return messages
            
            # Use LLM to compress the text
            prompt = f"Please compress the following text to fit within {max_tokens} tokens while retaining all critical information, core decisions, and context:\n\n{messages}"
            try:
                compressed = self.llm_provider.complete([
                    {"role": "system", "content": "You are a highly efficient text compressor. Your goal is to rewrite the input text to be as concise as possible without losing key meaning or specific data."},
                    {"role": "user", "content": prompt}
                ])
                return compressed
            except Exception as e:
                logger.error(f"Failed to compress text using LLM: {e}. Falling back to character truncation.")
                # Fallback: simple character truncation based on estimation
                ratio = max_tokens / max(1, curr_tokens)
                keep_chars = int(len(messages) * ratio)
                return messages[:keep_chars] + "... [Truncated]"

        elif isinstance(messages, list):
            # List of messages/items. Let's compact by filtering by information density or trimming.
            # We first estimate total tokens
            total_tokens = 0
            item_tokens = []
            for msg in messages:
                content = msg.get("content", "")
                tokens = self.estimate_tokens(content)
                total_tokens += tokens
                item_tokens.append(tokens)
            
            if total_tokens <= max_tokens:
                return messages

            # If we need to compact, we can first filter items by their score/importance (if available) or density
            # Let's calculate info density for each item
            scored_items = []
            for i, msg in enumerate(messages):
                content = msg.get("content", "")
                score = msg.get("final_score", msg.get("score", msg.get("rrf_score", 0.5)))
                density = self.calculate_info_density(content, score)
                scored_items.append({
                    "index": i,
                    "msg": msg,
                    "density": density,
                    "tokens": item_tokens[i]
                })

            # Sort by density descending
            scored_items.sort(key=lambda x: x["density"], reverse=True)

            selected = []
            accumulated_tokens = 0
            # Keep items with highest density until max_tokens is reached
            for item in scored_items:
                if accumulated_tokens + item["tokens"] <= max_tokens:
                    selected.append(item)
                    accumulated_tokens += item["tokens"]
                else:
                    # Try to compress the individual item to fit the remaining space
                    remaining_tokens = max_tokens - accumulated_tokens
                    if remaining_tokens > 10:
                        content = item["msg"].get("content", "")
                        comp_content = self.compact_sync(content, remaining_tokens)
                        new_msg = dict(item["msg"])
                        new_msg["content"] = comp_content
                        # Update tokens
                        item["tokens"] = self.estimate_tokens(comp_content)
                        if accumulated_tokens + item["tokens"] <= max_tokens:
                            selected.append(item)
                            accumulated_tokens += item["tokens"]
                    break

            # Restore original order
            selected.sort(key=lambda x: x["index"])
            return [item["msg"] for item in selected]
        
        return messages
