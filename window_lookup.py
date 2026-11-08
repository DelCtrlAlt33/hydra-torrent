"""Find a top-level window by exact title AND by who owns it (Windows only).

Looking a window up by title alone (FindWindowW(None, title)) returns the first
window anywhere with that title, and another program can share it. Confirmed
live: a File Explorer tab or window open on a folder called "Hydra Torrent" has
a hidden shell window (Windows.Internal.Shell.TabProxyWindow) with exactly that
title, so minimize/maximize/resize acted on Explorer instead of Hydra. Every
lookup here therefore also checks the owning process.

No pywebview or Hydra imports on purpose, so it is importable and testable on
its own.
"""
import ctypes
import ctypes.wintypes as wt
import os

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

_dlls = None


def _win32():
    """user32/kernel32 handles with correct 64-bit signatures. Private WinDLL
    instances, so setting argtypes here can't change how other modules' calls
    through ctypes.windll behave."""
    global _dlls
    if _dlls is None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.EnumWindows.argtypes = [_ENUM_WINDOWS_PROC, wt.LPARAM]
        user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        user32.GetWindowThreadProcessId.restype = wt.DWORD
        user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        kernel32.OpenProcess.restype = wt.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
        kernel32.CloseHandle.argtypes = [wt.HANDLE]
        _dlls = (user32, kernel32)
    return _dlls


def _image_name(pid):
    """Lower-case file name of the process's executable, or '' if it can't be read."""
    _, kernel32 = _win32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def find_window(title, pid=None, exe_prefix=None):
    """hwnd of a top-level window titled exactly `title`, or 0.

    pid        only a window owned by this process id
    exe_prefix only a window whose owning process's executable name starts with
               this (case-insensitive), e.g. "hydratorrent" for another running
               copy of the app
    At least one of the two should be given; with neither this is the unsafe
    title-only lookup and is refused.
    """
    if pid is None and not exe_prefix:
        raise ValueError("find_window needs pid or exe_prefix; a title alone can match another program's window")
    user32, _ = _win32()
    prefix = exe_prefix.lower() if exe_prefix else None
    found = []

    def _visit(hwnd, _lparam):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if pid is not None and owner.value != pid:
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value != title:
            return True
        if prefix is not None and not _image_name(owner.value).startswith(prefix):
            return True
        found.append(hwnd)
        return False

    user32.EnumWindows(_ENUM_WINDOWS_PROC(_visit), 0)
    return found[0] if found else 0
