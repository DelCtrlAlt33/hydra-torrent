"""Hydra must find its window by owner, never by title alone.

FindWindowW(None, "Hydra Torrent") returns the first window anywhere with that
title. A File Explorer tab open on a folder called "Hydra Torrent" has a hidden
shell window with exactly that title, and every minimize/maximize/resize (and
the frame styling) then acted on Explorer instead of Hydra.

webview_api.py and hydra_app.py can't be imported off Windows (and hydra_app
exits at import if another instance is running), so those are checked on their
source; window_lookup.py is exercised for real on Windows.
"""
import ctypes
import os
import re
import sys

import pytest


def _code(name):
    """Source with comments and docstrings removed, so a mention in prose doesn't count."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), name)
    with open(path, encoding="utf-8") as f:
        src = f.read()
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))


@pytest.mark.parametrize("module", ["webview_api.py", "hydra_app.py"])
def test_no_title_only_window_lookup(module):
    assert "FindWindowW(None" not in _code(module)


def test_webview_api_finds_its_window_through_the_owner_checked_helper():
    code = _code("webview_api.py")
    assert "find_window(title, pid=os.getpid())" in code


def test_second_instance_fallback_only_matches_another_copy_of_hydra():
    code = _code("hydra_app.py")
    assert "find_window(" in code and "exe_prefix=" in code


windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Win32 window lookup")


@windows_only
def test_find_window_refuses_a_title_only_lookup():
    import window_lookup
    with pytest.raises(ValueError):
        window_lookup.find_window("Program Manager")


@windows_only
def test_windows_owned_by_another_program_are_not_matched():
    """'Program Manager' is a real window owned by explorer.exe, the same kind of
    situation as Explorer's "Hydra Torrent" tab window."""
    import window_lookup
    assert ctypes.windll.user32.FindWindowW(None, "Program Manager"), "expected the desktop window to exist"
    assert window_lookup.find_window("Program Manager", pid=os.getpid()) == 0
    assert window_lookup.find_window("Program Manager", exe_prefix="hydratorrent") == 0
    assert window_lookup.find_window("Program Manager", exe_prefix="explorer") != 0


@windows_only
def test_own_window_is_found_by_pid_and_by_executable_name():
    import window_lookup
    user32 = ctypes.windll.user32
    user32.CreateWindowExW.restype = ctypes.c_void_p
    title = "Hydra Torrent (window lookup test)"
    hwnd = user32.CreateWindowExW(0, "STATIC", title, 0, 0, 0, 10, 10, None, None, None, None)
    assert hwnd, "could not create a test window"
    try:
        assert window_lookup.find_window(title, pid=os.getpid()) == hwnd
        # this test process is python.exe (or pythonw/py launcher): same rule the
        # second-instance fallback uses for a script run
        assert window_lookup.find_window(title, exe_prefix="python") == hwnd
        assert window_lookup.find_window(title, exe_prefix="hydratorrent") == 0
        assert window_lookup.find_window(title, pid=os.getpid() + 1) == 0
    finally:
        user32.DestroyWindow(ctypes.c_void_p(hwnd))
