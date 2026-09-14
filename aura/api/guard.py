"""Access rules shared by the routers: account dependencies, and the gate in front of the whole application.

- The kiosk (this machine) and the home network use the TV and the remote without an account.
- The NAS side (files, downloads, account settings, reading file contents) always needs an account.
- From the Internet (or through a reverse proxy) only the web app shell and the login answer without an account.
"""

from __future__ import annotations

import json
import secrets

from fastapi import HTTPException, Request
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..services import accounts, security

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
PUBLIC_PATHS = frozenset({"/", "/health", "/api/health", "/api/login", "/api/logout", "/api/me", "/api/setup-owner", "/favicon.ico"})
PUBLIC_PREFIXES = ("/web/", "/shared/", "/vendor/")

CSP = "; ".join((
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: http: https:",
    "media-src 'self' blob: data: http: https:",
    "connect-src 'self' ws: wss: http: https:",
    "worker-src 'self' blob:",
    "font-src 'self' data:",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'self'",
))
SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"content-security-policy", CSP.encode()),
)


def zone(conn: HTTPConnection) -> str:
    value = conn.scope.get("aura.zone")
    if not value:
        value = security.zone_of(conn)
        conn.scope["aura.zone"] = value
    return value


def session_of(conn: HTTPConnection) -> accounts.Session | None:
    if "aura.session" not in conn.scope:
        conn.scope["aura.session"] = accounts.session_from_token(conn.cookies.get(accounts.COOKIE))
    return conn.scope["aura.session"]


def need_user(request: Request) -> accounts.Session:
    session = session_of(request)
    if not session:
        raise HTTPException(401, "Session expirée. Reconnecte-toi.")
    return session


def need_account(request: Request) -> accounts.Session:
    """A signed-in account; unsafe methods must also carry the session's CSRF token."""
    session = need_user(request)
    if request.method in UNSAFE_METHODS:
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not secrets.compare_digest(sent, session.csrf):
            raise HTTPException(403, "Jeton de sécurité invalide. Recharge la page.")
    return session


def need_write(request: Request) -> accounts.Session:
    session = need_account(request)
    if not session.can_write:
        raise HTTPException(403, "Ton compte est en lecture seule.")
    return session


def need_torrent(request: Request) -> accounts.Session:
    session = need_account(request)
    if not session.can_torrent:
        raise HTTPException(403, "Ton compte n'a pas accès aux téléchargements.")
    return session


def home_or_account(request: Request) -> accounts.Session | None:
    """The TV and the phone remote at home, or a signed-in account from anywhere."""
    if zone(request) != security.REMOTE:
        return session_of(request)
    return need_account(request)


def local_or_account(request: Request) -> accounts.Session | None:
    """File contents: only the kiosk on this machine, or a signed-in account."""
    if zone(request) == security.LOCAL:
        return session_of(request)
    return need_account(request)


def _public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


class Gate:
    """ASGI middleware: blocks anonymous Internet traffic and adds security headers to every response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        conn = HTTPConnection(scope)
        path = scope.get("path", "")
        if zone(conn) == security.REMOTE and not _public(path) and session_of(conn) is None:
            await self._refuse(scope, send, path)
            return
        if scope["type"] == "websocket":
            await self.app(scope, receive, send)
            return
        https = security.is_https(conn)

        async def send_secured(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend((k, v) for k, v in SECURITY_HEADERS if k not in present)
                if https:
                    headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_secured)

    @staticmethod
    async def _refuse(scope: Scope, send: Send, path: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        if path.startswith("/api/"):
            body = json.dumps({"detail": "Connexion requise."}).encode()
            headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), *SECURITY_HEADERS]
            await send({"type": "http.response.start", "status": 401, "headers": headers})
        else:
            body = b""
            await send({"type": "http.response.start", "status": 303, "headers": [(b"location", b"/"), (b"content-length", b"0"), *SECURITY_HEADERS]})
        await send({"type": "http.response.body", "body": body})
