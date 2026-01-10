"""Service discovery module for applications and services."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

from ..utils import get_logger, SSHClient

logger = get_logger(__name__)


@dataclass
class ApplicationInfo:
    """Application/service information."""
    name: str = ""
    type: str = ""  # web, api, worker, database, cache, proxy
    technology: str = ""  # python, node, java, go, etc.
    port: int = 0
    status: str = ""
    pid: int = 0
    memory_mb: float = 0.0
    cpu_percent: float = 0.0
    uptime: str = ""
    config_files: list[str] = field(default_factory=list)
    environment: dict = field(default_factory=dict)
    health_check_url: str = ""
    health_status: str = ""


@dataclass
class WebServerInfo:
    """Web server information."""
    type: str = ""  # nginx, apache, caddy
    version: str = ""
    running: bool = False
    sites: list[dict] = field(default_factory=list)
    ssl_certificates: list[dict] = field(default_factory=list)


@dataclass
class ServicesReport:
    """Complete services report."""
    host: str = ""
    systemd_services: list[dict] = field(default_factory=list)
    applications: list[ApplicationInfo] = field(default_factory=list)
    web_servers: list[WebServerInfo] = field(default_factory=list)
    cron_jobs: list[str] = field(default_factory=list)
    supervisor_programs: list[dict] = field(default_factory=list)
    discovered_at: datetime = field(default_factory=datetime.now)


class ServiceDiscovery:
    """Discover running services and applications."""

    def __init__(self, ssh_client: SSHClient):
        self.ssh = ssh_client

    async def discover(self) -> ServicesReport:
        """Perform full service discovery."""
        logger.info(f"Starting service discovery on {self.ssh.creds.host}")

        report = ServicesReport(host=self.ssh.creds.host)

        await asyncio.gather(
            self._discover_systemd_services(report),
            self._discover_web_servers(report),
            self._discover_applications(report),
            self._discover_cron_jobs(report),
            self._discover_supervisor(report),
            return_exceptions=True,
        )

        return report

    async def _discover_systemd_services(self, report: ServicesReport):
        """Discover systemd services."""
        result = await self.ssh.execute(
            "systemctl list-units --type=service --state=running --no-pager --no-legend 2>/dev/null"
        )
        if result.success:
            for line in result.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 4:
                    service_name = parts[0]
                    report.systemd_services.append({
                        "name": service_name,
                        "load_state": parts[1] if len(parts) > 1 else "",
                        "active_state": parts[2] if len(parts) > 2 else "",
                        "sub_state": parts[3] if len(parts) > 3 else "",
                        "description": " ".join(parts[4:]) if len(parts) > 4 else "",
                    })

        logger.info(f"Found {len(report.systemd_services)} running systemd services")

    async def _discover_web_servers(self, report: ServicesReport):
        """Discover web servers (nginx, apache)."""
        # Check Nginx
        result = await self.ssh.execute("nginx -v 2>&1")
        if result.success or "nginx" in result.stderr.lower():
            nginx = WebServerInfo(type="nginx")
            version_str = result.stderr if "nginx" in result.stderr else result.stdout
            if "nginx/" in version_str:
                nginx.version = version_str.split("nginx/")[1].split()[0]

            # Check if running
            running_check = await self.ssh.execute("pgrep -x nginx >/dev/null && echo 'running'")
            nginx.running = "running" in running_check.stdout

            # Get sites
            sites_result = await self.ssh.execute(
                "ls /etc/nginx/sites-enabled/ 2>/dev/null || ls /etc/nginx/conf.d/*.conf 2>/dev/null"
            )
            if sites_result.success:
                for site in sites_result.stdout.strip().split("\n"):
                    if site.strip():
                        nginx.sites.append({"name": site.strip()})

            # Check SSL certificates
            ssl_result = await self.ssh.execute(
                "find /etc/letsencrypt/live -name 'cert.pem' 2>/dev/null | head -10"
            )
            if ssl_result.success:
                for cert_path in ssl_result.stdout.strip().split("\n"):
                    if cert_path.strip():
                        domain = cert_path.split("/")[-2]
                        # Get expiry
                        expiry_result = await self.ssh.execute(
                            f"openssl x509 -enddate -noout -in {cert_path} 2>/dev/null"
                        )
                        expiry = ""
                        if expiry_result.success:
                            expiry = expiry_result.stdout.replace("notAfter=", "").strip()
                        nginx.ssl_certificates.append({
                            "domain": domain,
                            "expiry": expiry,
                        })

            report.web_servers.append(nginx)
            logger.info(f"Found Nginx: {len(nginx.sites)} sites, {len(nginx.ssl_certificates)} SSL certs")

        # Check Apache
        result = await self.ssh.execute("apache2 -v 2>/dev/null || httpd -v 2>/dev/null")
        if result.success:
            apache = WebServerInfo(type="apache")
            for line in result.stdout.split("\n"):
                if "Apache/" in line:
                    apache.version = line.split("Apache/")[1].split()[0]
                    break

            running_check = await self.ssh.execute("pgrep -x apache2 >/dev/null || pgrep -x httpd >/dev/null && echo 'running'")
            apache.running = "running" in running_check.stdout

            report.web_servers.append(apache)

    async def _discover_applications(self, report: ServicesReport):
        """Discover running applications."""
        # Python applications
        result = await self.ssh.execute(
            "ps aux | grep -E 'python|gunicorn|uvicorn|celery' | grep -v grep"
        )
        if result.success:
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) >= 11:
                    cmd = " ".join(parts[10:])
                    app_type = "python"
                    if "gunicorn" in cmd:
                        app_type = "gunicorn"
                    elif "uvicorn" in cmd:
                        app_type = "uvicorn"
                    elif "celery" in cmd:
                        app_type = "celery"

                    app = ApplicationInfo(
                        name=parts[10].split("/")[-1],
                        type="worker" if "celery" in cmd else "web",
                        technology=app_type,
                        pid=int(parts[1]),
                        cpu_percent=float(parts[2]),
                        memory_mb=float(parts[3]),
                    )
                    report.applications.append(app)

        # Node.js applications
        result = await self.ssh.execute(
            "ps aux | grep -E 'node|npm|yarn' | grep -v grep"
        )
        if result.success:
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) >= 11:
                    app = ApplicationInfo(
                        name=parts[10].split("/")[-1],
                        type="web",
                        technology="nodejs",
                        pid=int(parts[1]),
                        cpu_percent=float(parts[2]),
                        memory_mb=float(parts[3]),
                    )
                    report.applications.append(app)

        # Java applications
        result = await self.ssh.execute(
            "ps aux | grep java | grep -v grep"
        )
        if result.success:
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) >= 11:
                    cmd = " ".join(parts[10:])
                    name = "java-app"
                    if "-jar" in cmd:
                        jar_idx = cmd.find("-jar")
                        jar_name = cmd[jar_idx:].split()[1] if jar_idx >= 0 else "java-app"
                        name = jar_name.split("/")[-1]

                    app = ApplicationInfo(
                        name=name,
                        type="web",
                        technology="java",
                        pid=int(parts[1]),
                        cpu_percent=float(parts[2]),
                        memory_mb=float(parts[3]),
                    )
                    report.applications.append(app)

        logger.info(f"Found {len(report.applications)} applications")

    async def _discover_cron_jobs(self, report: ServicesReport):
        """Discover cron jobs."""
        # System crontab
        result = await self.ssh.execute("cat /etc/crontab 2>/dev/null")
        if result.success:
            for line in result.stdout.strip().split("\n"):
                if line.strip() and not line.startswith("#"):
                    report.cron_jobs.append(line.strip())

        # User crontabs
        result = await self.ssh.execute("crontab -l 2>/dev/null")
        if result.success:
            for line in result.stdout.strip().split("\n"):
                if line.strip() and not line.startswith("#"):
                    report.cron_jobs.append(f"(user) {line.strip()}")

        # Cron.d directory
        result = await self.ssh.execute("cat /etc/cron.d/* 2>/dev/null | grep -v '^#' | grep -v '^$'")
        if result.success:
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    report.cron_jobs.append(f"(cron.d) {line.strip()}")

    async def _discover_supervisor(self, report: ServicesReport):
        """Discover supervisor programs."""
        result = await self.ssh.execute("supervisorctl status 2>/dev/null")
        if result.success:
            for line in result.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 2:
                    report.supervisor_programs.append({
                        "name": parts[0],
                        "status": parts[1],
                        "details": " ".join(parts[2:]) if len(parts) > 2 else "",
                    })

    def to_dict(self, report: ServicesReport) -> dict:
        """Convert report to dictionary."""
        return {
            "host": report.host,
            "discovered_at": report.discovered_at.isoformat(),
            "systemd_services": report.systemd_services[:50],
            "applications": [
                {
                    "name": a.name,
                    "type": a.type,
                    "technology": a.technology,
                    "pid": a.pid,
                    "cpu_percent": a.cpu_percent,
                    "memory_mb": a.memory_mb,
                }
                for a in report.applications
            ],
            "web_servers": [
                {
                    "type": w.type,
                    "version": w.version,
                    "running": w.running,
                    "sites": w.sites,
                    "ssl_certificates": w.ssl_certificates,
                }
                for w in report.web_servers
            ],
            "cron_jobs": report.cron_jobs[:30],
            "supervisor": report.supervisor_programs,
        }
