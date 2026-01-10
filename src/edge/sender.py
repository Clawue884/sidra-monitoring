"""
Central Sender.

Sends batched metrics, alerts, and logs to the Central Brain.
Handles retries, authentication, and error recovery.
"""

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Optional
import aiohttp
import logging

from .batching import Batch, Priority
from .buffer import AsyncMetricBuffer

logger = logging.getLogger(__name__)


@dataclass
class SendResult:
    """Result of a send operation."""
    success: bool
    status_code: int = 0
    error: Optional[str] = None
    retry_after: Optional[int] = None


class CentralSender:
    """
    Sends data to the Central Brain.

    Features:
    - Async HTTP client with connection pooling
    - Automatic retries with exponential backoff
    - Buffer integration for offline resilience
    - Health check before sending
    """

    def __init__(
        self,
        central_url: str,
        api_key: Optional[str] = None,
        timeout: int = 30,
        retry_count: int = 3,
        retry_delay: int = 5,
        buffer: Optional[AsyncMetricBuffer] = None,
    ):
        """Initialize the sender."""
        self.central_url = central_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.retry_count = retry_count
        self.retry_delay = retry_delay
        self.buffer = buffer

        self._session: Optional[aiohttp.ClientSession] = None
        self._healthy = False
        self._last_health_check = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def _get_headers(self) -> dict:
        """Get request headers."""
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'SidraEdgeAgent/1.0',
        }
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        return headers

    async def check_health(self) -> bool:
        """Check if the central server is healthy."""
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.central_url}/health",
                headers=self._get_headers(),
            ) as response:
                self._healthy = response.status == 200
                self._last_health_check = time.time()
                return self._healthy
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            self._healthy = False
            return False

    async def send_batch(self, batch: Batch) -> SendResult:
        """
        Send a batch to the central server.

        If sending fails, the batch is stored in the buffer.
        """
        # Serialize batch
        payload = self._serialize_batch(batch)

        # Determine endpoint based on content
        if batch.alerts:
            endpoint = "/api/v1/ingest/alerts"
        elif batch.logs:
            endpoint = "/api/v1/ingest/logs"
        else:
            endpoint = "/api/v1/ingest/metrics"

        # Try to send with retries
        result = await self._send_with_retry(endpoint, payload, batch.priority)

        # If failed and we have a buffer, store for later
        if not result.success and self.buffer:
            priority = 0 if batch.priority == Priority.CRITICAL else 2
            await self.buffer.add({
                'endpoint': endpoint,
                'payload': payload,
                'timestamp': batch.timestamp,
            }, priority=priority)
            logger.info(f"Batch buffered for later delivery")

        return result

    async def send_metrics(self, metrics: list[dict]) -> SendResult:
        """Send metrics directly (without batching)."""
        payload = {
            'timestamp': time.time(),
            'metrics': metrics,
        }
        return await self._send_with_retry(
            "/api/v1/ingest/metrics",
            json.dumps(payload),
            Priority.NORMAL
        )

    async def send_alert(self, alert: dict) -> SendResult:
        """Send a single alert immediately."""
        payload = {
            'timestamp': time.time(),
            'alert': alert,
        }
        return await self._send_with_retry(
            "/api/v1/ingest/alerts",
            json.dumps(payload),
            Priority.CRITICAL
        )

    async def send_logs(self, logs: list[dict]) -> SendResult:
        """Send logs."""
        payload = {
            'timestamp': time.time(),
            'logs': logs,
        }
        return await self._send_with_retry(
            "/api/v1/ingest/logs",
            json.dumps(payload),
            Priority.NORMAL
        )

    async def flush_buffer(self) -> int:
        """
        Try to send all buffered items.

        Returns the number of successfully sent items.
        """
        if not self.buffer:
            return 0

        # Check health first
        if not await self.check_health():
            logger.warning("Central server unhealthy, skipping buffer flush")
            return 0

        sent_count = 0
        items = await self.buffer.get_batch(limit=100)

        for item in items:
            try:
                data = json.loads(item.data)
                endpoint = data.get('endpoint', '/api/v1/ingest/metrics')
                payload = data.get('payload', '{}')

                result = await self._send_with_retry(
                    endpoint,
                    payload if isinstance(payload, str) else json.dumps(payload),
                    Priority.NORMAL,
                    max_retries=1  # Single retry for buffered items
                )

                if result.success:
                    await self.buffer.remove([item.id])
                    sent_count += 1
                else:
                    await self.buffer.mark_retry(item.id)

            except Exception as e:
                logger.error(f"Failed to send buffered item {item.id}: {e}")
                await self.buffer.mark_retry(item.id)

        return sent_count

    async def _send_with_retry(
        self,
        endpoint: str,
        payload: str,
        priority: Priority,
        max_retries: Optional[int] = None,
    ) -> SendResult:
        """Send with exponential backoff retry."""
        retries = max_retries if max_retries is not None else self.retry_count
        last_error = None

        for attempt in range(retries + 1):
            try:
                result = await self._send_once(endpoint, payload)

                if result.success:
                    return result

                # Handle rate limiting
                if result.status_code == 429 and result.retry_after:
                    await asyncio.sleep(result.retry_after)
                    continue

                # Don't retry client errors (except rate limit)
                if 400 <= result.status_code < 500:
                    return result

                last_error = result.error

            except Exception as e:
                last_error = str(e)
                logger.warning(f"Send attempt {attempt + 1} failed: {e}")

            # Exponential backoff
            if attempt < retries:
                delay = self.retry_delay * (2 ** attempt)
                await asyncio.sleep(delay)

        return SendResult(
            success=False,
            error=f"All {retries + 1} attempts failed: {last_error}",
        )

    async def _send_once(self, endpoint: str, payload: str) -> SendResult:
        """Send a single request."""
        try:
            session = await self._get_session()
            url = f"{self.central_url}{endpoint}"

            async with session.post(
                url,
                data=payload,
                headers=self._get_headers(),
            ) as response:
                if response.status in (200, 201, 202, 204):
                    return SendResult(success=True, status_code=response.status)

                error_text = await response.text()
                retry_after = None

                if response.status == 429:
                    retry_after = int(response.headers.get('Retry-After', 60))

                return SendResult(
                    success=False,
                    status_code=response.status,
                    error=error_text[:500],
                    retry_after=retry_after,
                )

        except asyncio.TimeoutError:
            return SendResult(success=False, error="Request timeout")
        except aiohttp.ClientError as e:
            return SendResult(success=False, error=str(e))

    def _serialize_batch(self, batch: Batch) -> str:
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
                    'host': a.host,
                    'labels': a.labels,
                }
                for a in batch.alerts
            ],
            'logs': batch.logs,
        })

    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
