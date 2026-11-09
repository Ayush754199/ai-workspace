"""Outbound URL safety checks.

The research tool fetches arbitrary URLs from search results. Without a guard a
result could point at cloud metadata (169.254.169.254) or an internal service and
turn the server into an SSRF proxy. Only public http(s) hosts are allowed, and
redirects are checked one hop at a time.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

BLOCKED_HOSTNAMES = {"localhost", "metadata", "metadata.google.internal", "instance-data"}


class UnsafeURLError(ValueError):
    pass


def _is_private_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def assert_safe_url(url: str) -> str:
    """Return the URL if it is a public http(s) address, else raise."""
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise UnsafeURLError("Malformed URL") from exc
    if parts.scheme not in {"http", "https"}:
        raise UnsafeURLError("Only http and https URLs can be fetched")
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise UnsafeURLError("URL has no host")
    if host in BLOCKED_HOSTNAMES or host.endswith(".local") or host.endswith(".internal"):
        raise UnsafeURLError("Internal hostnames are not allowed")
    # Literal IPs: reject outright when private.
    try:
        if _is_private_address(host):
            raise UnsafeURLError("Private network addresses are not allowed")
        return url
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError("Host could not be resolved") from exc
    for info in infos:
        if _is_private_address(info[4][0]):
            raise UnsafeURLError("Host resolves to a private network address")
    return url
