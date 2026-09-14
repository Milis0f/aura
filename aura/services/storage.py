"""Storage: plugged drives, folders the owner points Aura at, and safe removal.

On Linux `lsblk --json` lists partitions, `udisksctl` mounts new removable ones (a polkit rule installed by
os/install.sh lets the service user do it without a password) and `udevadm monitor` wakes the watcher the moment
a drive is plugged. A slow poll backs it up, so a missed event never hides a drive. On other systems (development
on Windows or macOS) volumes are only folders.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..config import IS_LINUX, SETTINGS

log = logging.getLogger(__name__)

LSBLK_COLUMNS = "NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,PARTLABEL,UUID,MOUNTPOINT,MOUNTPOINTS,HOTPLUG,RM,TRAN,MODEL,RO,PKNAME"
READABLE_FS = frozenset({
    "ntfs", "ntfs3", "exfat", "vfat", "msdos", "ext4", "ext3", "ext2", "btrfs", "xfs", "f2fs", "hfsplus", "hfs",
    "udf", "iso9660",
})
HIDDEN_FS = frozenset({"swap", "lvm2_member", "linux_raid_member"})
SYSTEM_MOUNTS = frozenset({"/", "/boot", "/boot/efi", "/efi", "/usr", "/var", "/home", "/opt", "/tmp", "[SWAP]"})
SYSTEM_LABELS = frozenset({
    "efi", "esp", "efi system partition", "system reserved", "recovery", "recovery hd", "msr",
    "microsoft reserved partition", "boot", "bios boot partition", "apple_boot",
})
EXTERNAL_TRANSPORTS = frozenset({"usb", "ieee1394", "mmc", "sdio"})
OMV_PREFIX = "/srv/dev-disk-by-"
GIB = 1024**3

OnChange = Callable[[list["Volume"], list["Volume"], bool], Awaitable[None]]


@dataclass(frozen=True)
class Volume:
    id: str
    label: str
    kind: str  # "disk" | "folder"
    device: str = ""
    parent: str = ""  # whole-disk device, needed to power a drive off
    fstype: str = ""
    size: int = 0
    mountpoint: str = ""
    removable: bool = False
    transport: str = ""
    model: str = ""
    system: bool = False
    readonly: bool = False

    @property
    def mounted(self) -> bool:
        return bool(self.mountpoint)

    @property
    def readable(self) -> bool:
        return self.kind == "folder" or self.fstype.lower() in READABLE_FS

    @property
    def managed(self) -> str:
        """'omv' when OpenMediaVault mounted it: ejecting it from Aura would fight OMV."""
        return "omv" if self.mountpoint.startswith(OMV_PREFIX) else ""

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "mounted": self.mounted, "readable": self.readable, "managed": self.managed}


def human_size(n: int) -> str:
    units = ("o", "Ko", "Mo", "Go", "To")
    value, i = float(n), 0
    while value >= 1024 and i < len(units) - 1:
        value /= 1024
        i += 1
    text = f"{value:.1f}".rstrip("0").rstrip(".") if i and value < 100 else f"{value:.0f}"
    return f"{text.replace('.', ',')} {units[i]}"


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in ("1", "true", "yes")


def _mountpoints(dev: dict[str, Any]) -> list[str]:
    mps = [m for m in (dev.get("mountpoints") or []) if m]
    if not mps and dev.get("mountpoint"):
        mps = [dev["mountpoint"]]
    return mps


def _best_mount(mps: list[str]) -> str:
    for prefix in (OMV_PREFIX, "/media/", "/run/media/", "/mnt/"):
        for m in mps:
            if m.startswith(prefix):
                return m
    return mps[0] if mps else ""


def _volume(dev: dict[str, Any], disk: dict[str, Any]) -> Volume:
    mps = _mountpoints(dev)
    fstype = (dev.get("fstype") or "").strip()
    label = (dev.get("label") or "").strip()
    partlabel = (dev.get("partlabel") or "").strip()
    size = int(dev.get("size") or 0)
    system = (
        any(m in SYSTEM_MOUNTS or m.startswith("/boot") for m in mps)
        or bool({label.lower(), partlabel.lower()} & SYSTEM_LABELS)
        or (fstype.lower() in ("vfat", "msdos") and 0 < size < GIB)
    )
    transport = (dev.get("tran") or disk.get("tran") or "").lower()
    removable = any(_truthy(x.get(k)) for x in (dev, disk) for k in ("hotplug", "rm")) or transport in EXTERNAL_TRANSPORTS
    uuid = (dev.get("uuid") or "").strip()
    name = dev.get("name") or ""
    model = (disk.get("model") or "").strip()
    return Volume(
        id=f"uuid:{uuid}" if uuid else f"dev:{name}",
        label=label or partlabel or f"{model or 'Disque'} {human_size(size)}",
        kind="disk",
        device=dev.get("path") or f"/dev/{name}",
        parent=disk.get("path") or "",
        fstype=fstype,
        size=size,
        mountpoint=_best_mount(mps),
        removable=removable,
        transport=transport,
        model=model,
        system=system,
        readonly=_truthy(dev.get("ro")),
    )


def parse_lsblk(data: dict[str, Any]) -> list[Volume]:
    """Flatten `lsblk --json --bytes` output into the partitions that carry a filesystem."""
    out: list[Volume] = []

    def visit(dev: dict[str, Any], disk: dict[str, Any] | None) -> None:
        if dev.get("type") == "disk":
            disk = dev
        fstype = (dev.get("fstype") or "").strip().lower()
        if dev.get("type") in ("part", "disk", "rom", "crypt", "lvm") and fstype and fstype not in HIDDEN_FS:
            out.append(_volume(dev, disk or dev))
        for child in dev.get("children") or []:
            visit(child, disk)

    for dev in data.get("blockdevices") or []:
        visit(dev, None)
    return out


def folder_volume(path: str, label: str = "") -> Volume | None:
    if not path:
        return None
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return None
    if not resolved.is_dir():
        return None
    digest = hashlib.sha1(str(resolved).encode()).hexdigest()[:12]
    return Volume(id=f"dir:{digest}", label=label or resolved.name or str(resolved), kind="folder", mountpoint=str(resolved))


def configured_folders() -> list[tuple[str, str]]:
    """(label, path) of every folder the owner configured: NAS roots, media root, watched folders."""
    from .. import db

    folders = list(SETTINGS.file_roots)
    if SETTINGS.media_root:
        folders.append(("Médias", SETTINGS.media_root))
    folders += [("", p) for p in SETTINGS.library_dirs]
    try:
        extra = json.loads(db.get_setting("library_dirs", "[]") or "[]")
    except json.JSONDecodeError:
        extra = []
    for entry in extra if isinstance(extra, list) else []:
        if isinstance(entry, dict):
            folders.append((str(entry.get("label") or ""), str(entry.get("path") or "")))
        elif isinstance(entry, str):
            folders.append(("", entry))
    return folders


async def _run(*args: str, timeout: float = 30) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        return proc.returncode or 0, out.decode(errors="replace")
    except (OSError, asyncio.TimeoutError) as exc:
        return 1, str(exc)


async def read_block_devices() -> list[Volume]:
    if not IS_LINUX or not shutil.which("lsblk"):
        return []
    code, out = await _run("lsblk", "--json", "--bytes", "-o", LSBLK_COLUMNS, timeout=15)
    if code != 0:  # util-linux older than 2.37 has no MOUNTPOINTS column
        code, out = await _run("lsblk", "--json", "--bytes", "-o", LSBLK_COLUMNS.replace(",MOUNTPOINTS", ""), timeout=15)
    try:
        return parse_lsblk(json.loads(out)) if code == 0 else []
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def _inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


async def list_volumes() -> list[Volume]:
    disks = await read_block_devices()
    mounts = {Path(v.mountpoint) for v in disks if v.mounted}
    folders: dict[str, Volume] = {}
    for label, path in configured_folders():
        vol = folder_volume(path, label)
        if vol and vol.id not in folders and Path(vol.mountpoint) not in mounts:
            folders[vol.id] = vol
    return [*disks, *folders.values()]


def library_volumes(volumes: list[Volume]) -> list[Volume]:
    """Volumes the library scans: mounted data disks, then folders that no scanned volume already contains."""
    chosen = [v for v in volumes if v.kind == "disk" and v.mounted and v.readable and not v.system]
    for folder in sorted((v for v in volumes if v.kind == "folder"), key=lambda v: len(v.mountpoint)):
        if not any(_inside(Path(folder.mountpoint), Path(c.mountpoint)) for c in chosen):
            chosen.append(folder)
    return chosen


def file_volumes(volumes: list[Volume]) -> list[Volume]:
    """Volumes shown in the file manager: every configured folder and every mounted data disk."""
    return [v for v in volumes if v.mounted and v.readable and not v.system]


def should_automount(volume: Volume) -> bool:
    return volume.kind == "disk" and not volume.mounted and not volume.system and volume.readable and volume.removable


async def mount(volume: Volume) -> tuple[bool, str]:
    if volume.kind != "disk" or volume.mounted:
        return True, volume.mountpoint
    if not shutil.which("udisksctl"):
        return False, "udisks2 n'est pas installé"
    code, out = await _run("udisksctl", "mount", "--no-user-interaction", "-b", volume.device, timeout=60)
    if code != 0 and volume.fstype.lower() in ("ntfs", "ntfs3", "exfat", "hfsplus"):
        # Windows fast startup and unclean ejects leave the volume "dirty": reading still works.
        code, out = await _run("udisksctl", "mount", "--no-user-interaction", "-o", "ro", "-b", volume.device, timeout=60)
    if code != 0:
        return False, out.strip()[-300:]
    m = re.search(r" at (.+)$", out.strip())
    where = m.group(1) if m else ""
    if where.endswith(".") and not Path(where).exists():
        where = where[:-1]
    return True, where


async def eject(volume: Volume, siblings: list[Volume]) -> tuple[bool, str]:
    if volume.kind != "disk":
        return False, "seuls les disques s'éjectent"
    if volume.managed:
        return False, "ce disque est géré par OpenMediaVault"
    if not shutil.which("udisksctl"):
        return False, "udisks2 n'est pas installé"
    if volume.mounted:
        code, out = await _run("udisksctl", "unmount", "--no-user-interaction", "-b", volume.device, timeout=90)
        if code != 0:
            busy = "busy" in out.lower() or "occupé" in out.lower()
            return False, "le disque est encore utilisé (lecture en cours ?)" if busy else out.strip()[-300:]
    others_mounted = any(s.parent == volume.parent and s.id != volume.id and s.mounted for s in siblings)
    if volume.parent and volume.removable and not others_mounted:
        await _run("udisksctl", "power-off", "--no-user-interaction", "-b", volume.parent, timeout=30)
    return True, "Tu peux débrancher le disque."


class DriveWatcher:
    """Keeps the list of volumes current and reports which ones appeared or disappeared."""

    def __init__(self, poll_seconds: float = 20.0) -> None:
        self.volumes: list[Volume] = []
        self.poll_seconds = poll_seconds
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()

    def poke(self) -> None:
        self._wake.set()

    def find(self, volume_id: str) -> Volume | None:
        return next((v for v in self.volumes if v.id == volume_id), None)

    async def refresh(self) -> tuple[list[Volume], list[Volume]]:
        from .. import db

        async with self._lock:
            current = await list_volumes()
            if SETTINGS.automount and db.get_setting("automount", "1") != "0":
                mounted_any = False
                for vol in current:
                    if should_automount(vol):
                        ok, detail = await mount(vol)
                        log.info("automount %s (%s): %s %s", vol.label, vol.device, ok, detail)
                        mounted_any = mounted_any or ok
                if mounted_any:
                    current = await list_volumes()
            before = {v.id: v for v in library_volumes(self.volumes)}
            after = {v.id: v for v in library_volumes(current)}
            appeared = [v for k, v in after.items() if k not in before or before[k].mountpoint != v.mountpoint]
            disappeared = [v for k, v in before.items() if k not in after]
            self.volumes = current
            return appeared, disappeared

    async def run(self, on_change: OnChange) -> None:
        monitor = asyncio.create_task(self._udev_monitor()) if IS_LINUX and shutil.which("udevadm") else None
        initial = True
        try:
            while True:
                try:
                    appeared, disappeared = await self.refresh()
                    if initial or appeared or disappeared:
                        await on_change(appeared, disappeared, initial)
                    initial = False
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - the watcher must outlive any single failure
                    log.warning("drive watcher: %s", exc)
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), self.poll_seconds)
                    await asyncio.sleep(1.5)  # let the kernel create the partitions and udisks settle
                except asyncio.TimeoutError:
                    pass
        finally:
            if monitor:
                monitor.cancel()

    async def _udev_monitor(self) -> None:
        while True:
            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    "udevadm", "monitor", "--udev", "--subsystem-match=block",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                )
                assert proc.stdout is not None
                async for line in proc.stdout:
                    if any(word in line for word in (b" add ", b" remove ", b" change ")):
                        self.poke()
                await proc.wait()
            except asyncio.CancelledError:
                if proc and proc.returncode is None:
                    proc.kill()
                raise
            except OSError as exc:
                log.info("udevadm monitor unavailable: %s", exc)
                return
            await asyncio.sleep(5)
