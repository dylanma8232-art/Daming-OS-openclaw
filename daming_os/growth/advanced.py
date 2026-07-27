"""Remaining Growth 2.0 capabilities wired into the standalone runtime.

These services replace the former host-specific implementations: the council,
meta-prompt evolution, command listener, workflow distillation and plugin
container all persist local, inspectable artifacts and feed the same proposal
and approval chain as ordinary growth events.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Protocol

from .learning import ExperienceStore, SkillDistiller


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CouncilRole(Protocol):
    def review(self, proposal: Dict[str, Any], role: str) -> Dict[str, Any]: ...


class DeterministicCouncil:
    """Default Builder/Reviewer/Judge council, replaceable by model providers."""
    def review(self, proposal: Dict[str, Any], role: str) -> Dict[str, Any]:
        payload = proposal.get("payload", {})
        complete = all(isinstance(payload.get(key), str) and payload[key].strip()
                       for key in ("target_file", "proposed_code"))
        if payload.get("artifact_type") == "skill":
            complete = complete and str(payload["target_file"]).endswith("/SKILL.md")
        return {"role": role, "score": 85.0 if complete else 0.0,
                "evidence": "complete explicit code proposal" if complete else "missing deployable proposal payload"}


class ThreePartyCouncil:
    """Runs Builder → Reviewer → Judge and writes auditable consensus evidence."""
    def __init__(self, path: str, role_provider: Optional[CouncilRole] = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.role_provider = role_provider or DeterministicCouncil()

    def review(self, proposal: Dict[str, Any]) -> Dict[str, Any]:
        opinions = [self.role_provider.review(proposal, role) for role in ("builder", "reviewer", "judge")]
        score = min(float(opinion.get("score", 0)) for opinion in opinions)
        entry = {"timestamp": _now(), "proposal_id": proposal["id"], "opinions": opinions, "score": score}
        with self.path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return {"builder": "three-party-builder", "reviewer": "three-party-reviewer", "score": score,
                "judge": opinions[2], "opinions": opinions}


class WorkflowDistillation:
    """Turns every verified experience into a reviewable Skill candidate."""
    def __init__(self, experiences: ExperienceStore, output_dir: str):
        self.experiences = experiences
        self.distiller = SkillDistiller(output_dir)

    def run(self) -> Dict[str, Any]:
        outputs = []
        for learning in self.experiences.candidates(min_confidence=0.0):
            outputs.append(str(self.distiller.distill(learning)))
        return {"distilled": len(outputs), "paths": outputs}


class GrowthEventPipeline:
    """Jaccard-clusters recurring growth events into durable experiences."""
    def __init__(self, experiences: ExperienceStore, threshold: float = .45):
        self.experiences, self.threshold = experiences, threshold

    @staticmethod
    def _tokens(event: Dict[str, Any]) -> set:
        return set(re.findall(r"[\w\u4e00-\u9fff]+", str(event.get("content", "")).lower()))

    def extract(self, events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        clusters = []
        for event in events:
            tokens = self._tokens(event)
            if not tokens:
                continue
            for cluster in clusters:
                overlap = len(tokens & cluster["tokens"]) / max(1, len(tokens | cluster["tokens"]))
                if overlap >= self.threshold:
                    cluster["events"].append(event); cluster["tokens"] |= tokens; break
            else:
                clusters.append({"tokens": tokens, "events": [event]})
        created = []
        for cluster in clusters:
            if len(cluster["events"]) < 2:
                continue
            pattern = " ".join(sorted(cluster["tokens"])[:12])
            created.append(self.experiences.create(pattern=pattern, lesson="Recurring event cluster detected.",
                                                   action_item="review and turn into a verified practice.",
                                                   confidence=min(.95, .5 + .1 * len(cluster["events"])),
                                                   source_events=cluster["events"]))
        return {"clusters": len(clusters), "experiences": created}


class MetaPromptRewriter:
    """Creates deterministic prompt-improvement proposals from health evidence."""
    def __init__(self, directory: str, proposals: Any = None):
        self.directory = Path(directory)
        self.proposals = proposals

    def rewrite(self, events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        items = list(events)
        failures = [event for event in items if event.get("log_type") in {"task_failure", "system_error", "rule_violation"}]
        digest = hashlib.sha256(json.dumps(failures[-20:], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
        proposal = {
            "id": f"meta-{digest}", "created_at": _now(), "kind": "meta_prompt_rewrite",
            "instruction": "Before executing, validate assumptions, scope and safety constraints.",
            "evidence_count": len(failures), "source_events": failures[-20:],
            "requires_approval": True,
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{proposal['id']}.json"
        path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
        # The JSON record is evidence, not a dead-end.  Route the proposed
        # operating rule through the same council/OTP/deploy chain as all other
        # evolution so any host can safely activate it.
        if self.proposals is not None:
            proposal["growth_proposal_id"] = self.proposals.create({
                "kind": "meta_prompt_rewrite",
                "artifact_type": "skill",
                "target_file": f"skills/auto-generated/{proposal['id']}/SKILL.md",
                "proposed_code": "# Meta prompt improvement\\n\\n" + proposal["instruction"] + "\\n",
                "source_events": proposal["source_events"],
            })
            path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"proposal": proposal, "path": str(path)}


class XuexiCommandListener:
    """Portable `/xuexi` gateway command: persist an immediate learning candidate."""
    COMMAND = "/xuexi"

    def __init__(self, experiences: ExperienceStore):
        self.experiences = experiences

    def consume(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if not text.strip().startswith(self.COMMAND):
            return None
        lesson = text.strip()[len(self.COMMAND):].strip()
        if not lesson:
            return None
        return self.experiences.create(pattern=lesson[:120], lesson=lesson,
                                       action_item="review and apply this learning", confidence=0.8,
                                       source_events=[metadata or {}], status="verified")


class DynamicKnowledgeGraph:
    """Extract Markdown wikilinks and atomically rebuild a portable graph store."""
    LINK = re.compile(r"\[\[([^\]]+)\]\]")

    def __init__(self, wiki_directory: str, graph: Any):
        self.wiki_directory = Path(wiki_directory)
        self.graph = graph

    def rebuild(self) -> Dict[str, int]:
        nodes = edges = 0
        for page in self.wiki_directory.rglob("*.md") if self.wiki_directory.exists() else []:
            source = page.stem
            nodes += 1
            for target in self.LINK.findall(page.read_text(encoding="utf-8")):
                target_path = self.wiki_directory / (target.strip() + ("" if target.strip().endswith(".md") else ".md"))
                target_id = target_path.stem if target_path.exists() else Path(target.strip()).stem
                self.graph.link(source, target_id, "wikilink", .8)
                edges += 1
        return {"nodes": nodes, "edges": edges}
