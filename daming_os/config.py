"""Dependency-free process defaults used by the Daming OS runtime."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass
class AgentOSConfig:
    WORKSPACE_ROOT: str = field(default_factory=lambda: os.getenv("DAMING_OS_WORKSPACE", os.getcwd()))
    MEMORY_DB_PATH: str = field(default_factory=lambda: os.getenv("DAMING_OS_MEMORY_DB", "memory/lancedb"))
    SQLITE_META_PATH: str = field(default_factory=lambda: os.getenv("DAMING_OS_SQLITE_META", "memory/memory_meta.db"))
    WIKI_DIR: str = field(default_factory=lambda: os.getenv("DAMING_OS_WIKI_DIR", "wiki/main"))
    HOT_MEMORY_DIR: str = field(default_factory=lambda: os.getenv("DAMING_OS_HOT_MEMORY_DIR", "memory/hot"))
    SKILL_CANDIDATE_DIR: str = field(default_factory=lambda: os.getenv("DAMING_OS_SKILL_CANDIDATE_DIR", "growth/skill-candidates"))
    EVENT_LOG_PATH: str = field(default_factory=lambda: os.getenv("DAMING_OS_EVENT_LOG", "memory/event_logs.jsonl"))
    GROWTH_DB_PATH: str = field(default_factory=lambda: os.getenv("DAMING_OS_GROWTH_DB", "memory/growth_system.db"))
    SANDBOX_BASE_DIR: str = field(default_factory=lambda: os.getenv("DAMING_OS_SANDBOX_DIR", "/tmp/daming-os-sandbox"))
    PROPOSAL_DIR: str = field(default_factory=lambda: os.getenv("DAMING_OS_PROPOSAL_DIR", "memory/evolution-proposals"))
    GEP_THRESHOLD: float = field(default_factory=lambda: _float_env("DAMING_OS_GEP_THRESHOLD", 3.0))
    MAX_SANDBOX_RETRIES: int = field(default_factory=lambda: _int_env("DAMING_OS_MAX_SANDBOX_RETRIES", 2))


config = AgentOSConfig()
