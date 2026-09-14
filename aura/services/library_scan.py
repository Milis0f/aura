"""Walk a volume, recognise video files, and reconcile the library with what is really on disk."""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import library, mediaparse

log = logging.getLogger(__name__)

SKIP_DIRS = frozenset({
    "$recycle.bin", "recycler", "system volume information", "lost+found", "windows", "program files",
    "program files (x86)", "programdata", "appdata", "application data", "node_modules", "@eadir", "#recycle",
    "@recycle", ".trash", "dcim", "camera", "screenshots", "captures", "whatsapp", "incomplete", ".incomplete",
    "sample", "samples", "extras", "featurettes", "trailers", "bonus", "behind the scenes", "deleted scenes",
    "backups.backupdb", "msocache", "boot", "efi",
})
MIN_BYTES = 25 * 1024**2
SAMPLE_BYTES = 350 * 1024**2
PARTIAL_SUFFIXES = (".part", ".!qb", ".!ut", ".crdownload", ".tmp", ".partial", ".download")
CAMERA = re.compile(r"(?i)^(?:vid|img|mvi|dsc|pxl|gopr|gh\d{2}|dji|screen[ _-]?recording|whatsapp[ _]video|enregistrement)[ _-]?\d")
TEASER = re.compile(r"(?i)(?<![a-z])(?:sample|trailer|teaser|bande[ ._-]annonce|extrait)(?![a-z])")
ART_NAMES = ("poster", "folder", "cover", "affiche")
ART_EXT = (".jpg", ".jpeg", ".png", ".webp")
MAX_DEPTH = 12


@dataclass(frozen=True)
class FoundFile:
    rel_path: str
    size: int
    mtime: int
    art: str = ""


@dataclass(frozen=True)
class ScanReport:
    drive_id: str
    files: int
    changed: int
    removed: int
    new_items: int


class EmptyVolume(Exception):
    """Nothing where files used to be: the disk is unmounted underneath its mount point. Never wipe on that."""


def is_candidate(name: str, size: int) -> bool:
    low = name.lower()
    if low.endswith(PARTIAL_SUFFIXES) or not mediaparse.is_video(low) or size < MIN_BYTES:
        return False
    if size < SAMPLE_BYTES and TEASER.search(PurePosixPath(low).stem):
        return False
    return not CAMERA.match(name)


def _join(folder: str, name: str) -> str:
    return f"{folder}/{name}" if folder else name


def walk(root: Path) -> Iterator[FoundFile]:
    """Depth-first walk that never follows symlinks and skips system, camera and bonus folders."""
    stack: list[tuple[Path, int, str]] = [(root, 0, "")]
    while stack:
        folder, depth, parent_art = stack.pop()
        try:
            with os.scandir(folder) as it:
                entries = list(it)
        except OSError as exc:
            log.debug("scan: cannot list %s: %s", folder, exc)
            continue
        rel_folder = "" if folder == root else folder.relative_to(root).as_posix()
        images: dict[str, str] = {}
        videos: list[tuple[str, int, int]] = []
        subdirs: list[Path] = []
        for entry in entries:
            name = entry.name
            if name.startswith((".", "$", "~")):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if depth < MAX_DEPTH and name.lower() not in SKIP_DIRS:
                        subdirs.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                low = name.lower()
                if low.endswith(ART_EXT):
                    images[low] = name
                    continue
                if not mediaparse.is_video(low):
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if is_candidate(name, stat.st_size):
                videos.append((name, stat.st_size, int(stat.st_mtime)))
        own_art = ""
        if rel_folder:  # a poster at the root of a drive describes no film in particular
            own_art = next((_join(rel_folder, images[n + e]) for n in ART_NAMES for e in ART_EXT if n + e in images), "")
        for name, size, mtime in videos:
            stem = PurePosixPath(name).stem.lower()
            specific = next((_join(rel_folder, images[stem + s + e]) for s in ("-poster", "") for e in ART_EXT if stem + s + e in images), "")
            yield FoundFile(_join(rel_folder, name), size, mtime, specific or own_art or parent_art)
        for sub in subdirs:
            stack.append((sub, depth + 1, own_art))


def scan(drive_id: str, root: Path, on_progress: Callable[[int], None] | None = None) -> ScanReport:
    if not root.is_dir():
        raise FileNotFoundError(str(root))
    existing = library.existing_files(drive_id)
    seen: set[str] = set()
    changed: list[tuple[FoundFile, mediaparse.ParsedName]] = []
    for count, found in enumerate(walk(root), 1):
        seen.add(found.rel_path)
        old = existing.get(found.rel_path)
        art = f"{drive_id}|{found.art}" if found.art else ""
        if not (old and old["size"] == found.size and old["mtime"] == found.mtime and old["art"] == art):
            changed.append((found, mediaparse.parse_path(found.rel_path)))
        if on_progress and count % 250 == 0:
            on_progress(count)
    if not root.is_dir():
        raise FileNotFoundError(str(root))
    if existing and not seen:
        raise EmptyVolume(str(root))
    removed = [row["id"] for rel, row in existing.items() if rel not in seen]
    new_items = library.apply_scan(drive_id, changed, removed, int(time.time())) if changed or removed else 0
    return ScanReport(drive_id, len(seen), len(changed), len(removed), new_items)
