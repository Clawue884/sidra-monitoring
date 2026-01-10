"""FastAPI application for DevOps Agent."""

import asyncio
from pathlib import Path
from typing import Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..config import settings
from ..agents import InfrastructureAgent, DocumentationAgent, MonitoringAgent

app = FastAPI(
    title="DevOps Agent API",
    description="AI-powered infrastructure discovery, monitoring, and documentation",
    version="1.0.0",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global agents
infra_agent = InfrastructureAgent()
doc_agent = DocumentationAgent()
monitor_agent: Optional[MonitoringAgent] = None


# Request/Response models
class ScanRequest(BaseModel):
    host: str


class NetworkScanRequest(BaseModel):
    cidr: str
    quick: bool = False


class MonitorRequest(BaseModel):
    hosts: list[str]
    interval: int = 60


class DocumentRequest(BaseModel):
    data: dict
    format: str = "markdown"


# Endpoints
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "monitoring_active": monitor_agent is not None and monitor_agent._running,
    }


@app.get("/discover")
async def full_discovery(background_tasks: BackgroundTasks):
    """Start full infrastructure discovery."""
    async def run_discovery():
        analysis = await infra_agent.full_discovery()
        result = infra_agent.to_dict(analysis)

        # Save to file
        settings.ensure_dirs()
        output_file = settings.output_dir / "discovery_result.json"
        import json
        output_file.write_text(json.dumps(result, indent=2))

    background_tasks.add_task(lambda: asyncio.run(run_discovery()))

    return {
        "status": "started",
        "message": "Discovery started in background. Check /discovery/status for results.",
    }


@app.get("/discovery/status")
async def discovery_status():
    """Get the latest discovery results."""
    output_file = settings.output_dir / "discovery_result.json"
    if output_file.exists():
        import json
        return json.loads(output_file.read_text())
    return {"status": "no_data", "message": "No discovery data available. Run /discover first."}


@app.post("/scan")
async def scan_host(request: ScanRequest):
    """Quick scan of a single host."""
    result = await infra_agent.quick_scan(request.host)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/network/scan")
async def scan_network(request: NetworkScanRequest):
    """Scan a network."""
    from ..discovery import NetworkScanner
    scanner = NetworkScanner()

    if request.quick:
        hosts = await scanner.quick_ping_scan(request.cidr)
        return {"cidr": request.cidr, "live_hosts": hosts}
    else:
        result = await scanner.scan_network(request.cidr)
        return scanner.to_dict(result)


@app.post("/monitor/start")
async def start_monitoring(request: MonitorRequest, background_tasks: BackgroundTasks):
    """Start monitoring specified hosts."""
    global monitor_agent

    if monitor_agent and monitor_agent._running:
        return {"status": "already_running", "hosts": monitor_agent.hosts}

    monitor_agent = MonitoringAgent(hosts=request.hosts, interval=request.interval)

    async def run_monitoring():
        await monitor_agent.start()

    background_tasks.add_task(lambda: asyncio.run(run_monitoring()))

    return {
        "status": "started",
        "hosts": request.hosts,
        "interval": request.interval,
    }


@app.post("/monitor/stop")
async def stop_monitoring():
    """Stop monitoring."""
    global monitor_agent

    if not monitor_agent or not monitor_agent._running:
        return {"status": "not_running"}

    await monitor_agent.stop()
    return {"status": "stopped"}


@app.get("/monitor/status")
async def monitoring_status():
    """Get current monitoring status."""
    global monitor_agent

    if not monitor_agent:
        return {"status": "not_configured"}

    return monitor_agent.get_status_summary()


@app.get("/monitor/alerts")
async def get_alerts():
    """Get active alerts."""
    global monitor_agent

    if not monitor_agent:
        return {"alerts": []}

    return {
        "alerts": [
            {
                "id": a.id,
                "severity": a.severity,
                "host": a.host,
                "message": a.message,
                "created_at": a.created_at.isoformat(),
                "acknowledged": a.acknowledged,
            }
            for a in monitor_agent.alerts
            if not a.acknowledged
        ]
    }


@app.post("/monitor/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str):
    """Acknowledge an alert."""
    global monitor_agent

    if not monitor_agent:
        raise HTTPException(status_code=404, detail="Monitoring not active")

    if monitor_agent.acknowledge_alert(alert_id):
        return {"status": "acknowledged", "alert_id": alert_id}
    raise HTTPException(status_code=404, detail="Alert not found")


@app.post("/document")
async def generate_documentation(request: DocumentRequest):
    """Generate documentation from discovery data."""
    if request.format == "markdown":
        doc = await doc_agent.generate_full_documentation(request.data)
        return {"format": "markdown", "content": doc}
    else:
        return {"format": "json", "content": request.data}


@app.get("/document/daily")
async def daily_report():
    """Generate a daily infrastructure report."""
    output_file = settings.output_dir / "discovery_result.json"
    if not output_file.exists():
        raise HTTPException(status_code=404, detail="No discovery data. Run /discover first.")

    import json
    data = json.loads(output_file.read_text())
    report = await doc_agent.generate_daily_report(data)

    return {"report": report, "generated_at": datetime.now().isoformat()}


@app.get("/networks")
async def list_networks():
    """List configured networks."""
    return {"networks": settings.networks_list}


@app.get("/config")
async def get_config():
    """Get current configuration (non-sensitive)."""
    return {
        "ollama_host": settings.ollama_host,
        "ollama_model": settings.ollama_model,
        "networks": settings.networks_list,
        "monitor_interval": settings.monitor_interval,
        "output_dir": str(settings.output_dir),
    }
