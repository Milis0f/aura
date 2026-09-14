"""NAS file manager: named roots, strict path confinement, listing and search. Ported from NAS Dashboard v2."""

from __future__ import annotations

import mimetypes
import os
import unicodedata
from pathlib import Path
from typing import Any

from ..config import SETTINGS
from .storage import Volume, file_volumes

EXT = {
    "video": {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm", ".ts", ".m2ts", ".wmv", ".flv", ".mpg", ".mpeg"},
    "audio": {".mp3", ".flac", ".wav", ".m4a", ".ogg", ".opus", ".aac", ".wma"},
    "image": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".svg", ".avif"},
    "doc": {".pdf", ".doc", ".docx", ".txt", ".md", ".odt", ".rtf", ".epub", ".xlsx", ".csv", ".srt"},
    "archive": {".zip", ".rar", ".7z", ".tar", ".gz", ".xz", ".iso", ".bz2"},
    "code": {".py", ".js", ".html", ".css", ".json", ".sh", ".yml", ".yaml", ".xml", ".conf"},
}
MEDIA_TYPES = {
    ".mkv": "video/x-matroska", ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
    ".mov": "video/quicktime", ".avi": "video/x-msvideo", ".ts": "video/mp2t", ".m2ts": "video/mp2t",
    ".wmv": "video/x-ms-wmv", ".mpg": "video/mpeg", ".mpeg": "video/mpeg", ".flv": "video/x-flv", ".ogv": "video/ogg",
    ".mp3": "audio/mpeg", ".flac": "audio/flac", ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".opus": "audio/ogg",
    ".wav": "audio/wav", ".aac": "audio/aac", ".srt": "text/plain; charset=utf-8", ".vtt": "text/vtt",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".avif": "image/avif",
}
SEARCH_DIRS = 4000
SEARCH_RESULTS = 300


class FileError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def roots(volumes: list[Volume]) -> dict[str, Path]:
    """Label -> folder, in display order: configured NAS roots, media root, then every mounted data volume."""
    out: dict[str, Path] = {}

    def add(label: str, path: str) -> None:
        try:
            resolved = Path(path).expanduser().resolve()
        except OSError:
            return
        if not resolved.is_dir() or resolved in out.values():
            return
        base = label or resolved.name or str(resolved)
        name, i = base, 2
        while name in out:
            name, i = f"{base} ({i})", i + 1
        out[name] = resolved

    for label, path in SETTINGS.file_roots:
        add(label, path)
    if SETTINGS.media_root:
        add("Médias", SETTINGS.media_root)
    for vol in file_volumes(volumes):
        add(vol.label, vol.mountpoint)
    if not out:
        default = SETTINGS.data_dir / "media"
        default.mkdir(parents=True, exist_ok=True)
        add("Médias", str(default))
    return out


def resolve(all_roots: dict[str, Path], root_name: str, rel: str) -> Path:
    root = all_roots.get(root_name)
    if root is None:
        raise FileError(404, "Emplacement inconnu.")
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if chr(0) in rel:
        raise FileError(400, "Chemin invalide.")
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise FileError(403, "Chemin hors de l'emplacement autorisé.")
    return target


def safe_name(name: str) -> str:
    cleaned = unicodedata.normalize("NFC", name).replace("\\", "/").split("/")[-1]
    cleaned = cleaned.replace(chr(0), "").strip().strip(".")
    if not cleaned or cleaned in {".", ".."}:
        raise FileError(400, "Nom de fichier invalide.")
    return cleaned[:255]


def kind_of(name: str) -> str:
    ext = Path(name).suffix.lower()
    return next((kind for kind, exts in EXT.items() if ext in exts), "file")


def media_type(path: Path) -> str:
    return MEDIA_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _entry(entry: os.DirEntry[str]) -> dict[str, Any] | None:
    try:
        st = entry.stat()
        is_dir = entry.is_dir()
    except OSError:
        return None
    return {"name": entry.name, "size": st.st_size, "mtime": int(st.st_mtime), "kind": "dir" if is_dir else kind_of(entry.name)}


def list_dir(target: Path, show_hidden: bool = False, sort: str = "name", desc: bool = False) -> list[dict[str, Any]]:
    if not target.is_dir():
        raise FileError(400, "Ce n'est pas un dossier.")
    try:
        entries = list(os.scandir(target))
    except PermissionError as exc:
        raise FileError(403, "Lecture refusée sur ce dossier.") from exc
    except OSError as exc:
        raise FileError(500, f"Lecture impossible : {exc.strerror or exc}") from exc
    dirs: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    for entry in entries:
        if not show_hidden and entry.name.startswith("."):
            continue
        info = _entry(entry)
        if info:
            (dirs if info["kind"] == "dir" else files).append(info)
    keys = {
        "name": lambda x: x["name"].lower(),
        "size": lambda x: x["size"],
        "date": lambda x: x["mtime"],
        "kind": lambda x: (x["kind"], x["name"].lower()),
    }
    key = keys.get(sort, keys["name"])
    dirs.sort(key=key, reverse=desc)
    files.sort(key=key, reverse=desc)
    return dirs + files


def search(root: Path, base: Path, query: str) -> tuple[list[dict[str, Any]], bool]:
    needle = query.strip().lower()
    if len(needle) < 2:
        raise FileError(400, "Tape au moins deux caractères.")
    out: list[dict[str, Any]] = []
    visited = 0
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        visited += 1
        if visited > SEARCH_DIRS or len(out) >= SEARCH_RESULTS:
            break
        for name in dirnames + filenames:
            if needle not in name.lower():
                continue
            full = Path(dirpath) / name
            try:
                st = full.stat()
            except OSError:
                continue
            parent = full.parent.relative_to(root).as_posix()
            out.append({
                "name": name, "dir": "" if parent == "." else parent, "size": st.st_size, "mtime": int(st.st_mtime),
                "kind": "dir" if full.is_dir() else kind_of(name),
            })
            if len(out) >= SEARCH_RESULTS:
                break
    return out, len(out) >= SEARCH_RESULTS


def unique_destination(folder: Path, name: str) -> Path:
    dest = folder / name
    stem, suffix, i = dest.stem, dest.suffix, 2
    while dest.exists():
        dest = folder / f"{stem} ({i}){suffix}"
        i += 1
    return dest
