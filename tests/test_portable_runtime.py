import tempfile
import unittest
from pathlib import Path

from daming_os import AgentContext, DamingHookBridge, SkillLazyLoader
from daming_os.memory.core import MemorySystem
from daming_os.scheduling import DurableScheduler
from daming_os.events import EvolutionTriggeredEvent, bus
from daming_os.growth.governance import GrowthLedger
from daming_os.growth.proposals import ProposalStore
from daming_os.growth.runtime import GrowthCoordinator
from daming_os.memory.services import (BitableSynchronizer, FilesystemWikiProvider, GlacierStore,
                                       JsonBitableProvider, WikiSynchronizer)
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
            records = root / "records.json"
            records.write_text('{"memory-1": {"title": "wiki"}}', encoding="utf-8")
            bitable = BitableSynchronizer(str(records), JsonBitableProvider(str(root / "bitable.json")))
            self.assertEqual(bitable.sync()["pushed"], 1)

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
            meta = runtime._rewrite_meta_prompt()
            self.assertTrue(Path(meta["path"]).exists())
            self.assertEqual(runtime.growth_ledger.state(meta["proposal"]["growth_proposal_id"]), "pending_review")
            self.assertIn("graph-refresh", runtime.scheduler.jobs)
            runtime.close()

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
            with sqlite3.connect(db) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0], 2)
                self.assertGreaterEqual(connection.execute("SELECT COUNT(*) FROM wiki_edges").fetchone()[0], 1)
            results = runtime.adapter.memory.query("alpha memory relation", scope=None)
            self.assertTrue(results)
            self.assertTrue(list((Path(tmp) / "wiki" / "main" / "experiences").glob("*.md")))
            self.assertTrue((Path(tmp) / "memory" / "lancedb" / "fallback-vectors.json").exists())
            self.assertEqual(runtime.bitable.sync()["pushed"], 2)
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
