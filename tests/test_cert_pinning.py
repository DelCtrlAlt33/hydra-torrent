"""Tests for cert_pinning.py - trust-on-first-use TLS certificate pinning
for the desktop launchers' connection to their own daemon.

Against a real self-signed TLS server, not mocked ssl calls - this is
security-critical code where "the mock returned what I told it to" proves
nothing. Regression coverage for the 2026-08-20 finding: requests.*
verify=False on its own accepts ANY certificate, which matters because
hydra_tray.py's default mode connects to a daemon on a different machine.
"""
import datetime
import socket
import ssl
import threading

import pytest

from cert_pinning import daemon_cert_fingerprint, verify_daemon_identity


def _make_self_signed_cert(tmp_path, cn):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    certfile = tmp_path / f"{cn}.crt"
    keyfile = tmp_path / f"{cn}.key"
    keyfile.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(certfile), str(keyfile)


class _TlsTestServer:
    """A real (not mocked) TLS server on loopback, presenting one cert."""

    def __init__(self, certfile, keyfile):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile, keyfile)
        self._ctx = ctx
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self.port = self._sock.getsockname()[1]
        self._sock.listen(5)
        self._sock.settimeout(0.5)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            try:
                with self._ctx.wrap_socket(conn, server_side=True) as tls_conn:
                    tls_conn.recv(1)
            except Exception:
                pass

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)
        self._sock.close()


@pytest.fixture
def tls_server(tmp_path):
    """Starts a real TLS server with a fresh self-signed cert; yields a
    factory to swap in a *different* cert on the same port afterward, to
    simulate an impersonator taking over the address."""
    servers = []

    def _start(cn="daemon-A", port=None):
        certfile, keyfile = _make_self_signed_cert(tmp_path, cn)
        srv = _TlsTestServer(certfile, keyfile)
        if port is not None:
            # Reuse the exact same port as a previous server in this test
            srv._sock.close()
            srv._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv._sock.bind(("127.0.0.1", port))
            srv.port = port
            srv._sock.listen(5)
            srv._sock.settimeout(0.5)
        srv.start()
        servers.append(srv)
        import time as _time
        _time.sleep(0.2)  # let accept() loop actually start
        return srv

    yield _start

    for s in servers:
        s.stop()


def test_daemon_cert_fingerprint_matches_real_cert(tls_server):
    srv = tls_server("daemon-A")
    fp = daemon_cert_fingerprint("127.0.0.1", srv.port)
    assert fp is not None
    assert len(fp) == 64  # hex sha256


def test_daemon_cert_fingerprint_none_on_unreachable_host():
    # Nothing listening on this port
    fp = daemon_cert_fingerprint("127.0.0.1", 1, timeout=1)
    assert fp is None


def test_ssl_disabled_skips_pinning_entirely():
    trusted, cfg, msg = verify_daemon_identity({"daemon_use_ssl": False}, "127.0.0.1", 9999)
    assert trusted is True
    assert msg is None


def test_first_connect_trusts_and_pins(tls_server):
    srv = tls_server("daemon-A")
    cfg = {"daemon_use_ssl": True}

    trusted, updated_cfg, msg = verify_daemon_identity(cfg, "127.0.0.1", srv.port)

    assert trusted is True
    assert msg is not None and "first connect" in msg
    key = f"daemon_cert_fingerprint_127.0.0.1_{srv.port}"
    assert key in updated_cfg
    assert updated_cfg is not cfg  # caller must persist a NEW dict, original untouched
    assert key not in cfg


def test_reconnect_to_same_daemon_matches_pin(tls_server):
    srv = tls_server("daemon-A")
    cfg = {"daemon_use_ssl": True}
    _, cfg, _ = verify_daemon_identity(cfg, "127.0.0.1", srv.port)

    trusted, cfg2, msg = verify_daemon_identity(cfg, "127.0.0.1", srv.port)

    assert trusted is True
    assert msg is None  # routine match - nothing worth telling the user
    assert cfg2 is cfg  # unchanged config object, nothing new to persist


def test_impersonator_on_same_address_is_rejected(tls_server):
    """The core security property: a DIFFERENT certificate answering on the
    SAME host:port after the real one was already trusted must be refused,
    not silently accepted the way plain verify=False would."""
    real = tls_server("daemon-A")
    port = real.port
    cfg = {"daemon_use_ssl": True}
    trusted, cfg, _ = verify_daemon_identity(cfg, "127.0.0.1", port)
    assert trusted is True

    real.stop()
    impersonator = tls_server("daemon-B-impersonator", port=port)

    trusted, cfg_after, msg = verify_daemon_identity(cfg, "127.0.0.1", port)

    assert trusted is False
    assert "changed" in msg
    assert cfg_after is cfg  # refused - config NOT updated to trust the new cert


def test_unreachable_daemon_does_not_block_startup():
    # No server on this port - _wait_for_daemon's retry loop is what
    # actually handles "not up yet", this must not itself report untrusted.
    trusted, cfg, msg = verify_daemon_identity({"daemon_use_ssl": True}, "127.0.0.1", 1)
    assert trusted is True
    assert msg is None
