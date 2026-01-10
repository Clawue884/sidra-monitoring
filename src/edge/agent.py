"""
Sidra Edge Agent - Main Daemon.

Lightweight monitoring agent that runs on each server, collecting
metrics, logs, and health data, then sending to the Central Brain.
"""

import asyncio
import logging
import signal
import socket
import sys
import time
from typing import Optional

from .config import EdgeConfig
from .collectors import (
    SystemCollector,
    GPUCollector,
    DockerCollector,
    LogCollector,
    ServiceCollector,
)
from .batching import BatchAggregator, MetricPoint, Alert, Priority, BatchScheduler
from .buffer import AsyncMetricBuffer
from .sender import CentralSender

logger = logging.getLogger(__name__)


class EdgeAgent:
    """
    Main Edge Agent daemon.

    Orchestrates all collectors and sends data to the Central Brain.
    """

    def __init__(self, config: Optional[EdgeConfig] = None):
        """Initialize the Edge Agent."""
        self.config = config or EdgeConfig.from_env()
        self.hostname = socket.gethostname()

        # Setup logging
        self._setup_logging()

        # Initialize collectors
        self.system_collector = SystemCollector(self.config.system)
        self.gpu_collector = GPUCollector(self.config.gpu)
        self.docker_collector = DockerCollector(self.config.docker)
        self.log_collector = LogCollector(self.config.logs)
        self.service_collector = ServiceCollector(self.config.services)

        # Initialize buffer
        self.buffer = AsyncMetricBuffer(
            path=self.config.buffer.path,
            max_size_mb=self.config.buffer.max_size_mb,
            retention_hours=self.config.buffer.retention_hours,
        ) if self.config.buffer.enabled else None

        # Initialize sender
        self.sender = CentralSender(
            central_url=self.config.central_url,
            api_key=self.config.api_key,
            timeout=self.config.central_timeout,
            retry_count=self.config.central_retry_count,
            retry_delay=self.config.central_retry_delay,
            buffer=self.buffer,
        )

        # Initialize batch aggregator
        self.aggregator = BatchAggregator(
            batch_interval=self.config.batching.batch_interval,
            max_batch_size=self.config.batching.max_batch_size,
            max_batch_age=self.config.batching.max_batch_age,
        )
        self.aggregator.set_host(self.hostname)

        # State
        self._running = False
        self._tasks = []

    def _setup_logging(self):
        """Setup logging configuration."""
        level = getattr(logging, self.config.log_level.upper(), logging.INFO)

        handlers = [logging.StreamHandler()]

        if self.config.log_file:
            from pathlib import Path
            Path(self.config.log_file).parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(self.config.log_file))

        logging.basicConfig(
            level=level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=handlers,
        )

    async def start(self):
        """Start the Edge Agent."""
        logger.info(f"Starting Sidra Edge Agent on {self.hostname}")
        logger.info(f"Central Brain URL: {self.config.central_url}")

        self._running = True

        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        # Check central health
        healthy = await self.sender.check_health()
        if not healthy:
            logger.warning("Central Brain not reachable, will buffer data")

        # Start collection tasks
        self._tasks = [
            asyncio.create_task(self._collect_system_metrics()),
            asyncio.create_task(self._collect_gpu_metrics()),
            asyncio.create_task(self._collect_docker_metrics()),
            asyncio.create_task(self._collect_logs()),
            asyncio.create_task(self._collect_services()),
            asyncio.create_task(self._batch_sender()),
            asyncio.create_task(self._buffer_flusher()),
            asyncio.create_task(self._health_reporter()),
        ]

        logger.info("Edge Agent started successfully")

        # Wait for tasks
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.info("Edge Agent tasks cancelled")

    async def stop(self):
        """Stop the Edge Agent."""
        logger.info("Stopping Edge Agent...")
        self._running = False

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()

        # Flush remaining data
        batch = await self.aggregator.flush()
        if batch:
            await self.sender.send_batch(batch)

        # Close connections
        await self.sender.close()
        if self.buffer:
            self.buffer.close()

        logger.info("Edge Agent stopped")

    async def _collect_system_metrics(self):
        """Collect system metrics periodically."""
        interval = self.config.system.interval

        while self._running:
            try:
                metrics = await self.system_collector.collect()

                # Convert to metric points
                await self._process_system_metrics(metrics)

                # Check thresholds and generate alerts
                alerts = self.system_collector.check_thresholds(
                    metrics,
                    self.config.priority.critical_thresholds
                )
                for alert_data in alerts:
                    await self._process_alert(alert_data)

            except Exception as e:
                logger.error(f"System metrics collection error: {e}")

            await asyncio.sleep(interval)

    async def _collect_gpu_metrics(self):
        """Collect GPU metrics periodically."""
        if not self.gpu_collector.available:
            logger.info("No GPU detected, skipping GPU collection")
            return

        interval = self.config.gpu.interval

        while self._running:
            try:
                metrics = await self.gpu_collector.collect()

                if metrics.available:
                    await self._process_gpu_metrics(metrics)

                    # Check thresholds
                    alerts = self.gpu_collector.check_thresholds(
                        metrics,
                        self.config.priority.critical_thresholds
                    )
                    for alert_data in alerts:
                        await self._process_alert(alert_data)

            except Exception as e:
                logger.error(f"GPU metrics collection error: {e}")

            await asyncio.sleep(interval)

    async def _collect_docker_metrics(self):
        """Collect Docker metrics periodically."""
        if not self.docker_collector.available:
            logger.info("Docker not available, skipping Docker collection")
            return

        interval = self.config.docker.interval

        while self._running:
            try:
                metrics = await self.docker_collector.collect()

                if metrics.available:
                    await self._process_docker_metrics(metrics)

                    # Check for unhealthy containers
                    alerts = self.docker_collector.check_thresholds(metrics)
                    for alert_data in alerts:
                        await self._process_alert(alert_data)

            except Exception as e:
                logger.error(f"Docker metrics collection error: {e}")

            await asyncio.sleep(interval)

    async def _collect_logs(self):
        """Collect logs periodically."""
        if not self.config.logs.enabled:
            return

        interval = self.config.logs.interval

        while self._running:
            try:
                log_batch = await self.log_collector.collect(
                    max_lines=self.config.logs.max_lines_per_batch
                )

                if log_batch.entries:
                    # Send critical logs immediately
                    critical_logs = [
                        {'level': e.level, 'message': e.message, 'source': e.source}
                        for e in log_batch.entries
                        if e.level in ('critical', 'error')
                    ]

                    if critical_logs:
                        batch = await self.aggregator.add_logs(critical_logs)
                        if batch:
                            await self.sender.send_batch(batch)

                    # Batch other logs
                    other_logs = [
                        {'level': e.level, 'message': e.message, 'source': e.source}
                        for e in log_batch.entries
                        if e.level not in ('critical', 'error')
                    ]

                    if other_logs:
                        await self.aggregator.add_logs(other_logs)

            except Exception as e:
                logger.error(f"Log collection error: {e}")

            await asyncio.sleep(interval)

    async def _collect_services(self):
        """Collect service status periodically."""
        if not self.config.services.enabled:
            return

        interval = self.config.services.interval

        while self._running:
            try:
                metrics = await self.service_collector.collect()

                await self._process_service_metrics(metrics)

                # Check for failed services
                alerts = self.service_collector.check_thresholds(metrics)
                for alert_data in alerts:
                    await self._process_alert(alert_data)

            except Exception as e:
                logger.error(f"Service collection error: {e}")

            await asyncio.sleep(interval)

    async def _batch_sender(self):
        """Periodically flush and send batched data."""
        interval = self.config.batching.batch_interval

        while self._running:
            await asyncio.sleep(interval)

            try:
                batch = await self.aggregator.flush()
                if batch:
                    result = await self.sender.send_batch(batch)
                    if result.success:
                        logger.debug(f"Sent batch: {len(batch.metrics)} metrics, {len(batch.alerts)} alerts")
                    else:
                        logger.warning(f"Failed to send batch: {result.error}")

            except Exception as e:
                logger.error(f"Batch sender error: {e}")

    async def _buffer_flusher(self):
        """Periodically try to flush buffered data."""
        if not self.buffer:
            return

        # Try every 5 minutes
        interval = 300

        while self._running:
            await asyncio.sleep(interval)

            try:
                stats = await self.buffer.get_stats()
                if stats['total_items'] > 0:
                    sent = await self.sender.flush_buffer()
                    if sent > 0:
                        logger.info(f"Flushed {sent} buffered items")

            except Exception as e:
                logger.error(f"Buffer flusher error: {e}")

    async def _health_reporter(self):
        """Report agent health periodically."""
        interval = 60  # Every minute

        while self._running:
            try:
                # Create health metric
                health_metric = MetricPoint(
                    name='sidra_agent_health',
                    value=1,
                    timestamp=time.time(),
                    labels={'host': self.hostname, 'version': self.config.agent_version},
                    priority=Priority.LOW,
                )
                await self.aggregator.add_metric(health_metric)

                # Report buffer stats if available
                if self.buffer:
                    stats = await self.buffer.get_stats()
                    buffer_metric = MetricPoint(
                        name='sidra_agent_buffer_items',
                        value=stats['total_items'],
                        timestamp=time.time(),
                        labels={'host': self.hostname},
                        priority=Priority.LOW,
                    )
                    await self.aggregator.add_metric(buffer_metric)

            except Exception as e:
                logger.error(f"Health reporter error: {e}")

            await asyncio.sleep(interval)

    async def _process_system_metrics(self, metrics):
        """Process and batch system metrics."""
        timestamp = metrics.timestamp

        # CPU
        await self.aggregator.add_metric(MetricPoint(
            name='sidra_cpu_usage_percent',
            value=metrics.cpu.usage_percent,
            timestamp=timestamp,
            labels={'host': self.hostname},
        ))

        await self.aggregator.add_metric(MetricPoint(
            name='sidra_load_1m',
            value=metrics.cpu.load_1m,
            timestamp=timestamp,
            labels={'host': self.hostname},
        ))

        # Memory
        await self.aggregator.add_metric(MetricPoint(
            name='sidra_memory_usage_percent',
            value=metrics.memory.usage_percent,
            timestamp=timestamp,
            labels={'host': self.hostname},
        ))

        # Disks
        for disk in metrics.disks:
            await self.aggregator.add_metric(MetricPoint(
                name='sidra_disk_usage_percent',
                value=disk.usage_percent,
                timestamp=timestamp,
                labels={'host': self.hostname, 'path': disk.path},
            ))

    async def _process_gpu_metrics(self, metrics):
        """Process and batch GPU metrics."""
        timestamp = metrics.timestamp

        for gpu in metrics.gpus:
            labels = {'host': self.hostname, 'gpu': str(gpu.index), 'name': gpu.name}

            await self.aggregator.add_metric(MetricPoint(
                name='sidra_gpu_utilization_percent',
                value=gpu.utilization_percent,
                timestamp=timestamp,
                labels=labels,
            ))

            await self.aggregator.add_metric(MetricPoint(
                name='sidra_gpu_memory_percent',
                value=gpu.memory_percent,
                timestamp=timestamp,
                labels=labels,
            ))

            await self.aggregator.add_metric(MetricPoint(
                name='sidra_gpu_temperature_celsius',
                value=gpu.temperature_celsius,
                timestamp=timestamp,
                labels=labels,
            ))

    async def _process_docker_metrics(self, metrics):
        """Process and batch Docker metrics."""
        timestamp = metrics.timestamp

        await self.aggregator.add_metric(MetricPoint(
            name='sidra_docker_containers_running',
            value=metrics.containers_running,
            timestamp=timestamp,
            labels={'host': self.hostname},
        ))

        for container in metrics.containers[:20]:  # Limit to top 20
            if container.state == 'running':
                labels = {'host': self.hostname, 'container': container.name}

                await self.aggregator.add_metric(MetricPoint(
                    name='sidra_container_cpu_percent',
                    value=container.cpu_percent,
                    timestamp=timestamp,
                    labels=labels,
                ))

                await self.aggregator.add_metric(MetricPoint(
                    name='sidra_container_memory_percent',
                    value=container.memory_percent,
                    timestamp=timestamp,
                    labels=labels,
                ))

    async def _process_service_metrics(self, metrics):
        """Process and batch service metrics."""
        timestamp = metrics.timestamp

        await self.aggregator.add_metric(MetricPoint(
            name='sidra_services_failed_total',
            value=len(metrics.failed_services),
            timestamp=timestamp,
            labels={'host': self.hostname},
        ))

    async def _process_alert(self, alert_data: dict):
        """Process an alert from any collector."""
        alert = Alert(
            metric=alert_data['metric'],
            value=alert_data['value'],
            threshold=alert_data.get('threshold'),
            severity=alert_data['severity'],
            message=alert_data['message'],
            timestamp=time.time(),
            host=self.hostname,
            labels=alert_data.get('labels', {}),
        )

        batch = await self.aggregator.add_alert(alert)

        # If critical, send immediately
        if batch and alert.severity in ('critical', 'high'):
            result = await self.sender.send_batch(batch)
            if result.success:
                logger.info(f"Sent alert: {alert.message}")
            else:
                logger.warning(f"Failed to send alert: {result.error}")


def run_agent(config_path: Optional[str] = None):
    """Run the Edge Agent."""
    if config_path:
        config = EdgeConfig.from_yaml(config_path)
    else:
        config = EdgeConfig.from_env()

    agent = EdgeAgent(config)

    try:
        asyncio.run(agent.start())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    run_agent(config_path)
