"""
Smart Batching System.

Aggregates metrics intelligently to reduce network load while ensuring
critical alerts are sent immediately.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from enum import Enum
import json


class Priority(Enum):
    """Alert/metric priority levels."""
    CRITICAL = 0  # Send immediately
    HIGH = 1  # Send within 1 minute
    NORMAL = 2  # Batch (default interval)
    LOW = 3  # Send with daily summary


@dataclass
class MetricPoint:
    """A single metric point."""
    name: str
    value: float
    timestamp: float
    labels: dict = field(default_factory=dict)
    priority: Priority = Priority.NORMAL


@dataclass
class Alert:
    """An alert to be sent."""
    metric: str
    value: Any
    threshold: Any
    severity: str
    message: str
    timestamp: float
    host: str
    labels: dict = field(default_factory=dict)


@dataclass
class Batch:
    """A batch of metrics and alerts ready to send."""
    metrics: list[MetricPoint] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    logs: list[dict] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    host: str = ""
    priority: Priority = Priority.NORMAL


class BatchAggregator:
    """
    Smart batch aggregator that:
    - Sends critical alerts immediately
    - Batches normal metrics based on interval
    - Compresses/deduplicates where possible
    - Handles backpressure gracefully
    """

    def __init__(
        self,
        batch_interval: int = 30,
        max_batch_size: int = 100,
        max_batch_age: int = 60,
        on_batch_ready: Optional[Callable] = None,
    ):
        """Initialize the batch aggregator."""
        self.batch_interval = batch_interval
        self.max_batch_size = max_batch_size
        self.max_batch_age = max_batch_age
        self.on_batch_ready = on_batch_ready

        self._current_batch = Batch()
        self._batch_start_time = time.time()
        self._lock = asyncio.Lock()

        # Deduplication tracking
        self._last_values = {}  # metric_name -> last_value
        self._alert_cooldowns = {}  # alert_key -> last_sent_time

    async def add_metric(self, metric: MetricPoint) -> Optional[Batch]:
        """
        Add a metric to the batch.
        Returns a batch immediately if critical, otherwise batches.
        """
        async with self._lock:
            # Critical metrics bypass batching
            if metric.priority == Priority.CRITICAL:
                return await self._create_immediate_batch([metric], [])

            # Deduplicate - skip if value hasn't changed significantly
            if self._should_skip_metric(metric):
                return None

            self._current_batch.metrics.append(metric)
            self._last_values[metric.name] = metric.value

            # Check if batch is ready
            return await self._check_batch_ready()

    async def add_alert(self, alert: Alert) -> Optional[Batch]:
        """
        Add an alert to the batch.
        Critical alerts are sent immediately.
        """
        async with self._lock:
            # Check cooldown to avoid alert spam
            alert_key = f"{alert.metric}:{alert.host}"
            if self._in_cooldown(alert_key, alert.severity):
                return None

            self._alert_cooldowns[alert_key] = time.time()

            # Critical alerts bypass batching
            if alert.severity in ('critical', 'high'):
                return await self._create_immediate_batch([], [alert])

            self._current_batch.alerts.append(alert)
            return await self._check_batch_ready()

    async def add_logs(self, logs: list[dict]) -> Optional[Batch]:
        """Add log entries to the batch."""
        async with self._lock:
            # Check for critical log entries
            critical_logs = [l for l in logs if l.get('level') in ('critical', 'error')]

            if critical_logs:
                # Send critical logs immediately
                batch = Batch(
                    logs=critical_logs,
                    timestamp=time.time(),
                    host=self._current_batch.host,
                    priority=Priority.CRITICAL,
                )
                return batch

            self._current_batch.logs.extend(logs)
            return await self._check_batch_ready()

    async def flush(self) -> Optional[Batch]:
        """Force flush the current batch."""
        async with self._lock:
            if self._is_batch_empty():
                return None

            batch = self._current_batch
            self._reset_batch()
            return batch

    async def _check_batch_ready(self) -> Optional[Batch]:
        """Check if the current batch should be sent."""
        batch_age = time.time() - self._batch_start_time
        batch_size = len(self._current_batch.metrics) + len(self._current_batch.alerts)

        # Send if batch is full or old enough
        if batch_size >= self.max_batch_size or batch_age >= self.max_batch_age:
            batch = self._current_batch
            self._reset_batch()
            return batch

        return None

    async def _create_immediate_batch(
        self,
        metrics: list[MetricPoint],
        alerts: list[Alert]
    ) -> Batch:
        """Create a batch for immediate sending."""
        return Batch(
            metrics=metrics,
            alerts=alerts,
            timestamp=time.time(),
            host=self._current_batch.host,
            priority=Priority.CRITICAL,
        )

    def _should_skip_metric(self, metric: MetricPoint) -> bool:
        """
        Check if metric should be skipped (deduplication).
        Skip if value hasn't changed more than 1% from last value.
        """
        if metric.name not in self._last_values:
            return False

        last_value = self._last_values[metric.name]

        # For percentage metrics, skip if change is < 1%
        if 'percent' in metric.name.lower():
            return abs(metric.value - last_value) < 1.0

        # For other metrics, skip if change is < 1%
        if last_value != 0:
            change_pct = abs((metric.value - last_value) / last_value) * 100
            return change_pct < 1.0

        return False

    def _in_cooldown(self, alert_key: str, severity: str) -> bool:
        """Check if alert is in cooldown period."""
        if alert_key not in self._alert_cooldowns:
            return False

        last_sent = self._alert_cooldowns[alert_key]
        cooldown_seconds = {
            'critical': 60,  # 1 minute
            'high': 300,  # 5 minutes
            'warning': 900,  # 15 minutes
            'normal': 3600,  # 1 hour
        }.get(severity, 300)

        return (time.time() - last_sent) < cooldown_seconds

    def _reset_batch(self):
        """Reset the current batch."""
        self._current_batch = Batch(host=self._current_batch.host)
        self._batch_start_time = time.time()

    def _is_batch_empty(self) -> bool:
        """Check if current batch is empty."""
        return (
            len(self._current_batch.metrics) == 0 and
            len(self._current_batch.alerts) == 0 and
            len(self._current_batch.logs) == 0
        )

    def set_host(self, host: str):
        """Set the host name for batches."""
        self._current_batch.host = host

    def to_json(self, batch: Batch) -> str:
        """Serialize a batch to JSON."""
        return json.dumps({
            'timestamp': batch.timestamp,
            'host': batch.host,
            'priority': batch.priority.name,
            'metrics': [
                {
                    'name': m.name,
                    'value': m.value,
                    'timestamp': m.timestamp,
                    'labels': m.labels,
                }
                for m in batch.metrics
            ],
            'alerts': [
                {
                    'metric': a.metric,
                    'value': a.value,
                    'threshold': a.threshold,
                    'severity': a.severity,
                    'message': a.message,
                    'timestamp': a.timestamp,
                    'labels': a.labels,
                }
                for a in batch.alerts
            ],
            'logs': batch.logs,
        })


class BatchScheduler:
    """Schedules periodic batch flushing."""

    def __init__(self, aggregator: BatchAggregator, send_callback: Callable):
        """Initialize the scheduler."""
        self.aggregator = aggregator
        self.send_callback = send_callback
        self._running = False
        self._task = None

    async def start(self):
        """Start the batch scheduler."""
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        """Stop the batch scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Flush any remaining data
        batch = await self.aggregator.flush()
        if batch:
            await self.send_callback(batch)

    async def _run(self):
        """Main scheduler loop."""
        while self._running:
            await asyncio.sleep(self.aggregator.batch_interval)

            batch = await self.aggregator.flush()
            if batch:
                try:
                    await self.send_callback(batch)
                except Exception as e:
                    # Log error but continue
                    print(f"Failed to send batch: {e}")
