"""
Edge Agent Configuration.
"""

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import yaml


@dataclass
class CollectorConfig:
    """Configuration for individual collectors."""
    enabled: bool = True
    interval: int = 10  # seconds


@dataclass
class SystemCollectorConfig(CollectorConfig):
    """System metrics collector config."""
    collect_cpu: bool = True
    collect_memory: bool = True
    collect_disk: bool = True
    collect_network: bool = True
    collect_load: bool = True
    disk_paths: list[str] = field(default_factory=lambda: ["/"])


@dataclass
class GPUCollectorConfig(CollectorConfig):
    """GPU metrics collector config."""
    enabled: bool = True  # Auto-detect if NVIDIA GPU present
    auto_detect: bool = True
    nvidia_smi_path: str = "/usr/bin/nvidia-smi"


@dataclass
class DockerCollectorConfig(CollectorConfig):
    """Docker metrics collector config."""
    enabled: bool = True
    auto_detect: bool = True
    socket_path: str = "/var/run/docker.sock"
    collect_stats: bool = True
    collect_logs: bool = False  # Can be heavy


@dataclass
class LogCollectorConfig(CollectorConfig):
    """Log collector config."""
    enabled: bool = True
    interval: int = 30
    paths: list[str] = field(default_factory=lambda: [
        "/var/log/syslog",
        "/var/log/auth.log",
        "/var/log/kern.log",
    ])
    docker_logs: bool = True
    max_lines_per_batch: int = 1000
    filter_patterns: list[str] = field(default_factory=list)


@dataclass
class ServiceCollectorConfig(CollectorConfig):
    """Service/systemd collector config."""
    enabled: bool = True
    interval: int = 60
    watch_services: list[str] = field(default_factory=lambda: [
        "docker",
        "sshd",
        "nginx",
        "postgresql",
        "redis",
    ])


@dataclass
class BatchingConfig:
    """Smart batching configuration."""
    enabled: bool = True
    batch_interval: int = 30  # seconds
    max_batch_size: int = 100  # metrics per batch
    max_batch_age: int = 60  # max seconds before force send
    critical_immediate: bool = True  # Send critical alerts immediately


@dataclass
class BufferConfig:
    """Local buffer configuration."""
    enabled: bool = True
    path: str = "/var/lib/sidra-agent/buffer.db"
    max_size_mb: int = 100
    retention_hours: int = 24


@dataclass
class PriorityRules:
    """Alert priority classification rules."""
    critical_thresholds: dict = field(default_factory=lambda: {
        "cpu_usage": 95,
        "memory_usage": 95,
        "disk_usage": 95,
        "gpu_temp": 85,
        "gpu_memory": 95,
    })
    high_thresholds: dict = field(default_factory=lambda: {
        "cpu_usage": 85,
        "memory_usage": 85,
        "disk_usage": 90,
        "gpu_memory": 90,
    })


@dataclass
class EdgeConfig:
    """Main Edge Agent configuration."""
    # Agent identity
    agent_id: str = field(default_factory=lambda: socket.gethostname())
    agent_version: str = "1.0.0"

    # Central brain connection
    central_url: str = "http://192.168.92.145:8200"
    central_timeout: int = 30
    central_retry_count: int = 3
    central_retry_delay: int = 5

    # Authentication
    api_key: Optional[str] = None

    # Collectors
    system: SystemCollectorConfig = field(default_factory=SystemCollectorConfig)
    gpu: GPUCollectorConfig = field(default_factory=GPUCollectorConfig)
    docker: DockerCollectorConfig = field(default_factory=DockerCollectorConfig)
    logs: LogCollectorConfig = field(default_factory=LogCollectorConfig)
    services: ServiceCollectorConfig = field(default_factory=ServiceCollectorConfig)

    # Batching and buffering
    batching: BatchingConfig = field(default_factory=BatchingConfig)
    buffer: BufferConfig = field(default_factory=BufferConfig)

    # Priority rules
    priority: PriorityRules = field(default_factory=PriorityRules)

    # Logging
    log_level: str = "INFO"
    log_file: Optional[str] = "/var/log/sidra-agent/agent.log"

    @classmethod
    def from_yaml(cls, path: str) -> "EdgeConfig":
        """Load configuration from YAML file."""
        with open(path, 'r') as f:
            data = yaml.safe_load(f)
        return cls._from_dict(data)

    @classmethod
    def from_env(cls) -> "EdgeConfig":
        """Load configuration from environment variables."""
        config = cls()

        # Override with environment variables
        if os.getenv("SIDRA_AGENT_ID"):
            config.agent_id = os.getenv("SIDRA_AGENT_ID")
        if os.getenv("SIDRA_CENTRAL_URL"):
            config.central_url = os.getenv("SIDRA_CENTRAL_URL")
        if os.getenv("SIDRA_API_KEY"):
            config.api_key = os.getenv("SIDRA_API_KEY")
        if os.getenv("SIDRA_LOG_LEVEL"):
            config.log_level = os.getenv("SIDRA_LOG_LEVEL")

        return config

    @classmethod
    def _from_dict(cls, data: dict) -> "EdgeConfig":
        """Create config from dictionary."""
        config = cls()

        # Simple fields
        for key in ["agent_id", "central_url", "central_timeout", "api_key", "log_level", "log_file"]:
            if key in data:
                setattr(config, key, data[key])

        # Collector configs
        if "collectors" in data:
            collectors = data["collectors"]
            if "system" in collectors:
                config.system = SystemCollectorConfig(**collectors["system"])
            if "gpu" in collectors:
                config.gpu = GPUCollectorConfig(**collectors["gpu"])
            if "docker" in collectors:
                config.docker = DockerCollectorConfig(**collectors["docker"])
            if "logs" in collectors:
                config.logs = LogCollectorConfig(**collectors["logs"])
            if "services" in collectors:
                config.services = ServiceCollectorConfig(**collectors["services"])

        # Batching config
        if "batching" in data:
            config.batching = BatchingConfig(**data["batching"])

        # Buffer config
        if "buffer" in data:
            config.buffer = BufferConfig(**data["buffer"])

        # Priority rules
        if "priority" in data:
            config.priority = PriorityRules(**data["priority"])

        return config

    def to_yaml(self, path: str) -> None:
        """Save configuration to YAML file."""
        import dataclasses

        def to_dict(obj):
            if dataclasses.is_dataclass(obj):
                return {k: to_dict(v) for k, v in dataclasses.asdict(obj).items()}
            return obj

        with open(path, 'w') as f:
            yaml.dump(to_dict(self), f, default_flow_style=False)
