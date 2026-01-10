"""
GPU Metrics Collector.

Collects NVIDIA GPU metrics using nvidia-smi.
"""

import asyncio
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional
import shutil


@dataclass
class GPUMetrics:
    """Metrics for a single GPU."""
    index: int
    uuid: str
    name: str
    temperature_celsius: float
    utilization_percent: float
    memory_total_mb: int
    memory_used_mb: int
    memory_free_mb: int
    memory_percent: float
    power_draw_watts: float
    power_limit_watts: float
    fan_speed_percent: Optional[float] = None
    driver_version: str = ""
    cuda_version: str = ""
    pcie_gen: int = 0
    pcie_width: int = 0


@dataclass
class GPUProcessInfo:
    """Information about a process using a GPU."""
    pid: int
    process_name: str
    gpu_index: int
    memory_used_mb: int


@dataclass
class AllGPUMetrics:
    """Complete GPU metrics snapshot."""
    timestamp: float
    hostname: str
    gpu_count: int
    driver_version: str
    cuda_version: str
    gpus: list[GPUMetrics] = field(default_factory=list)
    processes: list[GPUProcessInfo] = field(default_factory=list)
    available: bool = True
    error: Optional[str] = None


class GPUCollector:
    """Collects GPU metrics using nvidia-smi."""

    NVIDIA_SMI_QUERY_GPU = [
        "index",
        "uuid",
        "name",
        "temperature.gpu",
        "utilization.gpu",
        "memory.total",
        "memory.used",
        "memory.free",
        "power.draw",
        "power.limit",
        "fan.speed",
        "driver_version",
        "pcie.link.gen.current",
        "pcie.link.width.current",
    ]

    def __init__(self, config=None):
        """Initialize the GPU collector."""
        self.config = config
        self._nvidia_smi_path = self._find_nvidia_smi()
        self._available = self._nvidia_smi_path is not None

    def _find_nvidia_smi(self) -> Optional[str]:
        """Find the nvidia-smi executable."""
        # Check common paths
        paths = [
            "/usr/bin/nvidia-smi",
            "/usr/local/bin/nvidia-smi",
            "/opt/nvidia/bin/nvidia-smi",
        ]

        for path in paths:
            if shutil.which(path):
                return path

        # Try to find in PATH
        return shutil.which("nvidia-smi")

    @property
    def available(self) -> bool:
        """Check if GPU collection is available."""
        return self._available

    async def collect(self) -> AllGPUMetrics:
        """Collect all GPU metrics."""
        import socket

        if not self._available:
            return AllGPUMetrics(
                timestamp=time.time(),
                hostname=socket.gethostname(),
                gpu_count=0,
                driver_version="",
                cuda_version="",
                available=False,
                error="nvidia-smi not found",
            )

        try:
            loop = asyncio.get_event_loop()

            # Run nvidia-smi in thread pool
            gpu_data = await loop.run_in_executor(None, self._query_gpu_metrics)
            process_data = await loop.run_in_executor(None, self._query_gpu_processes)
            driver_info = await loop.run_in_executor(None, self._query_driver_info)

            return AllGPUMetrics(
                timestamp=time.time(),
                hostname=socket.gethostname(),
                gpu_count=len(gpu_data),
                driver_version=driver_info.get("driver_version", ""),
                cuda_version=driver_info.get("cuda_version", ""),
                gpus=gpu_data,
                processes=process_data,
                available=True,
            )

        except Exception as e:
            return AllGPUMetrics(
                timestamp=time.time(),
                hostname=socket.gethostname(),
                gpu_count=0,
                driver_version="",
                cuda_version="",
                available=False,
                error=str(e),
            )

    def _query_gpu_metrics(self) -> list[GPUMetrics]:
        """Query GPU metrics using nvidia-smi."""
        query = ",".join(self.NVIDIA_SMI_QUERY_GPU)
        cmd = [
            self._nvidia_smi_path,
            f"--query-gpu={query}",
            "--format=csv,noheader,nounits",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

        if result.returncode != 0:
            raise RuntimeError(f"nvidia-smi failed: {result.stderr}")

        gpus = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue

            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 14:
                continue

            try:
                memory_total = int(parts[5])
                memory_used = int(parts[6])

                gpu = GPUMetrics(
                    index=int(parts[0]),
                    uuid=parts[1],
                    name=parts[2],
                    temperature_celsius=float(parts[3]) if parts[3] != "[N/A]" else 0,
                    utilization_percent=float(parts[4]) if parts[4] != "[N/A]" else 0,
                    memory_total_mb=memory_total,
                    memory_used_mb=memory_used,
                    memory_free_mb=int(parts[7]),
                    memory_percent=(memory_used / memory_total * 100) if memory_total > 0 else 0,
                    power_draw_watts=float(parts[8]) if parts[8] != "[N/A]" else 0,
                    power_limit_watts=float(parts[9]) if parts[9] != "[N/A]" else 0,
                    fan_speed_percent=float(parts[10]) if parts[10] != "[N/A]" else None,
                    driver_version=parts[11],
                    pcie_gen=int(parts[12]) if parts[12] != "[N/A]" else 0,
                    pcie_width=int(parts[13]) if parts[13] != "[N/A]" else 0,
                )
                gpus.append(gpu)
            except (ValueError, IndexError) as e:
                continue

        return gpus

    def _query_gpu_processes(self) -> list[GPUProcessInfo]:
        """Query processes using GPUs."""
        cmd = [
            self._nvidia_smi_path,
            "--query-compute-apps=pid,process_name,gpu_uuid,used_memory",
            "--format=csv,noheader,nounits",
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

            if result.returncode != 0:
                return []

            processes = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue

                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 4:
                    continue

                try:
                    processes.append(GPUProcessInfo(
                        pid=int(parts[0]),
                        process_name=parts[1],
                        gpu_index=0,  # Would need to map UUID to index
                        memory_used_mb=int(parts[3]),
                    ))
                except (ValueError, IndexError):
                    continue

            return processes
        except Exception:
            return []

    def _query_driver_info(self) -> dict:
        """Query driver and CUDA version."""
        cmd = [
            self._nvidia_smi_path,
            "--query-gpu=driver_version",
            "--format=csv,noheader",
        ]

        info = {}

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                info["driver_version"] = result.stdout.strip().split("\n")[0]
        except Exception:
            pass

        # Get CUDA version from nvidia-smi header
        try:
            result = subprocess.run(
                [self._nvidia_smi_path],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "CUDA Version" in line:
                        parts = line.split("CUDA Version:")
                        if len(parts) > 1:
                            info["cuda_version"] = parts[1].strip().split()[0]
                        break
        except Exception:
            pass

        return info

    def to_prometheus_metrics(self, metrics: AllGPUMetrics) -> list[str]:
        """Convert metrics to Prometheus format."""
        lines = []
        labels = f'host="{metrics.hostname}"'

        if not metrics.available:
            lines.append(f'sidra_gpu_available{{{labels}}} 0')
            return lines

        lines.append(f'sidra_gpu_count{{{labels}}} {metrics.gpu_count}')

        for gpu in metrics.gpus:
            gpu_labels = f'{labels},gpu="{gpu.index}",name="{gpu.name}"'

            lines.append(f'sidra_gpu_temperature_celsius{{{gpu_labels}}} {gpu.temperature_celsius}')
            lines.append(f'sidra_gpu_utilization_percent{{{gpu_labels}}} {gpu.utilization_percent}')
            lines.append(f'sidra_gpu_memory_total_mb{{{gpu_labels}}} {gpu.memory_total_mb}')
            lines.append(f'sidra_gpu_memory_used_mb{{{gpu_labels}}} {gpu.memory_used_mb}')
            lines.append(f'sidra_gpu_memory_percent{{{gpu_labels}}} {gpu.memory_percent}')
            lines.append(f'sidra_gpu_power_draw_watts{{{gpu_labels}}} {gpu.power_draw_watts}')

            if gpu.fan_speed_percent is not None:
                lines.append(f'sidra_gpu_fan_speed_percent{{{gpu_labels}}} {gpu.fan_speed_percent}')

        return lines

    def check_thresholds(self, metrics: AllGPUMetrics, thresholds: dict) -> list[dict]:
        """Check GPU metrics against thresholds and return alerts."""
        alerts = []

        if not metrics.available:
            return alerts

        for gpu in metrics.gpus:
            # Temperature threshold
            if gpu.temperature_celsius >= thresholds.get('gpu_temp', 85):
                alerts.append({
                    'metric': 'gpu_temp',
                    'value': gpu.temperature_celsius,
                    'threshold': thresholds.get('gpu_temp', 85),
                    'severity': 'critical' if gpu.temperature_celsius >= 90 else 'high',
                    'message': f'GPU {gpu.index} ({gpu.name}) temperature at {gpu.temperature_celsius}°C',
                    'gpu_index': gpu.index,
                })

            # Memory threshold
            if gpu.memory_percent >= thresholds.get('gpu_memory', 95):
                alerts.append({
                    'metric': 'gpu_memory',
                    'value': gpu.memory_percent,
                    'threshold': thresholds.get('gpu_memory', 95),
                    'severity': 'critical' if gpu.memory_percent >= 98 else 'high',
                    'message': f'GPU {gpu.index} ({gpu.name}) memory at {gpu.memory_percent:.1f}%',
                    'gpu_index': gpu.index,
                })

        return alerts
