import tempfile
import unittest
import json
import importlib.util
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from daming_os import AgentContext, DamingHookBridge, SkillLazyLoader
from daming_os.blueprint import missing_runtime_triggers
from daming_os.cli import doctor, init_workspace, install_host, manage_approval, status
from daming_os.memory.core import MemorySystem
from daming_os.scheduling import DurableScheduler
from daming_os.events import EvolutionTriggeredEvent, bus
from daming_os.growth.governance import GrowthLedger
from daming_os.growth.proposals import ProposalStore
from daming_os.growth.runtime import GrowthCoordinator
from daming_os.memory.services import (FilesystemWikiProvider, GlacierStore,
                                       WikiSynchronizer)
from daming_os.memory.migration import MemoryMigrator
from daming_os.runtime import DamingRuntime


class _Embedding:
    def embed(self, text):
        return [1.0, 0.0]


class _CacheProbe:
    def __init__(self):
        self.vectors = []
    def get(self, query, vector):
        self.vectors.append(vector)
        return []
    def set(self, query, response, vector):
        self.vectors.append(vector)
    def clear(self):
        pass


class _Adapter:
    def __init__(self):
        self.calls = []
    def before_turn(self, text, context):
        self.calls.append(("before", text, context.session_id)); return [{"id": "memory"}]
    def after_turn(self, text, output, context):
        self.calls.append(("after", output, context.session_id))
    def on_error(self, error, context):
        self.calls.append(("error", str(error), context.session_id))
    def compact_context(self, context):
        return [{"role": "system", "content": "compacted"}]


class _Notifier:
    def __init__(self):
        self.messages = []
    def notify(self, kind, proposal_id, **details):
        self.messages.append((kind, proposal_id, details))


class TestPortableRuntime(unittest.TestCase):
    def test_embedding_is_ready_before_l2_cache_lookup(self):
        memory = MemorySystem(embedding_provider=_Embedding())
        probe = _CacheProbe()
        memory.cache = probe
        memory.query("semantic cache query")
        self.assertEqual(probe.vectors[0], [1.0, 0.0])

    def test_hook_bridge_supports_a_generic_agent_registrar(self):
        adapter = _Adapter()
        bridge = DamingHookBridge(adapter=adapter)
        registered = {}
        bridge.install(lambda name, callback: registered.setdefault(name, callback))
        payload = {"input": "hello", "session_id": "s", "metadata": {"messages": []}}
        self.assertEqual(registered["before_turn"](payload)["daming_memories"][0]["id"], "memory")
        self.assertEqual(payload["messages"][0]["content"], "compacted")
        registered["after_turn"]({"input": "hello", "output": "done", "session_id": "s"})
        registered["error"]({"error": "bad", "session_id": "s"})
        self.assertEqual([call[0] for call in adapter.calls], ["before", "after", "error"])

    def test_lazy_skill_loading_defers_file_read_until_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = Path(tmp) / "skills" / "review" / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("# Review\n", encoding="utf-8")
            loader = SkillLazyLoader([str(Path(tmp) / "skills")])
            self.assertEqual(loader.discover(), ["review"])
            self.assertEqual(loader.load("review").body, "# Review\n")

    def test_durable_scheduler_runs_jobs_from_hook_or_heartbeat_ticks(self):
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = DurableScheduler(str(Path(tmp) / "state.json"))
            calls = []
            scheduler.schedule("maintenance", "every:10", lambda: calls.append("run"))
            self.assertIn("maintenance", scheduler.run_due(now=10))
            self.assertEqual(calls, ["run"])
            self.assertEqual(scheduler.run_due(now=15), {})
            self.assertIn("maintenance", scheduler.run_due(now=20))

    def test_scheduler_catches_up_missed_daily_and_weekly_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            daily_calls = []
            daily = DurableScheduler(str(Path(tmp) / "daily.json"), timezone_name="UTC")
            daily.schedule("sleep", "daily@02:30", lambda: daily_calls.append("sleep"))
            installed = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc).timestamp()
            daily.defer_new_jobs(now=installed)
            resumed = datetime(2024, 1, 2, 1, 0, tzinfo=timezone.utc).timestamp()
            self.assertIn("sleep", daily.run_due(now=resumed))
            self.assertEqual(daily_calls, ["sleep"])

            weekly_calls = []
            weekly = DurableScheduler(str(Path(tmp) / "weekly.json"), timezone_name="UTC")
            weekly.schedule("governance", "weekly@Sun@23:30",
                            lambda: weekly_calls.append("governance"))
            weekly.defer_new_jobs(now=installed)
            monday = datetime(2024, 1, 8, 9, 0, tzinfo=timezone.utc).timestamp()
            self.assertIn("governance", weekly.run_due(now=monday))
            self.assertEqual(weekly_calls, ["governance"])

    def test_scheduler_retries_failures_without_marking_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            should_fail = [True]
            scheduler = DurableScheduler(
                str(Path(tmp) / "state.json"), retry_base_seconds=10,
                retry_max_seconds=40,
            )
            def callback():
                if should_fail[0]:
                    raise RuntimeError("temporary")
                return {"done": True}
            scheduler.schedule("job", "every:100", callback)
            self.assertFalse(scheduler.run_due(now=10)["job"]["ok"])
            state = json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))
            self.assertIsNone(state["jobs"]["job"]["last_success_at"])
            self.assertEqual(state["jobs"]["job"]["next_due_at"], 20)
            self.assertEqual(scheduler.run_due(now=19), {})
            should_fail[0] = False
            self.assertTrue(scheduler.run_due(now=20)["job"]["ok"])
            state = json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["jobs"]["job"]["failure_count"], 0)
            self.assertEqual(state["jobs"]["job"]["last_success_at"], 20)

    def test_plugin_init_owns_only_daming_state_and_doctor_reports_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "plugin-state"
            result = init_workspace(str(root), "generic")
            self.assertTrue(Path(result["config"]).exists())
            self.assertFalse((root / "AGENTS.md").exists())
            self.assertFalse((root / "USER.md").exists())
            self.assertFalse((root / ".env").exists())
            report = doctor(str(root))
            self.assertTrue(report["ok"])
            self.assertTrue(report["smoke_test"]["ok"])
            self.assertEqual(report["config"]["integration"], "generic")
            self.assertEqual(status(str(root))["workspace"], str(root.resolve()))
            config_path = Path(result["config"])
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertFalse(config["runtime"]["daily_digest_enabled"])
            self.assertNotIn("reviews_enabled", config["runtime"])
            config["runtime"]["watchdog_enabled"] = True
            config_path.write_text(json.dumps(config), encoding="utf-8")
            runtime = DamingRuntime(str(root))
            self.assertIn("watchdog", runtime.scheduler.jobs)
            runtime.close()

    def test_one_command_install_generates_a_working_idempotent_bridge(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Path(tmp) / "agent-project"
            installed = install_host(str(host))
            self.assertTrue(installed["ok"])
            self.assertEqual(installed["bootstrap_action"], "created")
            bootstrap = Path(installed["bootstrap"])
            spec = importlib.util.spec_from_file_location("test_daming_bootstrap", bootstrap)
            module = importlib.util.module_from_spec(spec)
            self.assertIsNotNone(spec.loader)
            spec.loader.exec_module(module)
            self.assertEqual(module.daming.status()["state"], "lazy")
            self.assertNotEqual(module.daming.default_agent_id, "default-agent")
            self.assertTrue(module.daming.default_session_id.startswith("session-"))
            before = module.daming.before_turn("remember this", agent_id="a", session_id="s")
            self.assertIn("daming_memories", before)
            module.daming.after_turn("remember this", "done", agent_id="a", session_id="s")
            self.assertTrue((host / ".daming" / "memory" / "event_logs.jsonl").exists())
            module.daming.close()
            repeated = install_host(str(host))
            self.assertEqual(repeated["bootstrap_action"], "unchanged")
            self.assertTrue(repeated["doctor"]["bootstrap_valid"])
            self.assertEqual((host / ".daming" / ".gitignore").read_text(encoding="utf-8"),
                             "*\n!.gitignore\n")

    def test_generated_bridge_fails_open_when_runtime_initialization_breaks(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Path(tmp) / "agent-project"
            installed = install_host(str(host))
            spec = importlib.util.spec_from_file_location(
                "test_daming_degraded_bootstrap", installed["bootstrap"]
            )
            module = importlib.util.module_from_spec(spec)
            self.assertIsNotNone(spec.loader)
            spec.loader.exec_module(module)
            broken = Path(tmp) / "broken"
            broken.mkdir()
            (broken / "daming-os.json").write_text(
                '{"version": 999, "runtime": {}}', encoding="utf-8"
            )
            plugin = module.DamingPlugin(state_dir=broken, strict=False)
            result = plugin.before_turn("host must continue")
            self.assertEqual(result["daming_memories"], [])
            self.assertIn("daming_degraded", result)
            plugin.close()

    def test_installer_rejects_unsafe_state_paths_and_host_file_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = Path(tmp) / "agent-project"
            host.mkdir()
            with self.assertRaises(ValueError):
                install_host(str(host), "../outside")
            (host / "daming_bootstrap.py").write_text("HOST_OWNED = True\n", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                install_host(str(host))

    def test_gep_trigger_is_queued_for_growth_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            proposals = ProposalStore(str(Path(tmp) / "proposals.db"))
            identifier = proposals.create({"kind": "growth"})
            ledger = GrowthLedger(str(Path(tmp) / "ledger.db"))
            coordinator = GrowthCoordinator(proposals, ledger)
            bus.publish(EvolutionTriggeredEvent(3.0, [], proposal_id=identifier))
            self.assertFalse(ledger.record_review(identifier, "builder", "reviewer", 80))

    def test_glacier_and_filesystem_wiki_provider_are_portable_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.md"
            source.write_text("memory", encoding="utf-8")
            glacier = GlacierStore(str(root))
            archive = glacier.archive([str(source)])
            restored = glacier.restore(str(archive), str(root / "restore"))
            self.assertEqual(restored[0].read_text(encoding="utf-8"), "memory")
            local = root / "wiki"
            (local / "a.md").parent.mkdir(parents=True)
            (local / "a.md").write_text("wiki", encoding="utf-8")
            sync = WikiSynchronizer(str(local), FilesystemWikiProvider(str(root / "mirror")))
            self.assertEqual(sync.sync()["pushed"], 1)

    def test_standalone_runtime_persists_events_and_runs_otp_gated_evolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = DamingRuntime(str(root))
            target = root / "agent_extension.py"
            target.write_text("VALUE = 1\n", encoding="utf-8")
            identifier = runtime.proposals.create({
                "kind": "code-update", "target_file": "agent_extension.py",
                "proposed_code": "VALUE = 2\n",
            })
            bus.publish(EvolutionTriggeredEvent(3.0, [], proposal_id=identifier))
            self.assertEqual(runtime.growth.audit_pending()[identifier], "awaiting_approval")
            otp = runtime.growth.issue_approval_otp(identifier)
            self.assertTrue((root / "memory" / "approval-outbox.jsonl").exists())
            self.assertTrue(runtime.growth_ledger.approve(identifier, otp))
            self.assertEqual(runtime.growth.advance(identifier), "validated")
            self.assertEqual(runtime.growth.advance(identifier), "approved")
            self.assertEqual(runtime.growth.advance(identifier), "deployed")
            self.assertEqual(runtime.growth.advance(identifier), "verified")
            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 2\n")
            runtime.adapter.after_turn("remember this", "ok", AgentContext(session_id="s", agent_id="a"))
            self.assertTrue((root / "memory" / "event_logs.jsonl").exists())
            runtime.close()

    def test_full_blueprint_extensions_are_default_runtime_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = DamingRuntime(str(root))
            runtime.experiences.create(pattern="retry failed sync", lesson="use backoff",
                                       action_item="add retry", confidence=.8,
                                       source_events=[], status="verified")
            self.assertEqual(runtime._distill_workflows()["distilled"], 1)
            self.assertTrue((root / "skills" / "auto-generated").exists())
            self.assertIsNotNone(runtime._consume_command("/xuexi capture a reusable lesson", AgentContext("a", "s")))
            runtime.adapter.on_error(RuntimeError("repeated host failure"), AgentContext("a", "s"))
            meta = runtime._rewrite_meta_prompt()
            self.assertTrue(Path(meta["path"]).exists())
            self.assertEqual(runtime.growth_ledger.state(meta["proposal"]["growth_proposal_id"]), "pending_review")
            self.assertIn("daily-maintenance", runtime.scheduler.jobs)
            self.assertNotIn("watchdog", runtime.scheduler.jobs)
            self.assertEqual(missing_runtime_triggers(runtime), [])
            runtime.close()

    def test_runtime_adds_optional_digest_and_watchdog_without_auto_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = DamingRuntime(tmp, watchdog_enabled=True, reviews_enabled=True)
            self.assertEqual(set(runtime.scheduler.jobs), {"daily-maintenance", "weekly-governance", "daily-digest", "watchdog"})
            self.assertIsNone(runtime.scheduler._worker)
            self.assertEqual(runtime.scheduler.jobs["watchdog"][0], "every:1800")
            self.assertEqual(runtime.tick(), {})
            runtime.close()

    def test_daily_digest_replaces_duplicate_review_and_diary_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = DamingRuntime(tmp, daily_digest_enabled=True)
            context = AgentContext(agent_id="agent", session_id="session")
            runtime.adapter.after_turn("summarize today", "done", context)
            report = runtime._run_daily_digest()
            output = Path(report["results"]["daily_digest"])
            self.assertEqual(report["pipeline"], "daily-digest")
            self.assertEqual(output.parent.name, "digests")
            self.assertTrue(output.name.startswith("daily-digest-"))
            self.assertFalse((Path(tmp) / "memory" / "diary").exists())
            runtime.close()

    def test_approval_reminders_are_conditional_and_rate_limited(self):
        with tempfile.TemporaryDirectory() as tmp:
            notifier = _Notifier()
            runtime = DamingRuntime(tmp, approval_notifier=notifier)
            runtime.growth_ledger.queue("overdue-proposal", deadline_hours=-1)
            first = runtime.tick()
            second = runtime.tick()
            self.assertIn("approval-reminders", first)
            self.assertNotIn("approval-reminders", second)
            self.assertEqual([message[:2] for message in notifier.messages],
                             [("approval_overdue", "overdue-proposal")])
            runtime.close()

    def test_cli_approval_flow_reissues_otp_and_completes_verified_deploy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = DamingRuntime(str(root))
            target = root / "approved_change.py"
            target.write_text("VALUE = 1\n", encoding="utf-8")
            identifier = runtime.proposals.create({
                "kind": "code-update", "target_file": "approved_change.py",
                "proposed_code": "VALUE = 2\n",
            })
            runtime.growth_ledger.queue(identifier)
            self.assertEqual(runtime.growth.audit_pending()[identifier], "awaiting_approval")
            runtime.close()
            issued = manage_approval(str(root), "issue", identifier)
            self.assertRegex(issued["otp"], r"^\d{6}$")
            approved = manage_approval(str(root), "approve", identifier, issued["otp"])
            self.assertTrue(approved["ok"])
            self.assertEqual(approved["outcome"], "verified")
            self.assertEqual(target.read_text(encoding="utf-8"), "VALUE = 2\n")
            shown = manage_approval(str(root), "show", identifier)
            self.assertEqual(shown["approval"]["proposal"]["state"], "verified")

    def test_agent_quality_persists_details_only_when_anomalous(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = DamingRuntime(tmp)
            report_path = Path(tmp) / "memory" / "health-reports" / "latest.json"
            self.assertTrue(runtime._assess_agent_quality()["healthy"])
            self.assertFalse(report_path.exists())
            runtime.adapter.on_error(RuntimeError("host failed"), AgentContext("agent", "session"))
            report = runtime._assess_agent_quality()
            self.assertFalse(report["healthy"])
            self.assertTrue(report_path.exists())
            runtime.close()

    def test_status_explains_core_and_optional_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_workspace(tmp)
            services = status(tmp)["services"]
            self.assertTrue(services["daily_sleep_memory"]["required_for_memory"])
            self.assertFalse(services["daily_digest"]["enabled"])
            self.assertFalse(services["daily_digest"]["required_for_memory"])
            self.assertEqual(services["approval_reminders"]["cooldown"], "24h")

    def test_init_migrates_legacy_review_setting_to_daily_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            initialized = init_workspace(tmp)
            config_path = Path(initialized["config"])
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["runtime"].pop("daily_digest_enabled")
            config["runtime"]["reviews_enabled"] = True
            config_path.write_text(json.dumps(config), encoding="utf-8")
            init_workspace(tmp)
            migrated = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertTrue(migrated["runtime"]["daily_digest_enabled"])
            self.assertNotIn("reviews_enabled", migrated["runtime"])

    def test_schema_migration_versions_and_backs_up_existing_databases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "memory" / "legacy.db"
            database.parent.mkdir(parents=True)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE legacy(value TEXT)")
                connection.execute("INSERT INTO legacy VALUES ('preserved')")
                connection.commit()
            migrator = MemoryMigrator(str(root))
            report = migrator.migrate()
            self.assertEqual(report["from_version"], 0)
            self.assertEqual(report["to_version"], 1)
            self.assertEqual(migrator.current_version(), 1)
            self.assertEqual(len(report["backups"]), 1)
            with closing(sqlite3.connect(report["backups"][0])) as backup:
                self.assertEqual(backup.execute("SELECT value FROM legacy").fetchone()[0],
                                 "preserved")

    def test_scheduler_surfaces_pipeline_errors_as_failed_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            scheduler = DurableScheduler(str(Path(tmp) / "state.json"))
            scheduler.schedule("pipeline", "every:10", lambda: {"errors": {"job": "failed"}})
            result = scheduler.run_due(now=10)
            self.assertFalse(result["pipeline"]["ok"])

    def test_every_default_whitepaper_job_executes_in_an_empty_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = DamingRuntime(tmp)
            failures = {}
            for name, (_, callback) in runtime.scheduler.jobs.items():
                try:
                    callback()
                except Exception as exc:
                    failures[name] = str(exc)
            self.assertEqual(failures, {})
            runtime.close()

    def test_memory_whitepaper_hot_to_warm_to_fts_to_graph_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = DamingRuntime(tmp)
            context = AgentContext(agent_id="agent", session_id="session")
            runtime.adapter.after_turn("alpha memory relation", "first", context)
            runtime.adapter.after_turn("beta memory relation", "second", context)
            self.assertEqual(runtime.adapter.memory.promote_pending_memories(), 2)
            db = Path(tmp) / "memory" / "memory_meta.db"
            import sqlite3
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0], 2)
                self.assertGreaterEqual(connection.execute("SELECT COUNT(*) FROM wiki_edges").fetchone()[0], 1)
            results = runtime.adapter.memory.query("alpha memory relation", scope=None)
            self.assertTrue(results)
            self.assertTrue(list((Path(tmp) / "wiki" / "main" / "experiences").glob("*.md")))
            lancedb_dir = Path(tmp) / "memory" / "lancedb"
            self.assertTrue((lancedb_dir / "fallback-vectors.json").exists() or (lancedb_dir / "learnings.lance").exists() or lancedb_dir.exists())
            # bitable-sync 已从系统中删除，此处改为验证图谱刷新
            self.assertEqual(runtime._refresh_graph()["nodes"], 2)
            runtime.close()

    def test_gep_default_chain_builds_a_skill_then_requires_otp_before_deploy(self):
        """A real GEP signal must not stop at an empty proposal payload."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = DamingRuntime(tmp)
            context = AgentContext(agent_id="agent", session_id="session")
            for message in ("failure one", "failure two", "failure three"):
                runtime.adapter.on_error(RuntimeError(message), context)
            pending = list(runtime.proposals.pending())
            self.assertEqual(len(pending), 1)
            identifier = pending[0]["id"]
            runtime._advance_growth()
            proposal = runtime.proposals.get(identifier)
            self.assertEqual(proposal["payload"]["artifact_type"], "skill")
            self.assertEqual(runtime.growth_ledger.state(identifier), "awaiting_approval")
            self.assertFalse((root / proposal["payload"]["target_file"]).exists())
            otp = runtime.growth.issue_approval_otp(identifier)
            self.assertTrue(runtime.growth_ledger.approve(identifier, otp))
            for _ in range(3):
                runtime._advance_growth()
            self.assertEqual(runtime.proposals.get(identifier)["state"], "verified")
            self.assertTrue((root / proposal["payload"]["target_file"]).exists())
            self.assertTrue(runtime.experiences.candidates())
            runtime.close()
