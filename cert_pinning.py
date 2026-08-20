"""Trust-on-first-use TLS certificate pinning for the desktop launchers'
connection to their own daemon.

hydra_app.py and hydra_tray.py both talk to the daemon over requests with
verify=False, because the daemon's cert is self-signed - there's no CA for
`requests` to check it against. On its own, verify=False accepts ANY
certificate from ANY server presenting on that host:port, which matters:
hydra_tray.py's default mode is "Remote/Server" (the daemon runs on a
different machine, not localhost), exactly the situation where a network
position between the two could otherwise impersonate the daemon undetected
and harvest the API key.

This pins the daemon's actual certificate fingerprint the first time it's
seen (like an SSH host key) and requires every later connection to match.
A changed fingerprint could mean the daemon was legitimately reinstalled or
its cert regenerated - or it could mean something is impersonating it.
Either way that's a decision for the user, not something to silently wave
through.

Deliberately zero platform-specific imports (no ctypes, no winreg) so this
can be unit-tested on any OS, independent of hydra_app.py / hydra_tray.py
themselves - both of which are Windows-only at module import time and
cannot be imported on the Linux CI runner.
"""
import hashlib
import ssl


def daemon_cert_fingerprint(host: str, port, timeout: float = 5.0):
    """SHA-256 fingerprint of the daemon's TLS certificate, or None if it
    can't be fetched (daemon not up yet, plain HTTP, unreachable - the
    actual request that follows will surface that error on its own)."""
    try:
        pem = ssl.get_server_certificate((host, int(port)), timeout=timeout)
        der = ssl.PEM_cert_to_DER_cert(pem)
        return hashlib.sha256(der).hexdigest()
    except Exception:
        return None


def verify_daemon_identity(cfg: dict, host: str, port, fetch_fingerprint=daemon_cert_fingerprint):
    """Check (and on first use, establish) the pinned fingerprint for this
    daemon.

    Returns (trusted, updated_cfg, message):
      - trusted: False only on an actual fingerprint mismatch. True if SSL
        isn't in use, the daemon couldn't be reached yet, or the
        fingerprint matches what's pinned.
      - updated_cfg: cfg, or a copy with the newly-pinned fingerprint added
        on first use. This module never touches disk - the caller persists
        it if it changed (`updated_cfg is not cfg`).
      - message: a human-readable line to log, or None if there's nothing
        worth telling the user (SSL off, or a routine match).
    """
    if not cfg.get('daemon_use_ssl', False):
        return True, cfg, None

    fp = fetch_fingerprint(host, port)
    if fp is None:
        return True, cfg, None

    key = f'daemon_cert_fingerprint_{host}_{port}'
    pinned = cfg.get(key)

    if pinned is None:
        updated = dict(cfg)
        updated[key] = fp
        return True, updated, f"Trusting daemon certificate at {host}:{port} on first connect ({fp[:16]}...)"

    if pinned != fp:
        return False, cfg, (
            f"WARNING: daemon certificate at {host}:{port} has changed since it was "
            f"first trusted. Refusing to connect - if you just reinstalled the daemon "
            f"or regenerated its cert, delete '{key}' from hydra_config.json to re-trust it."
        )

    return True, cfg, None
