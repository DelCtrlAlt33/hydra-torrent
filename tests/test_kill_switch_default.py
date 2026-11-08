"""The kill switch defaults to off (so a user with no VPN isn't left with every
torrent paused), but with it off a VPN drop rebinds Hydra to the real network
and keeps downloading - verified against a real VPN drop. So the first time a
VPN is seen, Hydra turns the kill switch on once. These tests pin that down.
"""

import pytest

import hydra_daemon as hd


@pytest.fixture
def engine():
    store = hd.DaemonStore()
    eng = hd.TorrentEngine(store)
    eng._create_session("127.0.0.1")
    return eng


@pytest.fixture
def fake_config(monkeypatch):
    cfg = {}
    monkeypatch.setattr(hd, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(hd, "save_config", lambda k, v: cfg.__setitem__(k, v))
    return cfg


def test_first_vpn_sighting_turns_the_kill_switch_on(engine, fake_config):
    engine._on_vpn_change(True, "wg0", "10.0.0.5")
    assert fake_config.get("vpn_required") is True
    assert fake_config.get("vpn_kill_switch_auto_armed") is True


def test_it_is_a_one_time_default_so_turning_it_off_sticks(engine, fake_config):
    engine._on_vpn_change(True, "wg0", "10.0.0.5")
    fake_config["vpn_required"] = False  # the user switches it off in Settings
    engine._on_vpn_change(False, None, None)
    engine._on_vpn_change(True, "wg0", "10.0.0.5")  # VPN comes back
    assert fake_config["vpn_required"] is False


def test_no_vpn_never_arms_it(engine, fake_config):
    engine._on_vpn_change(False, None, None)
    assert "vpn_required" not in fake_config
    assert "vpn_kill_switch_auto_armed" not in fake_config


def test_already_on_stays_on_and_gets_marked(engine, fake_config):
    fake_config["vpn_required"] = True
    engine._on_vpn_change(True, "wg0", "10.0.0.5")
    assert fake_config["vpn_required"] is True
    assert fake_config["vpn_kill_switch_auto_armed"] is True


def test_a_config_error_does_not_break_vpn_handling(engine, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(hd, "load_config", lambda: {})
    monkeypatch.setattr(hd, "save_config", boom)
    engine._on_vpn_change(True, "wg0", "10.0.0.5")  # must not raise
    assert engine.vpn_ip == "10.0.0.5"
