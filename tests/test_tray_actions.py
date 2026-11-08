"""Tray menu actions: Pause All, Resume All and Exit.

hydra_tray needs winreg (Windows), so this is skipped elsewhere, like
test_tray_notifications.py. The HTTP layer is replaced with fakes; nothing here
talks to a real daemon.
"""
import sys

import pytest

pytest.importorskip("winreg")
pytest.importorskip("pystray")

import hydra_tray
from hydra_tray import HydraTray


class _Resp:
    def __init__(self, data=None, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


@pytest.fixture
def tray():
    t = HydraTray(desktop_mode=False)
    t._daemon_url = "http://127.0.0.1:8765"
    t._api_key = "k"
    return t


def _fake_http(monkeypatch, transfers, get_status=200, failing=()):
    posts = []

    def fake_get(url, **kw):
        return _Resp(transfers, get_status)

    def fake_post(url, **kw):
        posts.append(url)
        if any(f in url for f in failing):
            raise TimeoutError("daemon did not answer")
        return _Resp({"ok": True})

    monkeypatch.setattr(hydra_tray.requests, "get", fake_get)
    monkeypatch.setattr(hydra_tray.requests, "post", fake_post)
    return posts


BASE = "http://127.0.0.1:8765/transfers/"


def test_pause_all_pauses_only_transfers_that_are_running(tray, monkeypatch):
    posts = _fake_http(monkeypatch, [
        {"name": "running", "paused": False},
        {"name": "already-paused", "paused": True},
    ])
    tray._action_pause_all(None, None)
    assert posts == [BASE + "running/pause"]


def test_resume_all_resumes_only_transfers_that_are_paused(tray, monkeypatch):
    posts = _fake_http(monkeypatch, [
        {"name": "running", "paused": False},
        {"name": "already-paused", "paused": True},
    ])
    tray._action_resume_all(None, None)
    assert posts == [BASE + "already-paused/resume"]


def test_names_with_slashes_brackets_and_accents_are_url_quoted(tray, monkeypatch):
    posts = _fake_http(monkeypatch, [{"name": "Show [S01]/Raphaël", "paused": False}])
    tray._action_pause_all(None, None)
    assert posts == [BASE + "Show%20%5BS01%5D%2FRapha%C3%ABl/pause"]


def test_one_failing_transfer_does_not_stop_the_rest(tray, monkeypatch, capsys):
    """Used to be one try/except around the whole loop: a timeout on the 2nd
    transfer meant the 3rd, 4th ... were never paused, with no sign why."""
    posts = _fake_http(monkeypatch,
                       [{"name": n, "paused": False} for n in ("a", "b", "c", "d")],
                       failing=("/b/",))
    tray._action_pause_all(None, None)
    assert posts == [BASE + "a/pause", BASE + "b/pause", BASE + "c/pause", BASE + "d/pause"]
    err = capsys.readouterr().err
    assert "'b'" in err and "1 transfer(s) could not be updated" in err


def test_daemon_error_or_unreachable_does_nothing(tray, monkeypatch):
    posts = _fake_http(monkeypatch, [{"name": "x", "paused": False}], get_status=500)
    tray._action_pause_all(None, None)
    assert posts == []

    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(hydra_tray.requests, "get", boom)
    tray._action_resume_all(None, None)        # must not raise
    assert posts == []


# ---------------------------------------------------------------------------
# Exit: the desktop app must save state before os._exit (which skips the
# daemon's normal shutdown, where fast-resume data is written).
# ---------------------------------------------------------------------------

class _FakeEngine:
    def __init__(self, order, raises=False):
        self.order, self.raises = order, raises

    def shutdown(self):
        self.order.append("shutdown")
        if self.raises:
            raise RuntimeError("disk full")


def _install_fake_daemon(monkeypatch, order, raises=False):
    fake = type(sys)("hydra_daemon")
    fake.engine = _FakeEngine(order, raises)
    monkeypatch.setitem(sys.modules, "hydra_daemon", fake)


def test_desktop_exit_saves_state_before_exiting(tray, monkeypatch):
    order = []
    _install_fake_daemon(monkeypatch, order)
    monkeypatch.setattr(hydra_tray.os, "_exit", lambda code: order.append(f"exit({code})"))
    tray._desktop_mode = True
    tray._action_exit(None, None)
    assert order == ["shutdown", "exit(0)"]


def test_desktop_exit_still_exits_if_saving_state_fails(tray, monkeypatch):
    order = []
    _install_fake_daemon(monkeypatch, order, raises=True)
    monkeypatch.setattr(hydra_tray.os, "_exit", lambda code: order.append(f"exit({code})"))
    tray._desktop_mode = True
    tray._action_exit(None, None)
    assert order == ["shutdown", "exit(0)"]


def test_remote_tray_exit_does_not_touch_the_daemon_or_kill_the_process(tray, monkeypatch):
    order = []
    _install_fake_daemon(monkeypatch, order)
    monkeypatch.setattr(hydra_tray.os, "_exit", lambda code: order.append("exit"))
    tray._desktop_mode = False
    tray._action_exit(None, None)
    assert order == []
