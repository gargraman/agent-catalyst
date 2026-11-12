"""SSRF / egress guard (feature 017 / F19) — the P1 app-level validator for outbound HTTP made by tools.

A prompt-injected agent must never become an SSRF/exfiltration primitive (Constitution P3). This module is
the single deterministic, **fail-closed** gate every outbound HTTP tool call passes through *before* any byte
leaves the process. It is a **pure library**: it does no I/O of its own except an explicit, injectable DNS
resolve, so it is exhaustively unit-testable and has no FastAPI/DB imports. It lives in `api/` (NOT
`services/runtime/`, which stays import-pure per P6) and is called by the future HTTP-tool executor — see the
"Integration seam" note at the bottom.

What it enforces (research R3 / `docs/backend/09 §10`):
  1. **Scheme** — only http/https (no file://, gopher://, ftp:// … SSRF classics).
  2. **Per-tool domain allow-list** — deny-by-default: an empty allow-list permits nothing (FR-008).
  3. **IP block-set** — every *resolved* IP is checked against private/loopback/link-local/ULA/multicast/
     reserved/unspecified ranges + the cloud-metadata endpoints, IPv4 **and** IPv6 (incl. IPv4-mapped IPv6).
     ANY resolved address being blocked refuses the whole call — the multi-A (one public, one private) edge
     case (FR-006).
  4. **DNS-rebinding defense** — `validate_connected_ip()` re-checks the address the socket *actually* dialed,
     so a TOCTOU rebind between pre-resolve and connect is caught (FR-008). The executor pins the connection
     to a validated IP returned here rather than re-resolving.
  5. **Redirects** — `validate_redirect()` runs every hop's target back through the full guard; a redirect
     from an allowed domain to an internal/off-allow-list host is refused (FR-007).

Refusals raise `EgressBlocked` with ONLY a coarse `reason` in the client envelope (never the resolved IP, full
URL, or secrets — FR-009) and are counted on `ac_egress_blocked_total{reason}` for diagnostics.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urljoin, urlsplit

from api import metrics
from api.errors import EgressBlocked
from api.settings import get_settings

_log = logging.getLogger("api.egress")

IPAddr = ipaddress.IPv4Address | ipaddress.IPv6Address

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}

# Cloud-metadata endpoints get a dedicated reason even though 169.254.169.254 is also link-local — the label
# is the single most important SSRF signal to surface in diagnostics. GCP/AWS IMDS + the IPv6 IMDSv2 address.
_METADATA_IPS = frozenset(
    ipaddress.ip_address(a) for a in ("169.254.169.254", "fd00:ec2::254")
)

# A DNS resolver: host, port -> list of IPs. Injectable so tests are deterministic (no real DNS) and so the
# executor can pass a resolver that also returns the addrinfo it will pin the socket to.
Resolver = Callable[[str, int], list[IPAddr]]


@dataclass(frozen=True)
class EgressTarget:
    """A validated outbound target. `ips` are the resolved, block-checked addresses the executor should pin
    the connection to (dial one of these directly; do NOT re-resolve `host`, or a rebind reopens the hole)."""
    scheme: str
    host: str
    port: int
    ips: tuple[IPAddr, ...]


def _as_ip(value: str) -> IPAddr | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _extra_deny_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Operator-added deny CIDRs from settings (validated at load, so this never raises)."""
    return [ipaddress.ip_network(c, strict=False) for c in get_settings().egress_extra_deny_cidrs]


def ip_block_reason(ip: IPAddr) -> str | None:
    """Return a coarse block reason for `ip`, or None if it is a permitted (public) address. Fail-closed:
    anything not clearly a routable public address is blocked. IPv4-mapped IPv6 is unwrapped so an attacker
    cannot smuggle a private IPv4 inside `::ffff:10.0.0.1`."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

    if ip in _METADATA_IPS:
        return "metadata"
    # Order gives the most specific reason; blocking is identical regardless of which fires.
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:          # 169.254.0.0/16, fe80::/10
        return "link_local"
    if ip.is_multicast:
        return "multicast"
    if ip.is_unspecified:         # 0.0.0.0, ::
        return "unspecified"
    if ip.is_private:             # RFC1918 + IPv6 ULA (fc00::/7) + others per stdlib
        return "private"
    if ip.is_reserved:
        return "reserved"
    for net in _extra_deny_networks():
        if ip.version == net.version and ip in net:
            return "denylisted"
    return None


def _host_allowed(host: str, allowlist_domains: Iterable[str]) -> bool:
    """Suffix-match the hostname against the per-tool allow-list (case-insensitive, trailing-dot tolerant).
    `example.com` matches `example.com` and `api.example.com`, but NOT `notexample.com` or `example.com.evil`.
    Deny-by-default: an empty allow-list matches nothing (FR-008)."""
    h = host.lower().rstrip(".")
    if not h:
        return False
    for raw in allowlist_domains or ():
        d = str(raw).lower().strip().lstrip("*").lstrip(".").rstrip(".")
        if not d:
            continue
        if h == d or h.endswith("." + d):
            return True
    return False


def _refuse(reason: str, *, host: str | None = None) -> None:
    """Record + raise a fail-closed refusal. Client sees ONLY `reason`; the host (hostname only — no IP, no
    query string, no secrets) goes to the server log for diagnostics (FR-009)."""
    metrics.record_egress_blocked(reason)
    _log.warning("egress refused: reason=%s host=%s", reason, host or "-")
    raise EgressBlocked("outbound request to the requested destination is not permitted",
                        detail={"reason": reason})


def _default_resolver(host: str, port: int) -> list[IPAddr]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return []
    out: list[IPAddr] = []
    seen: set[str] = set()
    for info in infos:
        addr = info[4][0]
        if addr in seen:
            continue
        seen.add(addr)
        ip = _as_ip(addr)
        if ip is not None:
            out.append(ip)
    return out


def validate_url(url: str, *, allowlist_domains: Iterable[str],
                 resolver: Resolver = _default_resolver) -> EgressTarget:
    """Validate an outbound URL end-to-end and return the pinned, block-checked target. Raises `EgressBlocked`
    (fail-closed) on any violation. This is the pre-flight the executor calls before dialing."""
    parts = urlsplit((url or "").strip())
    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        _refuse("scheme", host=parts.hostname)

    host = parts.hostname
    if not host:
        _refuse("no_host")

    try:
        port = parts.port or _DEFAULT_PORTS[scheme]
    except ValueError:                     # malformed port in the authority
        _refuse("bad_port", host=host)

    # Literal-IP URL (http://169.254.169.254/…): block-check the literal directly, no DNS. The IP check comes
    # FIRST so a blocked literal is refused even if someone allow-lists it.
    literal = _as_ip(host)
    if literal is not None:
        reason = ip_block_reason(literal)
        if reason:
            _refuse(reason, host=host)
        if not _host_allowed(host, allowlist_domains):
            _refuse("not_allowlisted", host=host)
        return EgressTarget(scheme, host, port, (literal,))

    # Hostname path: allow-list BEFORE resolving (deny-by-default; avoid a DNS lookup for a disallowed host).
    if not _host_allowed(host, allowlist_domains):
        _refuse("not_allowlisted", host=host)

    ips = resolver(host, port)
    if not ips:
        _refuse("dns", host=host)          # unresolvable → fail closed, never "allow because we don't know"

    for ip in ips:
        reason = ip_block_reason(ip)
        if reason:                         # ANY resolved IP blocked ⇒ refuse (multi-A split-horizon, rebind)
            _refuse(reason, host=host)

    return EgressTarget(scheme, host, port, tuple(ips))


def validate_connected_ip(ip: IPAddr | str) -> None:
    """DNS-rebinding / TOCTOU defense: call with the address the socket ACTUALLY connected to (post-connect),
    not a pre-resolve. Refuses fail-closed if that address is in the block-set. This is the last line even when
    the connection is pinned, defending the window between resolve and dial (FR-006/FR-008)."""
    addr = _as_ip(ip) if isinstance(ip, str) else ip
    if addr is None:
        _refuse("bad_connected_ip")
    reason = ip_block_reason(addr)
    if reason:
        _refuse(reason)


def validate_redirect(location: str, *, base_url: str, allowlist_domains: Iterable[str],
                      resolver: Resolver = _default_resolver) -> EgressTarget:
    """Re-validate a redirect hop through the SAME guard (FR-007). A relative `Location` is resolved against
    `base_url`; the resulting absolute target must pass the allow-list AND the IP block-set, so a redirect
    from an allowed public domain to an internal host (or off the allow-list) is refused. The executor MUST
    disable auto-redirects and call this for every hop — never hand redirect-following to the HTTP client."""
    target = urljoin(base_url, location or "")
    return validate_url(target, allowlist_domains=allowlist_domains, resolver=resolver)


def allowlist_for_tool(config_snapshot: dict | None) -> tuple[str, ...]:
    """Extract the per-tool egress allow-list from a tool binding's frozen `config_snapshot`.

    Contract (matches the shape seeded in tests/conftest `_tool_binding`): the allow-list is `config_snapshot
    ['egress']` as a list of domains, or the richer `{'egress': {'allow_domains': [...]}}` form. Returns an
    empty tuple when unset — deny-by-default (FR-008), never an implicit allow-all."""
    cfg = (config_snapshot or {}).get("egress")
    if isinstance(cfg, dict):
        cfg = cfg.get("allow_domains")
    if isinstance(cfg, (list, tuple)):
        return tuple(str(d) for d in cfg)
    if isinstance(cfg, str):
        return (cfg,)
    return ()


# ----------------------------------------------------------------------------------------------------------
# Integration seam (status: library built + tested; NOT yet wired — tool EXECUTION does not exist in P1).
#
# Today the runtime (services/runtime/invoke.py) only *validates* a model's tool call (name + args schema) and
# surfaces it UNEXECUTED (see test_invoke_tool_enforcement::test_valid_tool_call_surfaced_and_not_executed);
# there is no HTTP-tool executor and no outbound socket for a tool anywhere in the repo. The egress sandbox /
# gVisor proxy is P2 (09 §10). So this guard ships as a pure, independently-tested library with a concrete
# seam, exactly as the task + research R3 direct ("before the P2 egress sandbox exists").
#
# When the P1 HTTP-tool executor is built (in api/ or a sandbox caller — NOT in services/runtime/, which must
# stay import-pure, P6), it wires the guard like this:
#
#     allow = egress.allowlist_for_tool(tool_ref.config_snapshot)      # per-tool allow-list (FR-008)
#     target = egress.validate_url(url, allowlist_domains=allow)       # scheme + allow-list + resolve + IP block
#     # dial PINNED to target.ips[0] with the Host header = target.host, auto-redirects DISABLED:
#     sock = connect(target.ips[0], target.port)
#     egress.validate_connected_ip(sock.getpeername()[0])             # rebind/TOCTOU last line (FR-006/008)
#     # for each 3xx:
#     target = egress.validate_redirect(resp.headers["location"], base_url=url, allowlist_domains=allow)
#
# A refusal (EgressBlocked) is caught by the executor and fed back to the model as a sanitized tool-error
# result — the model never learns the internal target (FR-009). The guard is identity-/transport-agnostic so
# the same calls serve the P2 MCP/sandbox path unchanged.
# ----------------------------------------------------------------------------------------------------------
