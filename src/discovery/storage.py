"""Storage discovery module for GlusterFS, NFS, and local storage."""

import asyncio
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

from ..utils import get_logger, SSHClient

logger = get_logger(__name__)


@dataclass
class VolumeInfo:
    """Storage volume information."""
    name: str = ""
    type: str = ""  # glusterfs, nfs, local, lvm
    size_gb: float = 0.0
    used_gb: float = 0.0
    available_gb: float = 0.0
    usage_percent: float = 0.0
    mount_point: str = ""
    status: str = ""
    bricks: list[str] = field(default_factory=list)
    replicas: int = 0


@dataclass
class GlusterInfo:
    """GlusterFS cluster information."""
    version: str = ""
    peer_count: int = 0
    peers: list[dict] = field(default_factory=list)
    volumes: list[VolumeInfo] = field(default_factory=list)
    status: str = ""


@dataclass
class NFSInfo:
    """NFS configuration information."""
    exports: list[dict] = field(default_factory=list)
    mounts: list[dict] = field(default_factory=list)


@dataclass
class StorageReport:
    """Complete storage report."""
    host: str = ""
    local_disks: list[VolumeInfo] = field(default_factory=list)
    glusterfs: Optional[GlusterInfo] = None
    nfs: Optional[NFSInfo] = None
    lvm_volumes: list[VolumeInfo] = field(default_factory=list)
    total_storage_gb: float = 0.0
    used_storage_gb: float = 0.0
    discovered_at: datetime = field(default_factory=datetime.now)


class StorageDiscovery:
    """Discover storage configuration on servers."""

    def __init__(self, ssh_client: SSHClient):
        self.ssh = ssh_client

    async def discover(self) -> StorageReport:
        """Perform full storage discovery."""
        logger.info(f"Starting storage discovery on {self.ssh.creds.host}")

        report = StorageReport(host=self.ssh.creds.host)

        await asyncio.gather(
            self._discover_local_disks(report),
            self._discover_glusterfs(report),
            self._discover_nfs(report),
            self._discover_lvm(report),
            return_exceptions=True,
        )

        # Calculate totals
        for disk in report.local_disks:
            report.total_storage_gb += disk.size_gb
            report.used_storage_gb += disk.used_gb

        return report

    async def _discover_local_disks(self, report: StorageReport):
        """Discover local disk storage."""
        result = await self.ssh.execute(
            "df -BG --output=target,source,fstype,size,used,avail,pcent 2>/dev/null | tail -n +2"
        )
        if result.success:
            for line in result.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 7:
                    mount = parts[0]
                    # Skip virtual filesystems
                    if mount.startswith("/dev") or mount.startswith("/sys") or mount.startswith("/proc"):
                        continue
                    if parts[2] in ["tmpfs", "devtmpfs", "squashfs"]:
                        continue

                    volume = VolumeInfo(
                        name=parts[1],
                        type="local",
                        mount_point=mount,
                        size_gb=float(parts[3].replace("G", "")),
                        used_gb=float(parts[4].replace("G", "")),
                        available_gb=float(parts[5].replace("G", "")),
                        usage_percent=float(parts[6].replace("%", "")),
                        status="mounted",
                    )
                    report.local_disks.append(volume)

    async def _discover_glusterfs(self, report: StorageReport):
        """Discover GlusterFS configuration."""
        # Check if GlusterFS is installed
        result = await self.ssh.execute("which gluster 2>/dev/null")
        if not result.success:
            return

        info = GlusterInfo()

        # Get version
        result = await self.ssh.execute("gluster --version 2>/dev/null | head -1")
        if result.success:
            info.version = result.stdout.strip()

        # Get peer status
        result = await self.ssh.execute("gluster peer status 2>/dev/null")
        if result.success:
            lines = result.stdout.strip().split("\n")
            for i, line in enumerate(lines):
                if line.startswith("Hostname:"):
                    hostname = line.split(":")[1].strip()
                    state = ""
                    if i + 1 < len(lines) and "State:" in lines[i + 1]:
                        state = lines[i + 1].split(":")[1].strip()
                    info.peers.append({"hostname": hostname, "state": state})

            info.peer_count = len(info.peers)

        # Get volumes
        result = await self.ssh.execute("gluster volume list 2>/dev/null")
        if result.success and result.stdout.strip():
            volume_names = result.stdout.strip().split("\n")
            for vol_name in volume_names:
                if not vol_name.strip():
                    continue

                vol = VolumeInfo(name=vol_name.strip(), type="glusterfs")

                # Get volume info
                vol_result = await self.ssh.execute(f"gluster volume info {vol_name} 2>/dev/null")
                if vol_result.success:
                    for line in vol_result.stdout.split("\n"):
                        if "Status:" in line:
                            vol.status = line.split(":")[1].strip()
                        elif "Number of Bricks:" in line:
                            try:
                                # Parse "1 x 2 = 2" format
                                brick_info = line.split(":")[1].strip()
                                if "x" in brick_info:
                                    parts = brick_info.split("x")
                                    vol.replicas = int(parts[1].split("=")[0].strip())
                            except:
                                pass
                        elif line.strip().startswith("Brick"):
                            brick = line.split(":")[1].strip() if ":" in line else ""
                            if brick:
                                vol.bricks.append(brick)

                info.volumes.append(vol)

        if info.version:
            report.glusterfs = info
            logger.info(f"Found GlusterFS: {len(info.volumes)} volumes, {info.peer_count} peers")

    async def _discover_nfs(self, report: StorageReport):
        """Discover NFS configuration."""
        nfs_info = NFSInfo()

        # Check NFS exports
        result = await self.ssh.execute("cat /etc/exports 2>/dev/null")
        if result.success and result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                if line.strip() and not line.startswith("#"):
                    parts = line.split()
                    if parts:
                        nfs_info.exports.append({
                            "path": parts[0],
                            "options": " ".join(parts[1:]) if len(parts) > 1 else "",
                        })

        # Check NFS mounts
        result = await self.ssh.execute("mount -t nfs,nfs4 2>/dev/null")
        if result.success and result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 3:
                    nfs_info.mounts.append({
                        "source": parts[0],
                        "mount_point": parts[2],
                        "type": parts[4] if len(parts) > 4 else "nfs",
                    })

        if nfs_info.exports or nfs_info.mounts:
            report.nfs = nfs_info
            logger.info(f"Found NFS: {len(nfs_info.exports)} exports, {len(nfs_info.mounts)} mounts")

    async def _discover_lvm(self, report: StorageReport):
        """Discover LVM configuration."""
        result = await self.ssh.execute("lvs --noheadings -o lv_name,vg_name,lv_size,lv_attr 2>/dev/null")
        if result.success and result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 3:
                    size_str = parts[2]
                    size_gb = 0.0
                    try:
                        if "g" in size_str.lower():
                            size_gb = float(size_str.lower().replace("g", "").replace("<", ""))
                        elif "t" in size_str.lower():
                            size_gb = float(size_str.lower().replace("t", "").replace("<", "")) * 1024
                        elif "m" in size_str.lower():
                            size_gb = float(size_str.lower().replace("m", "").replace("<", "")) / 1024
                    except:
                        pass

                    volume = VolumeInfo(
                        name=parts[0],
                        type="lvm",
                        size_gb=size_gb,
                        status="active" if len(parts) > 3 and "a" in parts[3] else "inactive",
                    )
                    report.lvm_volumes.append(volume)

            logger.info(f"Found {len(report.lvm_volumes)} LVM volumes")

    def to_dict(self, report: StorageReport) -> dict:
        """Convert report to dictionary."""
        result = {
            "host": report.host,
            "discovered_at": report.discovered_at.isoformat(),
            "summary": {
                "total_storage_gb": round(report.total_storage_gb, 2),
                "used_storage_gb": round(report.used_storage_gb, 2),
                "usage_percent": round(
                    (report.used_storage_gb / report.total_storage_gb * 100)
                    if report.total_storage_gb > 0 else 0, 2
                ),
            },
            "local_disks": [
                {
                    "mount": d.mount_point,
                    "device": d.name,
                    "size_gb": d.size_gb,
                    "used_gb": d.used_gb,
                    "usage_percent": d.usage_percent,
                }
                for d in report.local_disks
            ],
        }

        if report.glusterfs:
            result["glusterfs"] = {
                "version": report.glusterfs.version,
                "peer_count": report.glusterfs.peer_count,
                "peers": report.glusterfs.peers,
                "volumes": [
                    {
                        "name": v.name,
                        "status": v.status,
                        "replicas": v.replicas,
                        "bricks": v.bricks,
                    }
                    for v in report.glusterfs.volumes
                ],
            }

        if report.nfs:
            result["nfs"] = {
                "exports": report.nfs.exports,
                "mounts": report.nfs.mounts,
            }

        if report.lvm_volumes:
            result["lvm"] = [
                {"name": v.name, "size_gb": v.size_gb, "status": v.status}
                for v in report.lvm_volumes
            ]

        return result
