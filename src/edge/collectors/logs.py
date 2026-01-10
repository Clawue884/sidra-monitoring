"""
Log Collector.

Collects and filters logs from system and Docker containers.
"""

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Optional, Generator
from pathlib import Path
import re


@dataclass
class LogEntry:
    """A single log entry."""
    timestamp: float
    source: str  # file path or container name
    level: str  # info, warning, error, critical
    message: str
    host: str = ""
    container: Optional[str] = None
    service: Optional[str] = None


@dataclass
class LogBatch:
    """A batch of log entries."""
    timestamp: float
    hostname: str
    entries: list[LogEntry] = field(default_factory=list)
    total_lines_processed: int = 0
    errors_count: int = 0
    warnings_count: int = 0


class LogCollector:
    """Collects logs from files and Docker containers."""

    # Patterns to detect log levels
    LEVEL_PATTERNS = {
        'critical': re.compile(r'\b(CRITICAL|FATAL|PANIC|EMERGENCY)\b', re.IGNORECASE),
        'error': re.compile(r'\b(ERROR|ERR|FAIL|FAILED|EXCEPTION)\b', re.IGNORECASE),
        'warning': re.compile(r'\b(WARNING|WARN|ALERT)\b', re.IGNORECASE),
        'info': re.compile(r'\b(INFO|NOTICE|DEBUG)\b', re.IGNORECASE),
    }

    # Patterns to filter out noise
    NOISE_PATTERNS = [
        re.compile(r'^\s*$'),  # Empty lines
        re.compile(r'^#'),  # Comments
        re.compile(r'healthcheck', re.IGNORECASE),  # Health checks
        re.compile(r'GET /health', re.IGNORECASE),
        re.compile(r'HTTP/1\.[01]" 200'),  # Successful HTTP requests
    ]

    # Important patterns to always capture
    IMPORTANT_PATTERNS = [
        re.compile(r'out of memory', re.IGNORECASE),
        re.compile(r'killed process', re.IGNORECASE),
        re.compile(r'segfault', re.IGNORECASE),
        re.compile(r'kernel panic', re.IGNORECASE),
        re.compile(r'disk full', re.IGNORECASE),
        re.compile(r'connection refused', re.IGNORECASE),
        re.compile(r'permission denied', re.IGNORECASE),
        re.compile(r'authentication fail', re.IGNORECASE),
        re.compile(r'ssl.*error', re.IGNORECASE),
        re.compile(r'certificate.*expir', re.IGNORECASE),
    ]

    def __init__(self, config=None):
        """Initialize the log collector."""
        self.config = config
        self._file_positions = {}  # Track file read positions
        self._last_collect_time = 0

    async def collect(self, max_lines: int = 1000) -> LogBatch:
        """Collect logs from all configured sources."""
        import socket

        entries = []
        total_lines = 0
        errors = 0
        warnings = 0

        loop = asyncio.get_event_loop()

        # Collect from file sources
        if self.config and self.config.paths:
            for path in self.config.paths:
                if os.path.exists(path):
                    file_entries, lines = await loop.run_in_executor(
                        None,
                        self._collect_from_file,
                        path,
                        max_lines // len(self.config.paths)
                    )
                    entries.extend(file_entries)
                    total_lines += lines

        # Collect from Docker if enabled
        if self.config and self.config.docker_logs:
            docker_entries = await loop.run_in_executor(
                None,
                self._collect_docker_logs,
                max_lines // 2
            )
            entries.extend(docker_entries)

        # Count errors and warnings
        for entry in entries:
            if entry.level == 'error' or entry.level == 'critical':
                errors += 1
            elif entry.level == 'warning':
                warnings += 1

        self._last_collect_time = time.time()

        return LogBatch(
            timestamp=time.time(),
            hostname=socket.gethostname(),
            entries=entries,
            total_lines_processed=total_lines,
            errors_count=errors,
            warnings_count=warnings,
        )

    def _collect_from_file(self, path: str, max_lines: int) -> tuple[list[LogEntry], int]:
        """Collect new log entries from a file."""
        entries = []
        lines_read = 0

        try:
            # Get file size
            file_size = os.path.getsize(path)

            # Get last read position
            last_pos = self._file_positions.get(path, 0)

            # If file was truncated (rotated), start from beginning
            if last_pos > file_size:
                last_pos = 0

            with open(path, 'r', errors='ignore') as f:
                f.seek(last_pos)

                for line in f:
                    lines_read += 1

                    if len(entries) >= max_lines:
                        break

                    # Filter noise
                    if self._is_noise(line):
                        continue

                    # Detect log level
                    level = self._detect_level(line)

                    # Only keep errors, warnings, and important logs
                    if level in ('error', 'critical', 'warning') or self._is_important(line):
                        entries.append(LogEntry(
                            timestamp=time.time(),
                            source=path,
                            level=level,
                            message=line.strip()[:500],  # Limit message length
                            service=self._extract_service(path),
                        ))

                # Update position
                self._file_positions[path] = f.tell()

        except Exception as e:
            entries.append(LogEntry(
                timestamp=time.time(),
                source=path,
                level='error',
                message=f"Failed to read log file: {e}",
            ))

        return entries, lines_read

    def _collect_docker_logs(self, max_lines: int) -> list[LogEntry]:
        """Collect recent logs from Docker containers."""
        import subprocess

        entries = []

        try:
            # Get list of running containers
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}"],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return entries

            containers = result.stdout.strip().split("\n")
            lines_per_container = max(10, max_lines // max(len(containers), 1))

            for container in containers[:20]:  # Limit to 20 containers
                if not container:
                    continue

                try:
                    # Get recent logs (last 1 minute, limited lines)
                    log_result = subprocess.run(
                        [
                            "docker", "logs", container,
                            "--since", "1m",
                            "--tail", str(lines_per_container),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )

                    # Check both stdout and stderr
                    for line in (log_result.stdout + log_result.stderr).split("\n"):
                        if not line or self._is_noise(line):
                            continue

                        level = self._detect_level(line)

                        if level in ('error', 'critical', 'warning') or self._is_important(line):
                            entries.append(LogEntry(
                                timestamp=time.time(),
                                source=f"docker://{container}",
                                level=level,
                                message=line.strip()[:500],
                                container=container,
                            ))

                except subprocess.TimeoutExpired:
                    continue
                except Exception:
                    continue

        except Exception:
            pass

        return entries

    def _detect_level(self, line: str) -> str:
        """Detect log level from line content."""
        for level, pattern in self.LEVEL_PATTERNS.items():
            if pattern.search(line):
                return level
        return 'info'

    def _is_noise(self, line: str) -> bool:
        """Check if line is noise that should be filtered."""
        for pattern in self.NOISE_PATTERNS:
            if pattern.search(line):
                return True
        return False

    def _is_important(self, line: str) -> bool:
        """Check if line contains important information."""
        for pattern in self.IMPORTANT_PATTERNS:
            if pattern.search(line):
                return True
        return False

    def _extract_service(self, path: str) -> Optional[str]:
        """Extract service name from log path."""
        # /var/log/nginx/error.log -> nginx
        # /var/log/postgresql/postgresql-14-main.log -> postgresql
        parts = Path(path).parts
        if 'log' in parts:
            idx = parts.index('log')
            if idx + 1 < len(parts):
                return parts[idx + 1]
        return None

    def get_summary(self, batch: LogBatch) -> dict:
        """Get a summary of the log batch for LLM analysis."""
        # Group by source
        by_source = {}
        for entry in batch.entries:
            source = entry.source
            if source not in by_source:
                by_source[source] = {'errors': [], 'warnings': [], 'critical': []}

            if entry.level == 'critical':
                by_source[source]['critical'].append(entry.message)
            elif entry.level == 'error':
                by_source[source]['errors'].append(entry.message)
            elif entry.level == 'warning':
                by_source[source]['warnings'].append(entry.message)

        return {
            'timestamp': batch.timestamp,
            'hostname': batch.hostname,
            'total_entries': len(batch.entries),
            'errors_count': batch.errors_count,
            'warnings_count': batch.warnings_count,
            'by_source': by_source,
        }
