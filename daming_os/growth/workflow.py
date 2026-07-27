"""A recoverable, host-neutral evolution workflow.

The workflow never writes application code itself.  A host supplies validation,
approval, deployment and verification adapters, which keeps Daming OS portable
and makes the dangerous step explicit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Protocol

from .proposals import ProposalStore


class ProposalValidator(Protocol):
    def validate(self, proposal: Dict[str, Any]) -> None: ...


class ApprovalProvider(Protocol):
    def is_approved(self, proposal: Dict[str, Any]) -> bool: ...


class Deployer(Protocol):
    def deploy(self, proposal: Dict[str, Any]) -> None: ...

    def rollback(self, proposal: Dict[str, Any]) -> None: ...


class Verifier(Protocol):
    def verify(self, proposal: Dict[str, Any]) -> bool: ...


@dataclass
class EvolutionWorkflow:
    """Advance one proposal one safe, durable state at a time."""
    proposals: ProposalStore
    validator: ProposalValidator
    approvals: ApprovalProvider
    deployer: Deployer
    verifier: Verifier

    def advance(self, proposal_id: str) -> str:
        proposal = self.proposals.get(proposal_id)
        state = proposal["state"]
        if state == "proposed":
            self.validator.validate(proposal)
            self.proposals.transition(proposal_id, "validated")
            return "validated"
        if state == "validated":
            if not self.approvals.is_approved(proposal):
                return "awaiting_approval"
            self.proposals.transition(proposal_id, "approved")
            return "approved"
        if state == "approved":
            self.deployer.deploy(proposal)
            self.proposals.transition(proposal_id, "deployed")
            return "deployed"
        if state == "deployed":
            if self.verifier.verify(proposal):
                self.proposals.transition(proposal_id, "verified")
                return "verified"
            self.deployer.rollback(proposal)
            self.proposals.transition(proposal_id, "rolled_back")
            return "rolled_back"
        return state
