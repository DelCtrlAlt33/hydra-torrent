"""The startup registry command the tray writes.

A packaged build used to register `"<running exe>" "<_MEIxxxx>\\hydra_tray.py"`:
the running exe could be a copy in Downloads, and the script path is inside
PyInstaller's temp folder, which no longer exists after exit.
"""
import pytest

pytest.importorskip("winreg")   # hydra_tray is Windows-only

import hydra_tray


class _FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.written = {}

    def OpenKey(self, *a, **k):
        class _Ctx:
            def __enter__(s):
                return s

            def __exit__(s, *x):
                return False
        return _Ctx()

    def SetValueEx(self, key, name, _r, _t, value):
        self.written[name] = value


@pytest.fixture
def fake_reg(monkeypatch):
    reg = _FakeWinreg()
    monkeypatch.setattr(hydra_tray, "winreg", reg)
    return reg


def test_packaged_build_registers_the_installed_exe_with_no_script_argument(fake_reg, monkeypatch, tmp_path):
    installed = tmp_path / "HydraTorrent.exe"
    installed.write_bytes(b"x")
    downloads_copy = tmp_path / "Downloads" / "HydraTorrent-latest.exe"
    monkeypatch.setattr(hydra_tray, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(hydra_tray.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hydra_tray.sys, "executable", str(downloads_copy))

    hydra_tray._enable_startup()

    assert fake_reg.written["HydraTorrent"] == f'"{installed}"'


def test_packaged_build_without_installed_copy_uses_running_exe(fake_reg, monkeypatch, tmp_path):
    running = tmp_path / "hydra_tray.exe"       # e.g. the standalone remote tray client
    monkeypatch.setattr(hydra_tray, "_INSTALL_DIR", str(tmp_path / "nothing_here"))
    monkeypatch.setattr(hydra_tray.sys, "frozen", True, raising=False)
    monkeypatch.setattr(hydra_tray.sys, "executable", str(running))

    hydra_tray._enable_startup()

    assert fake_reg.written["HydraTorrent"] == f'"{running}"'
