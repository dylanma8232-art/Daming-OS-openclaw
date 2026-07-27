"""Host-neutral integration contracts for Daming OS.

An agent framework only needs to translate its lifecycle into these records;
this module deliberately contains no OpenClaw, HTTP, or vendor SDK imports.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from .events import AgentLifecycleEvent, LogEvent, bus
from .memory.core import MemorySystem
from .memory.governance import MemoryScope


@dataclass(frozen=True)
class AgentContext:
    agent_id: str
    session_id: str
    tenant_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AgentAdapter(Protocol):
    """Minimal lifecycle contract implementable by any agent host."""
    def before_turn(self, user_input: str, context: AgentContext) -> Any: ...
    def after_turn(self, user_input: str, output: Any, context: AgentContext) -> None: ...
    def on_error(self, error: Exception, context: AgentContext) -> None: ...


class DamingAdapter:
    """Reference adapter that hosts can subclass or delegate to."""
    def __init__(self, memory: Optional[MemorySystem] = None, auto_recall: bool = True):
        self.memory = memory or MemorySystem()
        self.auto_recall = auto_recall

    def before_turn(self, user_input: str, context: AgentContext) -> Any:
        self._publish_lifecycle("turn.started", context)
        if not self.auto_recall:
            return []
        scope = MemoryScope(context.tenant_id, context.agent_id, context.session_id)
        return self.memory.query(user_input, messages=context.metadata.get("messages"), scope=scope)

    def after_turn(self, user_input: str, output: Any, context: AgentContext) -> None:
        metadata = {**context.metadata, "agent_id": context.agent_id,
                    "tenant_id": context.tenant_id, "session_id": context.session_id}
        scope = MemoryScope(context.tenant_id, context.agent_id, context.session_id)
        self.memory.store(f"Input: {user_input} | Result: {output}", metadata, context.session_id, scope)
        bus.publish(LogEvent("task_complete", "Agent task completed successfully.", metadata))
        self._publish_lifecycle("turn.completed", context)

    def on_error(self, error: Exception, context: AgentContext) -> None:
        metadata = {**context.metadata, "agent_id": context.agent_id,
                    "tenant_id": context.tenant_id, "session_id": context.session_id}
        bus.publish(LogEvent("task_failure", str(error), metadata))
        self._publish_lifecycle("turn.failed", context, {"error_type": type(error).__name__})

    def _publish_lifecycle(self, phase: str, context: AgentContext,
                           attributes: Optional[Dict[str, Any]] = None) -> None:
        trace_id = context.metadata.get("trace_id")
        bus.publish(AgentLifecycleEvent(
            phase, agent_id=context.agent_id, session_id=context.session_id,
            tenant_id=context.tenant_id, trace_id=trace_id, attributes=attributes,
        ))
