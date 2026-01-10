"""
Docker Metrics Collector.

Collects Docker container metrics and health status.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional
import json


@dataclass
class ContainerMetrics:
    """Metrics for a single container."""
    id: str
    name: str
    image: str
    status: str
    state: str  # running, exited, paused, etc.
    health: Optional[str]  # healthy, unhealthy, starting, none
    created: str
    started_at: Optional[str]
    cpu_percent: float = 0.0
    memory_usage_bytes: int = 0
    memory_limit_bytes: int = 0
    memory_percent: float = 0.0
    network_rx_bytes: int = 0
    network_tx_bytes: int = 0
    block_read_bytes: int = 0
    block_write_bytes: int = 0
    restart_count: int = 0
    labels: dict = field(default_factory=dict)


@dataclass
class DockerMetrics:
    """Complete Docker metrics snapshot."""
    timestamp: float
    hostname: str
    docker_version: str
    containers_total: int
    containers_running: int
    containers_paused: int
    containers_stopped: int
    images_count: int
    containers: list[ContainerMetrics] = field(default_factory=list)
    available: bool = True
    error: Optional[str] = None


class DockerCollector:
    """Collects Docker container metrics."""

    def __init__(self, config=None):
        """Initialize the Docker collector."""
        self.config = config
        self._socket_path = config.socket_path if config else "/var/run/docker.sock"
        self._available = self._check_docker_available()

    def _check_docker_available(self) -> bool:
        """Check if Docker is available."""
        import os
        return os.path.exists(self._socket_path)

    @property
    def available(self) -> bool:
        """Check if Docker collection is available."""
        return self._available

    async def collect(self) -> DockerMetrics:
        """Collect all Docker metrics."""
        import socket as sock

        if not self._available:
            return DockerMetrics(
                timestamp=time.time(),
                hostname=sock.gethostname(),
                docker_version="",
                containers_total=0,
                containers_running=0,
                containers_paused=0,
                containers_stopped=0,
                images_count=0,
                available=False,
                error="Docker socket not found",
            )

        try:
            loop = asyncio.get_event_loop()

            # Get Docker info
            docker_info = await loop.run_in_executor(None, self._get_docker_info)
            containers = await loop.run_in_executor(None, self._get_containers)

            # Get stats for running containers
            running_containers = [c for c in containers if c.state == "running"]
            if running_containers:
                stats = await loop.run_in_executor(
                    None,
                    self._get_container_stats,
                    [c.id for c in running_containers]
                )
                # Merge stats into containers
                stats_map = {s['id']: s for s in stats}
                for container in containers:
                    if container.id in stats_map:
                        s = stats_map[container.id]
                        container.cpu_percent = s.get('cpu_percent', 0)
                        container.memory_usage_bytes = s.get('memory_usage', 0)
                        container.memory_limit_bytes = s.get('memory_limit', 0)
                        container.memory_percent = s.get('memory_percent', 0)
                        container.network_rx_bytes = s.get('network_rx', 0)
                        container.network_tx_bytes = s.get('network_tx', 0)

            return DockerMetrics(
                timestamp=time.time(),
                hostname=sock.gethostname(),
                docker_version=docker_info.get('version', ''),
                containers_total=docker_info.get('containers_total', 0),
                containers_running=docker_info.get('containers_running', 0),
                containers_paused=docker_info.get('containers_paused', 0),
                containers_stopped=docker_info.get('containers_stopped', 0),
                images_count=docker_info.get('images', 0),
                containers=containers,
                available=True,
            )

        except Exception as e:
            return DockerMetrics(
                timestamp=time.time(),
                hostname=sock.gethostname(),
                docker_version="",
                containers_total=0,
                containers_running=0,
                containers_paused=0,
                containers_stopped=0,
                images_count=0,
                available=False,
                error=str(e),
            )

    def _get_docker_info(self) -> dict:
        """Get Docker daemon info."""
        import subprocess

        try:
            result = subprocess.run(
                ["docker", "info", "--format", "{{json .}}"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return {}

            info = json.loads(result.stdout)
            return {
                'version': info.get('ServerVersion', ''),
                'containers_total': info.get('Containers', 0),
                'containers_running': info.get('ContainersRunning', 0),
                'containers_paused': info.get('ContainersPaused', 0),
                'containers_stopped': info.get('ContainersStopped', 0),
                'images': info.get('Images', 0),
            }
        except Exception:
            return {}

    def _get_containers(self) -> list[ContainerMetrics]:
        """Get list of all containers."""
        import subprocess

        try:
            result = subprocess.run(
                [
                    "docker", "ps", "-a",
                    "--format", '{"id":"{{.ID}}","name":"{{.Names}}","image":"{{.Image}}","status":"{{.Status}}","state":"{{.State}}","created":"{{.CreatedAt}}"}'
                ],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return []

            containers = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue

                try:
                    data = json.loads(line)

                    # Get additional container info
                    inspect_data = self._inspect_container(data['id'])

                    containers.append(ContainerMetrics(
                        id=data['id'],
                        name=data['name'],
                        image=data['image'],
                        status=data['status'],
                        state=data['state'],
                        health=inspect_data.get('health'),
                        created=data['created'],
                        started_at=inspect_data.get('started_at'),
                        restart_count=inspect_data.get('restart_count', 0),
                        labels=inspect_data.get('labels', {}),
                    ))
                except (json.JSONDecodeError, KeyError):
                    continue

            return containers
        except Exception:
            return []

    def _inspect_container(self, container_id: str) -> dict:
        """Inspect a container for additional details."""
        import subprocess

        try:
            result = subprocess.run(
                ["docker", "inspect", container_id],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0:
                return {}

            data = json.loads(result.stdout)[0]
            state = data.get('State', {})
            config = data.get('Config', {})

            health_status = None
            if 'Health' in state:
                health_status = state['Health'].get('Status')

            return {
                'health': health_status,
                'started_at': state.get('StartedAt'),
                'restart_count': data.get('RestartCount', 0),
                'labels': config.get('Labels', {}),
            }
        except Exception:
            return {}

    def _get_container_stats(self, container_ids: list[str]) -> list[dict]:
        """Get stats for containers (CPU, memory, network)."""
        import subprocess

        stats = []

        for cid in container_ids[:10]:  # Limit to 10 to avoid slow collection
            try:
                result = subprocess.run(
                    ["docker", "stats", cid, "--no-stream", "--format", "{{json .}}"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )

                if result.returncode != 0:
                    continue

                data = json.loads(result.stdout)

                # Parse CPU percentage
                cpu_str = data.get('CPUPerc', '0%').rstrip('%')
                cpu_percent = float(cpu_str) if cpu_str else 0

                # Parse memory
                mem_usage = self._parse_size(data.get('MemUsage', '0').split('/')[0].strip())
                mem_limit = self._parse_size(data.get('MemUsage', '0/0').split('/')[-1].strip())
                mem_percent_str = data.get('MemPerc', '0%').rstrip('%')
                mem_percent = float(mem_percent_str) if mem_percent_str else 0

                # Parse network
                net_io = data.get('NetIO', '0B / 0B').split('/')
                net_rx = self._parse_size(net_io[0].strip()) if len(net_io) > 0 else 0
                net_tx = self._parse_size(net_io[1].strip()) if len(net_io) > 1 else 0

                stats.append({
                    'id': cid,
                    'cpu_percent': cpu_percent,
                    'memory_usage': mem_usage,
                    'memory_limit': mem_limit,
                    'memory_percent': mem_percent,
                    'network_rx': net_rx,
                    'network_tx': net_tx,
                })
            except Exception:
                continue

        return stats

    def _parse_size(self, size_str: str) -> int:
        """Parse size string like '1.5GiB' to bytes."""
        size_str = size_str.strip()
        if not size_str:
            return 0

        units = {
            'B': 1,
            'KB': 1024,
            'KiB': 1024,
            'MB': 1024 ** 2,
            'MiB': 1024 ** 2,
            'GB': 1024 ** 3,
            'GiB': 1024 ** 3,
            'TB': 1024 ** 4,
            'TiB': 1024 ** 4,
        }

        for unit, multiplier in units.items():
            if size_str.endswith(unit):
                try:
                    return int(float(size_str[:-len(unit)].strip()) * multiplier)
                except ValueError:
                    return 0

        try:
            return int(float(size_str))
        except ValueError:
            return 0

    def to_prometheus_metrics(self, metrics: DockerMetrics) -> list[str]:
        """Convert metrics to Prometheus format."""
        lines = []
        labels = f'host="{metrics.hostname}"'

        if not metrics.available:
            lines.append(f'sidra_docker_available{{{labels}}} 0')
            return lines

        lines.append(f'sidra_docker_available{{{labels}}} 1')
        lines.append(f'sidra_docker_containers_total{{{labels}}} {metrics.containers_total}')
        lines.append(f'sidra_docker_containers_running{{{labels}}} {metrics.containers_running}')
        lines.append(f'sidra_docker_containers_stopped{{{labels}}} {metrics.containers_stopped}')
        lines.append(f'sidra_docker_images_total{{{labels}}} {metrics.images_count}')

        for container in metrics.containers:
            c_labels = f'{labels},container="{container.name}",image="{container.image}"'

            # State as numeric (1 = running, 0 = not running)
            running = 1 if container.state == "running" else 0
            lines.append(f'sidra_container_running{{{c_labels}}} {running}')

            if container.state == "running":
                lines.append(f'sidra_container_cpu_percent{{{c_labels}}} {container.cpu_percent}')
                lines.append(f'sidra_container_memory_usage_bytes{{{c_labels}}} {container.memory_usage_bytes}')
                lines.append(f'sidra_container_memory_percent{{{c_labels}}} {container.memory_percent}')

            lines.append(f'sidra_container_restart_count{{{c_labels}}} {container.restart_count}')

        return lines

    def check_thresholds(self, metrics: DockerMetrics, thresholds: dict = None) -> list[dict]:
        """Check container health and return alerts."""
        alerts = []

        if not metrics.available:
            return alerts

        for container in metrics.containers:
            # Unhealthy container
            if container.health == "unhealthy":
                alerts.append({
                    'metric': 'container_health',
                    'value': 'unhealthy',
                    'severity': 'high',
                    'message': f'Container {container.name} is unhealthy',
                    'container': container.name,
                })

            # Exited container (unexpected)
            if container.state == "exited" and container.restart_count > 0:
                alerts.append({
                    'metric': 'container_exited',
                    'value': container.restart_count,
                    'severity': 'high',
                    'message': f'Container {container.name} exited (restarts: {container.restart_count})',
                    'container': container.name,
                })

            # High memory usage
            if container.memory_percent > 90:
                alerts.append({
                    'metric': 'container_memory',
                    'value': container.memory_percent,
                    'severity': 'high',
                    'message': f'Container {container.name} memory at {container.memory_percent:.1f}%',
                    'container': container.name,
                })

        return alerts
