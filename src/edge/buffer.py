"""
Local Buffer System.

SQLite-based buffer for storing metrics/alerts when the central server
is unreachable. Ensures no data loss during network outages.
"""

import asyncio
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Generator
import threading


@dataclass
class BufferedItem:
    """An item stored in the buffer."""
    id: int
    data: str
    priority: int
    created_at: float
    retry_count: int = 0


class MetricBuffer:
    """
    SQLite-based buffer for metrics, alerts, and logs.

    Features:
    - Persists data to disk during network outages
    - Priority-based retrieval (critical items first)
    - Automatic cleanup of old data
    - Thread-safe operations
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS buffer (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data TEXT NOT NULL,
        priority INTEGER DEFAULT 2,
        created_at REAL NOT NULL,
        retry_count INTEGER DEFAULT 0,
        last_retry REAL
    );

    CREATE INDEX IF NOT EXISTS idx_priority_created
    ON buffer(priority, created_at);

    CREATE TABLE IF NOT EXISTS metadata (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """

    def __init__(
        self,
        path: str = "/var/lib/sidra-agent/buffer.db",
        max_size_mb: int = 100,
        retention_hours: int = 24,
    ):
        """Initialize the buffer."""
        self.path = path
        self.max_size_mb = max_size_mb
        self.retention_hours = retention_hours

        self._lock = threading.Lock()
        self._conn = None

        # Ensure directory exists
        Path(path).parent.mkdir(parents=True, exist_ok=True)

        self._init_db()

    def _init_db(self):
        """Initialize the database."""
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        if self._conn is None:
            self._init_db()
        return self._conn

    def add(self, data: dict, priority: int = 2) -> int:
        """
        Add an item to the buffer.

        Args:
            data: Dictionary to store (will be JSON serialized)
            priority: 0=critical, 1=high, 2=normal, 3=low

        Returns:
            ID of the inserted item
        """
        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute(
                """
                INSERT INTO buffer (data, priority, created_at)
                VALUES (?, ?, ?)
                """,
                (json.dumps(data), priority, time.time())
            )
            conn.commit()

            # Cleanup if needed
            self._cleanup_if_needed()

            return cursor.lastrowid

    def get_batch(self, limit: int = 100) -> list[BufferedItem]:
        """
        Get a batch of items, prioritized by urgency.

        Returns items in order: critical first, then by age.
        """
        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute(
                """
                SELECT id, data, priority, created_at, retry_count
                FROM buffer
                ORDER BY priority ASC, created_at ASC
                LIMIT ?
                """,
                (limit,)
            )

            items = []
            for row in cursor.fetchall():
                items.append(BufferedItem(
                    id=row[0],
                    data=row[1],
                    priority=row[2],
                    created_at=row[3],
                    retry_count=row[4],
                ))

            return items

    def remove(self, item_ids: list[int]):
        """Remove items that have been successfully sent."""
        if not item_ids:
            return

        with self._lock:
            conn = self._get_conn()
            placeholders = ",".join("?" * len(item_ids))
            conn.execute(
                f"DELETE FROM buffer WHERE id IN ({placeholders})",
                item_ids
            )
            conn.commit()

    def mark_retry(self, item_id: int):
        """Mark an item for retry (increment retry count)."""
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                UPDATE buffer
                SET retry_count = retry_count + 1, last_retry = ?
                WHERE id = ?
                """,
                (time.time(), item_id)
            )
            conn.commit()

    def count(self) -> int:
        """Get the number of items in the buffer."""
        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute("SELECT COUNT(*) FROM buffer")
            return cursor.fetchone()[0]

    def size_bytes(self) -> int:
        """Get the size of the buffer in bytes."""
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def is_full(self) -> bool:
        """Check if the buffer is full."""
        return self.size_bytes() >= self.max_size_mb * 1024 * 1024

    def _cleanup_if_needed(self):
        """Clean up old data if buffer is getting full."""
        if not self.is_full():
            return

        conn = self._get_conn()

        # Delete items older than retention period
        cutoff = time.time() - (self.retention_hours * 3600)
        conn.execute(
            "DELETE FROM buffer WHERE created_at < ?",
            (cutoff,)
        )

        # If still too full, delete low priority items
        if self.is_full():
            conn.execute(
                """
                DELETE FROM buffer
                WHERE id IN (
                    SELECT id FROM buffer
                    WHERE priority >= 2
                    ORDER BY created_at ASC
                    LIMIT 1000
                )
                """
            )

        conn.commit()

        # Vacuum to reclaim space
        conn.execute("VACUUM")

    def clear(self):
        """Clear all items from the buffer."""
        with self._lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM buffer")
            conn.commit()
            conn.execute("VACUUM")

    def get_stats(self) -> dict:
        """Get buffer statistics."""
        with self._lock:
            conn = self._get_conn()

            # Total count
            cursor = conn.execute("SELECT COUNT(*) FROM buffer")
            total = cursor.fetchone()[0]

            # Count by priority
            cursor = conn.execute(
                """
                SELECT priority, COUNT(*)
                FROM buffer
                GROUP BY priority
                """
            )
            by_priority = {row[0]: row[1] for row in cursor.fetchall()}

            # Oldest item
            cursor = conn.execute(
                "SELECT MIN(created_at) FROM buffer"
            )
            oldest = cursor.fetchone()[0]

            return {
                'total_items': total,
                'by_priority': by_priority,
                'size_bytes': self.size_bytes(),
                'size_mb': self.size_bytes() / (1024 * 1024),
                'max_size_mb': self.max_size_mb,
                'oldest_item_age': time.time() - oldest if oldest else 0,
                'is_full': self.is_full(),
            }

    def close(self):
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class AsyncMetricBuffer:
    """Async wrapper for MetricBuffer."""

    def __init__(self, *args, **kwargs):
        """Initialize the async buffer."""
        self._buffer = MetricBuffer(*args, **kwargs)
        self._executor = None

    async def add(self, data: dict, priority: int = 2) -> int:
        """Add an item to the buffer."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            self._buffer.add,
            data,
            priority
        )

    async def get_batch(self, limit: int = 100) -> list[BufferedItem]:
        """Get a batch of items."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            self._buffer.get_batch,
            limit
        )

    async def remove(self, item_ids: list[int]):
        """Remove items."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            self._executor,
            self._buffer.remove,
            item_ids
        )

    async def mark_retry(self, item_id: int):
        """Mark item for retry."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            self._executor,
            self._buffer.mark_retry,
            item_id
        )

    async def count(self) -> int:
        """Get item count."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            self._buffer.count
        )

    async def get_stats(self) -> dict:
        """Get buffer statistics."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            self._buffer.get_stats
        )

    def close(self):
        """Close the buffer."""
        self._buffer.close()
