import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daming_os.growth.proposals import ProposalStore
from daming_os.growth.workflow import EvolutionWorkflow
from daming_os.growth.inspector import ProactiveInspector
from daming_os.growth.learning import ExperienceStore, SkillDistiller
from daming_os.growth.governance import GEPPolicy, GrowthLedger
from daming_os.memory.governance import MemoryPolicy, MemoryScope, visible_to_scope
from daming_os.memory.runtime import HotMemoryJournal
from daming_os.quality import QualityGate
from daming_os.operations import ArchiveStore, GoldenPathStore, HealthMonitor
from daming_os.memory.graph import KnowledgeGraph
from daming_os.memory.migration import MemoryMigrator
from daming_os.growth.reflection import ReflectionStore
from daming_os.growth.health import GrowthHealthInspector
from daming_os.growth.release import ReleaseLedger, VerifiedDeployment
from daming_os.scheduling import ConfigGuard, Heartbeat, HeartbeatRunner


class TestMemoryGovernance(unittest.TestCase):
    def test_redacts_scopes_and_expires_memory(self):
        policy = MemoryPolicy(default_ttl_days=1)
        now = datetime(2026, 7, 27, tzinfo=timezone.utc)
        content, metadata = policy.prepare(
            "api_key=super-secret-value", {"note": "token=test-value"},
            MemoryScope(tenant_id="tenant-a", agent_id="agent-1"), now,
        )
        self.assertNotIn("super-secret-value", content)
        self.assertNotIn("test-value", metadata["note"])
        self.assertTrue(visible_to_scope(metadata, MemoryScope(tenant_id="tenant-a")))
        self.assertFalse(visible_to_scope(metadata, MemoryScope(tenant_id="tenant-b")))
        self.assertTrue(policy.is_expired(metadata, now + timedelta(days=2)))


class TestEvolutionWorkflow(unittest.TestCase):
    def test_requires_validation_approval_and_verification(self):
        calls = []
        class Validator:
            def validate(self, proposal): calls.append("validate")
        class Approvals:
            def is_approved(self, proposal): calls.append("approve"); return True
        class Deployer:
            def deploy(self, proposal): calls.append("deploy")
            def rollback(self, proposal): calls.append("rollback")
        class Verifier:
            def verify(self, proposal): calls.append("verify"); return True

        with tempfile.TemporaryDirectory() as tmp:
            store = ProposalStore(str(Path(tmp) / "growth.db"))
            proposal_id = store.create({"kind": "instruction-update"})
            workflow = EvolutionWorkflow(store, Validator(), Approvals(), Deployer(), Verifier())
            self.assertEqual(workflow.advance(proposal_id), "validated")
            self.assertEqual(workflow.advance(proposal_id), "approved")
            self.assertEqual(workflow.advance(proposal_id), "deployed")
            self.assertEqual(workflow.advance(proposal_id), "verified")
            self.assertEqual(calls, ["validate", "approve", "deploy", "verify"])


class TestProductionLoopCapabilities(unittest.TestCase):
    def test_operations_archive_health_and_golden_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source=root/"session.json"; source.write_text("x")
            archived=ArchiveStore(str(root/"archive")).archive(str(source))
            self.assertTrue(archived.exists()); self.assertFalse(source.exists())
            restored=ArchiveStore(str(root/"archive")).restore(str(archived),str(root/"restore.json"))
            self.assertEqual(restored.read_text(),"x")
            self.assertTrue(GoldenPathStore(str(root/"golden")).save("task", [{"step":"read"}], {"tests":"pass"}).exists())
            self.assertTrue(HealthMonitor(str(root/"health")).check({"storage":lambda: True})["healthy"])
            self.assertTrue(all(MemoryMigrator(str(root/"workspace")).initialize().values()))
            graph=KnowledgeGraph(str(root/"graph.db")); graph.link("a","b","depends_on",1); self.assertEqual(graph.neighbors("a")[0]["id"],"b")
            self.assertEqual(ReflectionStore(str(root/"reflections.jsonl")).record("task_failure","timeout")["event_type"],"task_failure")
            self.assertTrue(GrowthHealthInspector().inspect({name:lambda: True for name in GrowthHealthInspector.DIMENSIONS})["healthy"])
            calls=[]; deployment=VerifiedDeployment(lambda:calls.append("deploy"),lambda:False,lambda:calls.append("rollback"),ReleaseLedger(str(root/"releases.jsonl")))
            self.assertFalse(deployment.run("1.3.0","p")); self.assertEqual(calls,["deploy","rollback"])
            self.assertTrue(HeartbeatRunner([Heartbeat("check",lambda:True)]).run()["check"]["ok"])
            guard=ConfigGuard(str(root/"config.hash")); self.assertFalse(guard.check({"a":1})); self.assertTrue(guard.check({"a":2}))
    def test_growth_governance_review_otp_and_gep(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = GrowthLedger(str(Path(tmp) / "growth.db"))
            ledger.queue("p")
            self.assertFalse(ledger.record_review("p", "build", "review", 80))
            self.assertTrue(ledger.record_review("p", "build", "review", 90))
            otp = ledger.issue_otp("p")
            self.assertTrue(ledger.approve("p", otp))
            now = datetime.now(timezone.utc)
            self.assertGreater(GEPPolicy().score([{"log_type":"discovery","content":"x","timestamp":now.isoformat()}]), 1.49)
    def test_hot_journal_keeps_turn_history_and_compacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = HotMemoryJournal(tmp)
            journal.append("s/unsafe", "first decision", tool_calls=["read"])
            journal.append("s/unsafe", "second decision", state_diff={"file": "a.py"})
            journal.append("s/unsafe", "third decision")
            self.assertEqual(len(journal.read("s/unsafe")), 3)
            window = journal.context_window("s/unsafe", max_tokens=100, keep_turns=1)
            self.assertEqual(len(window), 2)
            self.assertIn("COMPACTED HISTORY", window[0]["content"])

    def test_inspection_learning_skill_and_quality_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            event_log = root / "events.jsonl"
            now = datetime.now(timezone.utc).isoformat()
            event_log.write_text("\n".join([
                '{"event_type":"LogEvent","log_type":"task_failure","content":"database timeout in sync job","timestamp":"' + now + '"}',
                '{"event_type":"LogEvent","log_type":"task_failure","content":"database timeout in sync worker","timestamp":"' + now + '"}',
            ]) + "\n", encoding="utf-8")
            proposals = ProposalStore(str(root / "growth.db"))
            self.assertEqual(len(ProactiveInspector(str(event_log), proposals).inspect()), 1)

            experiences = ExperienceStore(str(root / "growth.db"))
            identifier = experiences.create(pattern="retry transactional sync", lesson="timeouts recur", action_item="add backoff", confidence=.9, source_events=[])
            experiences.transition(identifier, "verified")
            experiences.mark_applied(identifier)
            skill_path = SkillDistiller(str(root / "skills")).distill(experiences.candidates()[0])
            self.assertTrue(skill_path.exists())

            quality = QualityGate(str(root / "growth.db"))
            quality.register("deploy-1", "high")
            quality.complete("deploy-1")
            self.assertEqual(quality.blocked(), ["deploy-1"])
            quality.review("deploy-1", True)
            self.assertEqual(quality.blocked(), [])
