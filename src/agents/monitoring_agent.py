"""Monitoring agent for continuous infrastructure monitoring."""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable, Any

import httpx

from ..config import settings
from ..utils import get_logger, SSHClient, SSHCredentials, SSHConnectionPool

logger = get_logger(__name__)


@dataclass
class Alert:
    """Alert definition."""
    id: str = ""
    severity: str = ""  # critical, warning, info
    host: str = ""
    metric: str = ""
    value: float = 0.0
    threshold: float = 0.0
    message: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    acknowledged: bool = False


@dataclass
class HealthCheck:
    """Health check result."""
    host: str = ""
    status: str = ""  # healthy, degraded, unhealthy, unreachable
    cpu_usage: float = 0.0
    memory_usage: float = 0.0
    disk_usage: float = 0.0
    docker_running: bool = False
    containers_healthy: int = 0
    containers_unhealthy: int = 0
    services_up: int = 0
    services_down: int = 0
    last_check: datetime = field(default_factory=datetime.now)


class MonitoringAgent:
    """Agent for continuous infrastructure monitoring and alerting."""

    # Default thresholds
    THRESHOLDS = {
        "cpu_warning": 70,
        "cpu_critical": 90,
        "memory_warning": 80,
        "memory_critical": 95,
        "disk_warning": 80,
        "disk_critical": 95,
    }

    def __init__(
        self,
        hosts: list[str] = None,
        interval: int = None,
        webhook_url: str = None,
    ):
        self.hosts = hosts or []
        self.interval = interval or settings.monitor_interval
        self.webhook_url = webhook_url or settings.alert_webhook_url
        self.ssh_pool = SSHConnectionPool()
        self.alerts: list[Alert] = []
        self.health_checks: dict[str, HealthCheck] = {}
        self._running = False
        self._alert_callbacks: list[Callable[[Alert], None]] = []

    def add_hosts(self, hosts: list[str]):
        """Add hosts to monitor."""
        self.hosts.extend(hosts)

    def add_alert_callback(self, callback: Callable[[Alert], None]):
        """Add a callback for alerts."""
        self._alert_callbacks.append(callback)

    async def start(self):
        """Start the monitoring loop."""
        logger.info(f"Starting monitoring for {len(self.hosts)} hosts")
        self._running = True

        while self._running:
            try:
                await self._check_all_hosts()
                await self._process_alerts()
            except Exception as e:
                logger.error(f"Monitoring error: {e}")

            await asyncio.sleep(self.interval)

    async def stop(self):
        """Stop the monitoring loop."""
        logger.info("Stopping monitoring")
        self._running = False
        await self.ssh_pool.close_all()

    async def check_host(self, host: str) -> HealthCheck:
        """Check health of a single host."""
        check = HealthCheck(host=host)

        try:
            client = await self.ssh_pool.try_connect(host)
            if not client:
                check.status = "unreachable"
                return check

            # CPU usage
            result = await client.execute(
                "top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1"
            )
            if result.success:
                try:
                    check.cpu_usage = float(result.stdout.strip().replace(",", "."))
                except ValueError:
                    pass

            # Memory usage
            result = await client.execute(
                "free | grep Mem | awk '{print $3/$2 * 100}'"
            )
            if result.success:
                try:
                    check.memory_usage = float(result.stdout.strip())
                except ValueError:
                    pass

            # Disk usage (root partition)
            result = await client.execute(
                "df / | tail -1 | awk '{print $5}' | tr -d '%'"
            )
            if result.success:
                try:
                    check.disk_usage = float(result.stdout.strip())
                except ValueError:
                    pass

            # Docker status
            result = await client.execute("docker ps --format '{{.Status}}' 2>/dev/null")
            if result.success:
                check.docker_running = True
                statuses = result.stdout.strip().split("\n")
                for status in statuses:
                    if "healthy" in status.lower():
                        check.containers_healthy += 1
                    elif status and "unhealthy" in status.lower():
                        check.containers_unhealthy += 1
                    elif status:
                        check.containers_healthy += 1  # Running but no health check

            # Determine overall status
            if check.cpu_usage >= self.THRESHOLDS["cpu_critical"] or \
               check.memory_usage >= self.THRESHOLDS["memory_critical"] or \
               check.disk_usage >= self.THRESHOLDS["disk_critical"]:
                check.status = "unhealthy"
            elif check.cpu_usage >= self.THRESHOLDS["cpu_warning"] or \
                 check.memory_usage >= self.THRESHOLDS["memory_warning"] or \
                 check.disk_usage >= self.THRESHOLDS["disk_warning"]:
                check.status = "degraded"
            else:
                check.status = "healthy"

        except Exception as e:
            logger.error(f"Health check failed for {host}: {e}")
            check.status = "error"

        self.health_checks[host] = check
        return check

    async def _check_all_hosts(self):
        """Check all monitored hosts."""
        tasks = [self.check_host(host) for host in self.hosts]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Generate alerts based on checks
        for host, check in self.health_checks.items():
            await self._evaluate_thresholds(check)

    async def _evaluate_thresholds(self, check: HealthCheck):
        """Evaluate thresholds and generate alerts."""
        # CPU alerts
        if check.cpu_usage >= self.THRESHOLDS["cpu_critical"]:
            await self._create_alert(
                host=check.host,
                severity="critical",
                metric="cpu",
                value=check.cpu_usage,
                threshold=self.THRESHOLDS["cpu_critical"],
                message=f"Critical CPU usage: {check.cpu_usage:.1f}%",
            )
        elif check.cpu_usage >= self.THRESHOLDS["cpu_warning"]:
            await self._create_alert(
                host=check.host,
                severity="warning",
                metric="cpu",
                value=check.cpu_usage,
                threshold=self.THRESHOLDS["cpu_warning"],
                message=f"High CPU usage: {check.cpu_usage:.1f}%",
            )

        # Memory alerts
        if check.memory_usage >= self.THRESHOLDS["memory_critical"]:
            await self._create_alert(
                host=check.host,
                severity="critical",
                metric="memory",
                value=check.memory_usage,
                threshold=self.THRESHOLDS["memory_critical"],
                message=f"Critical memory usage: {check.memory_usage:.1f}%",
            )
        elif check.memory_usage >= self.THRESHOLDS["memory_warning"]:
            await self._create_alert(
                host=check.host,
                severity="warning",
                metric="memory",
                value=check.memory_usage,
                threshold=self.THRESHOLDS["memory_warning"],
                message=f"High memory usage: {check.memory_usage:.1f}%",
            )

        # Disk alerts
        if check.disk_usage >= self.THRESHOLDS["disk_critical"]:
            await self._create_alert(
                host=check.host,
                severity="critical",
                metric="disk",
                value=check.disk_usage,
                threshold=self.THRESHOLDS["disk_critical"],
                message=f"Critical disk usage: {check.disk_usage:.1f}%",
            )
        elif check.disk_usage >= self.THRESHOLDS["disk_warning"]:
            await self._create_alert(
                host=check.host,
                severity="warning",
                metric="disk",
                value=check.disk_usage,
                threshold=self.THRESHOLDS["disk_warning"],
                message=f"High disk usage: {check.disk_usage:.1f}%",
            )

        # Unreachable host alert
        if check.status == "unreachable":
            await self._create_alert(
                host=check.host,
                severity="critical",
                metric="connectivity",
                value=0,
                threshold=0,
                message=f"Host unreachable: {check.host}",
            )

    async def _create_alert(
        self,
        host: str,
        severity: str,
        metric: str,
        value: float,
        threshold: float,
        message: str,
    ):
        """Create a new alert."""
        # Check if similar alert already exists (within last 5 minutes)
        for existing in self.alerts:
            if (existing.host == host and
                existing.metric == metric and
                not existing.acknowledged and
                (datetime.now() - existing.created_at).seconds < 300):
                return  # Don't duplicate

        alert = Alert(
            id=f"{host}-{metric}-{datetime.now().timestamp()}",
            host=host,
            severity=severity,
            metric=metric,
            value=value,
            threshold=threshold,
            message=message,
        )
        self.alerts.append(alert)
        logger.warning(f"Alert: [{severity}] {message}")

        # Notify callbacks
        for callback in self._alert_callbacks:
            try:
                callback(alert)
            except Exception as e:
                logger.error(f"Alert callback error: {e}")

    async def _process_alerts(self):
        """Process and send alerts."""
        if not self.webhook_url:
            return

        unacknowledged = [a for a in self.alerts if not a.acknowledged]
        if not unacknowledged:
            return

        # Group by severity
        critical = [a for a in unacknowledged if a.severity == "critical"]
        warning = [a for a in unacknowledged if a.severity == "warning"]

        # Send webhook
        payload = {
            "timestamp": datetime.now().isoformat(),
            "summary": f"{len(critical)} critical, {len(warning)} warning alerts",
            "alerts": [
                {
                    "id": a.id,
                    "severity": a.severity,
                    "host": a.host,
                    "message": a.message,
                    "created_at": a.created_at.isoformat(),
                }
                for a in unacknowledged[:10]
            ],
        }

        try:
            async with httpx.AsyncClient() as client:
                await client.post(self.webhook_url, json=payload, timeout=10)
        except Exception as e:
            logger.error(f"Failed to send webhook: {e}")

    def get_status_summary(self) -> dict:
        """Get a summary of monitoring status."""
        return {
            "hosts_monitored": len(self.hosts),
            "hosts_healthy": sum(1 for h in self.health_checks.values() if h.status == "healthy"),
            "hosts_degraded": sum(1 for h in self.health_checks.values() if h.status == "degraded"),
            "hosts_unhealthy": sum(1 for h in self.health_checks.values() if h.status == "unhealthy"),
            "hosts_unreachable": sum(1 for h in self.health_checks.values() if h.status == "unreachable"),
            "active_alerts": len([a for a in self.alerts if not a.acknowledged]),
            "critical_alerts": len([a for a in self.alerts if a.severity == "critical" and not a.acknowledged]),
            "health_checks": {
                host: {
                    "status": check.status,
                    "cpu": check.cpu_usage,
                    "memory": check.memory_usage,
                    "disk": check.disk_usage,
                    "last_check": check.last_check.isoformat(),
                }
                for host, check in self.health_checks.items()
            },
        }

    def acknowledge_alert(self, alert_id: str):
        """Acknowledge an alert."""
        for alert in self.alerts:
            if alert.id == alert_id:
                alert.acknowledged = True
                logger.info(f"Alert acknowledged: {alert_id}")
                return True
        return False

    def clear_old_alerts(self, hours: int = 24):
        """Clear alerts older than specified hours."""
        cutoff = datetime.now()
        self.alerts = [
            a for a in self.alerts
            if (cutoff - a.created_at).seconds < hours * 3600
        ]
