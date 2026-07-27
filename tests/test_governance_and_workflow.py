import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from daming_os.growth.proposals import ProposalStore
from daming_os.growth.workflow import EvolutionWorkflow
from daming_os.growth.inspector import ProactiveInspector
from daming_os.growth.learning import ExperienceStore, SkillDistiller
from daming_os.memory.governance import MemoryPolicy, MemoryScope, visible_to_scope
from daming_os.memory.runtime import HotMemoryJournal
from daming_os.quality import QualityGate


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
