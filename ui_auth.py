"""
Optional password for the web UI.

Why this exists: /ui embeds the daemon's API key in the page it serves, so
anyone who can load /ui can control Hydra. Until now the only protection was
network reach (keep 8765 on your LAN). With a password set, /ui (and the
/download/tray bundle, which also carries the key) first shows a login page,
and only hands out the key once the browser holds a valid session cookie.

The API key is still the credential for every API call; the password only
gates who can get the key from the browser. That keeps the tray, scripts and
anything else already using the key working unchanged.

Stdlib only (hashlib/hmac/secrets) so it can be unit-tested without
libtorrent or FastAPI, and so a Docker image needs nothing extra.

Where the password comes from, in order:
  1. HYDRA_PASSWORD environment variable (easiest for Docker / Unraid).
  2. ui_password_hash in hydra_config.json (set from Settings in the UI).
If neither is set, no login is required (the old behaviour).

Forgot it? Remove ui_password_hash from hydra_config.json (or unset
HYDRA_PASSWORD) and restart Hydra.
"""

import hashlib
import hmac
import os
import secrets
import threading
import time
from typing import Dict, List, Optional

COOKIE_NAME = 'hydra_session'
SESSION_TTL = 30 * 24 * 3600          # stay logged in for 30 days
PBKDF2_ITERATIONS = 200_000
MIN_PASSWORD_LENGTH = 8
ENV_VAR = 'HYDRA_PASSWORD'

# Brute-force throttle: this many wrong passwords from one IP inside the
# window locks that IP out until the oldest failure ages out.
MAX_FAILURES = 5
FAILURE_WINDOW = 300.0                # seconds


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(password: str, salt: Optional[bytes] = None,
                  iterations: int = PBKDF2_ITERATIONS) -> str:
    """PBKDF2-SHA256, stored as pbkdf2_sha256$<iterations>$<salt hex>$<hash hex>."""
    salt = salt if salt is not None else secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, hash_hex = stored.split('$')
        if algo != 'pbkdf2_sha256':
            return False
        dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                                 bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


# ---------------------------------------------------------------------------
# Where the password lives
# ---------------------------------------------------------------------------

def env_password() -> str:
    return os.environ.get(ENV_VAR, '')


def password_source(cfg: dict) -> str:
    """'env', 'config', or '' (no password set)."""
    if env_password():
        return 'env'
    if cfg.get('ui_password_hash'):
        return 'config'
    return ''


def password_required(cfg: dict) -> bool:
    return bool(password_source(cfg))


def check_password(password: str, cfg: dict) -> bool:
    source = password_source(cfg)
    if source == 'env':
        return hmac.compare_digest(password.encode('utf-8'), env_password().encode('utf-8'))
    if source == 'config':
        return verify_password(password, cfg['ui_password_hash'])
    return False


def _fingerprint(cfg: dict) -> str:
    """Changes whenever the password changes, so changing it logs everyone out."""
    source = password_source(cfg)
    if source == 'env':
        return 'env:' + hashlib.sha256(env_password().encode('utf-8')).hexdigest()
    if source == 'config':
        return 'cfg:' + cfg['ui_password_hash']
    return ''


# ---------------------------------------------------------------------------
# Sessions: <expiry>.<hmac>, signed with the API key + password fingerprint.
# Stateless, so sessions survive a daemon restart (Hydra restarts on every
# deploy), and rotating either the API key or the password revokes them all.
# ---------------------------------------------------------------------------

def _session_key(secret: str, cfg: dict) -> bytes:
    return hmac.new(secret.encode('utf-8'), _fingerprint(cfg).encode('utf-8'),
                    hashlib.sha256).digest()


def make_session(secret: str, cfg: dict, now: Optional[float] = None,
                 ttl: int = SESSION_TTL) -> str:
    expiry = int((now if now is not None else time.time()) + ttl)
    sig = hmac.new(_session_key(secret, cfg), str(expiry).encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{sig}"


def check_session(token: Optional[str], secret: str, cfg: dict,
                  now: Optional[float] = None) -> bool:
    if not token or not password_required(cfg):
        return False
    try:
        expiry_str, sig = token.split('.', 1)
        expiry = int(expiry_str)
    except (ValueError, AttributeError):
        return False
    if expiry < (now if now is not None else time.time()):
        return False
    expected = hmac.new(_session_key(secret, cfg), expiry_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


# ---------------------------------------------------------------------------
# Login throttle
# ---------------------------------------------------------------------------

class LoginThrottle:
    def __init__(self, max_failures: int = MAX_FAILURES, window: float = FAILURE_WINDOW):
        self._max = max_failures
        self._window = window
        self._failures: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, ip: str, now: float) -> List[float]:
        recent = [t for t in self._failures.get(ip, []) if now - t < self._window]
        if recent:
            self._failures[ip] = recent
        else:
            self._failures.pop(ip, None)
        return recent

    def retry_after(self, ip: str, now: Optional[float] = None) -> int:
        """Seconds until this IP may try again; 0 if it may try now."""
        now = now if now is not None else time.monotonic()
        with self._lock:
            recent = self._recent(ip, now)
            if len(recent) < self._max:
                return 0
            return max(1, int(self._window - (now - recent[0])) + 1)

    def record_failure(self, ip: str, now: Optional[float] = None) -> None:
        now = now if now is not None else time.monotonic()
        with self._lock:
            self._recent(ip, now)
            self._failures.setdefault(ip, []).append(now)

    def reset(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)


def is_loopback(host: Optional[str]) -> bool:
    return host in ('127.0.0.1', '::1', 'localhost')
