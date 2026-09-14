"""REST: sign in, first account, and account security (password, two-factor, devices, activity)."""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import __version__
from ..config import SETTINGS
from ..services import accounts, security
from . import guard

router = APIRouter(prefix="/api")
_owner_lock = threading.Lock()


def _secure_cookie(request: Request) -> bool:
    mode = SETTINGS.secure_cookie
    if mode in ("1", "true", "yes"):
        return True
    if mode in ("0", "false", "no"):
        return False
    # auto: Secure only over real HTTPS. A Secure cookie on http://192.168.x.x is silently dropped by phones,
    # which is the "right password, still not signed in" bug NAS Dashboard v2 fixed.
    return security.is_https(request)


def _signed_in(request: Request, session: accounts.Session, token: str) -> JSONResponse:
    response = JSONResponse({"authenticated": True, **session.payload(), **_context(request)})
    response.set_cookie(
        accounts.COOKIE, token, httponly=True, samesite="lax", secure=_secure_cookie(request),
        max_age=SETTINGS.session_ttl, path="/",
    )
    return response


def _context(request: Request) -> dict[str, Any]:
    zone = guard.zone(request)
    users = accounts.user_count()
    return {
        "zone": zone,
        "has_users": users > 0,
        "can_setup": users == 0 and zone != security.REMOTE,
        "version": __version__,
        "jellyfin": SETTINGS.jellyfin_url,
    }


def _http(exc: accounts.AccountError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


@router.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": __version__}


@router.get("/me")
def me(request: Request) -> Any:
    session = guard.session_of(request)
    if not session:
        return {"authenticated": False, **_context(request)}  # a normal answer, not an error: the page is asking
    return {"authenticated": True, **session.payload(), **_context(request)}


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), totp: str = Form("")) -> JSONResponse:
    try:
        token, session = accounts.login(username, password, totp, security.client_ip(request), request.headers.get("user-agent", ""))
    except accounts.AccountError as exc:
        raise _http(exc) from exc
    return _signed_in(request, session, token)


@router.post("/setup-owner")
def setup_owner(request: Request, username: str = Form(...), password: str = Form(...)) -> JSONResponse:
    if guard.zone(request) == security.REMOTE:
        raise HTTPException(403, "Le premier compte se crée depuis le réseau de la maison.")
    ip = security.client_ip(request)
    with _owner_lock:
        if accounts.user_count() > 0:
            raise HTTPException(409, "Un compte existe déjà : connecte-toi.")
        try:
            user = accounts.create_user(username, password, can_write=True, can_torrent=True)
        except accounts.AccountError as exc:
            raise _http(exc) from exc
    token, session = accounts.open_session(user, ip, request.headers.get("user-agent", ""))
    accounts.audit(user["username"], ip, "owner_created")
    return _signed_in(request, session, token)


@router.post("/logout")
def logout(request: Request) -> JSONResponse:
    session = accounts.close_session(request.cookies.get(accounts.COOKIE))
    if session:
        accounts.audit(session.username, security.client_ip(request), "logout")
    response = JSONResponse({"ok": True})
    response.delete_cookie(accounts.COOKIE, path="/")
    return response


@router.post("/account/password")
def change_password(request: Request, current: str = Form(...), new: str = Form(...), session: accounts.Session = Depends(guard.need_account)) -> dict[str, bool]:
    try:
        accounts.change_password(session, current, new)
    except accounts.AccountError as exc:
        raise _http(exc) from exc
    accounts.audit(session.username, security.client_ip(request), "password_changed")
    return {"ok": True}


@router.post("/account/2fa/start")
def start_2fa(session: accounts.Session = Depends(guard.need_account)) -> dict[str, str]:
    secret, uri = accounts.start_totp(session.username)
    return {"secret": secret, "uri": uri}


@router.post("/account/2fa/enable")
def enable_2fa(request: Request, code: str = Form(...), session: accounts.Session = Depends(guard.need_account)) -> dict[str, bool]:
    try:
        accounts.enable_totp(session.username, code)
    except accounts.AccountError as exc:
        raise _http(exc) from exc
    accounts.audit(session.username, security.client_ip(request), "2fa_enabled")
    return {"ok": True}


@router.post("/account/2fa/disable")
def disable_2fa(request: Request, password: str = Form(...), session: accounts.Session = Depends(guard.need_account)) -> dict[str, bool]:
    try:
        accounts.disable_totp(session.username, password)
    except accounts.AccountError as exc:
        raise _http(exc) from exc
    accounts.audit(session.username, security.client_ip(request), "2fa_disabled")
    return {"ok": True}


@router.get("/account/sessions")
def list_sessions(session: accounts.Session = Depends(guard.need_account)) -> list[dict[str, Any]]:
    return accounts.sessions_of(session)


@router.post("/account/sessions/revoke")
def revoke_session(request: Request, id: str = Form(...), session: accounts.Session = Depends(guard.need_account)) -> dict[str, Any]:
    revoked = accounts.revoke_session(session, id)
    accounts.audit(session.username, security.client_ip(request), "session_revoked")
    return {"ok": True, "revoked": revoked}


@router.get("/account/audit")
def own_audit(limit: int = 60, session: accounts.Session = Depends(guard.need_account)) -> list[dict[str, Any]]:
    return accounts.audit_of(session.username, limit)
