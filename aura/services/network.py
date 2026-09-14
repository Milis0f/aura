"""Network management through NetworkManager's nmcli.

Everything here shells out to nmcli so the service user only needs a sudoers rule for nmcli
(installed by os/install.sh). On non-Linux hosts a mock returns plausible data for UI work.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import socket
import time
from dataclasses import asdict, dataclass
from typing import Any

from ..config import IS_LINUX, SETTINGS

log = logging.getLogger(__name__)

UNMANAGED = "Le réseau de cette machine est géré par le système (OpenMediaVault ou ifupdown), pas par Aura."
_reachable: tuple[float, bool] = (0.0, False)


@dataclass(frozen=True)
class NetStatus:
    online: bool
    hostname: str
    addresses: list[str]
    interfaces: list[dict[str, str]]
    wifi_ssid: str = ""
    wifi_ap_capable: bool = False
    hotspot_active: bool = False
    managed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def managed() -> bool:
    """False when someone else owns the network (OpenMediaVault, hand-written ifupdown) or nmcli is missing."""
    return not IS_LINUX or (SETTINGS.manage_network and shutil.which("nmcli") is not None)


async def internet_reachable(ttl: float = 60) -> bool:
    """A TCP handshake with two public resolvers, cached for a minute."""
    global _reachable
    now = time.monotonic()
    if now - _reachable[0] < ttl:
        return _reachable[1]
    ok = False
    for host in ("1.1.1.1", "9.9.9.9"):
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, 443), 3)
            writer.close()
            ok = True
            break
        except (OSError, asyncio.TimeoutError):
            continue
    _reachable = (now, ok)
    return ok


async def _run(*args: str, timeout: float = 25) -> tuple[int, str]:
    if not IS_LINUX:
        return 1, ""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        return proc.returncode or 0, out.decode(errors="replace")
    except (OSError, asyncio.TimeoutError) as exc:
        return 1, str(exc)


async def nmcli(*args: str, timeout: float = 25) -> tuple[int, str]:
    return await _run("nmcli", *args, timeout=timeout)


def _local_ips() -> list[str]:
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except socket.gaierror:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            ips.append(s.getsockname()[0])
            s.close()
        except OSError:
            pass
    return ips


async def status() -> NetStatus:
    hostname = socket.gethostname()
    if not IS_LINUX:
        return NetStatus(True, hostname, _local_ips(), [{"device": "mock0", "type": "ethernet", "state": "connected", "connection": "dev"}])
    if not managed():
        return NetStatus(await internet_reachable(), hostname, _local_ips(), [], managed=False)
    _, dev_out = await nmcli("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device")
    interfaces = []
    wifi_ssid = ""
    hotspot = False
    for line in dev_out.splitlines():
        parts = line.split(":")
        if len(parts) < 4 or parts[1] in ("loopback", "bridge", "tun"):
            continue
        d = {"device": parts[0], "type": parts[1], "state": parts[2], "connection": ":".join(parts[3:])}
        interfaces.append(d)
        if d["type"] == "wifi" and d["state"] == "connected":
            wifi_ssid = d["connection"]
            if d["connection"] == SETTINGS.hotspot_ssid:
                hotspot = True
    _, con_out = await nmcli("-t", "-f", "CONNECTIVITY", "general")
    online = con_out.strip() in ("full", "limited") and not hotspot
    ap = await ap_capable()
    _, ip_out = await nmcli("-t", "-f", "IP4.ADDRESS", "device", "show")
    addresses = [m.group(1) for m in re.finditer(r"IP4\.ADDRESS\[\d+\]:(\d+\.\d+\.\d+\.\d+)", ip_out) if not m.group(1).startswith("127.")]
    return NetStatus(online, hostname, addresses or _local_ips(), interfaces, wifi_ssid, ap, hotspot)


async def wifi_device() -> str:
    _, out = await nmcli("-t", "-f", "DEVICE,TYPE", "device")
    for line in out.splitlines():
        dev, _, typ = line.partition(":")
        if typ == "wifi":
            return dev
    return ""


async def ap_capable() -> bool:
    dev = await wifi_device()
    if not dev:
        return False
    _, out = await nmcli("-t", "-f", "WIFI-PROPERTIES.AP", "device", "show", dev)
    return out.strip().endswith("yes")


async def scan() -> list[dict[str, Any]]:
    if not IS_LINUX:
        return [
            {"ssid": "Freebox-Demo", "signal": 82, "security": "WPA2", "active": False},
            {"ssid": "Livebox-Test", "signal": 61, "security": "WPA2", "active": True},
            {"ssid": "Cafe-Ouvert", "signal": 35, "security": "", "active": False},
        ]
    if not managed():
        return []
    _, out = await nmcli("-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes", timeout=40)
    seen: dict[str, dict[str, Any]] = {}
    for line in out.splitlines():
        sentinel = "\x1f"  # nmcli escapes ':' inside SSIDs as '\:'
        parts = line.replace("\\:", sentinel).split(":")
        if len(parts) < 4:
            continue
        active, ssid, signal, sec = parts[0], parts[1].replace(sentinel, ":"), parts[2], ":".join(parts[3:])
        if not ssid:
            continue
        sig = int(signal) if signal.isdigit() else 0
        if ssid not in seen or seen[ssid]["signal"] < sig:
            seen[ssid] = {"ssid": ssid, "signal": sig, "security": sec, "active": active == "yes"}
    return sorted(seen.values(), key=lambda w: -w["signal"])


async def connect_wifi(ssid: str, password: str = "") -> tuple[bool, str]:
    if not IS_LINUX:
        return True, f"(mock) connecté à {ssid}"
    if not managed():
        return False, UNMANAGED
    await stop_hotspot()
    dev = await wifi_device()
    args = ["device", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    if dev:
        args += ["ifname", dev]
    code, out = await nmcli(*args, timeout=60)
    ok = code == 0
    if not ok:
        # remove the half-created profile so the next attempt starts clean
        await nmcli("connection", "delete", ssid)
    return ok, out.strip()


async def forget_wifi(ssid: str) -> None:
    if managed():
        await nmcli("connection", "delete", ssid)


async def start_hotspot() -> tuple[bool, str]:
    """Best-effort onboarding hotspot. b43 (Mac mini 2012) may refuse AP mode; the caller then
    falls back to on-screen setup."""
    if not IS_LINUX:
        return False, "mock: pas de hotspot hors Linux"
    if not managed():
        return False, UNMANAGED
    dev = await wifi_device()
    if not dev or not await ap_capable():
        return False, "la carte Wi-Fi ne supporte pas le mode point d'accès"
    code, out = await nmcli(
        "device", "wifi", "hotspot", "ifname", dev, "con-name", SETTINGS.hotspot_ssid,
        "ssid", SETTINGS.hotspot_ssid, "password", SETTINGS.hotspot_password, timeout=40,
    )
    return code == 0, out.strip()


async def stop_hotspot() -> None:
    if not managed():
        return
    await nmcli("connection", "down", SETTINGS.hotspot_ssid)
    await nmcli("connection", "delete", SETTINGS.hotspot_ssid)


async def set_ethernet_static(ip_cidr: str, gateway: str, dns: str) -> tuple[bool, str]:
    if not IS_LINUX:
        return True, "(mock) IP fixe appliquée"
    if not managed():
        return False, UNMANAGED
    _, out = await nmcli("-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active")
    name = next((l.split(":")[0] for l in out.splitlines() if ":802-3-ethernet:" in l or ":ethernet:" in l), "")
    if not name:
        return False, "aucune connexion Ethernet active"
    code, out = await nmcli("connection", "modify", name, "ipv4.method", "manual", "ipv4.addresses", ip_cidr, "ipv4.gateway", gateway, "ipv4.dns", dns)
    if code == 0:
        code, out = await nmcli("connection", "up", name)
    return code == 0, out.strip()


async def set_ethernet_dhcp() -> tuple[bool, str]:
    if not IS_LINUX:
        return True, "(mock) DHCP"
    if not managed():
        return False, UNMANAGED
    _, out = await nmcli("-t", "-f", "NAME,TYPE", "connection", "show", "--active")
    name = next((l.split(":")[0] for l in out.splitlines() if "ethernet" in l), "")
    if not name:
        return False, "aucune connexion Ethernet active"
    code, out = await nmcli("connection", "modify", name, "ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", "")
    if code == 0:
        code, out = await nmcli("connection", "up", name)
    return code == 0, out.strip()
