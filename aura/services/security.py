"""Who is asking: the kiosk on this machine, a device on the home network, or the Internet.

The TV and the phone remote work without an account on the home network, because guests must be able to zap.
The NAS side (personal files, downloads, accounts) always needs an account, and from the Internet nothing but the
login page answers without one. A request relayed by a reverse proxy carries X-Forwarded-For and is treated as
coming from the Internet even though its socket peer is 127.0.0.1: that is exactly how nginx relays public traffic.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping

LOCAL = "local"
LAN = "lan"
REMOTE = "remote"

_FORWARD_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # Tailscale and similar mesh VPNs
# Starlette's in-process test client reports this peer name. A real socket peer is always an IP address.
_TEST_PEERS = frozenset({"testclient", "localhost"})


def zone(peer: str | None, headers: Mapping[str, str]) -> str:
    """'local' for the kiosk, 'lan' for private networks and VPNs, 'remote' for everything else."""
    if any(h in headers for h in _FORWARD_HEADERS):
        return REMOTE
    host = (peer or "").strip()
    if host in _TEST_PEERS:
        return LOCAL
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return REMOTE
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return LOCAL
    if ip.is_private or ip.is_link_local or (isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT):
        return LAN
    return REMOTE


def zone_of(conn: object) -> str:
    """Zone of a Starlette Request or WebSocket."""
    client = getattr(conn, "client", None)
    return zone(client.host if client else None, getattr(conn, "headers", {}))


def is_https(conn: object) -> bool:
    headers = getattr(conn, "headers", {})
    proto = headers.get("x-forwarded-proto", "") if headers else ""
    if proto:
        return proto.split(",")[0].strip() == "https"
    url = getattr(conn, "url", None)
    return bool(url) and url.scheme in ("https", "wss")


def client_ip(conn: object) -> str:
    headers = getattr(conn, "headers", {})
    fwd = headers.get("x-forwarded-for", "") if headers else ""
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    client = getattr(conn, "client", None)
    return client.host if client else "?"
