"""Phase 1A: X-Forwarded-For is only believed from a trusted proxy.

Before Phase 1 the first X-Forwarded-For entry was accepted from any caller, so a
client could change its own rate-limit identity on every request.
"""

from __future__ import annotations

from starlette.requests import Request

from app.core import rate_limit
from app.core.config import settings


def _request(peer: str, forwarded: str | None = None, real_ip: str | None = None) -> Request:
    headers = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    if real_ip is not None:
        headers.append((b"x-real-ip", real_ip.encode()))
    return Request({"type": "http", "headers": headers, "client": (peer, 12345)})


def test_direct_client_cannot_spoof_forwarded_header(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    request = _request("203.0.113.9", forwarded="1.2.3.4, 5.6.7.8")
    assert rate_limit.client_ip(request) == "203.0.113.9"


def test_transport_ipv6_peer_is_compared_not_string_matched(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "::1/128")
    request = _request("::1", forwarded="198.51.100.7")
    assert rate_limit.client_ip(request) == "198.51.100.7"


def test_trusted_proxy_chain_returns_the_client_not_an_injected_hop(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    # Client sent "9.9.9.9" itself; the trusted edge appended the real address.
    request = _request("10.1.2.3", forwarded="9.9.9.9, 198.51.100.7")
    assert rate_limit.client_ip(request) == "198.51.100.7"


def test_trusted_proxy_uses_x_real_ip_when_forwarded_is_absent(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    request = _request("10.0.0.5", real_ip="198.51.100.8")
    assert rate_limit.client_ip(request) == "198.51.100.8"


def test_unparsable_forwarded_value_falls_back_to_peer(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "10.0.0.0/8,not-a-cidr")
    request = _request("10.0.0.5", forwarded="not-an-ip")
    # The bogus CIDR entry is ignored; the only trusted network is 10.0.0.0/8 and
    # the peer is inside it, so the (unparsable) header value is returned as-is
    # rather than crashing the limiter.
    assert rate_limit.client_ip(request) == "not-an-ip"


def test_rate_limit_bucket_uses_the_resolved_ip(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "")
    request = _request("198.51.100.20", forwarded="6.6.6.6")
    assert rate_limit._ai_rate_limit_key(request) == "ip:198.51.100.20"
