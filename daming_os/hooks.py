"""Host-hook bridge for the standalone Daming OS runtime.

Agent frameworks do not share one hook API.  This bridge deliberately accepts a
small registrar function instead of importing a particular framework: LangGraph,
an in-house agent, or any hook-capable runner can register the same callbacks.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .adapter import AgentContext, DamingAdapter


class DamingHookBridge:
    """Translate common agent hook payloads into the Daming lifecycle contract."""

    def __init__(self, adapter: Optional[DamingAdapter] = None,
                 context_factory: Optional[Callable[[Dict[str, Any]], AgentContext]] = None,
                 before_turn_callback: Optional[Callable[[str, AgentContext], Any]] = None,
                 after_turn_callback: Optional[Callable[[], Any]] = None,
                 skill_context_callback: Optional[Callable[[str, AgentContext], str]] = None):
        self.adapter = adapter or DamingAdapter()
        self.context_factory = context_factory or self._default_context
        self.before_turn_callback = before_turn_callback
        self.after_turn_callback = after_turn_callback
        self.skill_context_callback = skill_context_callback

    @staticmethod
    def _default_context(payload: Dict[str, Any]) -> AgentContext:
        return AgentContext(
            agent_id=str(payload.get("agent_id", "default-agent")),
            session_id=str(payload.get("session_id", "default-session")),
            tenant_id=payload.get("tenant_id"),
            metadata=dict(payload.get("metadata", {})),
        )

    def before_turn(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        context = self.context_factory(payload)
        user_input = str(payload.get("input", payload.get("user_input", "")))
        if self.before_turn_callback is not None:
            payload["daming_command"] = self.before_turn_callback(user_input, context)
        signals = payload.get("growth_signals", [])
        if isinstance(signals, list):
            for signal in signals:
                if isinstance(signal, dict):
                    self.adapter.record_signal(str(signal.get("type", "")), str(signal.get("content", "")), context)
        for field, kind in (("feedback", "user_feedback"), ("discovery", "discovery"),
                            ("rule_violation", "rule_violation"), ("system_error", "system_error")):
            if payload.get(field):
                self.adapter.record_signal(kind, str(payload[field]), context)
        payload["daming_memories"] = self.adapter.before_turn(user_input, context)
        messages = context.metadata.get("messages")
        if isinstance(messages, list):
            # This makes the blueprint's heuristic compaction available on the
            # standard hook path rather than as a disconnected utility.
            payload["messages"] = self.adapter.compact_context(context)
        if self.skill_context_callback is not None:
            skill_context = self.skill_context_callback(user_input, context)
            if skill_context:
                payload.setdefault("messages", []).append({"role": "system", "content": skill_context})
        return payload

    def after_turn(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        context = self.context_factory(payload)
        self.adapter.after_turn(str(payload.get("input", payload.get("user_input", ""))),
                                payload.get("output", ""), context)
        if self.after_turn_callback is not None:
            payload["daming_maintenance"] = self.after_turn_callback()
        return payload

    def on_error(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        context = self.context_factory(payload)
        error = payload.get("error")
        self.adapter.on_error(error if isinstance(error, Exception) else RuntimeError(str(error)), context)
        return payload

    def install(self, register: Callable[[str, Callable[[Dict[str, Any]], Dict[str, Any]]], Any]) -> None:
        """Install into a host exposing ``register(event_name, callback)``.

        Hosts with different naming conventions can call the individual methods
        directly; no OpenClaw hook, SDK, or daemon is required.
        """
        register("before_turn", self.before_turn)
        register("after_turn", self.after_turn)
        register("error", self.on_error)
