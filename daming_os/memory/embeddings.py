"""Vendor-neutral embedding contracts for semantic memory."""
from __future__ import annotations

import json
import os
import urllib.request
import hashlib
from typing import List, Optional, Protocol


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> List[float]: ...


class OpenAICompatibleEmbeddingProvider:
    """Works with any OpenAI-compatible embedding endpoint, not one vendor."""
    def __init__(self, model: str, base_url: str, api_key: Optional[str] = None, api_key_env: str = "OPENAI_API_KEY"):
        self.model, self.base_url = model, base_url.rstrip("/")
        self.api_key = api_key or os.getenv(api_key_env, "")

    def embed(self, text: str) -> List[float]:
        if not self.api_key:
            raise RuntimeError("An embedding API key is required")
        request = urllib.request.Request(
            self.base_url + "/embeddings",
            data=json.dumps({"model": self.model, "input": text[:8000]}).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return list(json.loads(response.read())["data"][0]["embedding"])


class LocalHashEmbeddingProvider:
    """Offline 2048-dimensional fallback, keeping the vector pipeline live.

    A host should replace this with OpenAICompatibleEmbeddingProvider for model
    quality; the fallback guarantees the default standalone runtime never loses
    the whitepaper's vector write/recall data path because credentials are absent.
    """
    def __init__(self, dimensions: int = 2048): self.dimensions = dimensions
    def embed(self, text: str) -> List[float]:
        vector = [0.0] * self.dimensions
        for token in text.lower().split():
            index = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % self.dimensions
            vector[index] += 1.0
        return vector
