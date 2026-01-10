"""
Sidra Infrastructure Monitor - LLM-Powered Report API.

Advanced dashboard with real-time metrics, AI analysis, filtering, and multi-network support.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from collections import defaultdict

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import aiohttp

logger = logging.getLogger(__name__)

# Configuration
VICTORIAMETRICS_URL = os.getenv("VICTORIAMETRICS_URL", "http://localhost:8428")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "devstral")
INGEST_API_URL = os.getenv("INGEST_API_URL", "http://localhost:8200")

# Network Configuration - All Sidra Networks
NETWORK_CONFIG = {
    "192.168.91": {
        "name": "Sidra-91 (Secondary)",
        "color": "#4CAF50",
        "description": "Secondary production network",
        "servers": {
            "192.168.91.62": {"name": "server012", "role": "compute"},
            "192.168.91.63": {"name": "server013", "role": "compute"},
            "192.168.91.64": {"name": "server014", "role": "compute"},
            "192.168.91.91": {"name": "server031", "role": "compute"},
            "192.168.91.92": {"name": "server032", "role": "compute"},
        }
    },
    "192.168.92": {
        "name": "Sidra-92 (Primary)",
        "color": "#2196F3",
        "description": "Primary production network with GPU servers",
        "servers": {
            "192.168.92.54": {"name": "server004", "role": "compute"},
            "192.168.92.58": {"name": "server008", "role": "compute"},
            "192.168.92.59": {"name": "server009", "role": "compute", "note": "High disk usage"},
            "192.168.92.81": {"name": "server021", "role": "gpu", "gpu": "RTX 5070 Ti"},
            "192.168.92.133": {"name": "server033", "role": "compute"},
            "192.168.92.134": {"name": "server034", "role": "compute"},
            "192.168.92.141": {"name": "server041", "role": "gpu", "gpu": "RTX 4090"},
            "192.168.92.143": {"name": "server043", "role": "gpu", "gpu": "RTX 5090"},
            "192.168.92.144": {"name": "server044", "role": "compute"},
            "192.168.92.145": {"name": "server045", "role": "central", "note": "Central Brain"},
        }
    },
    "192.168.93": {
        "name": "Sidra-93 (Development)",
        "color": "#FF9800",
        "description": "Development and testing network",
        "servers": {}
    },
    "192.168.94": {
        "name": "Sidra-94 (Storage)",
        "color": "#9C27B0",
        "description": "Storage and backup network",
        "servers": {}
    },
}


def get_network_for_host(hostname: str) -> dict:
    """Determine network info from hostname or IP."""
    for prefix, config in NETWORK_CONFIG.items():
        for ip, server_info in config.get("servers", {}).items():
            if server_info.get("name") == hostname or hostname == ip:
                return {
                    "network": prefix,
                    "network_name": config["name"],
                    "color": config["color"],
                    "role": server_info.get("role", "compute"),
                    "ip": ip,
                    **server_info
                }
    # Try to extract network from hostname pattern
    if "server" in hostname.lower():
        return {"network": "unknown", "network_name": "Unknown", "color": "#666", "role": "compute"}
    return {"network": "unknown", "network_name": "Unknown", "color": "#666", "role": "compute"}


class ReportResponse(BaseModel):
    timestamp: str
    report_type: str
    summary: str
    details: dict
    recommendations: list[str]


async def query_victoriametrics(query: str, time_range: str = "5m") -> dict:
    """Query VictoriaMetrics."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{VICTORIAMETRICS_URL}/api/v1/query",
            params={"query": query}
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            return {}


async def query_victoriametrics_range(query: str, start: str, end: str, step: str = "60s") -> dict:
    """Query VictoriaMetrics for range data."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{VICTORIAMETRICS_URL}/api/v1/query_range",
            params={"query": query, "start": start, "end": end, "step": step}
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            return {}


async def get_alerts() -> list:
    """Get recent alerts from ingest API."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{INGEST_API_URL}/api/v1/alerts/recent?count=100") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("alerts", [])
    except:
        pass
    return []


async def get_uptime_stats() -> dict:
    """Calculate uptime statistics from metrics."""
    stats = {"total_uptime_percent": 99.9, "hosts_uptime": {}}
    # In production, query historical data for real uptime calculation
    return stats


async def generate_llm_report(prompt: str) -> str:
    """Generate report using Ollama LLM."""
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 500}
            }
            async with session.post(
                f"{OLLAMA_HOST}/api/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120)
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result.get("response", "")
    except Exception as e:
        logger.error(f"LLM generation error: {e}")
    return ""


async def collect_infrastructure_data() -> dict:
    """Collect all infrastructure metrics with network classification."""
    data = {
        "timestamp": datetime.now().isoformat(),
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "hosts": [],
        "gpus": [],
        "alerts": [],
        "networks": defaultdict(lambda: {"hosts": [], "stats": {}}),
        "summary": {},
        "trends": {}
    }

    # Get host count
    result = await query_victoriametrics("count(sidra_cpu_percent)")
    if result.get("data", {}).get("result"):
        data["summary"]["host_count"] = int(float(result["data"]["result"][0]["value"][1]))

    # Get CPU metrics per host
    result = await query_victoriametrics("sidra_cpu_percent")
    if result.get("data", {}).get("result"):
        for r in result["data"]["result"]:
            host = r["metric"].get("host", "unknown")
            cpu = float(r["value"][1])
            network_info = get_network_for_host(host)
            host_data = {
                "name": host,
                "cpu": cpu,
                "network": network_info["network"],
                "network_name": network_info["network_name"],
                "network_color": network_info["color"],
                "role": network_info.get("role", "compute"),
                "ip": network_info.get("ip", ""),
            }
            data["hosts"].append(host_data)

    # Get memory metrics
    result = await query_victoriametrics("sidra_memory_percent")
    if result.get("data", {}).get("result"):
        for r in result["data"]["result"]:
            host = r["metric"].get("host", "unknown")
            mem = float(r["value"][1])
            for h in data["hosts"]:
                if h["name"] == host:
                    h["memory"] = mem
                    break

    # Get disk metrics
    result = await query_victoriametrics("sidra_disk_percent")
    if result.get("data", {}).get("result"):
        for r in result["data"]["result"]:
            host = r["metric"].get("host", "unknown")
            disk = float(r["value"][1])
            for h in data["hosts"]:
                if h["name"] == host:
                    h["disk"] = disk
                    break

    # Get network I/O
    result = await query_victoriametrics("sidra_net_bytes_sent")
    if result.get("data", {}).get("result"):
        for r in result["data"]["result"]:
            host = r["metric"].get("host", "unknown")
            net_sent = float(r["value"][1])
            for h in data["hosts"]:
                if h["name"] == host:
                    h["net_sent"] = net_sent
                    break

    result = await query_victoriametrics("sidra_net_bytes_recv")
    if result.get("data", {}).get("result"):
        for r in result["data"]["result"]:
            host = r["metric"].get("host", "unknown")
            net_recv = float(r["value"][1])
            for h in data["hosts"]:
                if h["name"] == host:
                    h["net_recv"] = net_recv
                    break

    # Get load average
    result = await query_victoriametrics("sidra_load_1m")
    if result.get("data", {}).get("result"):
        for r in result["data"]["result"]:
            host = r["metric"].get("host", "unknown")
            load = float(r["value"][1])
            for h in data["hosts"]:
                if h["name"] == host:
                    h["load_1m"] = load
                    break

    # Get GPU metrics
    result = await query_victoriametrics("sidra_gpu_temp")
    if result.get("data", {}).get("result"):
        for r in result["data"]["result"]:
            network_info = get_network_for_host(r["metric"].get("host", "unknown"))
            data["gpus"].append({
                "host": r["metric"].get("host", "unknown"),
                "name": r["metric"].get("name", "unknown"),
                "index": r["metric"].get("gpu", "0"),
                "temp": float(r["value"][1]),
                "network": network_info["network"],
                "network_name": network_info["network_name"],
            })

    # Get GPU utilization
    result = await query_victoriametrics("sidra_gpu_util")
    if result.get("data", {}).get("result"):
        for r in result["data"]["result"]:
            host = r["metric"].get("host", "unknown")
            gpu_idx = r["metric"].get("gpu", "0")
            util = float(r["value"][1])
            for g in data["gpus"]:
                if g["host"] == host and g["index"] == gpu_idx:
                    g["util"] = util
                    break

    # Get GPU memory
    result = await query_victoriametrics("sidra_gpu_memory_used")
    if result.get("data", {}).get("result"):
        for r in result["data"]["result"]:
            host = r["metric"].get("host", "unknown")
            gpu_idx = r["metric"].get("gpu", "0")
            mem_used = float(r["value"][1])
            for g in data["gpus"]:
                if g["host"] == host and g["index"] == gpu_idx:
                    g["memory_used"] = mem_used
                    break

    result = await query_victoriametrics("sidra_gpu_memory_total")
    if result.get("data", {}).get("result"):
        for r in result["data"]["result"]:
            host = r["metric"].get("host", "unknown")
            gpu_idx = r["metric"].get("gpu", "0")
            mem_total = float(r["value"][1])
            for g in data["gpus"]:
                if g["host"] == host and g["index"] == gpu_idx:
                    g["memory_total"] = mem_total
                    g["memory_percent"] = round((g.get("memory_used", 0) / mem_total * 100), 1) if mem_total > 0 else 0
                    break

    # Get alerts
    data["alerts"] = await get_alerts()

    # Classify hosts by network
    for h in data["hosts"]:
        network = h.get("network", "unknown")
        data["networks"][network]["hosts"].append(h)

    # Calculate per-network statistics
    for network, net_data in data["networks"].items():
        hosts = net_data["hosts"]
        if hosts:
            net_data["stats"] = {
                "host_count": len(hosts),
                "avg_cpu": round(sum(h.get("cpu", 0) for h in hosts) / len(hosts), 1),
                "avg_memory": round(sum(h.get("memory", 0) for h in hosts) / len(hosts), 1),
                "avg_disk": round(sum(h.get("disk", 0) for h in hosts) / len(hosts), 1),
                "max_cpu": max(h.get("cpu", 0) for h in hosts),
                "max_memory": max(h.get("memory", 0) for h in hosts),
                "max_disk": max(h.get("disk", 0) for h in hosts),
            }

    # Calculate overall statistics
    if data["hosts"]:
        data["summary"]["avg_cpu"] = round(sum(h.get("cpu", 0) for h in data["hosts"]) / len(data["hosts"]), 1)
        data["summary"]["avg_memory"] = round(sum(h.get("memory", 0) for h in data["hosts"]) / len(data["hosts"]), 1)
        data["summary"]["avg_disk"] = round(sum(h.get("disk", 0) for h in data["hosts"]) / len(data["hosts"]), 1)
        data["summary"]["max_cpu"] = round(max(h.get("cpu", 0) for h in data["hosts"]), 2)
        data["summary"]["max_memory"] = round(max(h.get("memory", 0) for h in data["hosts"]), 2)
        data["summary"]["max_disk"] = round(max(h.get("disk", 0) for h in data["hosts"]), 2)
        data["summary"]["min_cpu"] = round(min(h.get("cpu", 0) for h in data["hosts"]), 2)
        data["summary"]["min_memory"] = round(min(h.get("memory", 0) for h in data["hosts"]), 2)
        data["summary"]["min_disk"] = round(min(h.get("disk", 0) for h in data["hosts"]), 2)
        data["summary"]["total_load"] = round(sum(h.get("load_1m", 0) for h in data["hosts"]), 2)

    data["summary"]["gpu_count"] = len(data["gpus"])
    data["summary"]["alert_count"] = len(data["alerts"])
    data["summary"]["critical_alerts"] = len([a for a in data["alerts"] if a.get("severity") == "critical"])
    data["summary"]["high_alerts"] = len([a for a in data["alerts"] if a.get("severity") == "high"])
    data["summary"]["network_count"] = len([n for n, d in data["networks"].items() if d["hosts"]])

    # Identify issues
    data["issues"] = []
    for h in data["hosts"]:
        if h.get("disk", 0) > 90:
            data["issues"].append({"host": h["name"], "type": "critical", "message": f"Disk at {h['disk']:.1f}%"})
        elif h.get("disk", 0) > 80:
            data["issues"].append({"host": h["name"], "type": "warning", "message": f"Disk at {h['disk']:.1f}%"})
        if h.get("cpu", 0) > 90:
            data["issues"].append({"host": h["name"], "type": "critical", "message": f"CPU at {h['cpu']:.1f}%"})
        if h.get("memory", 0) > 95:
            data["issues"].append({"host": h["name"], "type": "critical", "message": f"Memory at {h['memory']:.1f}%"})

    return data


def create_app() -> FastAPI:
    """Create the FastAPI application."""

    app = FastAPI(
        title="Sidra Infrastructure Report API",
        description="LLM-powered infrastructure monitoring with multi-network support",
        version="2.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "timestamp": time.time(), "version": "2.0.0"}

    @app.get("/api/v1/networks")
    async def get_networks():
        """Get all configured networks."""
        return {"networks": NETWORK_CONFIG}

    @app.get("/api/v1/report/summary")
    async def get_summary_report(
        network: Optional[str] = Query(None, description="Filter by network (e.g., 192.168.92)")
    ):
        """Get AI-generated infrastructure summary with optional network filter."""
        data = await collect_infrastructure_data()

        # Filter by network if specified
        if network:
            data["hosts"] = [h for h in data["hosts"] if h.get("network") == network]
            data["gpus"] = [g for g in data["gpus"] if g.get("network") == network]

        # Build prompt for LLM
        prompt = f"""You are a DevOps monitoring assistant. Analyze this infrastructure data and provide a brief, actionable summary.

Infrastructure Status ({data['timestamp']}):
- Total Hosts: {len(data['hosts'])}
- Networks: {data['summary'].get('network_count', 0)}
- Average CPU: {data['summary'].get('avg_cpu', 0)}%
- Average Memory: {data['summary'].get('avg_memory', 0)}%
- Average Disk: {data['summary'].get('avg_disk', 0)}%
- Max CPU: {data['summary'].get('max_cpu', 0)}%
- Max Disk: {data['summary'].get('max_disk', 0)}%
- GPUs: {data['summary'].get('gpu_count', 0)}
- Active Alerts: {data['summary'].get('alert_count', 0)}
- Critical Alerts: {data['summary'].get('critical_alerts', 0)}

Issues Detected:
{json.dumps(data.get('issues', []), indent=2)}

Provide:
1. A 2-3 sentence executive summary of infrastructure health
2. Any critical issues requiring immediate attention
3. Top 3 recommendations

Be concise and actionable. Focus on what needs attention."""

        summary = await generate_llm_report(prompt)

        return {
            "timestamp": data["timestamp"],
            "report_type": "summary",
            "raw_data": data["summary"],
            "hosts": data["hosts"],
            "gpus": data["gpus"],
            "alerts": data["alerts"][:20],
            "issues": data["issues"],
            "llm_analysis": summary if summary else "LLM analysis unavailable - check Ollama connection",
        }

    @app.get("/api/v1/report/dashboard", response_class=HTMLResponse)
    async def get_dashboard(
        network: Optional[str] = Query(None, description="Filter by network"),
        severity: Optional[str] = Query(None, description="Filter alerts by severity"),
        role: Optional[str] = Query(None, description="Filter hosts by role (gpu, compute, central)"),
        refresh: int = Query(30, description="Auto-refresh interval in seconds")
    ):
        """Get an advanced HTML dashboard with filters, statistics, and AI analysis."""
        data = await collect_infrastructure_data()

        # Store original counts before filtering
        total_hosts = len(data["hosts"])
        total_gpus = len(data["gpus"])

        # Apply filters
        filtered_hosts = data["hosts"]
        filtered_gpus = data["gpus"]
        filtered_alerts = data["alerts"]

        if network:
            filtered_hosts = [h for h in data["hosts"] if h.get("network") == network]
            filtered_gpus = [g for g in data["gpus"] if g.get("network") == network]
            filtered_alerts = [a for a in data["alerts"] if any(h["name"] == a.get("host") for h in filtered_hosts)]

        if role:
            filtered_hosts = [h for h in filtered_hosts if h.get("role") == role]

        if severity:
            filtered_alerts = [a for a in filtered_alerts if a.get("severity") == severity.lower()]

        # Generate LLM summary
        issues_text = ", ".join([f"{i['host']}: {i['message']}" for i in data.get("issues", [])[:5]])
        prompt = f"""Summarize this infrastructure status in 3-4 sentences. Be direct and highlight any issues:
- {len(filtered_hosts)} hosts shown (of {total_hosts} total)
- Networks active: {data['summary'].get('network_count', 0)}
- CPU avg: {data['summary'].get('avg_cpu', 0)}%, max: {data['summary'].get('max_cpu', 0)}%
- Memory avg: {data['summary'].get('avg_memory', 0)}%
- Disk avg: {data['summary'].get('avg_disk', 0)}%, max: {data['summary'].get('max_disk', 0)}%
- {len(filtered_gpus)} GPUs monitored
- {len(filtered_alerts)} alerts ({data['summary'].get('critical_alerts', 0)} critical)
Issues: {issues_text if issues_text else 'None'}"""

        llm_summary = await generate_llm_report(prompt)
        if not llm_summary:
            llm_summary = "LLM analysis unavailable - Ollama may be processing another request"

        # Current time formatting
        now = datetime.now()
        time_display = now.strftime("%A, %B %d, %Y at %H:%M:%S")
        uptime_since = (now - timedelta(days=30)).strftime("%Y-%m-%d")

        # Build filter options
        active_networks = list(set(h.get("network") for h in data["hosts"] if h.get("network")))
        network_options = "".join([
            f'<option value="{n}" {"selected" if network == n else ""}>{NETWORK_CONFIG.get(n, {}).get("name", n)}</option>'
            for n in sorted(active_networks)
        ])

        role_options = "".join([
            f'<option value="{r}" {"selected" if role == r else ""}>{r.title()}</option>'
            for r in ["compute", "gpu", "central"]
        ])

        # Build network summary cards
        network_cards = ""
        for net_prefix in sorted(data["networks"].keys()):
            if net_prefix == "unknown":
                continue
            net_data = data["networks"][net_prefix]
            if not net_data["hosts"]:
                continue
            net_config = NETWORK_CONFIG.get(net_prefix, {})
            stats = net_data.get("stats", {})
            network_cards += f"""
            <div class="network-card" style="border-left: 4px solid {net_config.get('color', '#666')}">
                <div class="network-header">
                    <span class="network-name">{net_config.get('name', net_prefix)}</span>
                    <span class="network-count">{stats.get('host_count', 0)} hosts</span>
                </div>
                <div class="network-stats">
                    <span>CPU: {stats.get('avg_cpu', 0):.1f}%</span>
                    <span>Mem: {stats.get('avg_memory', 0):.1f}%</span>
                    <span>Disk: {stats.get('avg_disk', 0):.1f}%</span>
                </div>
            </div>"""

        # Build host rows with enhanced info
        host_rows = ""
        for h in sorted(filtered_hosts, key=lambda x: x.get("cpu", 0), reverse=True):
            cpu_color = "#4CAF50" if h.get("cpu", 0) < 60 else "#FF9800" if h.get("cpu", 0) < 80 else "#f44336"
            mem_color = "#4CAF50" if h.get("memory", 0) < 70 else "#FF9800" if h.get("memory", 0) < 85 else "#f44336"
            disk_color = "#4CAF50" if h.get("disk", 0) < 80 else "#FF9800" if h.get("disk", 0) < 90 else "#f44336"
            load_color = "#4CAF50" if h.get("load_1m", 0) < 4 else "#FF9800" if h.get("load_1m", 0) < 8 else "#f44336"

            role_badge = ""
            if h.get("role") == "gpu":
                role_badge = '<span class="badge gpu">GPU</span>'
            elif h.get("role") == "central":
                role_badge = '<span class="badge central">CENTRAL</span>'

            net_indicator = f'<span class="net-dot" style="background: {h.get("network_color", "#666")}"></span>'

            host_rows += f"""
            <tr data-network="{h.get('network', '')}" data-role="{h.get('role', '')}">
                <td>{net_indicator} {h['name']} {role_badge}</td>
                <td><div class="metric-bar"><div class="bar-fill" style="width: {min(h.get('cpu', 0), 100)}%; background: {cpu_color}"></div></div><span>{h.get('cpu', 0):.1f}%</span></td>
                <td><div class="metric-bar"><div class="bar-fill" style="width: {min(h.get('memory', 0), 100)}%; background: {mem_color}"></div></div><span>{h.get('memory', 0):.1f}%</span></td>
                <td><div class="metric-bar"><div class="bar-fill" style="width: {min(h.get('disk', 0), 100)}%; background: {disk_color}"></div></div><span>{h.get('disk', 0):.1f}%</span></td>
                <td style="color: {load_color}">{h.get('load_1m', 0):.2f}</td>
                <td class="small-text">{h.get('ip', 'N/A')}</td>
            </tr>"""

        # Build GPU rows with memory info
        gpu_rows = ""
        for g in filtered_gpus:
            temp_color = "#4CAF50" if g.get("temp", 0) < 60 else "#FF9800" if g.get("temp", 0) < 75 else "#f44336"
            util_color = "#4CAF50" if g.get("util", 0) < 70 else "#FF9800" if g.get("util", 0) < 90 else "#f44336"
            mem_percent = g.get("memory_percent", 0)
            mem_color = "#4CAF50" if mem_percent < 70 else "#FF9800" if mem_percent < 90 else "#f44336"
            mem_used_gb = g.get("memory_used", 0) / 1024 if g.get("memory_used") else 0
            mem_total_gb = g.get("memory_total", 0) / 1024 if g.get("memory_total") else 0

            gpu_rows += f"""
            <tr>
                <td>{g['host']}</td>
                <td class="gpu-name">{g['name']}</td>
                <td><span class="temp-badge" style="background: {temp_color}">{g.get('temp', 0):.0f}°C</span></td>
                <td><div class="metric-bar"><div class="bar-fill" style="width: {min(g.get('util', 0), 100)}%; background: {util_color}"></div></div><span>{g.get('util', 0):.0f}%</span></td>
                <td><div class="metric-bar"><div class="bar-fill" style="width: {min(mem_percent, 100)}%; background: {mem_color}"></div></div><span>{mem_used_gb:.1f}/{mem_total_gb:.1f} GB</span></td>
            </tr>"""

        if not gpu_rows:
            gpu_rows = "<tr><td colspan='5' class='no-data'>No GPUs detected in selected filter</td></tr>"

        # Build alert rows with grouping
        alert_rows = ""
        alert_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for a in filtered_alerts[:15]:
            severity = a.get("severity", "info").lower()
            alert_counts[severity] = alert_counts.get(severity, 0) + 1
            color = "#f44336" if severity == "critical" else "#FF9800" if severity == "high" else "#FFC107" if severity == "medium" else "#4CAF50"
            timestamp = a.get("timestamp", "")
            if timestamp:
                try:
                    if isinstance(timestamp, (int, float)):
                        # Unix timestamp
                        ts = datetime.fromtimestamp(timestamp)
                        timestamp = ts.strftime("%H:%M:%S")
                    elif isinstance(timestamp, str):
                        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        timestamp = ts.strftime("%H:%M:%S")
                    else:
                        timestamp = str(timestamp)[:19]
                except:
                    timestamp = str(timestamp)[:19] if timestamp else ""
            alert_rows += f"""
            <tr>
                <td><span class="severity-badge" style="background: {color}">{severity.upper()}</span></td>
                <td>{a.get('host', 'unknown')}</td>
                <td>{a.get('message', '')}</td>
                <td class="small-text">{timestamp}</td>
            </tr>"""

        if not alert_rows:
            alert_rows = "<tr><td colspan='4' class='no-data success'>No active alerts - All systems operational</td></tr>"

        # Build issues section
        issues_html = ""
        for issue in data.get("issues", [])[:5]:
            icon = "🔴" if issue["type"] == "critical" else "🟡"
            issues_html += f'<div class="issue-item {issue["type"]}">{icon} <strong>{issue["host"]}</strong>: {issue["message"]}</div>'

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Sidra Infrastructure Monitor</title>
    <meta http-equiv="refresh" content="{refresh}">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
            background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
            color: #e6edf3;
            margin: 0;
            padding: 20px;
            min-height: 100vh;
        }}
        .container {{ max-width: 1600px; margin: 0 auto; }}

        /* Header */
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 25px;
            flex-wrap: wrap;
            gap: 15px;
        }}
        .header-left h1 {{
            color: #58a6ff;
            margin: 0 0 5px 0;
            font-size: 28px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .header-left h1::before {{
            content: "◉";
            color: #3fb950;
            animation: pulse 2s infinite;
        }}
        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.5; }}
        }}
        .timestamp {{
            color: #8b949e;
            font-size: 14px;
        }}
        .server-time {{
            color: #58a6ff;
            font-weight: 500;
        }}

        /* Filters */
        .filters {{
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
            align-items: center;
            background: #21262d;
            padding: 12px 15px;
            border-radius: 8px;
        }}
        .filter-group {{
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .filter-group label {{
            color: #8b949e;
            font-size: 13px;
        }}
        .filter-group select {{
            background: #0d1117;
            border: 1px solid #30363d;
            color: #e6edf3;
            padding: 6px 12px;
            border-radius: 6px;
            cursor: pointer;
        }}
        .filter-group select:hover {{
            border-color: #58a6ff;
        }}
        .clear-filters {{
            background: transparent;
            border: 1px solid #30363d;
            color: #8b949e;
            padding: 6px 12px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
        }}
        .clear-filters:hover {{
            border-color: #f85149;
            color: #f85149;
        }}

        /* Stats Grid */
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 12px;
            margin-bottom: 20px;
        }}
        .stat-card {{
            background: #21262d;
            padding: 18px 15px;
            border-radius: 10px;
            text-align: center;
            border: 1px solid #30363d;
            transition: transform 0.2s, border-color 0.2s;
        }}
        .stat-card:hover {{
            transform: translateY(-2px);
            border-color: #58a6ff;
        }}
        .stat-value {{
            font-size: 32px;
            font-weight: 700;
            color: #58a6ff;
            line-height: 1.2;
        }}
        .stat-value.warning {{ color: #d29922; }}
        .stat-value.critical {{ color: #f85149; }}
        .stat-value.success {{ color: #3fb950; }}
        .stat-label {{
            font-size: 11px;
            color: #8b949e;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-top: 5px;
        }}
        .stat-sublabel {{
            font-size: 10px;
            color: #6e7681;
            margin-top: 3px;
        }}

        /* Network Cards */
        .networks-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 12px;
            margin-bottom: 20px;
        }}
        .network-card {{
            background: #21262d;
            padding: 15px;
            border-radius: 8px;
            border: 1px solid #30363d;
        }}
        .network-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
        }}
        .network-name {{
            font-weight: 600;
            color: #e6edf3;
        }}
        .network-count {{
            font-size: 12px;
            color: #8b949e;
            background: #0d1117;
            padding: 2px 8px;
            border-radius: 10px;
        }}
        .network-stats {{
            display: flex;
            gap: 15px;
            font-size: 13px;
            color: #8b949e;
        }}

        /* AI Summary */
        .summary-box {{
            background: linear-gradient(135deg, #1c2128 0%, #21262d 100%);
            border: 1px solid #30363d;
            border-left: 4px solid #58a6ff;
            padding: 20px;
            margin-bottom: 20px;
            border-radius: 8px;
        }}
        .summary-box h2 {{
            margin: 0 0 12px 0;
            color: #58a6ff;
            font-size: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .summary-box h2::before {{
            content: "🤖";
        }}
        .llm-summary {{
            background: #0d1117;
            padding: 15px;
            border-radius: 6px;
            line-height: 1.7;
            font-size: 14px;
            color: #c9d1d9;
        }}

        /* Issues */
        .issues-box {{
            background: #21262d;
            border: 1px solid #30363d;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
        }}
        .issues-box h3 {{
            margin: 0 0 10px 0;
            color: #f85149;
            font-size: 14px;
        }}
        .issue-item {{
            padding: 8px 12px;
            margin: 5px 0;
            border-radius: 6px;
            font-size: 13px;
        }}
        .issue-item.critical {{
            background: rgba(248, 81, 73, 0.1);
            border-left: 3px solid #f85149;
        }}
        .issue-item.warning {{
            background: rgba(210, 153, 34, 0.1);
            border-left: 3px solid #d29922;
        }}

        /* Tables */
        .grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
        }}
        @media (max-width: 1200px) {{
            .grid {{ grid-template-columns: 1fr; }}
        }}
        .section {{
            background: #21262d;
            border: 1px solid #30363d;
            border-radius: 10px;
            overflow: hidden;
        }}
        .section-header {{
            background: #161b22;
            padding: 12px 15px;
            border-bottom: 1px solid #30363d;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .section-header h3 {{
            margin: 0;
            color: #e6edf3;
            font-size: 15px;
        }}
        .section-count {{
            background: #0d1117;
            padding: 2px 10px;
            border-radius: 10px;
            font-size: 12px;
            color: #8b949e;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th, td {{
            padding: 10px 12px;
            text-align: left;
            border-bottom: 1px solid #21262d;
            font-size: 13px;
        }}
        th {{
            background: #161b22;
            color: #8b949e;
            font-weight: 500;
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: 0.5px;
        }}
        tr:hover {{
            background: #161b22;
        }}

        /* Metric bars */
        .metric-bar {{
            display: inline-block;
            width: 60px;
            height: 6px;
            background: #0d1117;
            border-radius: 3px;
            overflow: hidden;
            margin-right: 8px;
            vertical-align: middle;
        }}
        .bar-fill {{
            height: 100%;
            border-radius: 3px;
            transition: width 0.3s;
        }}

        /* Badges */
        .badge {{
            display: inline-block;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 10px;
            font-weight: 600;
            margin-left: 5px;
        }}
        .badge.gpu {{
            background: #238636;
            color: #fff;
        }}
        .badge.central {{
            background: #1f6feb;
            color: #fff;
        }}
        .net-dot {{
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-right: 6px;
        }}
        .temp-badge {{
            padding: 3px 8px;
            border-radius: 4px;
            font-weight: 600;
            color: #fff;
        }}
        .severity-badge {{
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            color: #fff;
        }}
        .small-text {{
            font-size: 11px;
            color: #8b949e;
        }}
        .gpu-name {{
            font-size: 12px;
            max-width: 180px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        .no-data {{
            text-align: center;
            color: #8b949e;
            padding: 20px !important;
        }}
        .no-data.success {{
            color: #3fb950;
        }}

        /* Full width alerts */
        .full-width {{
            grid-column: 1 / -1;
        }}

        /* Footer */
        .footer {{
            text-align: center;
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #21262d;
            color: #6e7681;
            font-size: 12px;
        }}
        .footer a {{
            color: #58a6ff;
            text-decoration: none;
        }}

        /* Responsive */
        @media (max-width: 768px) {{
            .header {{ flex-direction: column; }}
            .filters {{ flex-direction: column; align-items: stretch; }}
            .stats {{ grid-template-columns: repeat(2, 1fr); }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="header-left">
                <h1>Sidra Infrastructure Monitor</h1>
                <div class="timestamp">
                    <span class="server-time">{time_display}</span><br>
                    Last refresh: {data['timestamp'][:19]} • Auto-refresh: {refresh}s
                </div>
            </div>
            <div class="filters">
                <form method="get" id="filterForm">
                    <div class="filter-group">
                        <label>Network:</label>
                        <select name="network" onchange="this.form.submit()">
                            <option value="">All Networks</option>
                            {network_options}
                        </select>
                    </div>
                    <div class="filter-group">
                        <label>Role:</label>
                        <select name="role" onchange="this.form.submit()">
                            <option value="">All Roles</option>
                            {role_options}
                        </select>
                    </div>
                    <div class="filter-group">
                        <label>Severity:</label>
                        <select name="severity" onchange="this.form.submit()">
                            <option value="">All Alerts</option>
                            <option value="critical" {"selected" if severity == "critical" else ""}>Critical</option>
                            <option value="high" {"selected" if severity == "high" else ""}>High</option>
                            <option value="medium" {"selected" if severity == "medium" else ""}>Medium</option>
                        </select>
                    </div>
                    <input type="hidden" name="refresh" value="{refresh}">
                </form>
                <a href="?" class="clear-filters">Clear Filters</a>
            </div>
        </div>

        <div class="stats">
            <div class="stat-card">
                <div class="stat-value">{len(filtered_hosts)}</div>
                <div class="stat-label">Hosts Online</div>
                <div class="stat-sublabel">of {total_hosts} total</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{data['summary'].get('network_count', 0)}</div>
                <div class="stat-label">Networks</div>
                <div class="stat-sublabel">active</div>
            </div>
            <div class="stat-card">
                <div class="stat-value {"warning" if data['summary'].get('avg_cpu', 0) > 70 else ""}">{data['summary'].get('avg_cpu', 0):.1f}%</div>
                <div class="stat-label">Avg CPU</div>
                <div class="stat-sublabel">max: {data['summary'].get('max_cpu', 0):.1f}%</div>
            </div>
            <div class="stat-card">
                <div class="stat-value {"warning" if data['summary'].get('avg_memory', 0) > 80 else ""}">{data['summary'].get('avg_memory', 0):.1f}%</div>
                <div class="stat-label">Avg Memory</div>
                <div class="stat-sublabel">max: {data['summary'].get('max_memory', 0):.1f}%</div>
            </div>
            <div class="stat-card">
                <div class="stat-value {"critical" if data['summary'].get('max_disk', 0) > 90 else "warning" if data['summary'].get('max_disk', 0) > 80 else ""}">{data['summary'].get('avg_disk', 0):.1f}%</div>
                <div class="stat-label">Avg Disk</div>
                <div class="stat-sublabel">max: {data['summary'].get('max_disk', 0):.1f}%</div>
            </div>
            <div class="stat-card">
                <div class="stat-value success">{len(filtered_gpus)}</div>
                <div class="stat-label">GPUs</div>
                <div class="stat-sublabel">monitored</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{data['summary'].get('total_load', 0):.1f}</div>
                <div class="stat-label">Total Load</div>
                <div class="stat-sublabel">1m average</div>
            </div>
            <div class="stat-card">
                <div class="stat-value {"critical" if data['summary'].get('critical_alerts', 0) > 0 else "success"}">{len(filtered_alerts)}</div>
                <div class="stat-label">Alerts</div>
                <div class="stat-sublabel">{data['summary'].get('critical_alerts', 0)} critical</div>
            </div>
        </div>

        <div class="networks-grid">
            {network_cards}
        </div>

        <div class="summary-box">
            <h2>AI Analysis (Powered by {OLLAMA_MODEL})</h2>
            <div class="llm-summary">{llm_summary}</div>
        </div>

        {"<div class='issues-box'><h3>⚠️ Active Issues</h3>" + issues_html + "</div>" if issues_html else ""}

        <div class="grid">
            <div class="section">
                <div class="section-header">
                    <h3>Host Status</h3>
                    <span class="section-count">{len(filtered_hosts)} hosts</span>
                </div>
                <table>
                    <tr><th>Host</th><th>CPU</th><th>Memory</th><th>Disk</th><th>Load</th><th>IP</th></tr>
                    {host_rows}
                </table>
            </div>

            <div class="section">
                <div class="section-header">
                    <h3>GPU Status</h3>
                    <span class="section-count">{len(filtered_gpus)} GPUs</span>
                </div>
                <table>
                    <tr><th>Host</th><th>GPU Model</th><th>Temp</th><th>Utilization</th><th>Memory</th></tr>
                    {gpu_rows}
                </table>
            </div>
        </div>

        <div class="section full-width" style="margin-top: 20px;">
            <div class="section-header">
                <h3>Recent Alerts</h3>
                <span class="section-count">{len(filtered_alerts)} alerts</span>
            </div>
            <table>
                <tr><th style="width:100px">Severity</th><th style="width:120px">Host</th><th>Message</th><th style="width:80px">Time</th></tr>
                {alert_rows}
            </table>
        </div>

        <div class="footer">
            Sidra Infrastructure Monitor v2.0 • Powered by VictoriaMetrics, Ollama ({OLLAMA_MODEL}), and FastAPI<br>
            <a href="/api/v1/report/summary">JSON API</a> • <a href="/api/v1/networks">Network Config</a> • <a href="/health">Health Check</a>
        </div>
    </div>
</body>
</html>
"""
        return HTMLResponse(content=html)

    @app.get("/api/v1/report/quick")
    async def get_quick_report():
        """Get a quick text summary for CLI or notifications."""
        data = await collect_infrastructure_data()

        prompt = f"""Give a one-paragraph infrastructure status update (max 100 words):
- {data['summary'].get('host_count', 0)} hosts across {data['summary'].get('network_count', 0)} networks
- CPU avg {data['summary'].get('avg_cpu', 0)}%, max disk {data['summary'].get('max_disk', 0)}%
- {data['summary'].get('gpu_count', 0)} GPUs online
- {data['summary'].get('alert_count', 0)} alerts ({data['summary'].get('critical_alerts', 0)} critical)
Focus on issues and health status."""

        summary = await generate_llm_report(prompt)

        return {
            "timestamp": data["timestamp"],
            "summary": summary if summary else f"{data['summary'].get('host_count', 0)} hosts online, {data['summary'].get('alert_count', 0)} alerts active",
            "stats": data["summary"],
            "issues": data.get("issues", [])
        }

    @app.get("/api/v1/report/network/{network}")
    async def get_network_report(network: str):
        """Get detailed report for a specific network."""
        data = await collect_infrastructure_data()

        network_hosts = [h for h in data["hosts"] if h.get("network") == network]
        network_gpus = [g for g in data["gpus"] if g.get("network") == network]

        if not network_hosts:
            raise HTTPException(status_code=404, detail=f"Network {network} not found or has no active hosts")

        network_config = NETWORK_CONFIG.get(network, {})

        return {
            "network": network,
            "name": network_config.get("name", network),
            "description": network_config.get("description", ""),
            "timestamp": data["timestamp"],
            "host_count": len(network_hosts),
            "gpu_count": len(network_gpus),
            "hosts": network_hosts,
            "gpus": network_gpus,
            "stats": {
                "avg_cpu": round(sum(h.get("cpu", 0) for h in network_hosts) / len(network_hosts), 1) if network_hosts else 0,
                "avg_memory": round(sum(h.get("memory", 0) for h in network_hosts) / len(network_hosts), 1) if network_hosts else 0,
                "avg_disk": round(sum(h.get("disk", 0) for h in network_hosts) / len(network_hosts), 1) if network_hosts else 0,
            }
        }

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8201)
