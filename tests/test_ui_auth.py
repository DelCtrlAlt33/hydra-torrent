"""Tests for ui_auth.py - the optional web UI password."""

import pytest

import ui_auth


@pytest.fixture(autouse=True)
def _no_env_password(monkeypatch):
    monkeypatch.delenv(ui_auth.ENV_VAR, raising=False)


def _cfg(password="correct horse"):
    return {"ui_password_hash": ui_auth.hash_password(password, iterations=1000)}


def test_hash_roundtrip():
    stored = ui_auth.hash_password("hunter2hunter2", iterations=1000)
    assert ui_auth.verify_password("hunter2hunter2", stored)
    assert not ui_auth.verify_password("hunter2hunter3", stored)


def test_hash_is_salted():
    assert ui_auth.hash_password("same", iterations=1000) != ui_auth.hash_password("same", iterations=1000)


@pytest.mark.parametrize("bad", ["", "nonsense", "md5$1$aa$bb", "pbkdf2_sha256$x$zz$yy", None])
def test_verify_rejects_malformed(bad):
    assert not ui_auth.verify_password("anything", bad)


def test_no_password_means_no_login():
    assert ui_auth.password_source({}) == ""
    assert not ui_auth.password_required({})
    assert not ui_auth.check_password("anything", {})


def test_config_password():
    cfg = _cfg()
    assert ui_auth.password_source(cfg) == "config"
    assert ui_auth.check_password("correct horse", cfg)
    assert not ui_auth.check_password("wrong", cfg)


def test_env_password_takes_precedence(monkeypatch):
    monkeypatch.setenv(ui_auth.ENV_VAR, "from-the-env")
    cfg = _cfg("from-the-config")
    assert ui_auth.password_source(cfg) == "env"
    assert ui_auth.check_password("from-the-env", cfg)
    assert not ui_auth.check_password("from-the-config", cfg)


def test_session_roundtrip():
    cfg = _cfg()
    token = ui_auth.make_session("api-key", cfg, now=1000)
    assert ui_auth.check_session(token, "api-key", cfg, now=1001)


def test_session_expires():
    cfg = _cfg()
    token = ui_auth.make_session("api-key", cfg, now=1000, ttl=60)
    assert not ui_auth.check_session(token, "api-key", cfg, now=1061)


def test_session_revoked_by_password_change():
    token = ui_auth.make_session("api-key", _cfg("old password"), now=1000)
    assert not ui_auth.check_session(token, "api-key", _cfg("new password"), now=1001)


def test_session_revoked_by_api_key_rotation():
    cfg = _cfg()
    token = ui_auth.make_session("old-key", cfg, now=1000)
    assert not ui_auth.check_session(token, "new-key", cfg, now=1001)


def test_session_useless_once_password_removed():
    cfg = _cfg()
    token = ui_auth.make_session("api-key", cfg, now=1000)
    assert not ui_auth.check_session(token, "api-key", {}, now=1001)


@pytest.mark.parametrize("bad", [None, "", "garbage", "123", "abc.def", "9999999999.deadbeef"])
def test_session_rejects_forged(bad):
    assert not ui_auth.check_session(bad, "api-key", _cfg(), now=1000)


def test_tampered_expiry_rejected():
    cfg = _cfg()
    token = ui_auth.make_session("api-key", cfg, now=1000, ttl=60)
    _, sig = token.split(".")
    assert not ui_auth.check_session(f"9999999999.{sig}", "api-key", cfg, now=1001)


def test_throttle_locks_after_max_failures():
    t = ui_auth.LoginThrottle(max_failures=3, window=100)
    for i in range(3):
        assert t.retry_after("1.2.3.4", now=i) == 0
        t.record_failure("1.2.3.4", now=i)
    assert t.retry_after("1.2.3.4", now=3) > 0
    # other IPs are unaffected
    assert t.retry_after("5.6.7.8", now=3) == 0


def test_throttle_releases_after_window():
    t = ui_auth.LoginThrottle(max_failures=2, window=100)
    t.record_failure("ip", now=0)
    t.record_failure("ip", now=1)
    assert t.retry_after("ip", now=50) > 0
    assert t.retry_after("ip", now=101) == 0


def test_throttle_reset_on_success():
    t = ui_auth.LoginThrottle(max_failures=2, window=100)
    t.record_failure("ip", now=0)
    t.record_failure("ip", now=1)
    t.reset("ip")
    assert t.retry_after("ip", now=2) == 0


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True), ("::1", True), ("localhost", True),
    ("192.168.1.5", False), ("172.17.0.1", False), (None, False),
])
def test_is_loopback(host, expected):
    assert ui_auth.is_loopback(host) is expected
