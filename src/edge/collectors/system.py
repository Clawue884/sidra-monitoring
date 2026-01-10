"""
System Metrics Collector.

Collects CPU, Memory, Disk, Network, and Load metrics.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional
import psutil


@dataclass
class CPUMetrics:
    """CPU metrics."""
    usage_percent: float
    cores: int
    load_1m: float
    load_5m: float
    load_15m: float
    per_core: list[float] = field(default_factory=list)


@dataclass
class MemoryMetrics:
    """Memory metrics."""
    total_bytes: int
    used_bytes: int
    available_bytes: int
    usage_percent: float
    swap_total: int
    swap_used: int
    swap_percent: float


@dataclass
class DiskMetrics:
    """Disk metrics for a mount point."""
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    usage_percent: float
    read_bytes: int = 0
    write_bytes: int = 0
    read_count: int = 0
    write_count: int = 0


@dataclass
class NetworkMetrics:
    """Network interface metrics."""
    interface: str
    bytes_sent: int
    bytes_recv: int
    packets_sent: int
    packets_recv: int
    errors_in: int
    errors_out: int
    drops_in: int
    drops_out: int


@dataclass
class SystemMetrics:
    """Complete system metrics snapshot."""
    timestamp: float
    hostname: str
    cpu: CPUMetrics
    memory: MemoryMetrics
    disks: list[DiskMetrics]
    network: list[NetworkMetrics]
    uptime_seconds: float
    boot_time: float
    process_count: int


class SystemCollector:
    """Collects system-level metrics using psutil."""

    def __init__(self, config=None):
        """Initialize the system collector."""
        self.config = config
        self._last_disk_io = {}
        self._last_net_io = {}
        self._last_collect_time = 0

    async def collect(self) -> SystemMetrics:
        """Collect all system metrics."""
        import socket

        current_time = time.time()

        # Run blocking psutil calls in thread pool
        loop = asyncio.get_event_loop()

        cpu = await loop.run_in_executor(None, self._collect_cpu)
        memory = await loop.run_in_executor(None, self._collect_memory)
        disks = await loop.run_in_executor(None, self._collect_disks)
        network = await loop.run_in_executor(None, self._collect_network)

        # Get system info
        boot_time = psutil.boot_time()
        uptime = current_time - boot_time
        process_count = len(psutil.pids())

        self._last_collect_time = current_time

        return SystemMetrics(
            timestamp=current_time,
            hostname=socket.gethostname(),
            cpu=cpu,
            memory=memory,
            disks=disks,
            network=network,
            uptime_seconds=uptime,
            boot_time=boot_time,
            process_count=process_count,
        )

    def _collect_cpu(self) -> CPUMetrics:
        """Collect CPU metrics."""
        cpu_percent = psutil.cpu_percent(interval=0.1)
        cpu_count = psutil.cpu_count()
        load_avg = psutil.getloadavg()
        per_cpu = psutil.cpu_percent(interval=0.1, percpu=True)

        return CPUMetrics(
            usage_percent=cpu_percent,
            cores=cpu_count,
            load_1m=load_avg[0],
            load_5m=load_avg[1],
            load_15m=load_avg[2],
            per_core=per_cpu,
        )

    def _collect_memory(self) -> MemoryMetrics:
        """Collect memory metrics."""
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()

        return MemoryMetrics(
            total_bytes=mem.total,
            used_bytes=mem.used,
            available_bytes=mem.available,
            usage_percent=mem.percent,
            swap_total=swap.total,
            swap_used=swap.used,
            swap_percent=swap.percent,
        )

    def _collect_disks(self) -> list[DiskMetrics]:
        """Collect disk metrics for all mounted filesystems."""
        disks = []

        # Get disk partitions
        partitions = psutil.disk_partitions()

        for partition in partitions:
            # Skip special filesystems
            if partition.fstype in ('squashfs', 'tmpfs', 'devtmpfs'):
                continue

            try:
                usage = psutil.disk_usage(partition.mountpoint)

                disk = DiskMetrics(
                    path=partition.mountpoint,
                    total_bytes=usage.total,
                    used_bytes=usage.used,
                    free_bytes=usage.free,
                    usage_percent=usage.percent,
                )

                disks.append(disk)
            except (PermissionError, OSError):
                continue

        # Get disk I/O stats
        try:
            disk_io = psutil.disk_io_counters(perdisk=True)
            # Aggregate I/O for root disk
            for name, io in disk_io.items():
                if name.startswith(('sd', 'nvme', 'vd')):
                    for disk in disks:
                        if disk.path == '/':
                            disk.read_bytes = io.read_bytes
                            disk.write_bytes = io.write_bytes
                            disk.read_count = io.read_count
                            disk.write_count = io.write_count
                            break
        except Exception:
            pass

        return disks

    def _collect_network(self) -> list[NetworkMetrics]:
        """Collect network interface metrics."""
        networks = []

        net_io = psutil.net_io_counters(pernic=True)

        for interface, stats in net_io.items():
            # Skip loopback and virtual interfaces
            if interface.startswith(('lo', 'veth', 'docker', 'br-')):
                continue

            networks.append(NetworkMetrics(
                interface=interface,
                bytes_sent=stats.bytes_sent,
                bytes_recv=stats.bytes_recv,
                packets_sent=stats.packets_sent,
                packets_recv=stats.packets_recv,
                errors_in=stats.errin,
                errors_out=stats.errout,
                drops_in=stats.dropin,
                drops_out=stats.dropout,
            ))

        return networks

    def to_prometheus_metrics(self, metrics: SystemMetrics) -> list[str]:
        """Convert metrics to Prometheus format."""
        lines = []
        labels = f'host="{metrics.hostname}"'

        # CPU metrics
        lines.append(f'sidra_cpu_usage_percent{{{labels}}} {metrics.cpu.usage_percent}')
        lines.append(f'sidra_cpu_cores{{{labels}}} {metrics.cpu.cores}')
        lines.append(f'sidra_load_1m{{{labels}}} {metrics.cpu.load_1m}')
        lines.append(f'sidra_load_5m{{{labels}}} {metrics.cpu.load_5m}')
        lines.append(f'sidra_load_15m{{{labels}}} {metrics.cpu.load_15m}')

        # Memory metrics
        lines.append(f'sidra_memory_total_bytes{{{labels}}} {metrics.memory.total_bytes}')
        lines.append(f'sidra_memory_used_bytes{{{labels}}} {metrics.memory.used_bytes}')
        lines.append(f'sidra_memory_usage_percent{{{labels}}} {metrics.memory.usage_percent}')
        lines.append(f'sidra_swap_usage_percent{{{labels}}} {metrics.memory.swap_percent}')

        # Disk metrics
        for disk in metrics.disks:
            disk_labels = f'{labels},path="{disk.path}"'
            lines.append(f'sidra_disk_total_bytes{{{disk_labels}}} {disk.total_bytes}')
            lines.append(f'sidra_disk_used_bytes{{{disk_labels}}} {disk.used_bytes}')
            lines.append(f'sidra_disk_usage_percent{{{disk_labels}}} {disk.usage_percent}')

        # Network metrics
        for net in metrics.network:
            net_labels = f'{labels},interface="{net.interface}"'
            lines.append(f'sidra_network_bytes_sent{{{net_labels}}} {net.bytes_sent}')
            lines.append(f'sidra_network_bytes_recv{{{net_labels}}} {net.bytes_recv}')
            lines.append(f'sidra_network_errors_total{{{net_labels}}} {net.errors_in + net.errors_out}')

        # System metrics
        lines.append(f'sidra_uptime_seconds{{{labels}}} {metrics.uptime_seconds}')
        lines.append(f'sidra_process_count{{{labels}}} {metrics.process_count}')

        return lines

    def check_thresholds(self, metrics: SystemMetrics, thresholds: dict) -> list[dict]:
        """Check metrics against thresholds and return alerts."""
        alerts = []

        # CPU threshold
        if metrics.cpu.usage_percent >= thresholds.get('cpu_usage', 95):
            alerts.append({
                'metric': 'cpu_usage',
                'value': metrics.cpu.usage_percent,
                'threshold': thresholds.get('cpu_usage', 95),
                'severity': 'critical' if metrics.cpu.usage_percent >= 95 else 'high',
                'message': f'CPU usage at {metrics.cpu.usage_percent:.1f}%',
            })

        # Memory threshold
        if metrics.memory.usage_percent >= thresholds.get('memory_usage', 95):
            alerts.append({
                'metric': 'memory_usage',
                'value': metrics.memory.usage_percent,
                'threshold': thresholds.get('memory_usage', 95),
                'severity': 'critical' if metrics.memory.usage_percent >= 95 else 'high',
                'message': f'Memory usage at {metrics.memory.usage_percent:.1f}%',
            })

        # Disk threshold
        for disk in metrics.disks:
            if disk.usage_percent >= thresholds.get('disk_usage', 95):
                alerts.append({
                    'metric': 'disk_usage',
                    'value': disk.usage_percent,
                    'threshold': thresholds.get('disk_usage', 95),
                    'severity': 'critical' if disk.usage_percent >= 95 else 'high',
                    'message': f'Disk {disk.path} at {disk.usage_percent:.1f}%',
                    'path': disk.path,
                })

        return alerts
