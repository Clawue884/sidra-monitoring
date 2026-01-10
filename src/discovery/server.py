"""Server discovery and system information gathering."""

import asyncio
from dataclasses import dataclass, field
from typing import Optional, Any
from datetime import datetime

from ..utils import get_logger, SSHClient, SSHCredentials
from ..config import settings

logger = get_logger(__name__)


@dataclass
class CPUInfo:
    """CPU information."""
    model: str = ""
    cores: int = 0
    threads: int = 0
    usage_percent: float = 0.0


@dataclass
class MemoryInfo:
    """Memory information."""
    total_gb: float = 0.0
    used_gb: float = 0.0
    free_gb: float = 0.0
    usage_percent: float = 0.0


@dataclass
class DiskInfo:
    """Disk information."""
    mount_point: str = ""
    device: str = ""
    filesystem: str = ""
    total_gb: float = 0.0
    used_gb: float = 0.0
    free_gb: float = 0.0
    usage_percent: float = 0.0


@dataclass
class NetworkInterface:
    """Network interface information."""
    name: str = ""
    ip_address: str = ""
    netmask: str = ""
    mac_address: str = ""
    state: str = ""


@dataclass
class ProcessInfo:
    """Process information."""
    pid: int = 0
    name: str = ""
    user: str = ""
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    status: str = ""


@dataclass
class ServerInfo:
    """Comprehensive server information."""
    hostname: str = ""
    ip_address: str = ""
    os_name: str = ""
    os_version: str = ""
    kernel: str = ""
    architecture: str = ""
    uptime: str = ""
    cpu: CPUInfo = field(default_factory=CPUInfo)
    memory: MemoryInfo = field(default_factory=MemoryInfo)
    disks: list[DiskInfo] = field(default_factory=list)
    network_interfaces: list[NetworkInterface] = field(default_factory=list)
    top_processes: list[ProcessInfo] = field(default_factory=list)
    installed_packages: list[str] = field(default_factory=list)
    running_services: list[str] = field(default_factory=list)
    docker_installed: bool = False
    docker_info: dict = field(default_factory=dict)
    users: list[str] = field(default_factory=list)
    cron_jobs: list[str] = field(default_factory=list)
    environment_vars: dict = field(default_factory=dict)
    open_ports: list[int] = field(default_factory=list)
    firewall_status: str = ""
    discovered_at: datetime = field(default_factory=datetime.now)
    raw_data: dict = field(default_factory=dict)


class ServerDiscovery:
    """Discover and gather information about servers."""

    def __init__(self, ssh_client: Optional[SSHClient] = None):
        self.ssh = ssh_client

    async def set_target(self, host: str, username: str = None, password: str = None):
        """Set the target server."""
        creds = SSHCredentials(
            host=host,
            username=username or settings.ssh_user,
            password=password or settings.ssh_password,
        )
        self.ssh = SSHClient(creds)
        await self.ssh.connect()

    async def discover(self, host: str = None) -> ServerInfo:
        """Perform full server discovery."""
        if host:
            await self.set_target(host)

        if not self.ssh:
            raise ValueError("No SSH connection available")

        logger.info(f"Starting server discovery for {self.ssh.creds.host}")

        info = ServerInfo(ip_address=self.ssh.creds.host)

        # Gather all information in parallel where possible
        await asyncio.gather(
            self._get_basic_info(info),
            self._get_cpu_info(info),
            self._get_memory_info(info),
            self._get_disk_info(info),
            self._get_network_info(info),
            self._get_processes(info),
            self._get_services(info),
            self._get_docker_info(info),
            self._get_users(info),
            self._get_open_ports(info),
            return_exceptions=True,
        )

        logger.info(f"Discovery complete for {info.hostname}")
        return info

    async def _get_basic_info(self, info: ServerInfo):
        """Get basic system information."""
        # Hostname
        result = await self.ssh.execute("hostname -f")
        if result.success:
            info.hostname = result.stdout.strip()

        # OS Info
        result = await self.ssh.execute("cat /etc/os-release 2>/dev/null || cat /etc/*release 2>/dev/null | head -5")
        if result.success:
            for line in result.stdout.split("\n"):
                if line.startswith("PRETTY_NAME="):
                    info.os_name = line.split("=")[1].strip().strip('"')
                elif line.startswith("VERSION_ID="):
                    info.os_version = line.split("=")[1].strip().strip('"')

        # Kernel
        result = await self.ssh.execute("uname -r")
        if result.success:
            info.kernel = result.stdout.strip()

        # Architecture
        result = await self.ssh.execute("uname -m")
        if result.success:
            info.architecture = result.stdout.strip()

        # Uptime
        result = await self.ssh.execute("uptime -p")
        if result.success:
            info.uptime = result.stdout.strip()

    async def _get_cpu_info(self, info: ServerInfo):
        """Get CPU information."""
        # CPU Model
        result = await self.ssh.execute("cat /proc/cpuinfo | grep 'model name' | head -1")
        if result.success and ":" in result.stdout:
            info.cpu.model = result.stdout.split(":")[1].strip()

        # CPU Cores
        result = await self.ssh.execute("nproc")
        if result.success:
            info.cpu.cores = int(result.stdout.strip())

        # CPU Threads
        result = await self.ssh.execute("grep -c processor /proc/cpuinfo")
        if result.success:
            info.cpu.threads = int(result.stdout.strip())

        # CPU Usage
        result = await self.ssh.execute("top -bn1 | grep 'Cpu(s)' | awk '{print $2}'")
        if result.success:
            try:
                info.cpu.usage_percent = float(result.stdout.strip().replace(",", "."))
            except ValueError:
                pass

    async def _get_memory_info(self, info: ServerInfo):
        """Get memory information."""
        result = await self.ssh.execute("free -b | grep Mem")
        if result.success:
            parts = result.stdout.split()
            if len(parts) >= 4:
                total = int(parts[1])
                used = int(parts[2])
                free = int(parts[3])
                info.memory.total_gb = round(total / (1024**3), 2)
                info.memory.used_gb = round(used / (1024**3), 2)
                info.memory.free_gb = round(free / (1024**3), 2)
                if total > 0:
                    info.memory.usage_percent = round((used / total) * 100, 2)

    async def _get_disk_info(self, info: ServerInfo):
        """Get disk information."""
        result = await self.ssh.execute("df -B1 --output=target,source,fstype,size,used,avail,pcent 2>/dev/null | tail -n +2")
        if result.success:
            for line in result.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 7 and not parts[0].startswith("/dev"):
                    disk = DiskInfo(
                        mount_point=parts[0],
                        device=parts[1],
                        filesystem=parts[2],
                        total_gb=round(int(parts[3]) / (1024**3), 2),
                        used_gb=round(int(parts[4]) / (1024**3), 2),
                        free_gb=round(int(parts[5]) / (1024**3), 2),
                        usage_percent=float(parts[6].replace("%", "")),
                    )
                    info.disks.append(disk)

    async def _get_network_info(self, info: ServerInfo):
        """Get network interface information."""
        result = await self.ssh.execute("ip -o addr show | grep 'inet '")
        if result.success:
            for line in result.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 4:
                    iface = NetworkInterface(
                        name=parts[1],
                        ip_address=parts[3].split("/")[0],
                        netmask=parts[3].split("/")[1] if "/" in parts[3] else "",
                    )
                    info.network_interfaces.append(iface)

        # Get MAC addresses
        result = await self.ssh.execute("ip link show | grep -E '^[0-9]+:|link/ether'")
        if result.success:
            lines = result.stdout.strip().split("\n")
            current_iface = ""
            for line in lines:
                if line[0].isdigit():
                    current_iface = line.split(":")[1].strip()
                elif "link/ether" in line:
                    mac = line.split()[1]
                    for iface in info.network_interfaces:
                        if iface.name == current_iface:
                            iface.mac_address = mac

    async def _get_processes(self, info: ServerInfo):
        """Get top processes."""
        result = await self.ssh.execute("ps aux --sort=-%cpu | head -11 | tail -10")
        if result.success:
            for line in result.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 11:
                    proc = ProcessInfo(
                        user=parts[0],
                        pid=int(parts[1]),
                        cpu_percent=float(parts[2]),
                        memory_percent=float(parts[3]),
                        status=parts[7] if len(parts) > 7 else "",
                        name=" ".join(parts[10:]),
                    )
                    info.top_processes.append(proc)

    async def _get_services(self, info: ServerInfo):
        """Get running services."""
        result = await self.ssh.execute("systemctl list-units --type=service --state=running --no-pager --no-legend 2>/dev/null | awk '{print $1}'")
        if result.success:
            info.running_services = [s.strip() for s in result.stdout.strip().split("\n") if s.strip()]

    async def _get_docker_info(self, info: ServerInfo):
        """Get Docker information."""
        result = await self.ssh.execute("which docker")
        info.docker_installed = result.success and "docker" in result.stdout

        if info.docker_installed:
            # Docker version
            result = await self.ssh.execute("docker version --format '{{.Server.Version}}' 2>/dev/null")
            if result.success:
                info.docker_info["version"] = result.stdout.strip()

            # Docker info
            result = await self.ssh.execute("docker info --format '{{json .}}' 2>/dev/null")
            if result.success:
                try:
                    import json
                    info.docker_info["info"] = json.loads(result.stdout)
                except:
                    pass

            # Running containers
            result = await self.ssh.execute("docker ps --format '{{.Names}}' 2>/dev/null")
            if result.success:
                info.docker_info["running_containers"] = [c.strip() for c in result.stdout.strip().split("\n") if c.strip()]

            # Docker Swarm status
            result = await self.ssh.execute("docker info --format '{{.Swarm.LocalNodeState}}' 2>/dev/null")
            if result.success:
                info.docker_info["swarm_state"] = result.stdout.strip()

    async def _get_users(self, info: ServerInfo):
        """Get system users."""
        result = await self.ssh.execute("awk -F: '$3 >= 1000 && $3 < 65534 {print $1}' /etc/passwd")
        if result.success:
            info.users = [u.strip() for u in result.stdout.strip().split("\n") if u.strip()]

    async def _get_open_ports(self, info: ServerInfo):
        """Get open ports."""
        result = await self.ssh.execute("ss -tlnp 2>/dev/null | awk 'NR>1 {print $4}' | grep -oE '[0-9]+$' | sort -u")
        if result.success:
            info.open_ports = [int(p.strip()) for p in result.stdout.strip().split("\n") if p.strip().isdigit()]

    def to_dict(self, info: ServerInfo) -> dict:
        """Convert ServerInfo to dictionary."""
        return {
            "hostname": info.hostname,
            "ip_address": info.ip_address,
            "os": f"{info.os_name} {info.os_version}",
            "kernel": info.kernel,
            "architecture": info.architecture,
            "uptime": info.uptime,
            "cpu": {
                "model": info.cpu.model,
                "cores": info.cpu.cores,
                "threads": info.cpu.threads,
                "usage_percent": info.cpu.usage_percent,
            },
            "memory": {
                "total_gb": info.memory.total_gb,
                "used_gb": info.memory.used_gb,
                "free_gb": info.memory.free_gb,
                "usage_percent": info.memory.usage_percent,
            },
            "disks": [
                {
                    "mount": d.mount_point,
                    "device": d.device,
                    "total_gb": d.total_gb,
                    "used_gb": d.used_gb,
                    "usage_percent": d.usage_percent,
                }
                for d in info.disks
            ],
            "network_interfaces": [
                {"name": n.name, "ip": n.ip_address, "mac": n.mac_address}
                for n in info.network_interfaces
            ],
            "docker": info.docker_info if info.docker_installed else None,
            "running_services": info.running_services[:20],
            "open_ports": info.open_ports,
            "users": info.users,
            "discovered_at": info.discovered_at.isoformat(),
        }
