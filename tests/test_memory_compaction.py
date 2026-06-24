import unittest
import sys
import os
import re

# Add the project directory to sys.path to allow imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from daming_os.memory.compactor import MessageCompactor
from daming_os.memory.core import MemorySystem
from daming_os.middleware import attach_memory

class TestMemoryCompaction(unittest.TestCase):
    def setUp(self):
        self.compactor = MessageCompactor()

    def test_token_estimation(self):
        text = "Hello world! This is a simple test."
        tokens = self.compactor.estimate_tokens(text)
        self.assertGreater(tokens, 0)
        
        cn_text = "你好，大明天子记忆系统测试。"
        cn_tokens = self.compactor.estimate_tokens(cn_text)
        self.assertGreater(cn_tokens, 0)

    def test_heuristic_density(self):
        text1 = "Short text"
        text2 = "A much longer text that has lower density in comparison to shorter ones under the same relevance score."
        d1 = self.compactor.calculate_info_density(text1, 1.0)
        d2 = self.compactor.calculate_info_density(text2, 1.0)
        self.assertGreater(d1, d2)

    def test_compact_sync_list(self):
        messages = [
            {"content": "A" * 100, "rrf_score": 0.9, "id": "1"},
            {"content": "B" * 100, "rrf_score": 0.8, "id": "2"},
            {"content": "C" * 100, "rrf_score": 0.3, "id": "3"},
        ]
        # Restrict max_tokens so only some messages fit
        compacted = self.compactor.compact_sync(messages, max_tokens=20)
        self.assertLess(len(compacted), len(messages))
        self.assertGreater(len(compacted), 0)

    def test_middleware_regex_clean(self):
        agent_input = "User input here.\n<MemoryHint>\n[vector ID:123] Old memory context here...\n</MemoryHint>\nAnother query part."
        cleaned = re.sub(r"<MemoryHint>.*?</MemoryHint>", "", agent_input, flags=re.DOTALL).strip()
        self.assertNotIn("MemoryHint", cleaned)
        self.assertNotIn("Old memory context", cleaned)
        self.assertIn("User input here.", cleaned)
        self.assertIn("Another query part.", cleaned)

if __name__ == "__main__":
    unittest.main()
