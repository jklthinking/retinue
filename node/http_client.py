"""Shared proxy policy for Retinue HTTP clients."""

from __future__ import annotations

import ipaddress
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from enum import Enum
from typing import Any, Mapping


class RequestClass(Enum):
    INWARD = "inward"
    OUTWARD = "outward"


class InwardRequestError(urllib.error.URLError):
    """A direct Retinue-server request failed after proxy bypass."""


# RFC 6598 shared address space; carriers and mesh VPNs both hand these out.
_CARRIER_GRADE_NAT = ipaddress.ip_network("100.64.0.0/10")
_CREDENTIAL_URL = re.compile(
    r"[A-Za-z][A-Za-z0-9+.-]*://[^/@\s]+@(?P<host>\[[^]]+\]|[^/:\s]+)(?P<port>:\d+)?"
)


def _ip_bypass_reason(host: str) -> str | None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if address.is_loopback:
        return "loopback address"
    if address.version == 4 and address in _CARRIER_GRADE_NAT:
        return "carrier-grade NAT address"
    if address.is_private or address.is_link_local:
        return "private address"
    return None


def _bypass_reason(
    host: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    normalized = host.rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return "loopback hostname"
    ip_reason = _ip_bypass_reason(normalized)
    if ip_reason:
        return ip_reason

    environment = os.environ if environ is None else environ
    configured = environment.get("no_proxy") or environment.get("NO_PROXY", "")
    for raw_rule in configured.split(","):
        rule = raw_rule.strip()
        if not rule:
            continue
        if rule == "*":
            return "no_proxy wildcard"
        try:
            network = ipaddress.ip_network(rule, strict=False)
        except ValueError:
            network = None
        if network is not None:
            try:
                if ipaddress.ip_address(normalized) in network:
                    return "no_proxy CIDR"
            except ValueError:
                pass
            continue
        suffix = (urllib.parse.urlsplit("//" + rule).hostname or rule).strip(".").lower()
        if suffix and (normalized == suffix or normalized.endswith("." + suffix)):
            return "no_proxy host suffix"
    return None


def proxy_bypass(host: str, *, environ: Mapping[str, str] | None = None) -> bool:
    """Return whether host matches Retinue's CIDR, loopback, or suffix rules."""
    return _bypass_reason(host, environ=environ) is not None


def _safe_transport_detail(exc: BaseException) -> str:
    reason: Any = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, TimeoutError):
        return "timed out"
    detail = str(reason).strip() or reason.__class__.__name__
    return _CREDENTIAL_URL.sub(
        lambda match: match.group("host") + (match.group("port") or ""),
        detail,
    )


def open_url(
    request: urllib.request.Request | str,
    *,
    timeout: float,
    request_class: RequestClass,
) -> Any:
    """Open a URL with the proxy policy declared by the caller."""
    if request_class is RequestClass.OUTWARD:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler())
        return opener.open(request, timeout=timeout)
    if request_class is not RequestClass.INWARD:
        raise ValueError(f"unsupported request class: {request_class!r}")

    url = request.full_url if isinstance(request, urllib.request.Request) else request
    host = urllib.parse.urlsplit(url).hostname or "unknown host"
    reason = _bypass_reason(host) or "inward request policy"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        return opener.open(request, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, TimeoutError) as exc:
        detail = _safe_transport_detail(exc)
        raise InwardRequestError(
            f"proxy bypass selected for host {host!r} ({reason}); {detail}"
        ) from exc
