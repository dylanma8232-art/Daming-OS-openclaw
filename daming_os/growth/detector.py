import json
import logging
import hashlib
from datetime import datetime, timezone
from ..config import config
from ..events import bus, EvolutionTriggeredEvent, LogEvent
from .proposals import ProposalStore

logger = logging.getLogger("daming_os.growth.detector")

class GEPDetector:
    """
    Growth Experience Point (GEP) detection engine.
    Calculates sliding window exponentially decayed scores to detect evolution triggers.
    """
    def __init__(self, proposal_store: ProposalStore = None):
        self.gep_threshold = config.GEP_THRESHOLD
        self.decay_factor = 0.5  # half-life logic
        self.window_events = []
        self.seen_events = {}  # sha256 -> timestamp
        self.proposal_store = proposal_store or ProposalStore()
        
        # Subscribe to LogEvent to calculate real-time GEP
        bus.subscribe(LogEvent, self._on_log_event)

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
                "content": event.content,
                "hash": event_hash
            })
            
            total_gep = self.calculate_current_gep()
            logger.info(f"Current GEP: {total_gep:.2f} / {self.gep_threshold}")
            
            if total_gep >= self.gep_threshold:
                self.trigger_evolution()

    def _assign_base_score(self, log_type: str) -> float:
        """Assign base GEP score based on event type."""
        scores = {
            "rule_violation": 2.5,
            "task_failure": 3.0,
            "discovery": 1.5,
            "task_complete": 0.5
        }
        return scores.get(log_type, 0.0)

    def calculate_current_gep(self) -> float:
        """Calculate time-decayed GEP over the sliding window."""
        now = datetime.now(timezone.utc).timestamp()
        total_gep = 0.0
        
        # Clean old events
        valid_events = []
        for ev in self.window_events:
            hours_passed = (now - ev["timestamp"]) / 3600.0
            if hours_passed < 24:  # 24h sliding window
                decayed_score = ev["score"] * (self.decay_factor ** hours_passed)
                total_gep += decayed_score
                valid_events.append(ev)
                
        self.window_events = valid_events
        return total_gep

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
        ))
        logger.info("Created durable growth proposal: %s", proposal_id)
