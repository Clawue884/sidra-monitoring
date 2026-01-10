"""AI agent for infrastructure analysis using Ollama."""

import json
import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime

import httpx

from ..config import settings
from ..utils import get_logger
from ..discovery import (
    NetworkScanner,
    ServerDiscovery,
    DockerDiscovery,
    DatabaseDiscovery,
    StorageDiscovery,
    ServiceDiscovery,
)

logger = get_logger(__name__)


@dataclass
class InfrastructureAnalysis:
    """Result of infrastructure analysis."""
    summary: str = ""
    architecture_diagram: str = ""
    servers: list[dict] = field(default_factory=list)
    networks: list[dict] = field(default_factory=list)
    databases: list[dict] = field(default_factory=list)
    storage: dict = field(default_factory=dict)
    docker: dict = field(default_factory=dict)
    services: list[dict] = field(default_factory=list)
    security_findings: list[dict] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    health_score: int = 0
    generated_at: datetime = field(default_factory=datetime.now)


class InfrastructureAgent:
    """AI agent that discovers and analyzes infrastructure."""

    SYSTEM_PROMPT = """You are an expert DevOps and infrastructure engineer AI assistant.
Your role is to:
1. Analyze infrastructure data and provide insights
2. Identify security vulnerabilities and misconfigurations
3. Suggest optimizations and best practices
4. Create clear documentation and diagrams
5. Monitor and alert on infrastructure health

When analyzing infrastructure:
- Focus on security, reliability, and performance
- Identify single points of failure
- Check for proper redundancy and backup strategies
- Verify network segmentation and access controls
- Assess resource utilization and capacity planning

Always provide actionable recommendations."""

    def __init__(self):
        self.ollama_host = settings.ollama_host
        self.model = settings.ollama_model
        self.network_scanner = NetworkScanner()

    async def _call_ollama(self, prompt: str, system: str = None) -> str:
        """Call Ollama API for analysis."""
        system = system or self.SYSTEM_PROMPT

        async with httpx.AsyncClient(timeout=120) as client:
            try:
                response = await client.post(
                    f"{self.ollama_host}/api/chat",
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        "stream": False,
                        "options": {"temperature": 0.3},
                    },
                )
                response.raise_for_status()
                return response.json().get("message", {}).get("content", "")
            except Exception as e:
                logger.error(f"Ollama call failed: {e}")
                return ""

    async def full_discovery(self) -> InfrastructureAnalysis:
        """Perform full infrastructure discovery and analysis."""
        logger.info("Starting full infrastructure discovery...")
        analysis = InfrastructureAnalysis()

        # Discover networks
        logger.info("Scanning networks...")
        networks_data = []
        for cidr in settings.networks_list:
            try:
                network_info = await self.network_scanner.scan_network(cidr)
                networks_data.append(self.network_scanner.to_dict(network_info))
                analysis.networks.append(self.network_scanner.to_dict(network_info))
            except Exception as e:
                logger.error(f"Network scan failed for {cidr}: {e}")

        # Get all accessible hosts
        accessible_hosts = []
        for network in analysis.networks:
            for host in network.get("hosts", []):
                if host.get("ssh_accessible"):
                    accessible_hosts.append(host["ip"])

        logger.info(f"Found {len(accessible_hosts)} accessible hosts")

        # Discover each host
        for host_ip in accessible_hosts[:20]:  # Limit to 20 hosts
            try:
                await self._discover_host(host_ip, analysis)
            except Exception as e:
                logger.error(f"Host discovery failed for {host_ip}: {e}")

        # Analyze with AI
        await self._analyze_infrastructure(analysis)

        return analysis

    async def _discover_host(self, host_ip: str, analysis: InfrastructureAnalysis):
        """Discover a single host."""
        logger.info(f"Discovering host: {host_ip}")

        from ..utils import SSHClient, SSHCredentials

        # Try to connect
        creds = SSHCredentials(
            host=host_ip,
            username=settings.ssh_user,
            password=settings.ssh_password,
        )
        ssh = SSHClient(creds)

        if not await ssh.connect():
            # Try alternative credentials
            creds.username = settings.ssh_alt_user
            creds.password = settings.ssh_alt_password
            ssh = SSHClient(creds)
            if not await ssh.connect():
                logger.warning(f"Could not connect to {host_ip}")
                return

        try:
            # Server discovery
            server_discovery = ServerDiscovery(ssh)
            server_info = await server_discovery.discover()
            analysis.servers.append(server_discovery.to_dict(server_info))

            # Docker discovery
            docker_discovery = DockerDiscovery(ssh)
            docker_info = await docker_discovery.discover()
            if docker_info.version:
                analysis.docker[host_ip] = docker_discovery.to_dict(docker_info)

            # Database discovery
            db_discovery = DatabaseDiscovery(ssh)
            db_report = await db_discovery.discover()
            db_data = db_discovery.to_dict(db_report)
            if db_data.get("databases"):
                analysis.databases.append(db_data)

            # Storage discovery
            storage_discovery = StorageDiscovery(ssh)
            storage_report = await storage_discovery.discover()
            analysis.storage[host_ip] = storage_discovery.to_dict(storage_report)

            # Services discovery
            service_discovery = ServiceDiscovery(ssh)
            service_report = await service_discovery.discover()
            analysis.services.append(service_discovery.to_dict(service_report))

        finally:
            await ssh.disconnect()

    async def _analyze_infrastructure(self, analysis: InfrastructureAnalysis):
        """Use AI to analyze the infrastructure."""
        logger.info("Analyzing infrastructure with AI...")

        # Prepare data summary for AI
        data_summary = {
            "total_servers": len(analysis.servers),
            "total_networks": len(analysis.networks),
            "servers": [
                {
                    "hostname": s.get("hostname"),
                    "ip": s.get("ip_address"),
                    "os": s.get("os"),
                    "cpu_cores": s.get("cpu", {}).get("cores"),
                    "memory_gb": s.get("memory", {}).get("total_gb"),
                    "disk_usage": [d.get("usage_percent") for d in s.get("disks", [])],
                }
                for s in analysis.servers
            ],
            "docker_hosts": list(analysis.docker.keys()),
            "databases": analysis.databases,
            "has_glusterfs": any("glusterfs" in str(s) for s in analysis.storage.values()),
        }

        # Generate summary
        summary_prompt = f"""Analyze this infrastructure data and provide a concise summary:

{json.dumps(data_summary, indent=2)}

Include:
1. Overview of the infrastructure
2. Key services and applications
3. Storage architecture
4. Network topology

Keep it under 500 words."""

        analysis.summary = await self._call_ollama(summary_prompt)

        # Generate architecture diagram (ASCII)
        diagram_prompt = f"""Create a simple ASCII architecture diagram for this infrastructure:

Servers: {json.dumps([s.get('hostname') for s in analysis.servers])}
Docker hosts: {json.dumps(list(analysis.docker.keys()))}
Databases: {json.dumps([d.get('host') for d in analysis.databases])}

Create a simple ASCII diagram showing the relationships."""

        analysis.architecture_diagram = await self._call_ollama(diagram_prompt)

        # Security analysis
        security_prompt = f"""Analyze this infrastructure for security issues:

{json.dumps(data_summary, indent=2)}

Look for:
1. Default credentials in use
2. Exposed services
3. Missing security configurations
4. Network segmentation issues
5. Backup and disaster recovery gaps

Return findings as a JSON array of objects with 'severity', 'finding', and 'recommendation' fields.
Return ONLY valid JSON."""

        security_response = await self._call_ollama(security_prompt)
        try:
            # Try to parse JSON from response
            if "```" in security_response:
                security_response = security_response.split("```")[1]
                if security_response.startswith("json"):
                    security_response = security_response[4:]
            analysis.security_findings = json.loads(security_response)
        except:
            analysis.security_findings = [{"finding": security_response, "severity": "info"}]

        # Recommendations
        rec_prompt = f"""Based on this infrastructure analysis, provide 5-10 actionable recommendations:

{json.dumps(data_summary, indent=2)}

Focus on:
1. Performance optimization
2. Cost reduction
3. Reliability improvements
4. Security hardening
5. Operational efficiency

Return as a simple list of recommendations."""

        rec_response = await self._call_ollama(rec_prompt)
        analysis.recommendations = [
            r.strip().lstrip("0123456789.-) ")
            for r in rec_response.split("\n")
            if r.strip() and len(r.strip()) > 10
        ]

        # Calculate health score
        analysis.health_score = await self._calculate_health_score(analysis)

    async def _calculate_health_score(self, analysis: InfrastructureAnalysis) -> int:
        """Calculate overall infrastructure health score (0-100)."""
        score = 100

        # Deduct for high CPU usage
        for server in analysis.servers:
            cpu_usage = server.get("cpu", {}).get("usage_percent", 0)
            if cpu_usage > 80:
                score -= 5
            elif cpu_usage > 60:
                score -= 2

        # Deduct for high memory usage
        for server in analysis.servers:
            mem_usage = server.get("memory", {}).get("usage_percent", 0)
            if mem_usage > 90:
                score -= 10
            elif mem_usage > 80:
                score -= 5

        # Deduct for high disk usage
        for server in analysis.servers:
            for disk in server.get("disks", []):
                if disk.get("usage_percent", 0) > 90:
                    score -= 10
                elif disk.get("usage_percent", 0) > 80:
                    score -= 5

        # Deduct for security findings
        for finding in analysis.security_findings:
            severity = finding.get("severity", "").lower()
            if severity == "critical":
                score -= 15
            elif severity == "high":
                score -= 10
            elif severity == "medium":
                score -= 5

        return max(0, min(100, score))

    async def quick_scan(self, host: str) -> dict:
        """Quick scan of a single host."""
        logger.info(f"Quick scan of {host}")

        from ..utils import SSHClient, SSHCredentials

        creds = SSHCredentials(
            host=host,
            username=settings.ssh_user,
            password=settings.ssh_password,
        )
        ssh = SSHClient(creds)

        if not await ssh.connect():
            return {"error": f"Could not connect to {host}"}

        try:
            server_discovery = ServerDiscovery(ssh)
            server_info = await server_discovery.discover()
            return server_discovery.to_dict(server_info)
        finally:
            await ssh.disconnect()

    def to_dict(self, analysis: InfrastructureAnalysis) -> dict:
        """Convert analysis to dictionary."""
        return {
            "summary": analysis.summary,
            "architecture_diagram": analysis.architecture_diagram,
            "servers": analysis.servers,
            "networks": analysis.networks,
            "databases": analysis.databases,
            "storage": analysis.storage,
            "docker": analysis.docker,
            "services": analysis.services,
            "security_findings": analysis.security_findings,
            "recommendations": analysis.recommendations,
            "health_score": analysis.health_score,
            "generated_at": analysis.generated_at.isoformat(),
        }
