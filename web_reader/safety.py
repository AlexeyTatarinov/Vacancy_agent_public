"""Безопасность адресов: нормализация, запрет локальных адресов, чёрный список доменов.

Адрес может прийти от пользователя или из найденной на странице ссылки. Ссылкам
со страницы доверять нельзя, поэтому каждый адрес проходит проверку перед открытием.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "igshid", "yclid",
    "_hsenc", "_hsmi", "dclid", "wbraid", "gbraid",
}

_NUMERIC_HOST = re.compile(r"^(0x[0-9a-f]+|\d+)(\.(0x[0-9a-f]+|\d+))*$", re.I)
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".corp", ".intranet")


def normalize_url(url: str, base: str | None = None) -> str | None:
    """Приводит адрес к единому виду; возвращает None, если это не http(s)-адрес."""
    try:
        absolute = urljoin(base, url.strip()) if base else url.strip()
        parts = urlsplit(absolute)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    scheme = parts.scheme.lower()
    netloc = host
    if ":" in host:  # IPv6
        netloc = f"[{host}]"
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc += f":{port}"
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not (k.lower().startswith("utm_") or k.lower() in TRACKING_PARAMS)
    ]
    # Фрагмент вида «#/…» или «#!…» это маршрут одностраничного сайта: без него откроется другая страница.
    fragment = parts.fragment if parts.fragment.startswith(("/", "!")) else ""
    return urlunsplit((scheme, netloc, parts.path or "/", urlencode(query), fragment))


def url_key(url: str) -> str | None:
    """Ключ для проверки «эта страница уже читалась»: без фрагмента и без хвостового слэша."""
    norm = normalize_url(url)
    if not norm:
        return None
    parts = urlsplit(norm)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def host_matches(host: str, domains: list[str] | tuple[str, ...]) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)


def private_reason(host: str) -> str | None:
    """Если имя хоста указывает на локальный или частный адрес, возвращает причину."""
    h = (host or "").lower().strip("[]").rstrip(".")
    if not h:
        return "empty host"
    if h in ("localhost", "ip6-localhost") or h.endswith(_LOCAL_SUFFIXES):
        return "local name"
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        # Числовые формы вроде 2130706433 или 0x7f.1 браузер превращает в IP-адрес.
        if _NUMERIC_HOST.match(h):
            return "numeric host form"
        return None
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified):
        return "private address"
    return None


def _static_check(url: str, safety: Any) -> tuple[bool, str, str | None]:
    norm = normalize_url(url)
    if not norm:
        return False, "not an http(s) address", None
    host = urlsplit(norm).hostname or ""
    if host_matches(host, safety.blocked_domains):
        return False, "blocked domain", host
    if not safety.allow_private_hosts:
        reason = private_reason(host)
        if reason:
            return False, reason, host
    return True, "ok", host


async def check_url(url: str, safety: Any) -> tuple[bool, str]:
    """Полная проверка адреса, включая разрешение имени (защита от обхода через DNS)."""
    ok, why, host = _static_check(url, safety)
    if not ok or safety.allow_private_hosts or host is None:
        return ok, why
    try:
        loop = asyncio.get_running_loop()
        infos = await asyncio.wait_for(loop.getaddrinfo(host, None), timeout=4)
    except Exception:
        return True, "unresolved"  # открытие всё равно завершится ошибкой, её увидит отчёт
    for info in infos:
        if private_reason(info[4][0]):
            return False, "resolves to a private address"
    return True, "ok"


def check_url_sync(url: str, safety: Any) -> tuple[bool, str]:
    """То же, что check_url, но для кода без asyncio (быстрые проверки, PDF)."""
    ok, why, host = _static_check(url, safety)
    if not ok or safety.allow_private_hosts or host is None:
        return ok, why
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True, "unresolved"
    for info in infos:
        if private_reason(info[4][0]):
            return False, "resolves to a private address"
    return True, "ok"
