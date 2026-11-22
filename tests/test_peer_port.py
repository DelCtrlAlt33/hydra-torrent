"""The BitTorrent listening port is configurable (peer_port) and applied live.

A VPN that forwards a port to you (PIA, ProtonVPN, AirVPN) picks the number
itself; without listening on it, nobody can connect in and seeding barely
works. Seen on LXC 108: a seeding torrent with 33 peers listed and 0 connected,
because PIA forwarded nothing and Hydra only ever listened on 6001.
"""

import socket
import time

import pytest

import hydra_daemon as hd


@pytest.fixture
def fake_config(monkeypatch):
    cfg = {}
    monkeypatch.setattr(hd, "load_config", lambda: dict(cfg))
    return cfg


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _listening(engine):
    return engine.ses.get_settings()["listen_interfaces"]


@pytest.mark.parametrize("value,expected", [
    (None, hd.PEER_PORT), (0, hd.PEER_PORT), ("", hd.PEER_PORT), ("junk", hd.PEER_PORT),
    (80, hd.PEER_PORT),        # privileged: refused, falls back
    (70000, hd.PEER_PORT),     # out of range
    (51413, 51413), ("51413", 51413),
])
def test_peer_port_from_config(fake_config, value, expected):
    if value is not None:
        fake_config["peer_port"] = value
    assert hd._peer_port() == expected


def test_new_session_listens_on_the_configured_port(fake_config):
    port = _free_port()
    fake_config["peer_port"] = port
    eng = hd.TorrentEngine(hd.DaemonStore())
    eng._create_session("127.0.0.1")
    assert _listening(eng) == f"127.0.0.1:{port}"


def test_changing_the_port_applies_live_and_keeps_the_address(fake_config):
    first, second = _free_port(), _free_port()
    fake_config["peer_port"] = first
    eng = hd.TorrentEngine(hd.DaemonStore())
    eng._create_session("127.0.0.1")
    fake_config["peer_port"] = second
    eng.apply_peer_port()
    assert _listening(eng) == f"127.0.0.1:{second}"
    # libtorrent opens the new socket asynchronously
    for _ in range(50):
        if eng.ses.listen_port() == second:
            break
        time.sleep(0.1)
    assert eng.ses.listen_port() == second


def test_vpn_rebind_uses_the_configured_port(fake_config):
    port = _free_port()
    fake_config["peer_port"] = port
    fake_config["vpn_kill_switch_auto_armed"] = True
    eng = hd.TorrentEngine(hd.DaemonStore())
    eng._create_session("127.0.0.1")
    eng._on_vpn_change(True, "wg0", "127.0.0.2")
    assert _listening(eng) == f"127.0.0.2:{port}"
