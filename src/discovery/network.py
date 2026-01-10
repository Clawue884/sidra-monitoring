"""Network scanning and discovery module."""

import asyncio
import socket
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
import ipaddress

from ..utils import get_logger, SSHConnectionPool
from ..config import settings

logger = get_logger(__name__)


@dataclass
class PortInfo:
    """Information about an open port."""
    port: int
    protocol: str = "tcp"
    service: Optional[str] = None
    version: Optional[str] = None
    state: str = "open"


@dataclass
class HostInfo:
    """Information about a discovered host."""
    ip: str
    hostname: Optional[str] = None
    mac: Optional[str] = None
    os: Optional[str] = None
    open_ports: list[PortInfo] = field(default_factory=list)
    ssh_accessible: bool = False
    discovered_at: datetime = field(default_factory=datetime.now)
    metadata: dict = field(default_factory=dict)


@dataclass
class NetworkInfo:
    """Information about a network."""
    cidr: str
    name: Optional[str] = None
    gateway: Optional[str] = None
    dns_servers: list[str] = field(default_factory=list)
    hosts: list[HostInfo] = field(default_factory=list)
    scanned_at: datetime = field(default_factory=datetime.now)


class NetworkScanner:
    """Network discovery and scanning."""

    # Common ports to scan
    COMMON_PORTS = [
        22,    # SSH
        80,    # HTTP
        443,   # HTTPS
        3000,  # Node.js/Grafana
        3306,  # MySQL
        5432,  # PostgreSQL
        5672,  # RabbitMQ
        6379,  # Redis
        8080,  # HTTP Alt
        8443,  # HTTPS Alt
        9000,  # Portainer
        9090,  # Prometheus
        9100,  # Node Exporter
        11434, # Ollama
        27017, # MongoDB
    ]

    SERVICE_MAP = {
        22: "ssh",
        80: "http",
        443: "https",
        3000: "grafana/node",
        3306: "mysql",
        5432: "postgresql",
        5672: "rabbitmq",
        6379: "redis",
        8080: "http-alt",
        8443: "https-alt",
        9000: "portainer",
        9090: "prometheus",
        9100: "node-exporter",
        11434: "ollama",
        27017: "mongodb",
    }

    def __init__(self, ssh_pool: Optional[SSHConnectionPool] = None):
        self.ssh_pool = ssh_pool or SSHConnectionPool()

    async def scan_network(
        self,
        cidr: str,
        ports: Optional[list[int]] = None,
        check_ssh: bool = True,
    ) -> NetworkInfo:
        """Scan a network for active hosts."""
        logger.info(f"Scanning network: {cidr}")
        network = ipaddress.ip_network(cidr, strict=False)
        ports = ports or self.COMMON_PORTS

        network_info = NetworkInfo(cidr=cidr)
        hosts = []

        # Scan hosts in parallel
        tasks = []
        for ip in network.hosts():
            tasks.append(self._scan_host(str(ip), ports, check_ssh))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, HostInfo) and result.open_ports:
                hosts.append(result)

        network_info.hosts = hosts
        logger.info(f"Found {len(hosts)} active hosts in {cidr}")
        return network_info

    async def _scan_host(
        self,
        ip: str,
        ports: list[int],
        check_ssh: bool = True,
    ) -> Optional[HostInfo]:
        """Scan a single host for open ports."""
        host_info = HostInfo(ip=ip)

        # Try to resolve hostname
        try:
            hostname = socket.gethostbyaddr(ip)[0]
            host_info.hostname = hostname
        except socket.herror:
            pass

        # Scan ports
        open_ports = await self._scan_ports(ip, ports)
        host_info.open_ports = open_ports

        # Check SSH accessibility
        if check_ssh and any(p.port == 22 for p in open_ports):
            client = await self.ssh_pool.try_connect(ip)
            if client:
                host_info.ssh_accessible = True
                # Get additional info via SSH
                os_info = await client.execute("uname -a")
                if os_info.success:
                    host_info.os = os_info.stdout.strip()

                hostname_result = await client.execute("hostname -f")
                if hostname_result.success:
                    host_info.hostname = hostname_result.stdout.strip()

        return host_info

    async def _scan_ports(self, ip: str, ports: list[int]) -> list[PortInfo]:
        """Scan ports on a host."""
        open_ports = []

        async def check_port(port: int):
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, port),
                    timeout=settings.discovery_timeout,
                )
                writer.close()
                await writer.wait_closed()
                return PortInfo(
                    port=port,
                    service=self.SERVICE_MAP.get(port, "unknown"),
                )
            except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
                return None

        results = await asyncio.gather(*[check_port(p) for p in ports])
        open_ports = [r for r in results if r is not None]

        return open_ports

    async def scan_all_networks(self) -> list[NetworkInfo]:
        """Scan all configured networks."""
        networks = []
        for cidr in settings.networks_list:
            network_info = await self.scan_network(cidr)
            networks.append(network_info)
        return networks

    async def quick_ping_scan(self, cidr: str) -> list[str]:
        """Quick ping scan to find live hosts."""
        logger.info(f"Quick ping scan: {cidr}")
        network = ipaddress.ip_network(cidr, strict=False)
        live_hosts = []

        async def ping_host(ip: str):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ping", "-c", "1", "-W", "1", ip,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), timeout=2)
                if proc.returncode == 0:
                    return ip
            except (asyncio.TimeoutError, Exception):
                pass
            return None

        tasks = [ping_host(str(ip)) for ip in network.hosts()]
        results = await asyncio.gather(*tasks)
        live_hosts = [ip for ip in results if ip is not None]

        logger.info(f"Found {len(live_hosts)} live hosts")
        return live_hosts

    def to_dict(self, network_info: NetworkInfo) -> dict:
        """Convert NetworkInfo to dictionary."""
        return {
            "cidr": network_info.cidr,
            "name": network_info.name,
            "gateway": network_info.gateway,
            "dns_servers": network_info.dns_servers,
            "scanned_at": network_info.scanned_at.isoformat(),
            "host_count": len(network_info.hosts),
            "hosts": [
                {
                    "ip": h.ip,
                    "hostname": h.hostname,
                    "os": h.os,
                    "ssh_accessible": h.ssh_accessible,
                    "open_ports": [
                        {"port": p.port, "service": p.service}
                        for p in h.open_ports
                    ],
                }
                for h in network_info.hosts
            ],
        }
