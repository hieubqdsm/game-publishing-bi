"""
DNS fallback qua DNS-over-HTTPS (DoH) — chống ISP DNS fail/chặn từng domain.

Vấn đề: DNS nhà mạng (vd VNPT) thỉnh thoảng trả NXDOMAIN cho
api.steampowered.com dù domain sống khỏe → mọi crawler Steam chết.

Giải pháp: wrap `socket.getaddrinfo` toàn process (bên trong crawler).
Với domain nằm trong DNS_FALLBACK_HOSTS: system DNS chạy trước (bình thường
thì rẻ, không đổi hành vi); chỉ khi system fail mới resolve qua Google DoH
tại `https://8.8.8.8/resolve` — 8.8.8.8 là IP literal nên chính việc
resolve này cũng KHÔNG cần DNS.

Quan trọng: dùng GOOGLE DoH chứ không phải Cloudflare 1.1.1.1 — Google gửi
EDNS Client Subnet nên trả về Akamai/CDN edge đúng vùng (VN), còn Cloudflare
không gửi ECS (lý do privacy) → trả edge quốc tế mà VNPT có thể không route
được (đã verify: store.steampowered.com qua IP Cloudflare = timeout, qua IP
Google = HTTP 200 trong 0.2s).

requests/urllib3 vẫn dùng hostname gốc cho SNI + xác thực certificate nên
kết nối HTTPS an toàn. IP được cache trong process (mỗi lần crawl là process
mới nên không lo stale).
"""
from __future__ import annotations

import socket

import requests as _requests
from loguru import logger

from config import DNS_FALLBACK_HOSTS

_DOH_URL = "https://8.8.8.8/resolve"
_installed = False


def _doh_resolve(host: str) -> list[str]:
    """Resolve IPv4 qua Google DoH (JSON API tại 8.8.8.8). Trả list IP."""
    r = _requests.get(
        _DOH_URL,
        params={"name": host, "type": "A"},
        timeout=5,
    )
    answers = r.json().get("Answer", [])
    return [a["data"] for a in answers if a.get("type") == 1]


def install_dns_fallback(hosts: list[str] | None = None) -> None:
    """Cài wrap getaddrinfo (idempotent — gọi nhiều lần vẫn chỉ wrap 1 lớp)."""
    global _installed
    if _installed:
        return
    _installed = True

    targets = set(hosts if hosts is not None else DNS_FALLBACK_HOSTS)
    if not targets:
        return

    orig_getaddrinfo = socket.getaddrinfo
    cache: dict[str, list[str]] = {}

    def patched(host, *args, **kwargs):
        try:
            return orig_getaddrinfo(host, *args, **kwargs)
        except socket.gaierror:
            if host not in targets:
                raise
            ips = cache.get(host)
            if not ips:
                try:
                    ips = _doh_resolve(host)
                except Exception as e:  # noqa: BLE001 — DoH chết thì để lỗi gốc nổ
                    logger.debug(f"[dns-fallback] DoH resolve {host} lỗi: {e}")
                    raise
                if ips:
                    logger.info(f"[dns-fallback] {host}: system DNS fail → DoH {ips[0]}")
                    cache[host] = ips
            if ips:
                return orig_getaddrinfo(ips[0], *args, **kwargs)
            raise

    socket.getaddrinfo = patched
    logger.debug(f"[dns-fallback] đã cài cho: {sorted(targets)}")
