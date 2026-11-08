"""SSRF guard for hydra_daemon.py's "fetch this URL server-side" endpoints.

/transfers/url and /transfers/preview both take a URL straight from the
request body (an indexer's "download this .torrent" link, or a manual
paste) and fetch it server-side with the daemon's own network access - a
textbook SSRF: anyone who can call the API (which, per this project's own
homelab-deployment docs, commonly means the daemon has real LAN reach to
Proxmox/TrueNAS/other internal services) could point the URL at an
internal admin endpoint instead of a torrent file and use the daemon as a
blind proxy into the network it's running on.

Deliberately not part of hydra_daemon.py: that module imports libtorrent
and fastapi, neither of which the test suite's scoped CI environment
installs, so a guard living there can't be unit-tested there. This has
zero dependencies beyond the standard library.
"""
import ipaddress
import socket
from typing import Optional, Set
from urllib.parse import urlparse


def trusted_host_from_url(url: str) -> Optional[str]:
    """
    Turns a configured server URL (e.g. indexing_server from Settings) into
    the same "host:port" key ensure_safe_fetch_url's trusted_hosts set
    expects. Returns None for a blank/unparseable URL, so callers can pass
    the result straight through without an extra guard.
    """
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.hostname:
        return None
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    return f"{parsed.hostname}:{port}"


def ensure_safe_fetch_url(url: str, trusted_hosts: Optional[Set[str]] = None) -> None:
    """Raises ValueError if `url` isn't safe for the daemon to fetch itself.

    trusted_hosts: exact "host:port" strings (build with trusted_host_from_url)
    that bypass the private/loopback check below. This exists specifically
    for the user's own configured Jackett indexer - Jackett's proxy
    download links for indexers that don't expose a direct magnet URI
    point back at Jackett itself, which for the extremely common case of a
    self-hosted Jackett on the LAN *is* a private address. That's not an
    arbitrary attacker-supplied URL, it's the same already-trusted input
    search.py's search_jackett() already gets a pass on - this guard was
    written assuming every caller needed protection against a genuinely
    arbitrary URL, but the search-result "fetch this torrent" path never
    actually receives one: it only ever gets a URL Jackett itself handed
    back, for the server the user configured. Blocking it there wasn't
    extra safety, it just silently broke every indexer that doesn't return
    magnets - confirmed live against a public tracker. The genuinely
    untrusted case (POST /transfers/url with a raw pasted string) still
    gets no exemption unless its URL happens to match the same trusted
    host, which is the correct, narrower fix rather than trusting either
    endpoint wholesale.

    Known limitation: this resolves and checks the hostname once, here. A
    DNS response that changes between this check and the actual request
    (DNS rebinding) isn't caught. That's a real, accepted gap - closing it
    fully would mean pinning the resolved IP and connecting to it directly
    instead of letting `requests` re-resolve, a bigger change than this
    guard's scope. Blocking every private/loopback/link-local range still
    closes the overwhelmingly common case.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise ValueError(f"Refusing to fetch '{url}': only http/https URLs are allowed")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"Refusing to fetch '{url}': no hostname")

    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    if trusted_hosts and f"{hostname}:{port}" in trusted_hosts:
        return

    try:
        addrs = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise ValueError(f"Refusing to fetch '{url}': can't resolve hostname ({e})")

    for family, _, _, _, sockaddr in addrs:
        ip = ipaddress.ip_address(sockaddr[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ValueError(
                f"Refusing to fetch '{url}': resolves to {ip}, an internal/private "
                f"address. This endpoint is for torrent/indexer download links, not "
                f"for fetching arbitrary internal URLs."
            )
