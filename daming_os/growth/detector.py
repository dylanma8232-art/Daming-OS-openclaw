import json
import logging
import hashlib
from datetime import datetime, timezone
from ..config import config
from ..events import bus, EvolutionTriggeredEvent, LogEvent
from .governance import GEPPolicy
from .proposals import ProposalStore

logger = logging.getLogger("daming_os.growth.detector")

class GEPDetector:
    """
    Growth Experience Point (GEP) detection engine.
    Calculates sliding window exponentially decayed scores to detect evolution triggers.
    """
    def __init__(self, proposal_store: ProposalStore = None, policy: GEPPolicy = None):
        self.policy = policy or GEPPolicy(threshold=config.GEP_THRESHOLD)
        self.gep_threshold = self.policy.threshold
        self.decay_factor = 0.5  # compatibility only; policy is authoritative
        self.window_events = []
        self.seen_events = {}  # sha256 -> timestamp
        self.proposal_store = proposal_store or ProposalStore()
        
        # Subscribe to LogEvent to calculate real-time GEP
        bus.subscribe(LogEvent, self._on_log_event)

    def close(self) -> None:
        """Detach from the process-global bus when a host runtime stops."""
        bus.unsubscribe(LogEvent, self._on_log_event)

    def _on_log_event(self, event: LogEvent):
        """Processes incoming events (errors, new findings) and calculates GEP."""
        # 5-minute SHA256 sliding window deduplication
        event_hash = hashlib.sha256(event.content.encode('utf-8')).hexdigest()
        now = datetime.now(timezone.utc).timestamp()
        
        # Clean up old seen_events (5-minute window)
        self.seen_events = {h: ts for h, ts in self.seen_events.items() if now - ts < 300}
        
        if event_hash in self.seen_events:
            logger.debug(f"Event {event_hash} deduplicated (within 5 mins).")
            return
            
        self.seen_events[event_hash] = now
        
        raw_score = self._assign_base_score(event.log_type)
        # Enforce a min(3.0, score) ceiling cap per event
        score = min(3.0, raw_score)
        
        if score > 0:
            self.window_events.append({
                "timestamp": now,
                "score": score,
                "log_type": event.log_type,
                "content": event.content,
                "hash": event_hash
            })
            
            total_gep = self.calculate_current_gep()
            logger.info(f"Current GEP: {total_gep:.2f} / {self.gep_threshold}")
            
            # Decay is evaluated in real time; three just-recorded 1.0 events
            # are microscopically below 3.0 by the time of comparison.  Treat
            # that numerical noise as the documented threshold, not a missed
            # growth trigger.
            if total_gep + 0.01 >= self.gep_threshold:
                self.trigger_evolution()

    def reconcile(self, events) -> int:
        """Replay persisted lifecycle events after a host restart.

        The normal hook remains the low-latency path; this scheduled path means
        a short-lived agent process cannot silently lose GEP evidence.
        """
        before = len(self.window_events)
        for event in events:
            self._on_log_event(LogEvent(str(event.get("log_type", "")),
                                        str(event.get("content", "")),
                                        dict(event.get("metadata", {}))))
        return max(0, len(self.window_events) - before)

    def _assign_base_score(self, log_type: str) -> float:
        """Assign base GEP score based on event type."""
        return self.policy.weights.get(log_type, 0.0)

    def calculate_current_gep(self) -> float:
        """Calculate time-decayed GEP over the sliding window."""
        now = datetime.now(timezone.utc)
        valid_events = []
        for ev in self.window_events:
            timestamp = datetime.fromtimestamp(ev["timestamp"], timezone.utc)
            if (now - timestamp).total_seconds() < self.policy.window_hours * 3600:
                valid_events.append(ev)
        self.window_events = valid_events
        return self.policy.score([{"log_type": ev.get("log_type", "task_failure"), "content": ev["content"], "timestamp": datetime.fromtimestamp(ev["timestamp"], timezone.utc).isoformat()} for ev in valid_events], now)

    def trigger_evolution(self):
        """Trigger the evolution orchestrator when GEP threshold is reached."""
        logger.warning(f"GEP Threshold ({self.gep_threshold}) reached! Triggering Evolution Protocol.")
        triggering_events = list(self.window_events)
        self.window_events.clear()  # Reset after trigger
        proposal_id = self.proposal_store.create({
            "kind": "growth",
            "gep_score": self.gep_threshold,
            "source_events": triggering_events,
        })
        bus.publish(EvolutionTriggeredEvent(
            gep_score=self.gep_threshold,
            events=triggering_events,
            proposal_id=proposal_id,
        ))
        logger.info("Created durable growth proposal: %s", proposal_id)
