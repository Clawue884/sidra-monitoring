"""Docker and container discovery module."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Optional, Any
from datetime import datetime

from ..utils import get_logger, SSHClient
from ..config import settings

logger = get_logger(__name__)


@dataclass
class ContainerInfo:
    """Container information."""
    id: str = ""
    name: str = ""
    image: str = ""
    status: str = ""
    state: str = ""
    created: str = ""
    ports: list[dict] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)
    environment: dict = field(default_factory=dict)
    labels: dict = field(default_factory=dict)
    cpu_percent: float = 0.0
    memory_usage_mb: float = 0.0
    memory_limit_mb: float = 0.0


@dataclass
class ServiceInfo:
    """Docker Swarm service information."""
    id: str = ""
    name: str = ""
    image: str = ""
    mode: str = ""
    replicas: str = ""
    ports: list[dict] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    labels: dict = field(default_factory=dict)


@dataclass
class StackInfo:
    """Docker stack information."""
    name: str = ""
    services: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)


@dataclass
class DockerNodeInfo:
    """Docker Swarm node information."""
    id: str = ""
    hostname: str = ""
    status: str = ""
    availability: str = ""
    role: str = ""
    engine_version: str = ""
    ip_address: str = ""
    labels: dict = field(default_factory=dict)


@dataclass
class DockerSystemInfo:
    """Complete Docker system information."""
    version: str = ""
    api_version: str = ""
    os: str = ""
    architecture: str = ""
    kernel_version: str = ""
    total_memory_gb: float = 0.0
    cpus: int = 0
    storage_driver: str = ""
    swarm_mode: bool = False
    swarm_node_id: str = ""
    swarm_node_role: str = ""
    swarm_managers: int = 0
    swarm_nodes: int = 0
    containers_running: int = 0
    containers_paused: int = 0
    containers_stopped: int = 0
    images_count: int = 0
    nodes: list[DockerNodeInfo] = field(default_factory=list)
    services: list[ServiceInfo] = field(default_factory=list)
    stacks: list[StackInfo] = field(default_factory=list)
    containers: list[ContainerInfo] = field(default_factory=list)
    networks: list[dict] = field(default_factory=list)
    volumes: list[dict] = field(default_factory=list)
    discovered_at: datetime = field(default_factory=datetime.now)


class DockerDiscovery:
    """Discover Docker containers, services, and swarm configuration."""

    def __init__(self, ssh_client: SSHClient):
        self.ssh = ssh_client

    async def discover(self) -> DockerSystemInfo:
        """Perform full Docker discovery."""
        logger.info(f"Starting Docker discovery on {self.ssh.creds.host}")

        info = DockerSystemInfo()

        # Check if Docker is available
        result = await self.ssh.execute("docker version --format '{{.Server.Version}}' 2>/dev/null")
        if not result.success:
            logger.warning("Docker not available or accessible")
            return info

        info.version = result.stdout.strip()

        # Gather all information
        await asyncio.gather(
            self._get_system_info(info),
            self._get_swarm_info(info),
            self._get_containers(info),
            self._get_services(info),
            self._get_stacks(info),
            self._get_networks(info),
            self._get_volumes(info),
            return_exceptions=True,
        )

        logger.info(f"Docker discovery complete: {info.containers_running} containers, {len(info.services)} services")
        return info

    async def _get_system_info(self, info: DockerSystemInfo):
        """Get Docker system information."""
        result = await self.ssh.execute("docker info --format '{{json .}}'")
        if result.success:
            try:
                data = json.loads(result.stdout)
                info.os = data.get("OperatingSystem", "")
                info.architecture = data.get("Architecture", "")
                info.kernel_version = data.get("KernelVersion", "")
                info.cpus = data.get("NCPU", 0)
                info.total_memory_gb = round(data.get("MemTotal", 0) / (1024**3), 2)
                info.storage_driver = data.get("Driver", "")
                info.containers_running = data.get("ContainersRunning", 0)
                info.containers_paused = data.get("ContainersPaused", 0)
                info.containers_stopped = data.get("ContainersStopped", 0)
                info.images_count = data.get("Images", 0)

                # Swarm info
                swarm = data.get("Swarm", {})
                info.swarm_mode = swarm.get("LocalNodeState") == "active"
                info.swarm_node_id = swarm.get("NodeID", "")
                info.swarm_managers = swarm.get("Managers", 0)
                info.swarm_nodes = swarm.get("Nodes", 0)
                if swarm.get("ControlAvailable"):
                    info.swarm_node_role = "manager"
                elif info.swarm_mode:
                    info.swarm_node_role = "worker"
            except json.JSONDecodeError:
                pass

    async def _get_swarm_info(self, info: DockerSystemInfo):
        """Get Docker Swarm node information."""
        if not info.swarm_mode:
            return

        result = await self.ssh.execute("docker node ls --format '{{json .}}' 2>/dev/null")
        if result.success:
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    try:
                        data = json.loads(line)
                        node = DockerNodeInfo(
                            id=data.get("ID", ""),
                            hostname=data.get("Hostname", ""),
                            status=data.get("Status", ""),
                            availability=data.get("Availability", ""),
                            role=data.get("ManagerStatus", "") or "worker",
                            engine_version=data.get("EngineVersion", ""),
                        )
                        info.nodes.append(node)
                    except json.JSONDecodeError:
                        pass

    async def _get_containers(self, info: DockerSystemInfo):
        """Get running containers."""
        result = await self.ssh.execute(
            "docker ps --format '{{json .}}' 2>/dev/null"
        )
        if result.success:
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    try:
                        data = json.loads(line)
                        container = ContainerInfo(
                            id=data.get("ID", ""),
                            name=data.get("Names", ""),
                            image=data.get("Image", ""),
                            status=data.get("Status", ""),
                            state=data.get("State", ""),
                            created=data.get("CreatedAt", ""),
                            ports=self._parse_ports(data.get("Ports", "")),
                            networks=data.get("Networks", "").split(",") if data.get("Networks") else [],
                        )
                        info.containers.append(container)
                    except json.JSONDecodeError:
                        pass

        # Get container stats
        result = await self.ssh.execute(
            "docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' 2>/dev/null"
        )
        if result.success:
            for line in result.stdout.strip().split("\n"):
                parts = line.split(",")
                if len(parts) >= 3:
                    name = parts[0]
                    cpu = parts[1].replace("%", "")
                    mem = parts[2]

                    for container in info.containers:
                        if container.name == name:
                            try:
                                container.cpu_percent = float(cpu)
                            except ValueError:
                                pass
                            # Parse memory like "100MiB / 2GiB"
                            if "/" in mem:
                                used = mem.split("/")[0].strip()
                                if "MiB" in used:
                                    container.memory_usage_mb = float(used.replace("MiB", ""))
                                elif "GiB" in used:
                                    container.memory_usage_mb = float(used.replace("GiB", "")) * 1024

    async def _get_services(self, info: DockerSystemInfo):
        """Get Docker Swarm services."""
        if not info.swarm_mode:
            return

        result = await self.ssh.execute(
            "docker service ls --format '{{json .}}' 2>/dev/null"
        )
        if result.success:
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    try:
                        data = json.loads(line)
                        service = ServiceInfo(
                            id=data.get("ID", ""),
                            name=data.get("Name", ""),
                            image=data.get("Image", ""),
                            mode=data.get("Mode", ""),
                            replicas=data.get("Replicas", ""),
                            ports=self._parse_ports(data.get("Ports", "")),
                        )
                        info.services.append(service)
                    except json.JSONDecodeError:
                        pass

    async def _get_stacks(self, info: DockerSystemInfo):
        """Get Docker stacks."""
        if not info.swarm_mode:
            return

        result = await self.ssh.execute("docker stack ls --format '{{.Name}}' 2>/dev/null")
        if result.success:
            for stack_name in result.stdout.strip().split("\n"):
                if stack_name.strip():
                    stack = StackInfo(name=stack_name.strip())

                    # Get services in this stack
                    svc_result = await self.ssh.execute(
                        f"docker stack services {stack_name} --format '{{{{.Name}}}}' 2>/dev/null"
                    )
                    if svc_result.success:
                        stack.services = [s.strip() for s in svc_result.stdout.strip().split("\n") if s.strip()]

                    info.stacks.append(stack)

    async def _get_networks(self, info: DockerSystemInfo):
        """Get Docker networks."""
        result = await self.ssh.execute(
            "docker network ls --format '{{json .}}' 2>/dev/null"
        )
        if result.success:
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    try:
                        data = json.loads(line)
                        info.networks.append({
                            "id": data.get("ID", ""),
                            "name": data.get("Name", ""),
                            "driver": data.get("Driver", ""),
                            "scope": data.get("Scope", ""),
                        })
                    except json.JSONDecodeError:
                        pass

    async def _get_volumes(self, info: DockerSystemInfo):
        """Get Docker volumes."""
        result = await self.ssh.execute(
            "docker volume ls --format '{{json .}}' 2>/dev/null"
        )
        if result.success:
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    try:
                        data = json.loads(line)
                        info.volumes.append({
                            "name": data.get("Name", ""),
                            "driver": data.get("Driver", ""),
                            "mountpoint": data.get("Mountpoint", ""),
                        })
                    except json.JSONDecodeError:
                        pass

    def _parse_ports(self, ports_str: str) -> list[dict]:
        """Parse Docker ports string."""
        if not ports_str:
            return []

        ports = []
        for port_mapping in ports_str.split(", "):
            if "->" in port_mapping:
                parts = port_mapping.split("->")
                host_part = parts[0]
                container_part = parts[1] if len(parts) > 1 else ""
                ports.append({
                    "host": host_part,
                    "container": container_part,
                })
            elif port_mapping:
                ports.append({"container": port_mapping})
        return ports

    def to_dict(self, info: DockerSystemInfo) -> dict:
        """Convert DockerSystemInfo to dictionary."""
        return {
            "version": info.version,
            "os": info.os,
            "architecture": info.architecture,
            "cpus": info.cpus,
            "total_memory_gb": info.total_memory_gb,
            "storage_driver": info.storage_driver,
            "swarm": {
                "enabled": info.swarm_mode,
                "node_role": info.swarm_node_role,
                "managers": info.swarm_managers,
                "nodes": info.swarm_nodes,
            } if info.swarm_mode else None,
            "containers": {
                "running": info.containers_running,
                "paused": info.containers_paused,
                "stopped": info.containers_stopped,
                "list": [
                    {
                        "name": c.name,
                        "image": c.image,
                        "status": c.status,
                        "cpu_percent": c.cpu_percent,
                        "memory_mb": c.memory_usage_mb,
                        "ports": c.ports,
                        "networks": c.networks,
                    }
                    for c in info.containers
                ],
            },
            "services": [
                {
                    "name": s.name,
                    "image": s.image,
                    "replicas": s.replicas,
                    "ports": s.ports,
                }
                for s in info.services
            ] if info.swarm_mode else [],
            "stacks": [
                {"name": s.name, "services": s.services}
                for s in info.stacks
            ] if info.swarm_mode else [],
            "nodes": [
                {
                    "hostname": n.hostname,
                    "status": n.status,
                    "role": n.role,
                    "availability": n.availability,
                }
                for n in info.nodes
            ] if info.swarm_mode else [],
            "networks": info.networks,
            "volumes": info.volumes[:20],
            "discovered_at": info.discovered_at.isoformat(),
        }
