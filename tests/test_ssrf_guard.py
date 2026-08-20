"""Tests for ssrf_guard.py - regression coverage for the 2026-08-20 finding:
/transfers/url and /transfers/preview fetched an arbitrary client-supplied
URL server-side with no validation, letting anyone with the API key use
the daemon as a blind proxy into whatever network it's actually running
on (a real concern given this project's own homelab deployments put the
daemon on a LAN with real internal services).
"""
import pytest

from ssrf_guard import ensure_safe_fetch_url


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
