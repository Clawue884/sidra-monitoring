"""SSH connection utilities for remote server access."""

import asyncio
from dataclasses import dataclass, field
from typing import Optional, Any
from pathlib import Path
import asyncssh
import paramiko

from ..config import settings
from .logger import get_logger

logger = get_logger(__name__)


@dataclass
class SSHCredentials:
    """SSH connection credentials."""
    host: str
    port: int = 22
    username: str = "root"
    password: Optional[str] = None
    key_path: Optional[str] = None
    timeout: int = 30


@dataclass
class CommandResult:
    """Result of a remote command execution."""
    stdout: str
    stderr: str
    exit_code: int
    success: bool = field(init=False)

    def __post_init__(self):
        self.success = self.exit_code == 0


class SSHClient:
    """Async SSH client for remote command execution."""

    def __init__(self, credentials: SSHCredentials):
        self.creds = credentials
        self._conn: Optional[asyncssh.SSHClientConnection] = None

    async def connect(self) -> bool:
        """Establish SSH connection."""
        try:
            connect_kwargs = {
                "host": self.creds.host,
                "port": self.creds.port,
                "username": self.creds.username,
                "known_hosts": None,
                "connect_timeout": self.creds.timeout,
            }

            if self.creds.key_path and Path(self.creds.key_path).exists():
                connect_kwargs["client_keys"] = [self.creds.key_path]
            elif self.creds.password:
                connect_kwargs["password"] = self.creds.password

            self._conn = await asyncssh.connect(**connect_kwargs)
            logger.info(f"Connected to {self.creds.host}")
            return True

        except asyncssh.DisconnectError as e:
            logger.error(f"SSH disconnect error for {self.creds.host}: {e}")
            return False
        except asyncssh.PermissionDenied:
            logger.error(f"Permission denied for {self.creds.host}")
            return False
        except Exception as e:
            logger.error(f"SSH connection failed for {self.creds.host}: {e}")
            return False

    async def disconnect(self):
        """Close SSH connection."""
        if self._conn:
            self._conn.close()
            await self._conn.wait_closed()
            self._conn = None
            logger.debug(f"Disconnected from {self.creds.host}")

    async def execute(self, command: str, timeout: int = 60) -> CommandResult:
        """Execute a command on the remote server."""
        if not self._conn:
            if not await self.connect():
                return CommandResult(stdout="", stderr="Connection failed", exit_code=-1)

        try:
            result = await asyncio.wait_for(
                self._conn.run(command, check=False),
                timeout=timeout
            )
            return CommandResult(
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                exit_code=result.exit_status or 0,
            )
        except asyncio.TimeoutError:
            return CommandResult(
                stdout="", stderr=f"Command timed out after {timeout}s", exit_code=-1
            )
        except Exception as e:
            return CommandResult(stdout="", stderr=str(e), exit_code=-1)

    async def execute_script(self, script: str, timeout: int = 300) -> CommandResult:
        """Execute a multi-line script on the remote server."""
        # Create a temporary script and execute it
        script_cmd = f"bash -c '{script}'"
        return await self.execute(script_cmd, timeout)

    async def read_file(self, path: str) -> Optional[str]:
        """Read a file from the remote server."""
        result = await self.execute(f"cat {path}")
        if result.success:
            return result.stdout
        return None

    async def file_exists(self, path: str) -> bool:
        """Check if a file exists on the remote server."""
        result = await self.execute(f"test -e {path} && echo 'exists'")
        return "exists" in result.stdout

    async def get_file_list(self, path: str, pattern: str = "*") -> list[str]:
        """Get list of files in a directory."""
        result = await self.execute(f"ls -1 {path}/{pattern} 2>/dev/null")
        if result.success:
            return [f.strip() for f in result.stdout.split("\n") if f.strip()]
        return []

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()


class SSHConnectionPool:
    """Pool of SSH connections for multiple servers."""

    def __init__(self, max_connections: int = 10):
        self.max_connections = max_connections
        self._connections: dict[str, SSHClient] = {}
        self._semaphore = asyncio.Semaphore(max_connections)

    async def get_client(
        self,
        host: str,
        port: int = 22,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> SSHClient:
        """Get or create an SSH client for a host."""
        key = f"{host}:{port}"

        if key not in self._connections:
            async with self._semaphore:
                creds = SSHCredentials(
                    host=host,
                    port=port,
                    username=username or settings.ssh_user,
                    password=password or settings.ssh_password,
                    key_path=settings.ssh_key_path,
                    timeout=settings.ssh_timeout,
                )
                client = SSHClient(creds)
                if await client.connect():
                    self._connections[key] = client

        return self._connections.get(key)

    async def try_connect(
        self,
        host: str,
        port: int = 22,
    ) -> Optional[SSHClient]:
        """Try to connect with multiple credential sets."""
        credentials_to_try = [
            (settings.ssh_user, settings.ssh_password),
            (settings.ssh_alt_user, settings.ssh_alt_password),
            ("root", "123456"),
            ("sidra", "Wsxk_8765"),
        ]

        for username, password in credentials_to_try:
            creds = SSHCredentials(
                host=host,
                port=port,
                username=username,
                password=password,
                timeout=settings.ssh_timeout,
            )
            client = SSHClient(creds)
            if await client.connect():
                key = f"{host}:{port}"
                self._connections[key] = client
                logger.info(f"Connected to {host} with user {username}")
                return client

        logger.warning(f"Failed to connect to {host} with any credentials")
        return None

    async def execute_on_all(
        self, command: str, hosts: list[str]
    ) -> dict[str, CommandResult]:
        """Execute a command on multiple hosts."""
        results = {}

        async def run_on_host(host: str):
            client = await self.get_client(host)
            if client:
                results[host] = await client.execute(command)
            else:
                results[host] = CommandResult(
                    stdout="", stderr="Connection failed", exit_code=-1
                )

        await asyncio.gather(*[run_on_host(h) for h in hosts])
        return results

    async def close_all(self):
        """Close all connections."""
        for client in self._connections.values():
            await client.disconnect()
        self._connections.clear()


# Synchronous SSH client for simpler use cases
class SyncSSHClient:
    """Synchronous SSH client using paramiko."""

    def __init__(self, host: str, username: str, password: str, port: int = 22):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self._client: Optional[paramiko.SSHClient] = None

    def connect(self) -> bool:
        """Establish SSH connection."""
        try:
            self._client = paramiko.SSHClient()
            self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self._client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=settings.ssh_timeout,
                allow_agent=False,
                look_for_keys=False,
            )
            return True
        except Exception as e:
            logger.error(f"SSH connection failed: {e}")
            return False

    def execute(self, command: str) -> CommandResult:
        """Execute a command."""
        if not self._client:
            if not self.connect():
                return CommandResult(stdout="", stderr="Connection failed", exit_code=-1)

        try:
            stdin, stdout, stderr = self._client.exec_command(command, timeout=60)
            exit_code = stdout.channel.recv_exit_status()
            return CommandResult(
                stdout=stdout.read().decode("utf-8", errors="ignore"),
                stderr=stderr.read().decode("utf-8", errors="ignore"),
                exit_code=exit_code,
            )
        except Exception as e:
            return CommandResult(stdout="", stderr=str(e), exit_code=-1)

    def close(self):
        """Close the connection."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
