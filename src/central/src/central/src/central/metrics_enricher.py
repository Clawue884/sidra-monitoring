"""
Metrics Enricher for Sidra Autonomous Ops
------------------------------------------
Normalizes and adds context to metrics from edge agents.
"""

from typing import Dict

class MetricsEnricher:
    def __init__(self):
        pass

    def enrich(self, node: str, metrics: Dict[str, float]) -> Dict[str, float]:
        enriched = metrics.copy()
        # Example enrichment
        if "cpu" in metrics:
            enriched["cpu_per_core"] = metrics["cpu"] / 8  # Assuming 8 cores
        if "gpu_temp" in metrics:
            enriched["gpu_overheat_flag"] = metrics["gpu_temp"] > 80
        return enriched

# Standalone Test
if __name__ == "__main__":
    me = MetricsEnricher()
    sample_metrics = {"cpu": 90, "gpu_temp": 85}
    print(me.enrich("server041", sample_metrics))
