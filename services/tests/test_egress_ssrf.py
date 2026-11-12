"""Red→Green suite — 017 M5 SSRF / Egress guard (F19). Constitution P9: written FIRST, failing on behavior
(the `api.egress` guard did not exist), not on import.

Infra-free & deterministic: DNS is an INJECTED resolver (never a real lookup), so the "resolves to a private
IP" / rebind / multi-A cases are exercised without network. The guard is a pure validator, so there is no
socket to mock — "zero bytes sent" (SC-003) is proven by asserting the refusal is raised BEFORE the resolver
(the thing that would dial) is ever consulted on the no-DNS paths.
"""
from __future__ import annotations

import ipaddress

import pytest

from api import egress, metrics
from api.egress import EgressTarget, validate_connected_ip, validate_redirect, validate_url
from api.errors import EgressBlocked

ALLOW = ("api.example.com",)


def _resolver(*addrs: str):
    """A deterministic resolver returning fixed IPs; records whether it was called (dial proxy)."""
    calls: list[tuple[str, int]] = []

    def r(host: str, port: int):
        calls.append((host, port))
        return [ipaddress.ip_address(a) for a in addrs]

    r.calls = calls  # type: ignore[attr-defined]
    return r


def _never_resolver(host: str, port: int):
    raise AssertionError("resolver (dial) must NOT be reached on a pre-DNS refusal — bytes would leak")


def _blocked_count(reason: str) -> float:
    return metrics.ac_egress_blocked_total.labels(reason=reason)._value.get()


# ---- scaffold --------------------------------------------------------------------------------------
def test_scaffold_imports():
    assert all(hasattr(egress, n) for n in
               ("validate_url", "validate_connected_ip", "validate_redirect", "ip_block_reason",
                "allowlist_for_tool", "EgressTarget"))


# ---- AS1 / FR-006: direct private / loopback / link-local / metadata are refused -------------------
@pytest.mark.parametrize("url, reason", [
    ("http://10.0.0.1/admin", "private"),
    ("http://192.168.1.1/", "private"),
    ("http://172.16.0.5/", "private"),
    ("http://127.0.0.1/", "loopback"),
    ("http://169.254.0.1/", "link_local"),
    ("http://169.254.169.254/latest/meta-data/", "metadata"),   # AWS/GCP IMDS
    ("http://0.0.0.0/", "unspecified"),
])
def test_literal_internal_ip_refused(url, reason):
    with pytest.raises(EgressBlocked) as ei:
        # allow-list the literal so we prove the IP block fires FIRST (even if allow-listed)
        validate_url(url, allowlist_domains=(url.split("//")[1].split("/")[0].split(":")[0],),
                     resolver=_never_resolver)
    assert ei.value.detail == {"reason": reason}


def test_literal_ip_block_precedes_dns_no_bytes_sent():
    # SC-003: a blocked literal never reaches the resolver (which would dial) → zero bytes leave.
    with pytest.raises(EgressBlocked):
        validate_url("http://169.254.169.254/", allowlist_domains=("169.254.169.254",),
                     resolver=_never_resolver)


# ---- FR-008: validate the RESOLVED IP, not just the hostname --------------------------------------
def test_allowlisted_host_resolving_private_is_refused():
    r = _resolver("10.1.2.3")
    with pytest.raises(EgressBlocked) as ei:
        validate_url("https://api.example.com/x", allowlist_domains=ALLOW, resolver=r)
    assert ei.value.detail == {"reason": "private"}
    assert r.calls == [("api.example.com", 443)]   # it DID resolve, then blocked on the IP


def test_allowlisted_host_resolving_metadata_is_refused():
    with pytest.raises(EgressBlocked) as ei:
        validate_url("https://api.example.com/", allowlist_domains=ALLOW,
                     resolver=_resolver("169.254.169.254"))
    assert ei.value.detail == {"reason": "metadata"}


# ---- Edge case: multi-A (one public, one private) → validate the set, refuse ----------------------
def test_multi_a_one_private_refused():
    with pytest.raises(EgressBlocked) as ei:
        validate_url("https://api.example.com/", allowlist_domains=ALLOW,
                     resolver=_resolver("93.184.216.34", "10.0.0.9"))   # public + private
    assert ei.value.detail == {"reason": "private"}


# ---- Edge case: IPv6 private/ULA/link-local + IPv4-mapped IPv6 ------------------------------------
@pytest.mark.parametrize("addr, reason", [
    ("::1", "loopback"),
    ("fe80::1", "link_local"),
    ("fc00::1", "private"),            # ULA
    ("fd12:3456::1", "private"),       # ULA
    ("::ffff:10.0.0.1", "private"),    # IPv4-mapped private smuggling
    ("::ffff:169.254.169.254", "metadata"),
])
def test_ipv6_ranges_blocked(addr, reason):
    with pytest.raises(EgressBlocked) as ei:
        validate_url("https://api.example.com/", allowlist_domains=ALLOW, resolver=_resolver(addr))
    assert ei.value.detail == {"reason": reason}


# ---- FR-027: fail closed on bad scheme / no host / DNS failure -----------------------------------
@pytest.mark.parametrize("url, reason", [
    ("file:///etc/passwd", "scheme"),
    ("ftp://api.example.com/", "scheme"),
    ("gopher://api.example.com/", "scheme"),
    ("https:///nohost", "no_host"),
])
def test_bad_scheme_or_host_fails_closed_before_dns(url, reason):
    with pytest.raises(EgressBlocked) as ei:
        validate_url(url, allowlist_domains=ALLOW, resolver=_never_resolver)  # never dials
    assert ei.value.detail == {"reason": reason}


def test_unresolvable_host_fails_closed():
    with pytest.raises(EgressBlocked) as ei:
        validate_url("https://api.example.com/", allowlist_domains=ALLOW, resolver=_resolver())  # []
    assert ei.value.detail == {"reason": "dns"}


# ---- FR-008: per-tool allow-list, deny-by-default ------------------------------------------------
def test_empty_allowlist_denies_even_public():
    with pytest.raises(EgressBlocked) as ei:
        validate_url("https://api.example.com/", allowlist_domains=(), resolver=_never_resolver)
    assert ei.value.detail == {"reason": "not_allowlisted"}


@pytest.mark.parametrize("host", ["notexample.com", "example.com.evil.com", "evil.com"])
def test_allowlist_rejects_lookalikes(host):
    with pytest.raises(EgressBlocked) as ei:
        validate_url(f"https://{host}/", allowlist_domains=("example.com",), resolver=_never_resolver)
    assert ei.value.detail == {"reason": "not_allowlisted"}


def test_allowlist_suffix_match_allows_subdomain():
    tgt = validate_url("https://api.example.com/v1", allowlist_domains=("example.com",),
                       resolver=_resolver("93.184.216.34"))
    assert isinstance(tgt, EgressTarget)
    assert tgt.host == "api.example.com" and tgt.port == 443
    assert tgt.ips == (ipaddress.ip_address("93.184.216.34"),)


def test_public_target_passes_and_returns_pinned_ips():
    tgt = validate_url("http://api.example.com:8080/x", allowlist_domains=ALLOW,
                       resolver=_resolver("93.184.216.34"))
    assert tgt.scheme == "http" and tgt.port == 8080
    assert tgt.ips == (ipaddress.ip_address("93.184.216.34"),)


# ---- AS2 / FR-007: redirects re-validated through the same guard ----------------------------------
def test_redirect_to_metadata_is_refused():
    with pytest.raises(EgressBlocked) as ei:
        validate_redirect("http://169.254.169.254/latest/", base_url="https://api.example.com/x",
                          allowlist_domains=ALLOW, resolver=_never_resolver)
    assert ei.value.detail == {"reason": "metadata"}


def test_redirect_to_internal_resolved_host_refused():
    with pytest.raises(EgressBlocked) as ei:
        validate_redirect("https://api.example.com/next", base_url="https://api.example.com/x",
                          allowlist_domains=ALLOW, resolver=_resolver("127.0.0.1"))
    assert ei.value.detail == {"reason": "loopback"}


def test_redirect_off_allowlist_refused():
    with pytest.raises(EgressBlocked) as ei:
        validate_redirect("https://evil.example.org/", base_url="https://api.example.com/x",
                          allowlist_domains=ALLOW, resolver=_never_resolver)
    assert ei.value.detail == {"reason": "not_allowlisted"}


def test_redirect_relative_against_base_stays_allowed():
    tgt = validate_redirect("/other-path", base_url="https://api.example.com/x",
                            allowlist_domains=ALLOW, resolver=_resolver("93.184.216.34"))
    assert tgt.host == "api.example.com"


# ---- AS3 / FR-008: DNS-rebinding defense via the CONNECTED IP -------------------------------------
@pytest.mark.parametrize("ip, reason", [
    ("10.0.0.5", "private"), ("127.0.0.1", "loopback"), ("169.254.169.254", "metadata"),
    ("fc00::1", "private"),
])
def test_connected_ip_rebind_blocked(ip, reason):
    with pytest.raises(EgressBlocked) as ei:
        validate_connected_ip(ip)   # the address the socket ACTUALLY dialed
    assert ei.value.detail == {"reason": reason}


def test_connected_ip_public_ok():
    assert validate_connected_ip("93.184.216.34") is None
    assert validate_connected_ip(ipaddress.ip_address("8.8.8.8")) is None


# ---- AS4 / FR-009: refusal is non-leaking + recorded ---------------------------------------------
def test_refusal_is_non_leaking():
    with pytest.raises(EgressBlocked) as ei:
        validate_url("https://api.example.com/secret?token=abc123", allowlist_domains=ALLOW,
                     resolver=_resolver("10.9.8.7"))
    exc = ei.value
    assert exc.code == "egress.blocked"
    assert exc.detail == {"reason": "private"}          # coarse reason ONLY
    # no resolved IP, URL, query, or secret leaks into the client-visible message/detail
    for leak in ("10.9.8.7", "token", "abc123", "api.example.com"):
        assert leak not in exc.message
        assert leak not in str(exc.detail)


def test_block_increments_metric():
    before = _blocked_count("private")
    with pytest.raises(EgressBlocked):
        validate_url("http://10.0.0.1/", allowlist_domains=("10.0.0.1",), resolver=_never_resolver)
    assert _blocked_count("private") == before + 1


# ---- operator extra deny CIDRs (settings addition) -----------------------------------------------
def test_extra_deny_cidr_blocks_otherwise_public_ip(monkeypatch):
    monkeypatch.setattr(egress, "_extra_deny_networks",
                        lambda: [ipaddress.ip_network("93.184.216.0/24")])
    with pytest.raises(EgressBlocked) as ei:
        validate_url("https://api.example.com/", allowlist_domains=ALLOW,
                     resolver=_resolver("93.184.216.34"))
    assert ei.value.detail == {"reason": "denylisted"}


# ---- allowlist_for_tool helper (the per-tool allow-list seam) -------------------------------------
@pytest.mark.parametrize("cfg, expected", [
    ({"url": "https://api.example.com", "egress": ["api.example.com"]}, ("api.example.com",)),
    ({"egress": {"allow_domains": ["a.com", "b.com"]}}, ("a.com", "b.com")),
    ({"egress": "a.com"}, ("a.com",)),
    ({}, ()),
    (None, ()),
])
def test_allowlist_for_tool(cfg, expected):
    assert egress.allowlist_for_tool(cfg) == expected
