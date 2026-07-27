import json
import inspect
import logging
import weakref
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Any, Optional

from .config import config

logger = logging.getLogger("daming_os.events")

class Event:
    """Base class for all Daming OS events."""
    def to_record(self) -> Dict[str, Any]:
        return {"event_type": type(self).__name__}

class EvolutionCompletedEvent(Event):
    """Fired when the 大明成长系统 completes a successful evolution."""
    def __init__(self, proposal_id: str, diff_summary: str, scope_tags: List[str]):
        self.proposal_id = proposal_id
        self.diff_summary = diff_summary
        self.scope_tags = scope_tags

    def to_record(self) -> Dict[str, Any]:
        return {"event_type": type(self).__name__, "proposal_id": self.proposal_id,
                "diff_summary": self.diff_summary, "scope_tags": self.scope_tags}

class EvolutionTriggeredEvent(Event):
    """A durable hand-off from signal detection to a proposal workflow."""
    def __init__(self, gep_score: float, events: List[Dict[str, Any]], proposal_id: Optional[str] = None):
        self.gep_score = gep_score
        self.events = events
        self.proposal_id = proposal_id

    def to_record(self) -> Dict[str, Any]:
        return {"event_type": type(self).__name__, "gep_score": self.gep_score,
                "events": self.events, "proposal_id": self.proposal_id}

class LogEvent(Event):
    """Fired when an Agent encounters an error or discovers a new finding."""
    def __init__(self, log_type: str, content: str, metadata: dict = None):
        self.log_type = log_type
        self.content = content
        self.metadata = metadata or {}

    def to_record(self) -> Dict[str, Any]:
        return {"event_type": type(self).__name__, "log_type": self.log_type,
                "content": self.content, "metadata": self.metadata}

class AgentLifecycleEvent(Event):
    """Portable telemetry for a turn, tool, model, or policy decision.

    ``phase`` is deliberately an open string so every framework can map its
    native lifecycle without Daming OS acquiring a vendor-specific dependency.
    """
    def __init__(self, phase: str, *, agent_id: str, session_id: str,
                 tenant_id: Optional[str] = None, trace_id: Optional[str] = None,
                 attributes: Optional[Dict[str, Any]] = None):
        self.phase = phase
        self.agent_id = agent_id
        self.session_id = session_id
        self.tenant_id = tenant_id
        self.trace_id = trace_id
        self.attributes = attributes or {}

    def to_record(self) -> Dict[str, Any]:
        return {"event_type": type(self).__name__, "phase": self.phase,
                "agent_id": self.agent_id, "session_id": self.session_id,
                "tenant_id": self.tenant_id, "trace_id": self.trace_id,
                "attributes": self.attributes}

class EventBus:
    """A simple synchronous Pub/Sub event bus to decouple subsystems."""
    def __init__(self, event_log_path: Optional[str] = None):
        self._subscribers: Dict[type, List[Any]] = {}
        configured_path = event_log_path or config.EVENT_LOG_PATH
        path = Path(configured_path)
        self.event_log_path = path if path.is_absolute() else Path(config.WORKSPACE_ROOT) / path

    def subscribe(self, event_type: type, callback: Callable[[Event], None]):
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        # Runtime coordinators subscribe with bound methods.  Keeping a strong
        # reference here would retain a stopped Agent and its closed databases.
        reference: Any = weakref.WeakMethod(callback) if inspect.ismethod(callback) else callback
        self._subscribers[event_type].append(reference)

    def unsubscribe(self, event_type: type, callback: Callable[[Event], None]) -> None:
        """Remove a runtime callback during orderly host shutdown."""
        retained = []
        for reference in self._subscribers.get(event_type, []):
            current = reference() if isinstance(reference, weakref.WeakMethod) else reference
            if current is not None and current != callback:
                retained.append(reference)
        self._subscribers[event_type] = retained

    def configure_log_path(self, event_log_path: str) -> None:
        """Point the durable event log at a host workspace after bootstrap.

        The bus itself deliberately remains process-global so independently
        loaded modules share lifecycle events.  Its storage location, however,
        must be selected by the runtime that owns the host workspace.
        """
        path = Path(event_log_path)
        self.event_log_path = path if path.is_absolute() else Path(config.WORKSPACE_ROOT) / path

    def publish(self, event: Event):
        self._persist(event)
        event_type = type(event)
        subscribers = self._subscribers.get(event_type, [])
        live = []
        for reference in subscribers:
            callback = reference() if isinstance(reference, weakref.WeakMethod) else reference
            if callback is None:
                continue
            live.append(reference)
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Error in event subscriber {callback}: {e}")
        self._subscribers[event_type] = live

    def _persist(self, event: Event) -> None:
        """Append an interoperable JSONL event without coupling to any Agent host."""
        try:
            self.event_log_path.parent.mkdir(parents=True, exist_ok=True)
            record = event.to_record()
            record["timestamp"] = datetime.now(timezone.utc).isoformat()
            with self.event_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error("Failed to persist event: %s", exc)

# Global event bus instance
bus = EventBus()
