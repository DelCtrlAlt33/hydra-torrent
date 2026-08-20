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
from urllib.parse import urlparse


def ensure_safe_fetch_url(url: str) -> None:
    """Raises ValueError if `url` isn't safe for the daemon to fetch itself.

    Deliberately scoped to just the two "fetch an arbitrary server-supplied
    URL" endpoints, not search.py's search_jackett(), which takes a
    server_url the user configured themselves in Settings - a different,
    already-trusted input; blocking private IPs there would break the
    extremely common case of a self-hosted Jackett on the LAN.

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
