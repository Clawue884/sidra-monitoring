"""
Central Ingest API.

Receives metrics, logs, and alerts from Edge Agents and stores them
in VictoriaMetrics and OpenObserve.
"""

import asyncio
import json
import logging
import os
import time
from typing import Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import aiohttp

logger = logging.getLogger(__name__)


# Configuration
VICTORIAMETRICS_URL = os.getenv("VICTORIAMETRICS_URL", "http://localhost:8428")
OPENOBSERVE_URL = os.getenv("OPENOBSERVE_URL", "http://localhost:5080")
OPENOBSERVE_USER = os.getenv("OPENOBSERVE_USER", "admin@sidra.local")
OPENOBSERVE_PASSWORD = os.getenv("OPENOBSERVE_PASSWORD", "SidraMonitor2024!")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "devstral")


# Request/Response models
class MetricPoint(BaseModel):
    name: str
    value: float
    timestamp: float
    labels: dict = Field(default_factory=dict)


class Alert(BaseModel):
    metric: str
    value: str | float | int
    threshold: str | float | int | None = None
    severity: str
    message: str
    timestamp: float
    host: str = ""
    labels: dict = Field(default_factory=dict)


class LogEntry(BaseModel):
    level: str
    message: str
    source: str = ""
    timestamp: float = Field(default_factory=time.time)


class MetricsPayload(BaseModel):
    timestamp: float
    host: str = ""
    priority: str = "NORMAL"
    metrics: list[MetricPoint] = Field(default_factory=list)


class AlertsPayload(BaseModel):
    timestamp: float
    host: str = ""
    alert: Alert | None = None
    alerts: list[Alert] = Field(default_factory=list)


class LogsPayload(BaseModel):
    timestamp: float
    host: str = ""
    logs: list[LogEntry] = Field(default_factory=list)


class BatchPayload(BaseModel):
    timestamp: float
    host: str = ""
    priority: str = "NORMAL"
    metrics: list[MetricPoint] = Field(default_factory=list)
    alerts: list[Alert] = Field(default_factory=list)
    logs: list[dict] = Field(default_factory=list)


# Storage clients
class VictoriaMetricsClient:
    """Client for VictoriaMetrics."""

    def __init__(self, url: str):
        self.url = url.rstrip('/')
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def write(self, metrics: list[MetricPoint]) -> bool:
        """Write metrics in Prometheus format."""
        if not metrics:
            return True

        # Convert to Prometheus format
        lines = []
        for m in metrics:
            labels_str = ",".join(f'{k}="{v}"' for k, v in m.labels.items())
            if labels_str:
                lines.append(f"{m.name}{{{labels_str}}} {m.value} {int(m.timestamp * 1000)}")
            else:
                lines.append(f"{m.name} {m.value} {int(m.timestamp * 1000)}")

        data = "\n".join(lines)

        try:
            session = await self._get_session()
            async with session.post(
                f"{self.url}/api/v1/import/prometheus",
                data=data,
                headers={"Content-Type": "text/plain"},
            ) as response:
                return response.status in (200, 204)
        except Exception as e:
            logger.error(f"VictoriaMetrics write error: {e}")
            return False

    async def query(self, query: str) -> dict:
        """Execute a PromQL query."""
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.url}/api/v1/query",
                params={"query": query},
            ) as response:
                return await response.json()
        except Exception as e:
            logger.error(f"VictoriaMetrics query error: {e}")
            return {}

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class OpenObserveClient:
    """Client for OpenObserve."""

    def __init__(self, url: str, user: str, password: str):
        self.url = url.rstrip('/')
        self.user = user
        self.password = password
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            auth = aiohttp.BasicAuth(self.user, self.password)
            self._session = aiohttp.ClientSession(auth=auth)
        return self._session

    async def write_logs(self, logs: list[dict], stream: str = "logs") -> bool:
        """Write logs to OpenObserve."""
        if not logs:
            return True

        # Format logs for OpenObserve
        formatted = []
        for log in logs:
            formatted.append({
                "_timestamp": int(log.get('timestamp', time.time()) * 1000000),  # microseconds
                "level": log.get('level', 'info'),
                "message": log.get('message', ''),
                "source": log.get('source', ''),
                "host": log.get('host', ''),
            })

        try:
            session = await self._get_session()
            async with session.post(
                f"{self.url}/api/default/{stream}/_json",
                json=formatted,
            ) as response:
                return response.status in (200, 204)
        except Exception as e:
            logger.error(f"OpenObserve write error: {e}")
            return False

    async def write_alerts(self, alerts: list[Alert]) -> bool:
        """Write alerts to OpenObserve alerts stream."""
        if not alerts:
            return True

        formatted = []
        for alert in alerts:
            formatted.append({
                "_timestamp": int(alert.timestamp * 1000000),
                "metric": alert.metric,
                "value": str(alert.value),
                "threshold": str(alert.threshold) if alert.threshold else "",
                "severity": alert.severity,
                "message": alert.message,
                "host": alert.host,
            })

        return await self.write_logs(formatted, stream="alerts")

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# Alert store for LLM analysis
class AlertStore:
    """In-memory store for recent alerts."""

    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self._alerts: list[Alert] = []
        self._lock = asyncio.Lock()

    async def add(self, alert: Alert):
        async with self._lock:
            self._alerts.append(alert)
            if len(self._alerts) > self.max_size:
                self._alerts = self._alerts[-self.max_size:]

    async def get_recent(self, count: int = 100) -> list[Alert]:
        async with self._lock:
            return self._alerts[-count:]

    async def get_by_severity(self, severity: str, count: int = 50) -> list[Alert]:
        async with self._lock:
            return [a for a in self._alerts if a.severity == severity][-count:]


# Create FastAPI app
def create_app() -> FastAPI:
    """Create the FastAPI application."""

    app = FastAPI(
        title="Sidra Central Brain - Ingest API",
        description="Receives metrics, logs, and alerts from Edge Agents",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Initialize clients
    vm_client = VictoriaMetricsClient(VICTORIAMETRICS_URL)
    oo_client = OpenObserveClient(OPENOBSERVE_URL, OPENOBSERVE_USER, OPENOBSERVE_PASSWORD)
    alert_store = AlertStore()

    @app.on_event("shutdown")
    async def shutdown():
        await vm_client.close()
        await oo_client.close()

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {
            "status": "healthy",
            "timestamp": time.time(),
            "components": {
                "victoriametrics": VICTORIAMETRICS_URL,
                "openobserve": OPENOBSERVE_URL,
            }
        }

    @app.post("/api/v1/ingest/metrics")
    async def ingest_metrics(payload: MetricsPayload, background_tasks: BackgroundTasks):
        """Ingest metrics from edge agents."""
        if not payload.metrics:
            return {"status": "ok", "message": "No metrics to ingest"}

        # Add host label to all metrics
        for metric in payload.metrics:
            if payload.host and 'host' not in metric.labels:
                metric.labels['host'] = payload.host

        # Write to VictoriaMetrics
        success = await vm_client.write(payload.metrics)

        if not success:
            raise HTTPException(status_code=500, detail="Failed to write metrics")

        return {
            "status": "ok",
            "metrics_received": len(payload.metrics),
        }

    @app.post("/api/v1/ingest/alerts")
    async def ingest_alerts(payload: AlertsPayload, background_tasks: BackgroundTasks):
        """Ingest alerts from edge agents."""
        alerts = payload.alerts
        if payload.alert:
            alerts.append(payload.alert)

        if not alerts:
            return {"status": "ok", "message": "No alerts to ingest"}

        # Store alerts
        for alert in alerts:
            if not alert.host and payload.host:
                alert.host = payload.host
            await alert_store.add(alert)

        # Write to OpenObserve
        success = await oo_client.write_alerts(alerts)

        # Log critical alerts
        for alert in alerts:
            if alert.severity in ('critical', 'high'):
                logger.warning(f"ALERT [{alert.severity.upper()}] {alert.host}: {alert.message}")

        return {
            "status": "ok",
            "alerts_received": len(alerts),
        }

    @app.post("/api/v1/ingest/logs")
    async def ingest_logs(payload: LogsPayload):
        """Ingest logs from edge agents."""
        if not payload.logs:
            return {"status": "ok", "message": "No logs to ingest"}

        # Add host to logs
        logs = []
        for log in payload.logs:
            log_dict = log.dict()
            log_dict['host'] = payload.host
            logs.append(log_dict)

        # Write to OpenObserve
        success = await oo_client.write_logs(logs)

        if not success:
            raise HTTPException(status_code=500, detail="Failed to write logs")

        return {
            "status": "ok",
            "logs_received": len(payload.logs),
        }

    @app.post("/api/v1/ingest/batch")
    async def ingest_batch(payload: BatchPayload, background_tasks: BackgroundTasks):
        """Ingest a batch of metrics, alerts, and logs."""
        results = {}

        # Process metrics
        if payload.metrics:
            for metric in payload.metrics:
                if payload.host and 'host' not in metric.labels:
                    metric.labels['host'] = payload.host
            await vm_client.write(payload.metrics)
            results['metrics'] = len(payload.metrics)

        # Process alerts
        if payload.alerts:
            for alert in payload.alerts:
                if not alert.host:
                    alert.host = payload.host
                await alert_store.add(alert)
            await oo_client.write_alerts(payload.alerts)
            results['alerts'] = len(payload.alerts)

        # Process logs
        if payload.logs:
            logs = []
            for log in payload.logs:
                log['host'] = payload.host
                logs.append(log)
            await oo_client.write_logs(logs)
            results['logs'] = len(payload.logs)

        return {
            "status": "ok",
            "received": results,
        }

    @app.get("/api/v1/alerts/recent")
    async def get_recent_alerts(count: int = 100):
        """Get recent alerts."""
        alerts = await alert_store.get_recent(count)
        return {
            "count": len(alerts),
            "alerts": [a.dict() for a in alerts],
        }

    @app.get("/api/v1/alerts/critical")
    async def get_critical_alerts(count: int = 50):
        """Get critical alerts."""
        alerts = await alert_store.get_by_severity("critical", count)
        return {
            "count": len(alerts),
            "alerts": [a.dict() for a in alerts],
        }

    @app.get("/api/v1/query")
    async def query_metrics(q: str):
        """Query metrics using PromQL."""
        result = await vm_client.query(q)
        return result

    @app.get("/api/v1/summary")
    async def get_summary():
        """Get a summary of infrastructure status."""
        # Query for current metrics
        queries = {
            "hosts_up": 'count(sidra_agent_health == 1)',
            "avg_cpu": 'avg(sidra_cpu_usage_percent)',
            "avg_memory": 'avg(sidra_memory_usage_percent)',
            "critical_alerts": 'count(alerts{severity="critical"})',
        }

        results = {}
        for name, query in queries.items():
            try:
                result = await vm_client.query(query)
                if result.get('data', {}).get('result'):
                    results[name] = result['data']['result'][0]['value'][1]
            except Exception:
                results[name] = 'N/A'

        # Get recent alerts
        recent_alerts = await alert_store.get_recent(10)

        return {
            "timestamp": time.time(),
            "metrics": results,
            "recent_alerts": [
                {"severity": a.severity, "host": a.host, "message": a.message}
                for a in recent_alerts
            ],
        }

    return app


# Run with uvicorn
app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8200)
