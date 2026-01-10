"""Database discovery module."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

from ..utils import get_logger, SSHClient
from ..config import settings

logger = get_logger(__name__)


@dataclass
class DatabaseInfo:
    """Information about a database."""
    type: str = ""  # postgresql, mysql, mongodb, redis
    version: str = ""
    host: str = ""
    port: int = 0
    running: bool = False
    size_gb: float = 0.0
    databases: list[str] = field(default_factory=list)
    users: list[str] = field(default_factory=list)
    connections: int = 0
    max_connections: int = 0
    replication_enabled: bool = False
    replication_role: str = ""
    config: dict = field(default_factory=dict)


@dataclass
class DatabasesReport:
    """Report of all discovered databases."""
    host: str = ""
    postgresql: Optional[DatabaseInfo] = None
    mysql: Optional[DatabaseInfo] = None
    mongodb: Optional[DatabaseInfo] = None
    redis: Optional[DatabaseInfo] = None
    other_databases: list[DatabaseInfo] = field(default_factory=list)
    discovered_at: datetime = field(default_factory=datetime.now)


class DatabaseDiscovery:
    """Discover databases on remote servers."""

    # Default ports
    DB_PORTS = {
        "postgresql": 5432,
        "mysql": 3306,
        "mongodb": 27017,
        "redis": 6379,
        "mariadb": 3306,
    }

    def __init__(self, ssh_client: SSHClient):
        self.ssh = ssh_client

    async def discover(self) -> DatabasesReport:
        """Perform full database discovery."""
        logger.info(f"Starting database discovery on {self.ssh.creds.host}")

        report = DatabasesReport(host=self.ssh.creds.host)

        await asyncio.gather(
            self._discover_postgresql(report),
            self._discover_mysql(report),
            self._discover_mongodb(report),
            self._discover_redis(report),
            return_exceptions=True,
        )

        return report

    async def _discover_postgresql(self, report: DatabasesReport):
        """Discover PostgreSQL databases."""
        # Check if PostgreSQL is running
        result = await self.ssh.execute("pgrep -x postgres >/dev/null && echo 'running' || echo 'stopped'")
        if "running" not in result.stdout:
            # Check Docker containers
            result = await self.ssh.execute("docker ps --filter 'name=postgres' --format '{{.Names}}' 2>/dev/null")
            if not result.stdout.strip():
                return

        info = DatabaseInfo(type="postgresql", host=self.ssh.creds.host, port=5432, running=True)

        # Get version
        result = await self.ssh.execute("psql --version 2>/dev/null || docker exec $(docker ps -q --filter 'name=postgres' | head -1) psql --version 2>/dev/null")
        if result.success:
            info.version = result.stdout.strip().split()[-1] if result.stdout else ""

        # Get databases (try local first, then docker)
        result = await self.ssh.execute(
            "sudo -u postgres psql -c '\\l' 2>/dev/null | grep -E '^ [a-zA-Z]' | awk '{print $1}'"
        )
        if result.success and result.stdout.strip():
            info.databases = [db.strip() for db in result.stdout.strip().split("\n") if db.strip()]

        # Get connection count
        result = await self.ssh.execute(
            "sudo -u postgres psql -c 'SELECT count(*) FROM pg_stat_activity;' -t 2>/dev/null"
        )
        if result.success and result.stdout.strip():
            try:
                info.connections = int(result.stdout.strip())
            except ValueError:
                pass

        # Check replication
        result = await self.ssh.execute(
            "sudo -u postgres psql -c 'SELECT pg_is_in_recovery();' -t 2>/dev/null"
        )
        if result.success:
            if "t" in result.stdout.lower():
                info.replication_enabled = True
                info.replication_role = "replica"
            elif "f" in result.stdout.lower():
                # Check if it has replicas
                rep_check = await self.ssh.execute(
                    "sudo -u postgres psql -c 'SELECT count(*) FROM pg_stat_replication;' -t 2>/dev/null"
                )
                if rep_check.success and int(rep_check.stdout.strip() or 0) > 0:
                    info.replication_enabled = True
                    info.replication_role = "primary"

        report.postgresql = info
        logger.info(f"Found PostgreSQL: {len(info.databases)} databases")

    async def _discover_mysql(self, report: DatabasesReport):
        """Discover MySQL/MariaDB databases."""
        # Check if MySQL is running
        result = await self.ssh.execute("pgrep -x mysqld >/dev/null && echo 'running' || echo 'stopped'")
        if "running" not in result.stdout:
            result = await self.ssh.execute("docker ps --filter 'name=mysql\\|mariadb' --format '{{.Names}}' 2>/dev/null")
            if not result.stdout.strip():
                return

        info = DatabaseInfo(type="mysql", host=self.ssh.creds.host, port=3306, running=True)

        # Get version
        result = await self.ssh.execute("mysql --version 2>/dev/null")
        if result.success:
            info.version = result.stdout.strip()
            if "MariaDB" in result.stdout:
                info.type = "mariadb"

        # Try to get databases (requires credentials)
        result = await self.ssh.execute(
            "mysql -e 'SHOW DATABASES;' 2>/dev/null | tail -n +2"
        )
        if result.success:
            info.databases = [db.strip() for db in result.stdout.strip().split("\n") if db.strip()]

        report.mysql = info
        logger.info(f"Found MySQL/MariaDB: {len(info.databases)} databases")

    async def _discover_mongodb(self, report: DatabasesReport):
        """Discover MongoDB databases."""
        # Check if MongoDB is running
        result = await self.ssh.execute("pgrep -x mongod >/dev/null && echo 'running' || echo 'stopped'")
        if "running" not in result.stdout:
            result = await self.ssh.execute("docker ps --filter 'name=mongo' --format '{{.Names}}' 2>/dev/null")
            if not result.stdout.strip():
                return

        info = DatabaseInfo(type="mongodb", host=self.ssh.creds.host, port=27017, running=True)

        # Get version
        result = await self.ssh.execute("mongod --version 2>/dev/null | head -1")
        if result.success:
            info.version = result.stdout.strip()

        # Get databases
        result = await self.ssh.execute(
            "mongosh --quiet --eval 'db.adminCommand({listDatabases: 1}).databases.map(d => d.name)' 2>/dev/null"
        )
        if result.success:
            try:
                info.databases = json.loads(result.stdout)
            except:
                pass

        # Check replica set
        result = await self.ssh.execute(
            "mongosh --quiet --eval 'rs.status().ok' 2>/dev/null"
        )
        if result.success and "1" in result.stdout:
            info.replication_enabled = True
            # Get role
            role_result = await self.ssh.execute(
                "mongosh --quiet --eval 'rs.isMaster().ismaster' 2>/dev/null"
            )
            if role_result.success:
                info.replication_role = "primary" if "true" in role_result.stdout else "secondary"

        report.mongodb = info
        logger.info(f"Found MongoDB: {len(info.databases)} databases")

    async def _discover_redis(self, report: DatabasesReport):
        """Discover Redis instances."""
        # Check if Redis is running
        result = await self.ssh.execute("pgrep -x redis-server >/dev/null && echo 'running' || echo 'stopped'")
        if "running" not in result.stdout:
            result = await self.ssh.execute("docker ps --filter 'name=redis' --format '{{.Names}}' 2>/dev/null")
            if not result.stdout.strip():
                return

        info = DatabaseInfo(type="redis", host=self.ssh.creds.host, port=6379, running=True)

        # Get version
        result = await self.ssh.execute("redis-server --version 2>/dev/null")
        if result.success:
            info.version = result.stdout.strip()

        # Get info
        result = await self.ssh.execute("redis-cli INFO 2>/dev/null")
        if result.success:
            for line in result.stdout.split("\n"):
                if line.startswith("connected_clients:"):
                    try:
                        info.connections = int(line.split(":")[1])
                    except:
                        pass
                elif line.startswith("used_memory_human:"):
                    size_str = line.split(":")[1].strip()
                    try:
                        if "G" in size_str:
                            info.size_gb = float(size_str.replace("G", ""))
                        elif "M" in size_str:
                            info.size_gb = float(size_str.replace("M", "")) / 1024
                    except:
                        pass
                elif line.startswith("role:"):
                    role = line.split(":")[1].strip()
                    if role in ["master", "slave"]:
                        info.replication_enabled = True
                        info.replication_role = "primary" if role == "master" else "replica"

        # Get database count
        result = await self.ssh.execute("redis-cli INFO keyspace 2>/dev/null")
        if result.success:
            dbs = [line.split(":")[0] for line in result.stdout.split("\n") if line.startswith("db")]
            info.databases = dbs

        report.redis = info
        logger.info(f"Found Redis: {len(info.databases)} databases, {info.connections} connections")

    def to_dict(self, report: DatabasesReport) -> dict:
        """Convert report to dictionary."""
        result = {
            "host": report.host,
            "discovered_at": report.discovered_at.isoformat(),
            "databases": {},
        }

        if report.postgresql:
            result["databases"]["postgresql"] = {
                "version": report.postgresql.version,
                "running": report.postgresql.running,
                "databases": report.postgresql.databases,
                "connections": report.postgresql.connections,
                "replication": {
                    "enabled": report.postgresql.replication_enabled,
                    "role": report.postgresql.replication_role,
                } if report.postgresql.replication_enabled else None,
            }

        if report.mysql:
            result["databases"]["mysql"] = {
                "type": report.mysql.type,
                "version": report.mysql.version,
                "running": report.mysql.running,
                "databases": report.mysql.databases,
            }

        if report.mongodb:
            result["databases"]["mongodb"] = {
                "version": report.mongodb.version,
                "running": report.mongodb.running,
                "databases": report.mongodb.databases,
                "replication": {
                    "enabled": report.mongodb.replication_enabled,
                    "role": report.mongodb.replication_role,
                } if report.mongodb.replication_enabled else None,
            }

        if report.redis:
            result["databases"]["redis"] = {
                "version": report.redis.version,
                "running": report.redis.running,
                "size_gb": report.redis.size_gb,
                "connections": report.redis.connections,
                "databases": report.redis.databases,
                "replication": {
                    "enabled": report.redis.replication_enabled,
                    "role": report.redis.replication_role,
                } if report.redis.replication_enabled else None,
            }

        return result
