"""Default standalone runtime wiring for Daming OS.

This replaces the OpenClaw-only combination of hooks and native cron.  Hook
capable agents call the bridge; long-running agents may also start the bundled
scheduler.  Neither path imports an agent framework.
"""
from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from .adapter import DamingAdapter
from .config import config
from .events import bus
from .hooks import DamingHookBridge
from .memory.maintenance import MemoryMaintenance
from .memory.services import (FilesystemWikiProvider,
                              GlacierStore, MemoryReviewService,
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
                 approval_notifier: Optional[ApprovalNotifier] = None,
                 watchdog_enabled: Optional[bool] = None, reviews_enabled: Optional[bool] = None,
                 daily_digest_enabled: Optional[bool] = None,
                 start_scheduler: bool = False):
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._closed = False
        runtime_config = self._runtime_config()
        if watchdog_enabled is None:
            watchdog_enabled = bool(runtime_config.get("watchdog_enabled", False))
        if daily_digest_enabled is None:
            # ``reviews_enabled`` is retained as a compatibility alias for
            # installations created before the daily review bundle was split.
            if reviews_enabled is not None:
                daily_digest_enabled = reviews_enabled
            else:
                daily_digest_enabled = bool(runtime_config.get(
                    "daily_digest_enabled", runtime_config.get("reviews_enabled", False)
                ))
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
        self.scheduler = DurableScheduler(
            str(self.workspace / ".daming-os" / "scheduler-state.json"),
            timezone_name=str(runtime_config.get("timezone", "local")),
        )
        self._approval_reminder_state = self.workspace / ".daming-os" / "approval-reminders.json"
        self.skills = SkillLazyLoader([str(self.workspace / "skills" / "auto-generated"), *skill_dirs])
        self.skills.discover()
        self.scheduler.schedule("daily-maintenance", "daily@02:30", self._run_daily_maintenance)
        self.scheduler.schedule("weekly-governance", "weekly@Sun@23:30", self._run_weekly_governance)
        if daily_digest_enabled:
            self.scheduler.schedule("daily-digest", "daily@23:00", self._run_daily_digest)
        if watchdog_enabled:
            self.scheduler.schedule("watchdog", "every:1800", self._run_watchdog)
        self.scheduler.defer_new_jobs()
        self.hooks = DamingHookBridge(adapter=self.adapter, before_turn_callback=self._consume_command,
                                      after_turn_callback=self.tick, skill_context_callback=self._skill_context)
        if start_scheduler:
            self.start_scheduler()

    def _runtime_config(self) -> Dict[str, Any]:
        """Read optional plugin settings without coupling the host to env files."""
        path = self.workspace / "daming-os.json"
        if not path.exists():
            return {}
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid Daming OS configuration at {path}: {exc}") from exc
        if not isinstance(content, dict) or content.get("version") != 1:
            raise ValueError(f"unsupported Daming OS configuration schema at {path}")
        runtime = content.get("runtime", {})
        if not isinstance(runtime, dict):
            raise ValueError(f"runtime configuration must be an object at {path}")
        return runtime

    def _run_tasks(self, pipeline: str, tasks: Iterable[Tuple[str, Any]],
                   *, persist: bool = True) -> Dict[str, Any]:
        """Run a pipeline while preserving every task outcome for diagnosis."""
        results, errors = {}, {}
        for name, func in tasks:
            try:
                results[name] = func()
            except Exception as exc:
                errors[name] = str(exc)
        report = {"pipeline": pipeline, "at": datetime.now(timezone.utc).isoformat(),
                  "results": results, "errors": errors}
        if persist:
            self._write_task_report(report)
        return report

    def _write_task_report(self, report: Dict[str, Any]) -> None:
        path = self.workspace / "memory" / "maintenance" / "task-runs.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(report, ensure_ascii=False, default=str) + "\n")

    def _run_daily_maintenance(self) -> Dict[str, Any]:
        """Daily storage maintenance; expensive graph work runs only on changes."""
        report = self._run_tasks("daily-maintenance", [
            ("deep_sleep", self.deep_sleep.run),
            ("gep_reconciliation", self._reconcile_gep),
            ("growth_health", self._growth_health),
            ("session_cleanup", self.session_cleaner.run),
            ("memory_quality", self._memory_quality),
            ("agent_quality", self._assess_agent_quality),
            ("approval_reminders", self._remind_overdue_with_cooldown),
            ("file_tracker", self._track_files),
            ("config_guard", self._check_config),
            ("sica_integrity", self._sica_snapshot),
        ], persist=False)
        deep_sleep = report["results"].get("deep_sleep", {})
        tracked = report["results"].get("file_tracker", {})
        changed = bool(deep_sleep.get("promoted", 0)) if isinstance(deep_sleep, dict) else False
        if changed:
            self.adapter.memory.cache.clear()
        changed = changed or bool(tracked.get("changed", [])) if isinstance(tracked, dict) else changed
        if changed:
            sync = self._run_tasks("knowledge-refresh", [("wiki_sync", self.wiki.sync),
                                                           ("graph_refresh", self._refresh_graph)])
            report["knowledge_refresh"] = sync
        else:
            report["knowledge_refresh"] = {"skipped": "no memory or workspace changes"}
        self._write_task_report(report)
        return report

    def _run_daily_digest(self) -> Dict[str, Any]:
        """Create one optional digest instead of duplicate summary/diary files."""
        events = self._events(1)
        if not events:
            return self._run_tasks("daily-digest", [
                ("daily_digest", lambda: {"skipped": "no events"}),
            ])
        return self._run_tasks("daily-digest", [
            ("daily_digest", lambda: self.reviews.review("daily", events)),
        ])

    def _run_weekly_governance(self) -> Dict[str, Any]:
        return self._run_tasks("weekly-governance", [
            ("growth_audit", self.growth.audit_pending),
            ("proactive_inspector", self._inspect_growth),
            ("growth_experiences", self._extract_growth_experiences),
            ("workflow_distillation", self._distill_workflows),
            ("meta_prompt", self._rewrite_meta_prompt),
            ("weekly_review", lambda: self.reviews.review("weekly", self._events(7))),
            ("glacier_archive", self._archive_memory),
        ])

    def _run_watchdog(self) -> Dict[str, Any]:
        """Optional low-frequency check for long-running hosts."""
        return self._run_tasks("watchdog", [("session_watchdog", self.session_watchdog.run)])

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
        """Advance proposals to their next stable gate; deployment remains OTP-gated."""
        outcomes: Dict[str, str] = dict(self.growth.audit_pending())
        for proposal in self.proposals.pending():
            identifier = proposal["id"]
            try:
                previous = None
                for _ in range(5):
                    outcome = self.growth.advance(identifier)
                    outcomes[identifier] = outcome
                    if outcome in {"awaiting_approval", "verified", "rolled_back", "rejected"} or outcome == previous:
                        break
                    previous = outcome
                if outcomes[identifier] == "verified":
                    self._record_evolution_feedback(proposal)
            except Exception as exc:
                outcomes[identifier] = f"blocked:{exc}"
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
            with closing(sqlite3.connect(db)) as connection:
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
            if not self.growth_ledger.approve(parts[1], parts[2]):
                return "approval_failed"
            return "approved:" + str(self._advance_growth().get(parts[1], "queued"))
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
        identifier = (result.get("proposal") or {}).get("growth_proposal_id")
        if identifier:
            self.growth_ledger.queue(identifier)
        return result

    def _extract_growth_experiences(self) -> Dict[str, Any]:
        return self.growth_pipeline.extract(self._events(7))

    def _sica_snapshot(self) -> Dict[str, Any]:
        files = [str(path) for path in self.workspace.rglob("*.py")]
        entry = self.sica.snapshot(files, "scheduled_integrity_check")
        self.version_manager.record("sica_integrity", files=len(entry["hashes"]))
        return entry

    def _archive_memory(self) -> Dict[str, Any]:
        sources = [str(path) for path in (self.workspace / "memory" / "reviews").glob("*.json")]
        sources += [str(path) for path in (self.workspace / "memory" / "digests").glob("*.json")]
        # Include artifacts created by pre-1.5 daily-review installations.
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

    def _assess_agent_quality(self) -> Dict[str, Any]:
        """Return a compact healthy result and persist details only on anomalies."""
        events = self._events(1)
        failures = [event for event in events
                    if event.get("log_type") in {"task_failure", "system_error"}]
        blocked = list(self.quality.blocked())
        if not failures and not blocked:
            return {"healthy": True, "failure_count": 0, "blocked_quality_gates": 0}
        report = self.system_health.check({
            "event_quality": lambda: not failures,
            "quality_gates": lambda: not blocked,
        })
        report["failure_count"] = len(failures)
        report["blocked_quality_gates"] = blocked
        return report

    def _judge_recent_events(self) -> Dict[str, Any]:
        """Compatibility alias for hosts that called the former daily reviewer."""
        return self._assess_agent_quality()

    def _remind_overdue_with_cooldown(self, cooldown_hours: int = 24) -> Dict[str, Any]:
        """Notify only overdue approvals that have not been reminded recently."""
        overdue = self.growth_ledger.overdue()
        if not overdue:
            return {"pending": 0, "reminded": [], "cooldown_hours": cooldown_hours}
        state: Dict[str, str] = {}
        if self._approval_reminder_state.exists():
            try:
                value = json.loads(self._approval_reminder_state.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    state = {str(key): str(timestamp) for key, timestamp in value.items()}
            except (OSError, ValueError, json.JSONDecodeError):
                state = {}
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=cooldown_hours)
        eligible = []
        for proposal_id in overdue:
            try:
                last_reminded = datetime.fromisoformat(state.get(proposal_id, ""))
                if last_reminded.tzinfo is None:
                    last_reminded = last_reminded.replace(tzinfo=timezone.utc)
            except ValueError:
                last_reminded = None
            if last_reminded is None or last_reminded <= cutoff:
                eligible.append(proposal_id)
        reminded = self.growth.remind_overdue(eligible)
        if reminded:
            state = {proposal_id: timestamp for proposal_id, timestamp in state.items()
                     if proposal_id in overdue}
            state.update({proposal_id: now.isoformat() for proposal_id in reminded})
            self._approval_reminder_state.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._approval_reminder_state.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self._approval_reminder_state)
        return {"pending": len(overdue), "reminded": sorted(reminded),
                "cooldown_hours": cooldown_hours}

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
        """Run due maintenance and condition-triggered approval reminders."""
        result = self.scheduler.run_due()
        reminders = self._remind_overdue_with_cooldown()
        if reminders["reminded"]:
            result["approval-reminders"] = {"ok": True, "result": reminders}
        return result

    def start_scheduler(self, poll_seconds: float = 30) -> None:
        """Explicitly start background scheduling for a long-running host."""
        self.scheduler.start(poll_seconds)

    def start(self, poll_seconds: float = 30) -> None:
        """Backward-compatible alias for explicit background scheduling."""
        self.start_scheduler(poll_seconds)

    def close(self) -> None:
        """Release background maintenance and event subscriptions for this host."""
        if self._closed:
            return
        self._closed = True
        self.scheduler.stop()
        self.growth.close()
        closer = getattr(self.adapter, "close", None)
        if callable(closer):
            closer()
        else:
            gep = getattr(getattr(self.adapter, "growth_detector", None), "close", None)
            if callable(gep):
                gep()
