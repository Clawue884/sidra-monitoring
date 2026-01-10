"""Documentation agent for generating infrastructure documentation."""

import json
from pathlib import Path
from datetime import datetime
from typing import Any

import httpx
from jinja2 import Environment, FileSystemLoader, BaseLoader

from ..config import settings
from ..utils import get_logger

logger = get_logger(__name__)


class DocumentationAgent:
    """AI agent for generating infrastructure documentation."""

    SYSTEM_PROMPT = """You are a technical documentation expert specializing in infrastructure and DevOps.
Your role is to create clear, comprehensive documentation including:
1. Infrastructure overviews and architecture diagrams
2. Network topology documentation
3. Server and service inventories
4. Runbooks and operational procedures
5. Security documentation

Use clear, professional language. Include diagrams in ASCII or Mermaid format when helpful.
Structure documentation with proper headings and sections."""

    def __init__(self):
        self.ollama_host = settings.ollama_host
        self.model = settings.ollama_model
        self.output_dir = settings.reports_dir

    async def _call_ollama(self, prompt: str, system: str = None) -> str:
        """Call Ollama API."""
        system = system or self.SYSTEM_PROMPT

        async with httpx.AsyncClient(timeout=180) as client:
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

    async def generate_full_documentation(
        self, infrastructure_data: dict, output_path: Path = None
    ) -> str:
        """Generate comprehensive infrastructure documentation."""
        logger.info("Generating full infrastructure documentation...")

        sections = []

        # Title and overview
        sections.append(await self._generate_overview(infrastructure_data))

        # Network documentation
        sections.append(await self._generate_network_docs(infrastructure_data))

        # Server inventory
        sections.append(await self._generate_server_inventory(infrastructure_data))

        # Docker/Container documentation
        sections.append(await self._generate_docker_docs(infrastructure_data))

        # Database documentation
        sections.append(await self._generate_database_docs(infrastructure_data))

        # Storage documentation
        sections.append(await self._generate_storage_docs(infrastructure_data))

        # Security documentation
        sections.append(await self._generate_security_docs(infrastructure_data))

        # Operational runbooks
        sections.append(await self._generate_runbooks(infrastructure_data))

        # Combine all sections
        full_doc = "\n\n---\n\n".join(sections)

        # Save to file
        if output_path:
            output_path.write_text(full_doc)
            logger.info(f"Documentation saved to {output_path}")
        else:
            default_path = self.output_dir / f"infrastructure_docs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            self.output_dir.mkdir(parents=True, exist_ok=True)
            default_path.write_text(full_doc)
            logger.info(f"Documentation saved to {default_path}")

        return full_doc

    async def _generate_overview(self, data: dict) -> str:
        """Generate infrastructure overview section."""
        prompt = f"""Generate an executive overview section for this infrastructure documentation.

Infrastructure Data:
- Total Servers: {len(data.get('servers', []))}
- Networks: {len(data.get('networks', []))}
- Docker Hosts: {len(data.get('docker', {}))}
- Databases: {len(data.get('databases', []))}
- Health Score: {data.get('health_score', 'N/A')}

Summary from analysis:
{data.get('summary', 'No summary available')}

Create a professional overview section with:
1. Title: "# Sidra Production Infrastructure Documentation"
2. Executive summary
3. Quick facts table
4. Architecture diagram (ASCII or description)
5. Table of contents

Use markdown formatting."""

        return await self._call_ollama(prompt)

    async def _generate_network_docs(self, data: dict) -> str:
        """Generate network documentation."""
        networks = data.get("networks", [])

        prompt = f"""Generate network documentation for these networks:

{json.dumps(networks, indent=2)}

Include:
## Network Architecture

1. Network segments and their purposes
2. IP addressing scheme table
3. Key hosts in each network
4. Network diagram (ASCII)
5. Firewall rules summary (if available)
6. VPN configuration

Use markdown formatting with tables."""

        return await self._call_ollama(prompt)

    async def _generate_server_inventory(self, data: dict) -> str:
        """Generate server inventory documentation."""
        servers = data.get("servers", [])

        prompt = f"""Generate a server inventory document for these servers:

{json.dumps(servers[:20], indent=2)}

Include:
## Server Inventory

1. Server summary table with:
   - Hostname
   - IP Address
   - OS
   - CPU/Memory/Disk
   - Role/Purpose

2. Detailed specifications for each server
3. Resource utilization summary
4. Server roles and responsibilities

Use markdown tables for clarity."""

        return await self._call_ollama(prompt)

    async def _generate_docker_docs(self, data: dict) -> str:
        """Generate Docker/container documentation."""
        docker_data = data.get("docker", {})

        if not docker_data:
            return "## Container Infrastructure\n\nNo Docker/container infrastructure detected."

        prompt = f"""Generate Docker infrastructure documentation:

{json.dumps(docker_data, indent=2)}

Include:
## Container Infrastructure

1. Docker Swarm overview (if applicable)
   - Manager nodes
   - Worker nodes
   - Services deployed

2. Container inventory table
3. Stack/Service relationships
4. Network configuration
5. Volume mappings
6. Scaling configuration

Use markdown formatting."""

        return await self._call_ollama(prompt)

    async def _generate_database_docs(self, data: dict) -> str:
        """Generate database documentation."""
        databases = data.get("databases", [])

        if not databases:
            return "## Database Infrastructure\n\nNo databases detected."

        prompt = f"""Generate database infrastructure documentation:

{json.dumps(databases, indent=2)}

Include:
## Database Infrastructure

1. Database summary table
2. Connection details (without passwords)
3. Replication configuration
4. Backup strategy (recommended)
5. Database schemas/tables (if available)
6. Access patterns

Use markdown formatting."""

        return await self._call_ollama(prompt)

    async def _generate_storage_docs(self, data: dict) -> str:
        """Generate storage documentation."""
        storage = data.get("storage", {})

        prompt = f"""Generate storage infrastructure documentation:

{json.dumps(storage, indent=2)}

Include:
## Storage Infrastructure

1. Storage summary
   - Total capacity
   - Usage statistics
   - Storage types (local, GlusterFS, NFS)

2. GlusterFS configuration (if present)
   - Volumes
   - Bricks
   - Replication

3. Mount points and mappings
4. Backup locations
5. Storage growth projections

Use markdown formatting."""

        return await self._call_ollama(prompt)

    async def _generate_security_docs(self, data: dict) -> str:
        """Generate security documentation."""
        security = data.get("security_findings", [])
        recommendations = data.get("recommendations", [])

        prompt = f"""Generate security documentation:

Security Findings:
{json.dumps(security, indent=2)}

Recommendations:
{json.dumps(recommendations, indent=2)}

Include:
## Security Assessment

1. Security overview and health score
2. Findings table with severity
3. Detailed findings with remediation steps
4. Security recommendations
5. Compliance considerations
6. Next steps

Use markdown formatting with severity indicators."""

        return await self._call_ollama(prompt)

    async def _generate_runbooks(self, data: dict) -> str:
        """Generate operational runbooks."""
        servers = data.get("servers", [])
        docker = data.get("docker", {})

        prompt = f"""Generate operational runbooks for this infrastructure:

Servers: {len(servers)}
Docker hosts: {len(docker)}
Services: Various web apps, APIs, databases

Include:
## Operational Runbooks

### Common Operations
1. Server health check procedure
2. Service restart procedures
3. Log checking commands
4. Resource monitoring

### Incident Response
1. High CPU usage response
2. High memory usage response
3. Disk space issues
4. Service outage response

### Maintenance
1. System updates procedure
2. Docker image updates
3. Database maintenance
4. Certificate renewal

### Backup & Recovery
1. Backup verification
2. Restore procedures
3. Disaster recovery steps

Use code blocks for commands."""

        return await self._call_ollama(prompt)

    async def generate_quick_report(self, host_data: dict) -> str:
        """Generate a quick report for a single host."""
        prompt = f"""Generate a quick server report:

{json.dumps(host_data, indent=2)}

Include:
1. Server summary (1 paragraph)
2. Key specs table
3. Health status
4. Active services
5. Any concerns

Keep it concise (under 300 words). Use markdown."""

        return await self._call_ollama(prompt)

    async def generate_daily_report(self, infrastructure_data: dict) -> str:
        """Generate a daily infrastructure report."""
        prompt = f"""Generate a daily infrastructure status report:

Date: {datetime.now().strftime('%Y-%m-%d')}

Infrastructure Summary:
- Servers: {len(infrastructure_data.get('servers', []))}
- Health Score: {infrastructure_data.get('health_score', 'N/A')}

Server Status:
{json.dumps([{'hostname': s.get('hostname'), 'cpu': s.get('cpu', {}).get('usage_percent'), 'memory': s.get('memory', {}).get('usage_percent')} for s in infrastructure_data.get('servers', [])], indent=2)}

Security Findings: {len(infrastructure_data.get('security_findings', []))}

Create a concise daily report with:
1. Overall status (Green/Yellow/Red)
2. Key metrics summary
3. Alerts and warnings
4. Action items

Use markdown formatting. Keep it under 500 words."""

        return await self._call_ollama(prompt)
