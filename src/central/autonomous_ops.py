# src/central/autonomous_ops.py
"""
Sidra Autonomous Ops Engine
---------------------------
Adds predictive intelligence, root-cause analysis,
and autonomous decision-making on top of existing monitoring stack.
"""

import time
import statistics
import requests
from typing import Dict, List, Any
from datetime import datetime

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "devstral"

# =========================
# Core Event Schema
# =========================
class InfraEvent:
    def __init__(self, node: str, metric: str, value: float, severity: str):
        self.node = node
        self.metric = metric
        self.value = value
        self.severity = severity
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self):
        return self.__dict__


# =========================
# Anomaly & Prediction
# =========================
class AnomalyDetector:
    def __init__(self, window_size: int = 10):
        self.window_size = window_size
        self.history: Dict[str, List[float]] = {}

    def add(self, key: str, value: float):
        self.history.setdefault(key, []).append(value)
        if len(self.history[key]) > self.window_size:
            self.history[key].pop(0)

    def is_anomaly(self, key: str, value: float) -> bool:
        if key not in self.history or len(self.history[key]) < 5:
            return False
        mean = statistics.mean(self.history[key])
        stdev = statistics.stdev(self.history[key])
        return abs(value - mean) > (2 * stdev)


# =========================
# LLM Reasoning Engine
# =========================
class LLMReasoner:
    def analyze(self, events: List[InfraEvent]) -> str:
        prompt = f"""
You are an AIOps engine.
Analyze the following infrastructure events.
Identify root cause, cascading risks, and recommendations.

Events:
{[e.to_dict() for e in events]}

Return concise operational insight.
"""
        try:
            res = requests.post(
                OLLAMA_URL,
                json={
                    "model": MODEL,
                    "prompt": prompt,
                    "stream": False
                },
                timeout=20
            )
            return res.json().get("response", "No analysis returned")
        except Exception as e:
            return f"LLM error: {e}"


# =========================
# Decision Engine
# =========================
class DecisionEngine:
    def decide(self, events: List[InfraEvent], analysis: str) -> Dict[str, Any]:
        severity_levels = [e.severity for e in events]

        if "critical" in severity_levels:
            action = "ESCALATE_IMMEDIATELY"
        elif "high" in severity_levels:
            action = "PRIORITIZE_INVESTIGATION"
        else:
            action = "MONITOR"

        return {
            "action": action,
            "analysis": analysis,
            "event_count": len(events),
            "timestamp": datetime.utcnow().isoformat()
        }


# =========================
# Autonomous Orchestrator
# =========================
class AutonomousOps:
    def __init__(self):
        self.detector = AnomalyDetector()
        self.llm = LLMReasoner()
        self.decision_engine = DecisionEngine()

    def ingest_metrics(self, node: str, metrics: Dict[str, float]) -> Dict[str, Any]:
        events = []

        for metric, value in metrics.items():
            key = f"{node}:{metric}"
            self.detector.add(key, value)

            if self.detector.is_anomaly(key, value):
                severity = "critical" if value > 90 else "high"
                events.append(InfraEvent(node, metric, value, severity))

        if not events:
            return {"status": "ok", "message": "No anomaly detected"}

        analysis = self.llm.analyze(events)
        decision = self.decision_engine.decide(events, analysis)

        return {
            "status": "anomaly_detected",
            "events": [e.to_dict() for e in events],
            "decision": decision
        }


# =========================
# Standalone Test
# =========================
if __name__ == "__main__":
    ops = AutonomousOps()

    # simulate metrics
    for i in range(12):
        result = ops.ingest_metrics(
            node="server041",
            metrics={
                "cpu": 60 + i * 3,
                "memory": 70 + i * 2,
                "gpu_temp": 65 + i * 2
            }
        )
        print(result)
        time.sleep(1)
