"""NAS accounts: Argon2id passwords, optional TOTP, server-side sessions, audit trail.

Ported from NAS Dashboard v2 with two changes. Sessions live in SQLite, so restarting the service no longer logs
everyone out. Only a SHA-256 of each session token is stored, so a copy of the database cannot be replayed as a
cookie.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from .. import db
from ..config import SETTINGS

COOKIE = "aura_session"
MIN_PASSWORD = 12
TOUCH_EVERY = 60  # seconds between 'last seen' writes for one session
USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,31}$")

_hasher = PasswordHasher()
_lock = threading.Lock()
_fails: dict[str, tuple[int, float]] = {}  # ip -> (failed attempts, locked until)
_pending_totp: dict[str, str] = {}  # username -> secret waiting for its first code


class AccountError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Session:
    token_hash: str
    username: str
    csrf: str
    can_write: bool
    can_torrent: bool
    has_2fa: bool
    created: int
    expires: int
    seen: int
    ip: str
    agent: str

    @property
    def short_id(self) -> str:
        return self.token_hash[:10]

    def payload(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "can_write": self.can_write,
            "can_torrent": self.can_torrent,
            "csrf": self.csrf,
            "has_2fa": self.has_2fa,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---- users ----------------------------------------------------------------

def user_count() -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM users")
    return int(row["n"]) if row else 0


def get_user(username: str) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),))


def list_users() -> list[dict[str, Any]]:
    return db.query(
        "SELECT username, can_write, can_torrent, totp_secret IS NOT NULL AS has_2fa, created_at "
        "FROM users ORDER BY username COLLATE NOCASE"
    )


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AccountError(400, f"Le mot de passe doit faire {MIN_PASSWORD} caractères minimum.")


def create_user(username: str, password: str, can_write: bool = False, can_torrent: bool = False, totp_secret: str | None = None) -> dict[str, Any]:
    name = username.strip()
    if not USERNAME.match(name):
        raise AccountError(400, "Identifiant : 2 à 32 caractères, lettres, chiffres, point, tiret.")
    validate_password(password)
    if get_user(name):
        raise AccountError(409, f"Le compte « {name} » existe déjà.")
    db.execute(
        "INSERT INTO users(username, password_hash, totp_secret, can_write, can_torrent, created_at) VALUES (?,?,?,?,?,?)",
        (name, _hasher.hash(password), totp_secret, int(can_write), int(can_torrent), _now_iso()),
    )
    return get_user(name) or {}


def set_password(username: str, password: str) -> None:
    validate_password(password)
    if not db.execute("UPDATE users SET password_hash=? WHERE username=? COLLATE NOCASE", (_hasher.hash(password), username)):
        raise AccountError(404, "Compte introuvable.")


def set_permissions(username: str, can_write: bool, can_torrent: bool) -> None:
    if not db.execute("UPDATE users SET can_write=?, can_torrent=? WHERE username=? COLLATE NOCASE", (int(can_write), int(can_torrent), username)):
        raise AccountError(404, "Compte introuvable.")
    db.execute("DELETE FROM sessions WHERE username=? COLLATE NOCASE", (username,))


def delete_user(username: str) -> None:
    with db.transaction() as con:
        con.execute("DELETE FROM sessions WHERE username=? COLLATE NOCASE", (username,))
        con.execute("DELETE FROM users WHERE username=? COLLATE NOCASE", (username,))


def password_matches(user: dict[str, Any], password: str) -> bool:
    try:
        _hasher.verify(user["password_hash"], password)
    except (VerificationError, InvalidHashError):
        return False
    if _hasher.check_needs_rehash(user["password_hash"]):
        db.execute("UPDATE users SET password_hash=? WHERE username=?", (_hasher.hash(password), user["username"]))
    return True


# ---- login / sessions -----------------------------------------------------

def _check_lock(ip: str) -> None:
    with _lock:
        count, until = _fails.get(ip, (0, 0.0))
    if count >= SETTINGS.max_fails and time.time() < until:
        wait = int(until - time.time()) // 60 + 1
        raise AccountError(429, f"Trop de tentatives. Réessaie dans {wait} min.")


def _fail(ip: str, username: str, reason: str, message: str = "Identifiant ou mot de passe incorrect.") -> AccountError:
    with _lock:
        count, until = _fails.get(ip, (0, 0.0))
        count += 1
        if count >= SETTINGS.max_fails:
            until = time.time() + SETTINGS.lockout_seconds
        _fails[ip] = (count, until)
    audit(username, ip, "login_failed", reason)
    return AccountError(401, message)


def reset_failures() -> None:
    with _lock:
        _fails.clear()


def login(username: str, password: str, totp: str, ip: str, agent: str) -> tuple[str, Session]:
    _check_lock(ip)
    name = username.strip()
    user = get_user(name)
    if not user:
        _hasher.hash("timing-equaliser")  # same cost as a real check: no user enumeration by timing
        raise _fail(ip, name, "compte inconnu")
    if not password_matches(user, password):
        raise _fail(ip, name, "mot de passe")
    if user["totp_secret"]:
        code = totp.strip().replace(" ", "")
        if not code:
            raise AccountError(401, "Code à 6 chiffres requis.")
        if not pyotp.TOTP(user["totp_secret"]).verify(code, valid_window=1):
            raise _fail(ip, name, "code 2FA", "Code à 6 chiffres incorrect.")
    with _lock:
        _fails.pop(ip, None)
    token, session = open_session(user, ip, agent)
    audit(user["username"], ip, "login_ok")
    return token, session


def open_session(user: dict[str, Any], ip: str, agent: str) -> tuple[str, Session]:
    token = secrets.token_urlsafe(48)
    now = int(time.time())
    row = {
        "token_hash": _digest(token),
        "username": user["username"],
        "csrf": secrets.token_urlsafe(32),
        "created": now,
        "expires": now + SETTINGS.session_ttl,
        "seen": now,
        "ip": ip[:64],
        "agent": agent[:160],
    }
    db.execute(
        "INSERT INTO sessions(token_hash, username, csrf, created, expires, seen, ip, agent) "
        "VALUES (:token_hash,:username,:csrf,:created,:expires,:seen,:ip,:agent)",
        row,
    )
    return token, _session(row, user)


def _session(row: dict[str, Any], user: dict[str, Any]) -> Session:
    return Session(
        token_hash=row["token_hash"],
        username=user["username"],
        csrf=row["csrf"],
        can_write=bool(user["can_write"]),
        can_torrent=bool(user["can_torrent"]),
        has_2fa=bool(user["totp_secret"]),
        created=int(row["created"]),
        expires=int(row["expires"]),
        seen=int(row["seen"]),
        ip=row["ip"],
        agent=row["agent"],
    )


def session_from_token(token: str | None) -> Session | None:
    if not token or len(token) > 200:
        return None
    now = int(time.time())
    row = db.query_one("SELECT * FROM sessions WHERE token_hash=?", (_digest(token),))
    if not row:
        return None
    if row["expires"] < now or row["seen"] + SETTINGS.idle_ttl < now:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (row["token_hash"],))
        return None
    user = get_user(row["username"])
    if not user:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (row["token_hash"],))
        return None
    if now - row["seen"] > TOUCH_EVERY:
        db.execute("UPDATE sessions SET seen=? WHERE token_hash=?", (now, row["token_hash"]))
        row = {**row, "seen": now}
    return _session(row, user)


def close_session(token: str | None) -> Session | None:
    session = session_from_token(token)
    if session:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (session.token_hash,))
    return session


def purge_expired() -> int:
    now = int(time.time())
    return db.execute("DELETE FROM sessions WHERE expires < ? OR seen + ? < ?", (now, SETTINGS.idle_ttl, now))


def sessions_of(session: Session) -> list[dict[str, Any]]:
    now = int(time.time())
    rows = db.query("SELECT * FROM sessions WHERE username=? COLLATE NOCASE ORDER BY seen DESC", (session.username,))
    return [
        {
            "id": r["token_hash"][:10],
            "current": r["token_hash"] == session.token_hash,
            "ip": r["ip"],
            "agent": r["agent"],
            "since": datetime.fromtimestamp(r["created"], timezone.utc).isoformat(timespec="seconds"),
            "idle": max(0, now - int(r["seen"])),
        }
        for r in rows
    ]


def revoke_session(session: Session, short_id: str) -> int:
    return db.execute(
        "DELETE FROM sessions WHERE username=? COLLATE NOCASE AND substr(token_hash, 1, 10)=? AND token_hash<>?",
        (session.username, short_id[:10], session.token_hash),
    )


def change_password(session: Session, current: str, new: str) -> None:
    user = get_user(session.username)
    if not user or not password_matches(user, current):
        raise AccountError(401, "Mot de passe actuel incorrect.")
    set_password(session.username, new)
    db.execute("DELETE FROM sessions WHERE username=? COLLATE NOCASE AND token_hash<>?", (session.username, session.token_hash))


# ---- two-factor -----------------------------------------------------------

def start_totp(username: str) -> tuple[str, str]:
    secret = pyotp.random_base32()
    with _lock:
        _pending_totp[username.lower()] = secret
    return secret, pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name="Aura")


def enable_totp(username: str, code: str) -> None:
    with _lock:
        secret = _pending_totp.get(username.lower())
    if not secret:
        raise AccountError(400, "Recommence la configuration.")
    if not pyotp.TOTP(secret).verify(code.strip().replace(" ", ""), valid_window=1):
        raise AccountError(400, "Code incorrect. Vérifie l'heure de ton téléphone.")
    db.execute("UPDATE users SET totp_secret=? WHERE username=? COLLATE NOCASE", (secret, username))
    with _lock:
        _pending_totp.pop(username.lower(), None)


def disable_totp(username: str, password: str) -> None:
    user = get_user(username)
    if not user or not password_matches(user, password):
        raise AccountError(401, "Mot de passe incorrect.")
    db.execute("UPDATE users SET totp_secret=NULL WHERE username=? COLLATE NOCASE", (username,))


# ---- audit ----------------------------------------------------------------

def audit(username: str | None, ip: str, action: str, detail: str = "") -> None:
    try:
        db.execute(
            "INSERT INTO audit(ts, username, ip, action, detail) VALUES (?,?,?,?,?)",
            (_now_iso(), username, ip[:64], action, detail[:400]),
        )
    except sqlite3.DatabaseError:
        pass  # the trail must never break the action it records


def audit_of(username: str, limit: int = 60) -> list[dict[str, Any]]:
    return db.query(
        "SELECT ts, ip, action, detail FROM audit WHERE username=? COLLATE NOCASE ORDER BY id DESC LIMIT ?",
        (username, max(1, min(limit, 200))),
    )


def recent_audit(limit: int = 40) -> list[dict[str, Any]]:
    return db.query("SELECT ts, username, ip, action, detail FROM audit ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),))


# ---- migration from NAS Dashboard v2 --------------------------------------

def import_nasdash(path: str | Path) -> tuple[int, int]:
    """Copy accounts (same Argon2 hashes, same TOTP secrets) and the audit trail. Existing names are kept."""
    src = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    try:
        users = src.execute(
            "SELECT username, password_hash, totp_secret, can_write, can_torrent, created_at FROM users"
        ).fetchall()
        try:
            trail = src.execute("SELECT ts, username, ip, action, detail FROM audit ORDER BY id").fetchall()
        except sqlite3.DatabaseError:
            trail = []
    finally:
        src.close()
    imported = 0
    with db.transaction() as con:
        for u in users:
            if con.execute("SELECT 1 FROM users WHERE username=? COLLATE NOCASE", (u["username"],)).fetchone():
                continue
            con.execute(
                "INSERT INTO users(username, password_hash, totp_secret, can_write, can_torrent, created_at) VALUES (?,?,?,?,?,?)",
                (u["username"], u["password_hash"], u["totp_secret"], u["can_write"], u["can_torrent"], u["created_at"]),
            )
            imported += 1
        if trail and not con.execute("SELECT 1 FROM audit WHERE action='nasdash_import'").fetchone():
            con.executemany(
                "INSERT INTO audit(ts, username, ip, action, detail) VALUES (?,?,?,?,?)",
                [(r["ts"], r["username"], r["ip"], r["action"], r["detail"]) for r in trail],
            )
            con.execute("INSERT INTO audit(ts, username, ip, action, detail) VALUES (?,?,?,?,?)", (_now_iso(), None, "local", "nasdash_import", str(path)))
    return imported, len(trail)
