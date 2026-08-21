"""Tests for ssrf_guard.py - regression coverage for the 2026-08-20 finding:
/transfers/url and /transfers/preview fetched an arbitrary client-supplied
URL server-side with no validation, letting anyone with the API key use
the daemon as a blind proxy into whatever network it's actually running
on (a real concern given this project's own homelab deployments put the
daemon on a LAN with real internal services).
"""
import pytest

from ssrf_guard import ensure_safe_fetch_url, trusted_host_from_url


def test_public_https_url_is_allowed():
    # Real public DNS name, real resolution - not mocked.
    ensure_safe_fetch_url("https://www.google.com/some/torrent/link")


def test_public_http_url_is_allowed():
    ensure_safe_fetch_url("http://www.google.com/some/torrent/link")


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/file",
    "gopher://example.com/",
    "data:text/plain;base64,aGVsbG8=",
])
def test_non_http_schemes_are_rejected(url):
    with pytest.raises(ValueError, match="only http/https"):
        ensure_safe_fetch_url(url)


@pytest.mark.parametrize("url,description", [
    ("http://127.0.0.1:8006/api2/json/nodes", "loopback (e.g. a local admin API)"),
    ("http://localhost:8006/", "localhost by name"),
    ("http://192.168.1.1/", "private LAN (RFC1918)"),
    ("http://10.0.0.5/", "private LAN (RFC1918)"),
    ("http://172.16.0.5/", "private LAN (RFC1918)"),
    ("http://169.254.169.254/latest/meta-data/", "link-local (cloud metadata endpoint)"),
    ("http://0.0.0.0/", "unspecified address"),
])
def test_internal_and_private_addresses_are_rejected(url, description):
    with pytest.raises(ValueError, match="internal/private"):
        ensure_safe_fetch_url(url)


def test_url_with_no_hostname_is_rejected():
    with pytest.raises(ValueError):
        ensure_safe_fetch_url("http:///just/a/path")


def test_unresolvable_hostname_is_rejected():
    with pytest.raises(ValueError, match="can't resolve"):
        ensure_safe_fetch_url("http://this-domain-should-never-exist-hydra-torrent-test.invalid/")


# ---------------------------------------------------------------------------
# trusted_hosts exemption - regression coverage for the 2026-08-22 finding:
# the guard above correctly blocks arbitrary private URLs, but was applied
# uniformly to /transfers/preview too, whose URL is never actually
# arbitrary - it always comes from the user's own configured Jackett
# server. Any indexer that returns a .torrent proxy link instead of a
# magnet URI (confirmed live: TorrentGalaxy) was silently unusable for
# every self-hosted Jackett on a private LAN, which is nearly all of them.
# ---------------------------------------------------------------------------

def test_trusted_host_from_url_extracts_host_and_port():
    assert trusted_host_from_url("http://192.168.20.40:9117/dl/x") == "192.168.20.40:9117"


def test_trusted_host_from_url_defaults_port_for_http():
    assert trusted_host_from_url("http://jackett.local/dl/x") == "jackett.local:80"


def test_trusted_host_from_url_defaults_port_for_https():
    assert trusted_host_from_url("https://jackett.example.com/dl/x") == "jackett.example.com:443"


@pytest.mark.parametrize("bad", ["", None, "not a url", "http://"])
def test_trusted_host_from_url_returns_none_for_unusable_input(bad):
    assert trusted_host_from_url(bad) is None


def test_trusted_private_host_is_allowed():
    # Same private LAN address the parametrized rejection test above
    # blocks by default - but now it's the explicitly-trusted indexer.
    ensure_safe_fetch_url("http://192.168.1.1:9117/dl/x", trusted_hosts={"192.168.1.1:9117"})


def test_trusted_hosts_does_not_blanket_exempt_other_private_addresses():
    # Trusting the configured Jackett host must not accidentally trust
    # every private address - a different private host is still rejected.
    with pytest.raises(ValueError, match="internal/private"):
        ensure_safe_fetch_url("http://10.0.0.99/admin", trusted_hosts={"192.168.1.1:9117"})


def test_trusted_hosts_matches_by_exact_host_and_port():
    # Same host, different port - still not trusted. Guards against a
    # too-loose match that would trust anything on the indexer's host.
    with pytest.raises(ValueError, match="internal/private"):
        ensure_safe_fetch_url("http://192.168.1.1:8080/", trusted_hosts={"192.168.1.1:9117"})
