"""Default Growth System 2.0 coordination outside OpenClaw."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Optional, Protocol

from ..events import EvolutionCompletedEvent, EvolutionTriggeredEvent, bus
from .governance import GrowthLedger
from .proposals import ProposalStore
from .workflow import ApprovalProvider, EvolutionWorkflow


class BuilderReviewerAudit(Protocol):
    """Return a 0-100 consensus score and durable audit evidence."""
    def review(self, proposal: Dict[str, Any]) -> Dict[str, Any]: ...


class ApprovalNotifier(Protocol):
    def notify(self, kind: str, proposal_id: str, **details: Any) -> None: ...


class JsonlApprovalNotifier:
    """Default durable outbox; any agent may read it or replace it with a chat API."""
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def notify(self, kind: str, proposal_id: str, **details: Any) -> None:
        from datetime import datetime, timezone
        # A file outbox is a routing/audit record, never an OTP secret store.
        details.pop("otp", None)
        entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "kind": kind,
                 "proposal_id": proposal_id, "details": details}
        with self.path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(entry, ensure_ascii=False) + "\n")


class LedgerApprovalProvider(ApprovalProvider):
    """Use the OTP-backed GrowthLedger as the workflow approval gate."""
    def __init__(self, ledger: GrowthLedger):
        self.ledger = ledger

    def is_approved(self, proposal: Dict[str, Any]) -> bool:
        return self.ledger.state(proposal["id"]) == "approved"


class DefaultPolicyAudit:
    """Built-in two-role evidence check used when no external reviewer exists.

    It is deliberately conservative: it only makes a complete code proposal
    eligible for the separate OTP approval step.  It cannot deploy anything.
    Hosts may replace this with a model, ticketing, or human-review provider.
    """
    def review(self, proposal: Dict[str, Any]) -> Dict[str, Any]:
        payload = proposal.get("payload", {})
        complete = all(isinstance(payload.get(key), str) and payload[key].strip()
                       for key in ("target_file", "proposed_code"))
        score = 85.0 if complete else 0.0
        return {
            "builder": "daming-default-builder-policy",
            "reviewer": "daming-default-reviewer-policy",
            "score": score,
        }


@dataclass
class GrowthCoordinator:
    """Queue every GEP proposal for review, OTP approval and safe workflow."""
    proposals: ProposalStore
    ledger: GrowthLedger
    auditor: Optional[BuilderReviewerAudit] = None
    workflow: Optional[EvolutionWorkflow] = None
    notifier: Optional[ApprovalNotifier] = None

    def __post_init__(self) -> None:
        bus.subscribe(EvolutionTriggeredEvent, self._on_trigger)

    def close(self) -> None:
        bus.unsubscribe(EvolutionTriggeredEvent, self._on_trigger)

    def _on_trigger(self, event: EvolutionTriggeredEvent) -> None:
        if event.proposal_id:
            self.ledger.queue(event.proposal_id)

    def audit_pending(self) -> Dict[str, str]:
        outcomes: Dict[str, str] = {}
        if self.auditor is None:
            return outcomes
        for proposal in self.proposals.pending():
            identifier = proposal["id"]
            if self.ledger.state(identifier) != "pending_review":
                continue
            try:
                # GEP evidence is intentionally not executable code.  Convert
                # it into a portable skill proposal so the default chain can
                # reach council and OTP review instead of dying on missing
                # target_file/proposed_code fields.
                payload = proposal["payload"]
                if not payload.get("target_file"):
                    evidence = payload.get("source_events", [])
                    lesson = "\\n".join(str(item.get("content", "")) for item in evidence[-5:]) or "Review recurring growth evidence."
                    payload.update({
                        "artifact_type": "skill",
                        "target_file": f"skills/auto-generated/evolution-{identifier}/SKILL.md",
                        "proposed_code": f"# Evolution practice {identifier}\\n\\n{lesson}\\n",
                    })
                    self.proposals.update_payload(identifier, payload)
                    proposal = self.proposals.get(identifier)
                result = self.auditor.review(proposal)
                score = float(result.get("score", 0))
                ready = self.ledger.record_review(identifier, str(result.get("builder", "builder")),
                                                  str(result.get("reviewer", "reviewer")), score)
                if ready:
                    self.issue_approval_otp(identifier)
                    outcomes[identifier] = "awaiting_approval"
                else:
                    outcomes[identifier] = "needs_revision"
            except Exception as exc:
                outcomes[identifier] = f"audit_error:{exc}"
        return outcomes

    def issue_approval_otp(self, proposal_id: str) -> str:
        token = self.ledger.issue_otp(proposal_id)
        if self.notifier is not None:
            # Persistent fallback outboxes deliberately never retain OTPs.
            # Interactive/Feishu providers receive the token in-memory.
            self.notifier.notify("approval_otp_issued", proposal_id, otp=token, ttl_minutes=10)
        return token

    def remind_overdue(self) -> Dict[str, str]:
        overdue = self.ledger.overdue()
        for proposal_id in overdue:
            if self.notifier is not None:
                self.notifier.notify("approval_overdue", proposal_id)
        return {proposal_id: "reminded" for proposal_id in overdue}

    def advance(self, proposal_id: str) -> str:
        """Advance only through the validated, OTP-approved workflow."""
        if self.workflow is None:
            return "workflow_not_configured"
        result = self.workflow.advance(proposal_id)
        if result == "verified":
            proposal = self.proposals.get(proposal_id)
            bus.publish(EvolutionCompletedEvent(
                proposal_id, f"Verified evolution: {proposal['payload'].get('target_file', proposal_id)}",
                [str(proposal['payload'].get('target_file', ''))],
            ))
        return result
