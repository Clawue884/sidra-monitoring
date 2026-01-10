"""
Service Collector.

Monitors systemd services and critical processes.
"""

import asyncio
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ServiceStatus:
    """Status of a systemd service."""
    name: str
    active: bool
    running: bool
    enabled: bool
    status: str  # active, inactive, failed, etc.
    sub_state: str  # running, dead, exited, etc.
    description: str = ""
    pid: Optional[int] = None
    memory_bytes: int = 0
    cpu_percent: float = 0.0
    uptime_seconds: float = 0.0
    restart_count: int = 0
    last_restart: Optional[str] = None


@dataclass
class ProcessInfo:
    """Information about a running process."""
    pid: int
    name: str
    cmdline: str
    user: str
    cpu_percent: float
    memory_percent: float
    memory_bytes: int
    status: str
    create_time: float


@dataclass
class ServiceMetrics:
    """Complete service metrics snapshot."""
    timestamp: float
    hostname: str
    services: list[ServiceStatus] = field(default_factory=list)
    failed_services: list[str] = field(default_factory=list)
    critical_processes: list[ProcessInfo] = field(default_factory=list)
    systemd_available: bool = True


class ServiceCollector:
    """Collects service and process metrics."""

    # Critical services to always monitor
    DEFAULT_SERVICES = [
        "docker",
        "sshd",
        "nginx",
        "postgresql",
        "postgresql@14-main",
        "redis",
        "redis-server",
        "mysql",
        "mariadb",
        "mongod",
        "ollama",
        "netdata",
        "prometheus",
        "grafana-server",
        "wazuh-agent",
    ]

    # Critical processes to monitor even if not services
    CRITICAL_PROCESSES = [
        "dockerd",
        "containerd",
        "ollama",
        "python",
        "node",
        "java",
        "postgres",
        "redis-server",
        "nginx",
        "gunicorn",
        "uvicorn",
    ]

    def __init__(self, config=None):
        """Initialize the service collector."""
        self.config = config
        self._services_to_watch = self.DEFAULT_SERVICES.copy()
        if config and config.watch_services:
            self._services_to_watch.extend(config.watch_services)
        self._services_to_watch = list(set(self._services_to_watch))

    async def collect(self) -> ServiceMetrics:
        """Collect all service metrics."""
        import socket

        loop = asyncio.get_event_loop()

        # Check if systemd is available
        systemd_available = await loop.run_in_executor(
            None, self._check_systemd
        )

        services = []
        failed_services = []

        if systemd_available:
            # Get status of watched services
            services = await loop.run_in_executor(
                None, self._get_service_statuses
            )

            # Find failed services
            failed_result = await loop.run_in_executor(
                None, self._get_failed_services
            )
            failed_services = failed_result

        # Get critical processes
        critical_procs = await loop.run_in_executor(
            None, self._get_critical_processes
        )

        return ServiceMetrics(
            timestamp=time.time(),
            hostname=socket.gethostname(),
            services=services,
            failed_services=failed_services,
            critical_processes=critical_procs,
            systemd_available=systemd_available,
        )

    def _check_systemd(self) -> bool:
        """Check if systemd is available."""
        try:
            result = subprocess.run(
                ["systemctl", "--version"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def _get_service_statuses(self) -> list[ServiceStatus]:
        """Get status of watched services."""
        services = []

        for service_name in self._services_to_watch:
            try:
                # Get service status
                result = subprocess.run(
                    [
                        "systemctl", "show", service_name,
                        "--property=ActiveState,SubState,Description,MainPID,MemoryCurrent,CPUUsageNSec,NRestarts,StateChangeTimestamp,UnitFileState"
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5
                )

                if result.returncode != 0:
                    continue

                # Parse properties
                props = {}
                for line in result.stdout.strip().split("\n"):
                    if "=" in line:
                        key, value = line.split("=", 1)
                        props[key] = value

                active_state = props.get("ActiveState", "unknown")
                sub_state = props.get("SubState", "unknown")

                # Skip if service doesn't exist
                if active_state == "inactive" and sub_state == "dead":
                    # Check if unit file exists
                    if props.get("UnitFileState", "") == "":
                        continue

                service = ServiceStatus(
                    name=service_name,
                    active=active_state == "active",
                    running=sub_state == "running",
                    enabled=props.get("UnitFileState", "") == "enabled",
                    status=active_state,
                    sub_state=sub_state,
                    description=props.get("Description", ""),
                    pid=int(props.get("MainPID", 0)) or None,
                    memory_bytes=int(props.get("MemoryCurrent", 0)) if props.get("MemoryCurrent", "[not set]") != "[not set]" else 0,
                    restart_count=int(props.get("NRestarts", 0)),
                    last_restart=props.get("StateChangeTimestamp", ""),
                )

                services.append(service)

            except Exception:
                continue

        return services

    def _get_failed_services(self) -> list[str]:
        """Get list of all failed services."""
        try:
            result = subprocess.run(
                ["systemctl", "--failed", "--no-legend", "--plain"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return []

            failed = []
            for line in result.stdout.strip().split("\n"):
                if line:
                    # First column is the unit name
                    parts = line.split()
                    if parts:
                        failed.append(parts[0])

            return failed

        except Exception:
            return []

    def _get_critical_processes(self) -> list[ProcessInfo]:
        """Get information about critical processes."""
        import psutil

        processes = []

        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'username', 'cpu_percent', 'memory_percent', 'memory_info', 'status', 'create_time']):
            try:
                pinfo = proc.info
                name = pinfo['name'] or ""

                # Check if this is a critical process
                is_critical = False
                for critical in self.CRITICAL_PROCESSES:
                    if critical.lower() in name.lower():
                        is_critical = True
                        break

                if not is_critical:
                    continue

                cmdline = " ".join(pinfo['cmdline'] or [])[:200]

                processes.append(ProcessInfo(
                    pid=pinfo['pid'],
                    name=name,
                    cmdline=cmdline,
                    user=pinfo['username'] or "",
                    cpu_percent=pinfo['cpu_percent'] or 0,
                    memory_percent=pinfo['memory_percent'] or 0,
                    memory_bytes=pinfo['memory_info'].rss if pinfo['memory_info'] else 0,
                    status=pinfo['status'] or "",
                    create_time=pinfo['create_time'] or 0,
                ))

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return processes

    def to_prometheus_metrics(self, metrics: ServiceMetrics) -> list[str]:
        """Convert metrics to Prometheus format."""
        lines = []
        labels = f'host="{metrics.hostname}"'

        # Failed services count
        lines.append(f'sidra_services_failed_total{{{labels}}} {len(metrics.failed_services)}')

        # Individual service status
        for service in metrics.services:
            svc_labels = f'{labels},service="{service.name}"'

            # Active state (1 = active, 0 = inactive)
            active = 1 if service.active else 0
            lines.append(f'sidra_service_active{{{svc_labels}}} {active}')

            # Running state
            running = 1 if service.running else 0
            lines.append(f'sidra_service_running{{{svc_labels}}} {running}')

            if service.memory_bytes > 0:
                lines.append(f'sidra_service_memory_bytes{{{svc_labels}}} {service.memory_bytes}')

            lines.append(f'sidra_service_restarts_total{{{svc_labels}}} {service.restart_count}')

        # Critical processes
        for proc in metrics.critical_processes:
            proc_labels = f'{labels},process="{proc.name}",pid="{proc.pid}"'
            lines.append(f'sidra_process_cpu_percent{{{proc_labels}}} {proc.cpu_percent}')
            lines.append(f'sidra_process_memory_bytes{{{proc_labels}}} {proc.memory_bytes}')

        return lines

    def check_thresholds(self, metrics: ServiceMetrics, thresholds: dict = None) -> list[dict]:
        """Check service health and return alerts."""
        alerts = []

        # Failed services
        for failed in metrics.failed_services:
            alerts.append({
                'metric': 'service_failed',
                'value': failed,
                'severity': 'critical',
                'message': f'Service {failed} has failed',
                'service': failed,
            })

        # Watched services that should be running but aren't
        for service in metrics.services:
            if service.enabled and not service.running:
                severity = 'critical' if service.name in ['docker', 'sshd', 'postgresql'] else 'high'
                alerts.append({
                    'metric': 'service_down',
                    'value': service.status,
                    'severity': severity,
                    'message': f'Service {service.name} is not running (status: {service.status})',
                    'service': service.name,
                })

            # High restart count
            if service.restart_count > 5:
                alerts.append({
                    'metric': 'service_restarts',
                    'value': service.restart_count,
                    'severity': 'warning',
                    'message': f'Service {service.name} has restarted {service.restart_count} times',
                    'service': service.name,
                })

        return alerts
