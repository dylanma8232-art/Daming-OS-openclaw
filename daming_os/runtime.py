"""Default standalone runtime wiring for Daming OS.

This replaces the OpenClaw-only combination of hooks and native cron.  Hook
capable agents call the bridge; long-running agents may also start the bundled
scheduler.  Neither path imports an agent framework.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .adapter import DamingAdapter
from .config import config
from .events import bus
from .hooks import DamingHookBridge
from .memory.maintenance import MemoryMaintenance
from .memory.services import (BitableSyncProvider, BitableSynchronizer, FilesystemWikiProvider,
                              GlacierStore, JsonBitableProvider, MemoryReviewService,
                              SkillUsageLedger, WikiSyncProvider, WikiSynchronizer)
from .growth.inspector import ProactiveInspector
from .growth.governance import GrowthLedger
from .growth.proposals import ProposalStore
from .growth.runtime import (ApprovalNotifier, BuilderReviewerAudit,
                             GrowthCoordinator, JsonlApprovalNotifier)
from .growth.runtime import LedgerApprovalProvider
from .growth.workflow import EvolutionWorkflow
from .growth.deployment import AtomicWorkspaceDeployer, SyntaxVerifier, WorkspaceProposalValidator
from .growth.advanced import (GrowthEventPipeline, MetaPromptRewriter, ThreePartyCouncil,
                              WorkflowDistillation, XuexiCommandListener)
from .memory.graph import KnowledgeGraph
from .growth.learning import ExperienceStore
from .memory.extended import (DeepSleepMaintenance, FileTracker, MemoryHealthcheck, SessionCleaner,
                              SessionWatchdog, SicaGuard, VersionManager)
from .memory.migration import MemoryMigrator
from .growth.health import GrowthHealthInspector
from .quality import QualityGate
from .growth.release import ReleaseLedger
from .scheduling import DurableScheduler
from .scheduling import ConfigGuard
from .operations import GoldenPathStore, HealthMonitor
from .blueprint import missing_runtime_triggers
from .skills import SkillLazyLoader


class DamingRuntime:
    """Boot the standard memory, growth and maintenance loop for any host."""

    def __init__(self, workspace: str, *, adapter: Optional[DamingAdapter] = None,
                 skill_dirs: Iterable[str] = (), auditor: Optional[BuilderReviewerAudit] = None,
                 wiki_provider: Optional[WikiSyncProvider] = None,
                 bitable_provider: Optional[BitableSyncProvider] = None,
                 approval_notifier: Optional[ApprovalNotifier] = None):
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        # Existing storage modules use the package-wide configuration for their
        # relative paths.  Bootstrap it before constructing the adapter so a
        # standalone runtime really writes under the requested workspace.
        config.WORKSPACE_ROOT = str(self.workspace)
        bus.configure_log_path(str(self.workspace / "memory" / "event_logs.jsonl"))
        self.memory_migration = MemoryMigrator(str(self.workspace))
        self.memory_migration.initialize()
        self.proposals = ProposalStore()
        self.adapter = adapter or DamingAdapter(proposal_store=self.proposals)
        self.maintenance = MemoryMaintenance(str(self.workspace))
        self.deep_sleep = DeepSleepMaintenance(str(self.workspace), str(self.workspace / "memory" / "memory_meta.db"))
        self.session_cleaner = SessionCleaner(str(self.workspace / "memory" / "hot"))
        self.session_watchdog = SessionWatchdog(str(self.workspace / "memory" / "sessions"))
        self.memory_health = MemoryHealthcheck(str(self.workspace))
        self.file_tracker = FileTracker(str(self.workspace / "memory" / "file-tracker.json"))
        self.sica = SicaGuard(str(self.workspace / "memory" / "sica-audit.jsonl"))
        self.version_manager = VersionManager(str(self.workspace / "memory" / "versions.jsonl"))
        self.config_guard = ConfigGuard(str(self.workspace / "memory" / "config.hash"))
        self.system_health = HealthMonitor(str(self.workspace / "memory" / "health-reports"))
        self.golden_paths = GoldenPathStore(str(self.workspace / "memory" / "golden-paths"))
        self.reviews = MemoryReviewService(str(self.workspace))
        self.glacier = GlacierStore(str(self.workspace))
        self.skill_usage = SkillUsageLedger(str(self.workspace / "memory" / "skill-usage.jsonl"))
        self.wiki = WikiSynchronizer(
            str(self.workspace / "wiki" / "main"),
            wiki_provider or FilesystemWikiProvider(str(self.workspace / "memory" / "wiki-mirror")),
        )
        self.bitable = BitableSynchronizer(
            str(self.workspace / "memory" / "bitable-records.json"),
            bitable_provider or JsonBitableProvider(str(self.workspace / "memory" / "bitable-mirror.json")),
            str(self.workspace / "memory" / "memory_meta.db"),
        )
        self.growth_ledger = GrowthLedger()
        self.evolution_workflow = EvolutionWorkflow(
            self.proposals,
            WorkspaceProposalValidator(str(self.workspace)),
            LedgerApprovalProvider(self.growth_ledger),
            AtomicWorkspaceDeployer(str(self.workspace)),
            SyntaxVerifier(),
        )
        self.experiences = ExperienceStore()
        self.growth_pipeline = GrowthEventPipeline(self.experiences)
        self.workflow_distillation = WorkflowDistillation(
            self.experiences, str(self.workspace / "skills" / "auto-generated"))
        self.meta_prompt = MetaPromptRewriter(str(self.workspace / "memory" / "meta-prompts"), self.proposals)
        self.xuexi = XuexiCommandListener(self.experiences)
        self.council = ThreePartyCouncil(str(self.workspace / "memory" / "council-audit.jsonl"))
        self.graph = KnowledgeGraph(str(self.workspace / "memory" / "memory_meta.db"))
        self.growth = GrowthCoordinator(self.proposals, self.growth_ledger,
                                        auditor=auditor or self.council,
                                        workflow=self.evolution_workflow,
                                        notifier=approval_notifier or JsonlApprovalNotifier(
                                            str(self.workspace / "memory" / "approval-outbox.jsonl")))
        self.growth_health = GrowthHealthInspector()
        self.quality = QualityGate()
        self.release_ledger = ReleaseLedger(str(self.workspace / "memory" / "version-changelog.jsonl"))
        self.scheduler = DurableScheduler(str(self.workspace / ".daming-os" / "scheduler-state.json"))
        self.skills = SkillLazyLoader([str(self.workspace / "skills" / "auto-generated"), *skill_dirs])
        self.skills.discover()
        self.scheduler.schedule("memory-consolidator", "daily@02:00", self.adapter.memory.promote_pending_memories)
        self.scheduler.schedule("deep-sleep-agent", "daily@02:05", self.deep_sleep.run)
        self.scheduler.schedule("daily-review", "daily@23:00", self.maintenance.review)
        self.scheduler.schedule("daily-diary", "daily@23:10", lambda: self.reviews.review("daily", self._events(1)))
        self.scheduler.schedule("weekly-review", "weekly@Sun@23:00", lambda: self.reviews.review("weekly", self._events(7)))
        self.scheduler.schedule("wiki-sync", "daily@03:15", self.wiki.sync)
        self.scheduler.schedule("graph-refresh", "daily@03:17", self._refresh_graph)
        self.scheduler.schedule("bitable-sync", "daily@03:20", self.bitable.sync)
        self.scheduler.schedule("session-cleanup", "daily@03:00", self.session_cleaner.run)
        self.scheduler.schedule("session-watchdog", "every:300", self.session_watchdog.run)
        self.scheduler.schedule("memory-healthcheck", "daily@03:25", self.memory_health.run)
        self.scheduler.schedule("memory-quality-gate", "daily@03:27", self._memory_quality)
        self.scheduler.schedule("file-tracker", "daily@03:28", self._track_files)
        self.scheduler.schedule("feishu-faq-extraction", "daily@23:05", self._extract_faq)
        self.scheduler.schedule("config-guard", "daily@03:30", self._check_config)
        self.scheduler.schedule("agent-as-judge", "daily@03:35", self._judge_recent_events)
        self.scheduler.schedule("proactive-inspector", "daily@02:30", self._inspect_growth)
        self.scheduler.schedule("growth-health", "daily@02:45", self._growth_health)
        self.scheduler.schedule("growth-audit", "weekly@Wed@03:00", self.growth.audit_pending)
        self.scheduler.schedule("growth-event-clustering", "daily@02:20", self._extract_growth_experiences)
        self.scheduler.schedule("gep-reconciliation", "daily@02:25", self._reconcile_gep)
        self.scheduler.schedule("workflow-distillation", "daily@04:10", self._distill_workflows)
        self.scheduler.schedule("meta-prompt-evolution", "weekly@Wed@03:10", self._rewrite_meta_prompt)
        self.scheduler.schedule("sica-integrity", "daily@03:40", self._sica_snapshot)
        self.scheduler.schedule("glacier-archive", "weekly@Sun@23:20", self._archive_memory)
        self.scheduler.schedule("approval-reminders", "daily@09:00", self.growth.remind_overdue)
        self.scheduler.schedule("growth-workflow", "every:300", self._advance_growth)
        self.hooks = DamingHookBridge(adapter=self.adapter, before_turn_callback=self._consume_command,
                                      after_turn_callback=self.tick, skill_context_callback=self._skill_context)
        self.start()

    def _inspect_growth(self) -> Dict[str, Any]:
        proposals = ProactiveInspector(
            str(self.workspace / "memory" / "event_logs.jsonl"), self.proposals
        ).inspect()
        for identifier in proposals:
            self.growth_ledger.queue(identifier)
        return {"proposals": proposals}

    def _growth_health(self) -> Dict[str, Any]:
        checks = {
            "scheduler": lambda: bool(self.scheduler.jobs),
            "experience_freshness": lambda: bool(self._events(7)),
            "rule_compliance": lambda: not bool(self.quality.blocked()),
            "path_integrity": lambda: (self.workspace / "memory").exists(),
            "security": lambda: (self.workspace / ".daming-os").exists(),
        }
        return self.growth_health.inspect(checks)

    def _advance_growth(self) -> Dict[str, str]:
        """Advance each proposal one state; deployment remains OTP-gated."""
        outcomes: Dict[str, str] = dict(self.growth.audit_pending())
        for proposal in self.proposals.pending():
            try:
                outcomes[proposal["id"]] = self.growth.advance(proposal["id"])
                if outcomes[proposal["id"]] == "verified":
                    self._record_evolution_feedback(proposal)
            except Exception as exc:
                outcomes[proposal["id"]] = f"blocked:{exc}"
        return outcomes

    def _record_evolution_feedback(self, proposal: Dict[str, Any]) -> None:
        lesson = f"Verified evolution deployed: {proposal['payload'].get('target_file', proposal['id'])}"
        self.experiences.create(pattern=proposal["id"], lesson=lesson,
                                action_item="monitor deployed evolution", confidence=.9,
                                source_events=proposal["payload"].get("source_events", []), status="verified")
        self.adapter.memory.store(lesson, {"category": "experience", "proposal_id": proposal["id"]}, "growth")
        # Evolution feedback becomes searchable knowledge immediately, rather
        # than waiting for tonight's consolidation window.
        self.adapter.memory.promote_pending_memories()
        self.wiki.sync()
        self._refresh_graph()
        self.version_manager.record("evolution_verified", proposal_id=proposal["id"],
                                    target=proposal["payload"].get("target_file"))
        self.release_ledger.record("daming-os", proposal["id"], "verified", {
            "target_file": proposal["payload"].get("target_file"),
            "artifact_type": proposal["payload"].get("artifact_type", "code"),
        })
        self.quality.register(f"evolution:{proposal['id']}", "high")
        self.quality.review(f"evolution:{proposal['id']}", True, "OTP-gated deployment verified")

    def _reconcile_gep(self) -> Dict[str, Any]:
        return {"replayed": self.adapter.growth_detector.reconcile(self._events(1))}

    def _refresh_graph(self) -> Dict[str, Any]:
        # Wikitext edges and automatic consolidation both write the single
        # ``wiki_edges`` table consumed by graph-spreading recall.
        from .growth.advanced import DynamicKnowledgeGraph
        return DynamicKnowledgeGraph(str(self.workspace / "wiki" / "main"), self.graph).rebuild()

    def _track_files(self) -> Dict[str, Any]:
        files = [str(path) for path in self.workspace.rglob("*.py")] + [str(path) for path in self.workspace.rglob("SKILL.md")]
        changed = self.file_tracker.changed(files)
        snapshot = self.file_tracker.snapshot(files)
        if changed:
            from .events import LogEvent
            bus.publish(LogEvent("discovery", "Tracked workspace files changed: " + ", ".join(changed[:20]),
                                 {"files": changed}))
        return {"tracked": len(snapshot), "changed": changed}

    def _memory_quality(self) -> Dict[str, Any]:
        report = self.memory_health.run()
        import json
        import sqlite3
        db = self.workspace / "memory" / "memory_meta.db"
        evidence = {"items": 0, "fts": False, "edges": 0,
                    "vectors": (self.workspace / "memory" / "lancedb" / "fallback-vectors.json").exists()}
        if db.exists():
            with sqlite3.connect(db) as connection:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
                evidence["fts"] = "memory_fts" in tables
                if "items" in tables:
                    evidence["items"] = connection.execute("SELECT COUNT(*) FROM items").fetchone()[0]
                if "wiki_edges" in tables:
                    evidence["edges"] = connection.execute("SELECT COUNT(*) FROM wiki_edges").fetchone()[0]
        self.quality.register("memory-pipeline", "high")
        passed = bool(report["healthy"] and report["wiki"] and evidence["fts"])
        self.quality.review("memory-pipeline", passed, json.dumps(evidence, ensure_ascii=False))
        return {**report, "evidence": evidence, "passed": passed}

    def _distill_workflows(self) -> Dict[str, Any]:
        result = self.workflow_distillation.run()
        result["loaded_skills"] = self.skills.discover()
        for name in result["loaded_skills"]:
            self.skill_usage.record(name, "auto_discovered")
        return result

    def _consume_command(self, text: str, context: Any) -> Optional[str]:
        parts = text.strip().split()
        if len(parts) == 3 and parts[0] == "/daming-approve":
            return "approved" if self.growth_ledger.approve(parts[1], parts[2]) else "approval_failed"
        learning = self.xuexi.consume(text, {"agent_id": context.agent_id, "session_id": context.session_id})
        if learning:
            result = self._distill_workflows()
            return f"learning_saved:{learning}; skills_distilled:{result['distilled']}"
        return None

    def _skill_context(self, text: str, context: Any) -> str:
        """Inject active meta rules and explicitly relevant skills into any host turn."""
        self.skills.discover()
        selected = []
        normalized = text.lower()
        for name in self.skills.discover():
            if name.startswith("meta-") or name.lower() in normalized:
                skill = self.skills.load(name)
                if skill:
                    self.skill_usage.record(name, "injected", agent_id=context.agent_id, session_id=context.session_id)
                    selected.append(skill.body)
        return "\n\n".join(selected[-5:])

    def _rewrite_meta_prompt(self) -> Dict[str, Any]:
        result = self.meta_prompt.rewrite(self._events(7))
        identifier = result["proposal"].get("growth_proposal_id")
        if identifier:
            self.growth_ledger.queue(identifier)
        return result

    def _extract_growth_experiences(self) -> Dict[str, Any]:
        return self.growth_pipeline.extract(self._events(1))

    def _sica_snapshot(self) -> Dict[str, Any]:
        files = [str(path) for path in self.workspace.rglob("*.py")]
        entry = self.sica.snapshot(files, "scheduled_integrity_check")
        self.version_manager.record("sica_integrity", files=len(entry["hashes"]))
        return entry

    def _extract_faq(self) -> Dict[str, Any]:
        """Portable replacement for the Feishu FAQ extractor: persist event FAQ candidates."""
        events = self._events(1)
        questions = [str(event.get("content", "")) for event in events
                     if event.get("log_type") in {"user_feedback", "task_failure", "system_error"}]
        path = self.workspace / "memory" / "faq" / "daily.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        import json
        path.write_text(json.dumps({"questions": questions[-50:]}, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"candidates": len(questions), "path": str(path)}

    def _archive_memory(self) -> Dict[str, Any]:
        sources = [str(path) for path in (self.workspace / "memory" / "reviews").glob("*.json")]
        sources += [str(path) for path in (self.workspace / "memory" / "diary").glob("*.json")]
        sources += [str(path) for path in (self.workspace / "wiki" / "main").rglob("*.md")]
        archive = self.glacier.archive(sources, label="weekly-memory")
        return {"archive": str(archive), "sources": len(sources)}

    def blueprint_gaps(self) -> list[str]:
        """Machine-checkable proof that every de-duplicated whitepaper capability has a route."""
        return missing_runtime_triggers(self)

    def _check_config(self) -> Dict[str, Any]:
        snapshot = {"jobs": sorted(self.scheduler.jobs), "workspace": str(self.workspace),
                    "gep_threshold": config.GEP_THRESHOLD}
        return {"drift": self.config_guard.check(snapshot), "snapshot": snapshot}

    def _judge_recent_events(self) -> Dict[str, Any]:
        events = self._events(1)
        failures = sum(1 for event in events if event.get("log_type") in {"task_failure", "system_error"})
        report = self.system_health.check({"event_quality": lambda: failures == 0,
                                           "memory": lambda: self.memory_health.run()["healthy"]})
        self.golden_paths.save("daily-agent-operation", [{"step": "judge recent events"}], report)
        return report

    def _events(self, days: int) -> list[Dict[str, Any]]:
        from datetime import datetime, timedelta, timezone
        import json
        path = self.workspace / "memory" / "event_logs.jsonl"
        if not path.exists():
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
                timestamp = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
                if timestamp >= cutoff:
                    result.append(event)
            except (KeyError, ValueError, json.JSONDecodeError):
                continue
        return result

    def tick(self) -> Dict[str, Any]:
        """Run due maintenance; safe to call after every host turn."""
        return self.scheduler.run_due()

    def start(self) -> None:
        """Start maintenance for hosts that remain alive without turn hooks."""
        self.scheduler.start()

    def close(self) -> None:
        """Release background maintenance and event subscriptions for this host."""
        self.scheduler.stop()
        self.growth.close()
        closer = getattr(self.adapter, "close", None)
        if callable(closer):
            closer()
