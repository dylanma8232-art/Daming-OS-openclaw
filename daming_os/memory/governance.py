"""Host-neutral controls for safe, scoped agent memory."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional, Tuple


_SECRET_PATTERNS = (
    # Common credentials.  The replacement intentionally preserves no secret text.
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|token|secret|password)\s*([:=])\s*[^\s,;]+"),
    re.compile(r"\b(?:sk|ghp|xoxb)-[A-Za-z0-9_\-]{16,}\b"),
)


@dataclass(frozen=True)
class MemoryScope:
    """The isolation boundary for a memory record.

    ``tenant_id`` should be supplied in multi-tenant deployments.  ``agent_id``
    and ``session_id`` make the same API useful for a single local agent.
    """
    tenant_id: Optional[str] = None
    agent_id: Optional[str] = None
    session_id: Optional[str] = None

    def as_metadata(self) -> Dict[str, str]:
        return {key: value for key, value in {
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
        }.items() if value}


@dataclass(frozen=True)
class MemoryPolicy:
    """Portable memory governance, independent of a model or agent host."""
    default_ttl_days: Optional[int] = 90
    redact_secrets: bool = True
    max_content_chars: int = 12_000
    protected_metadata_keys: Tuple[str, ...] = ("tenant_id", "agent_id", "session_id")

    def prepare(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        scope: Optional[MemoryScope] = None,
        now: Optional[datetime] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Return a bounded, redacted record ready for durable storage."""
        clean_content = self._redact(str(content))[:self.max_content_chars]
        clean_metadata = self._redact_mapping(metadata or {})
        if scope:
            clean_metadata.update(scope.as_metadata())
        clean_metadata["stored_at"] = (now or datetime.now(timezone.utc)).isoformat()
        if self.default_ttl_days is not None:
            expiry = (now or datetime.now(timezone.utc)) + timedelta(days=self.default_ttl_days)
            clean_metadata.setdefault("expires_at", expiry.isoformat())
        return clean_content, clean_metadata

    def is_expired(self, metadata: Dict[str, Any], now: Optional[datetime] = None) -> bool:
        raw_expiry = metadata.get("expires_at")
        if not raw_expiry:
            return False
        try:
            expiry = datetime.fromisoformat(str(raw_expiry).replace("Z", "+00:00"))
        except ValueError:
            return False
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry <= (now or datetime.now(timezone.utc))

    def _redact(self, value: str) -> str:
        if not self.redact_secrets:
            return value
        redacted = value
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub("[REDACTED]", redacted)
        return redacted

    def _redact_mapping(self, value: Dict[str, Any]) -> Dict[str, Any]:
        def clean(item: Any) -> Any:
            if isinstance(item, str):
                return self._redact(item)
            if isinstance(item, dict):
                return {str(key): clean(nested) for key, nested in item.items()}
            if isinstance(item, (list, tuple)):
                return [clean(nested) for nested in item]
            return item
        return clean(value)


def visible_to_scope(metadata: Dict[str, Any], scope: Optional[MemoryScope]) -> bool:
    """Prevent a scoped query from returning another tenant's record."""
    if scope is None or scope.tenant_id is None:
        return True
    return metadata.get("tenant_id") == scope.tenant_id
