"""Safe default implementation of the Growth 2.0 deployment adapters.

No host-specific agent runtime is assumed.  A proposal must explicitly name a
file inside the configured workspace and include complete replacement source.
The workflow still requires a successful dual review and OTP approval before
this module can write anything.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .sandbox import SandboxGate
from ..memory.extended import PathScopedRules


class WorkspaceProposalValidator:
    """AST and smoke-test validation for an explicit workspace file proposal."""
    def __init__(self, workspace: str, sandbox: Optional[SandboxGate] = None,
                 path_rules: Optional[PathScopedRules] = None):
        self.workspace = Path(workspace).resolve()
        self.sandbox = sandbox or SandboxGate()
        self.path_rules = path_rules or PathScopedRules({
            str(self.workspace / ".daming-os"): ["runtime state is not deployable"],
            str(self.workspace / "memory" / "glacier"): ["cold archive is immutable"],
        })

    def _fields(self, proposal: Dict[str, Any]) -> Tuple[Path, str]:
        payload = proposal["payload"]
        target = payload.get("target_file")
        source = payload.get("proposed_code")
        if not isinstance(target, str) or not isinstance(source, str) or not source.strip():
            raise ValueError("proposal requires target_file and non-empty proposed_code")
        path = (self.workspace / target).resolve()
        if self.workspace not in path.parents:
            raise ValueError("target_file must stay inside the configured workspace")
        return path, source

    def validate(self, proposal: Dict[str, Any]) -> None:
        path, source = self._fields(proposal)
        rules = self.path_rules.rules_for(str(path))
        if rules:
            raise ValueError("; ".join(rules))
        if proposal["payload"].get("artifact_type") == "skill":
            if path.name != "SKILL.md":
                raise ValueError("skill artifact must be stored as SKILL.md")
            if not source.lstrip().startswith("#"):
                raise ValueError("skill artifact requires a Markdown heading")
            return
        safe, evidence = self.sandbox.validate_ast(source)
        if not safe:
            raise ValueError(evidence)
        # Syntax-only modules cannot be executed safely as standalone programs;
        # explicit smoke commands are opt-in and still run inside the sandbox.
        if proposal["payload"].get("smoke_test", True):
            passed, evidence = self.sandbox.run_smoke_test(path.name, source, evo_id=proposal["id"])
            if not passed:
                raise ValueError(evidence)


class AtomicWorkspaceDeployer:
    """Atomic write with an in-workspace backup for rollback."""
    def __init__(self, workspace: str):
        self.workspace = Path(workspace).resolve()
        self.backups = self.workspace / ".daming-os" / "evolution-backups"

    def _target(self, proposal: Dict[str, Any]) -> Path:
        target = proposal["payload"].get("target_file")
        if not isinstance(target, str):
            raise ValueError("proposal requires target_file")
        path = (self.workspace / target).resolve()
        if self.workspace not in path.parents:
            raise ValueError("target_file must stay inside the configured workspace")
        return path

    def _backup(self, proposal: Dict[str, Any]) -> Path:
        return self.backups / f"{proposal['id']}.bak"

    def deploy(self, proposal: Dict[str, Any]) -> None:
        target = self._target(proposal)
        source = proposal["payload"]["proposed_code"]
        target.parent.mkdir(parents=True, exist_ok=True)
        self.backups.mkdir(parents=True, exist_ok=True)
        backup = self._backup(proposal)
        backup.write_bytes(target.read_bytes() if target.exists() else b"")
        temporary = target.with_name(f".{target.name}.{proposal['id']}.tmp")
        temporary.write_text(source, encoding="utf-8")
        os.replace(temporary, target)

    def rollback(self, proposal: Dict[str, Any]) -> None:
        target, backup = self._target(proposal), self._backup(proposal)
        if not backup.exists():
            raise RuntimeError(f"rollback backup missing for {proposal['id']}")
        temporary = target.with_name(f".{target.name}.{proposal['id']}.rollback")
        temporary.write_bytes(backup.read_bytes())
        os.replace(temporary, target)


class SyntaxVerifier:
    """Default post-deploy verifier; hosts may replace it with integration tests."""
    def verify(self, proposal: Dict[str, Any]) -> bool:
        import ast
        target = proposal["payload"].get("target_file")
        source = proposal["payload"].get("proposed_code")
        if not isinstance(target, str) or not isinstance(source, str):
            return False
        if proposal["payload"].get("artifact_type") == "skill":
            return target.endswith("/SKILL.md") and source.lstrip().startswith("#")
        try:
            ast.parse(source)
            return True
        except SyntaxError:
            return False
