"""PyWebView JS API and frameless window helpers for Hydra Torrent."""

import ctypes
import ctypes.wintypes
import os
import threading
import time

import webview

from window_lookup import find_window
from window_placement import fix_placement, resize_rect

# ── Win32 constants ──────────────────────────────────────────────────────────

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
GWL_STYLE = -16
GWLP_WNDPROC = -4
WS_THICKFRAME = 0x00040000
WS_CAPTION = 0x00C00000
HTCAPTION = 2
HTCLIENT = 1
TITLEBAR_HEIGHT = 33           # px - must match .titlebar height in index.html
TITLEBAR_CONTROLS_WIDTH = 138  # px - space reserved for min/max/close buttons

# ── Win32 function signatures (64-bit) ───────────────────────────────────────

SetWindowLongPtrW = ctypes.windll.user32.SetWindowLongPtrW
SetWindowLongPtrW.restype = ctypes.c_int64
SetWindowLongPtrW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_int64]

CallWindowProcW = ctypes.windll.user32.CallWindowProcW
CallWindowProcW.restype = ctypes.c_int64
CallWindowProcW.argtypes = [ctypes.c_int64, ctypes.wintypes.HWND, ctypes.c_uint,
                            ctypes.c_uint64, ctypes.c_int64]

GetWindowRect = ctypes.windll.user32.GetWindowRect

WNDPROC = ctypes.CFUNCTYPE(ctypes.c_int64, ctypes.wintypes.HWND, ctypes.c_uint,
                           ctypes.c_uint64, ctypes.c_int64)

# ── Window state via Win32 ───────────────────────────────────────────────────
# pywebview's own minimize()/maximize()/move() misbehaved on a 125%-scaled
# display: the window ended up "maximized" at (-26214, 26214) or parked at
# x=26214, i.e. +/-32768 divided by the display scale, invisible but still
# "visible" to Windows, with the min/max buttons seemingly dead. Windows' own
# ShowWindow/SetWindowPos behave correctly there (verified on the affected
# laptop), so min/max/resize go through them directly, and a small watchdog
# rescues a window that still ends up off every screen.

SW_MAXIMIZE = 3
SW_MINIMIZE = 6
SW_RESTORE = 9
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
SPI_GETWORKAREA = 48
WINDOW_TITLE = 'Hydra Torrent'


def _find_hwnd(title=WINDOW_TITLE):
    """This process's own top-level window with the given title, or 0.

    Never title alone: another program can have a window with the same title
    (a File Explorer tab on a folder called "Hydra Torrent" does), and every
    minimize/maximize/resize then acted on that one. See window_lookup.py.
    """
    return find_window(title, pid=os.getpid())


def _window_rect(hwnd):
    r = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def _work_area():
    r = ctypes.wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0)
    return (r.left, r.top, r.right, r.bottom)


def _virtual_screen():
    gm = ctypes.windll.user32.GetSystemMetrics
    left, top = gm(SM_XVIRTUALSCREEN), gm(SM_YVIRTUALSCREEN)
    return (left, top, left + gm(SM_CXVIRTUALSCREEN), top + gm(SM_CYVIRTUALSCREEN))


def ensure_on_screen():
    """If the window is visible to Windows but effectively on no screen (or
    bigger than the usable screen), put it back. Returns True if it moved it.
    Skips minimized and hidden windows."""
    user32 = ctypes.windll.user32
    hwnd = _find_hwnd()
    if not hwnd or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return False
    maximized = bool(user32.IsZoomed(hwnd))
    fix = fix_placement(_window_rect(hwnd), _virtual_screen(), _work_area(), maximized=maximized)
    if fix is None:
        return False
    if maximized:
        # A maximized window parked off-screen: leave that state first, then
        # place the restored window.
        user32.ShowWindow(hwnd, SW_RESTORE)
        fix = fix_placement(_window_rect(hwnd), _virtual_screen(), _work_area())
        if fix is None:
            return True
    x, y, w, h = fix
    user32.SetWindowPos(hwnd, 0, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)
    return True


def _watch_window_placement():
    while True:
        time.sleep(1.5)
        try:
            ensure_on_screen()
        except Exception:
            pass


# ── Resize overlay JS (injected into page) ───────────────────────────────────

_RESIZE_JS = """
(function() {
    if (document.getElementById('resize-edges')) return;
    var B = 8;
    // The minimize/maximize/close buttons sit in the top-right of the title
    // bar. These invisible resize strips used to run across the whole top edge
    // and right edge, so a click near the top of a button started a resize drag
    // instead of pressing the button. Keep every strip clear of the controls.
    var T = 33;    // title bar height (TITLEBAR_HEIGHT)
    var C = 146;   // controls width plus margin (TITLEBAR_CONTROLS_WIDTH + 8)
    var edges = [
        {id:'r-left',   css:'left:0;top:0;width:'+B+'px;height:100%;cursor:ew-resize',              dir:'left'},
        {id:'r-right',  css:'right:0;top:'+T+'px;width:'+B+'px;height:calc(100% - '+T+'px);cursor:ew-resize', dir:'right'},
        {id:'r-top',    css:'left:0;top:0;width:calc(100% - '+C+'px);height:'+B+'px;cursor:ns-resize', dir:'top'},
        {id:'r-bottom', css:'left:0;bottom:0;width:100%;height:'+B+'px;cursor:ns-resize',           dir:'bottom'},
        {id:'r-tl',     css:'left:0;top:0;width:'+B*2+'px;height:'+B*2+'px;cursor:nwse-resize',     dir:'topleft'},
        {id:'r-bl',     css:'left:0;bottom:0;width:'+B*2+'px;height:'+B*2+'px;cursor:nesw-resize',  dir:'bottomleft'},
        {id:'r-br',     css:'right:0;bottom:0;width:'+B*2+'px;height:'+B*2+'px;cursor:nwse-resize', dir:'bottomright'},
    ];
    var dragging = null, lastX = 0, lastY = 0;
    edges.forEach(function(e) {
        var el = document.createElement('div');
        el.id = e.id;
        el.style.cssText = 'position:fixed;z-index:9999;' + e.css;
        el.addEventListener('mousedown', function(ev) {
            ev.preventDefault(); ev.stopPropagation();
            dragging = e.dir; lastX = ev.screenX; lastY = ev.screenY;
        });
        document.body.appendChild(el);
    });
    document.addEventListener('mousemove', function(ev) {
        if (!dragging) return;
        var dx = ev.screenX - lastX, dy = ev.screenY - lastY;
        lastX = ev.screenX; lastY = ev.screenY;
        if (window.pywebview && window.pywebview.api) {
            window.pywebview.api.do_resize(dx, dy, dragging);
        }
    });
    document.addEventListener('mouseup', function() { dragging = null; });
    document.body.insertAdjacentHTML('beforeend', '<span id="resize-edges"></span>');
})();
"""


# ── WebViewAPI ───────────────────────────────────────────────────────────────

class WebViewAPI:
    """JS-callable API for pywebview window controls (min/max/close)."""

    def __init__(self, hide_on_close: bool = False):
        self._window = None
        self._hide_on_close = hide_on_close

    def set_window(self, window):
        self._window = window

    def minimize(self):
        hwnd = _find_hwnd()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, SW_MINIMIZE)
        elif self._window:
            self._window.minimize()

    def maximize(self):
        hwnd = _find_hwnd()
        if hwnd:
            user32 = ctypes.windll.user32
            user32.ShowWindow(hwnd, SW_RESTORE if user32.IsZoomed(hwnd) else SW_MAXIMIZE)
        elif self._window:
            if self._window.maximized:
                self._window.restore()
            else:
                self._window.maximize()

    def is_maximized(self):
        """For the title bar: lets the page show the "restore" icon instead of
        "maximize" while the window is maximized (however it got maximized:
        the button, a title-bar double-click, Win+Up)."""
        hwnd = _find_hwnd()
        if hwnd:
            return bool(ctypes.windll.user32.IsZoomed(hwnd))
        return bool(self._window and self._window.maximized)

    def close(self):
        if self._window:
            if self._hide_on_close:
                self._window.hide()
            else:
                self._window.destroy()

    def size_to_content(self, width, height):
        """Resize the window to fit the page's actual rendered content
        (called once on first load with document.documentElement.scrollHeight)
        instead of guessing a fixed pixel size that's wrong on some
        screen/DPI/content-state combination. No-op if already maximized -
        respect that instead of fighting it."""
        if not self._window or self._window.maximized:
            return
        try:
            self._window.resize(int(width), int(height))
        except Exception:
            pass
        # The content can be taller than a small/scaled screen: fit it.
        try:
            ensure_on_screen()
        except Exception:
            pass

    def browse_folder(self, current_path=''):
        """Native folder picker for the add-torrent dialog's Save at field.
        Returns the chosen path, or None if cancelled or no window/dialog
        support (e.g. accessed as a plain web page instead of the desktop app)."""
        if not self._window:
            return None
        try:
            result = self._window.create_file_dialog(
                webview.FOLDER_DIALOG, directory=current_path or '')
        except Exception:
            return None
        return result[0] if result else None


# ── ResizeAPI ────────────────────────────────────────────────────────────────

class ResizeAPI(WebViewAPI):
    """Extends WebViewAPI with edge-resize support via JS overlay divs."""

    def do_resize(self, dx, dy, direction):
        """Called from JS mousemove handler on edge overlay divs."""
        # Windows' own rectangle and SetWindowPos, not pywebview's x/y/move,
        # which misreport positions on scaled displays (see ensure_on_screen).
        hwnd = _find_hwnd()
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
            return
        x, y, w, h = resize_rect(_window_rect(hwnd), dx, dy, direction)
        user32.SetWindowPos(hwnd, 0, int(x), int(y), int(w), int(h),
                            SWP_NOZORDER | SWP_NOACTIVATE)


# ── Frameless window helper ──────────────────────────────────────────────────

def apply_frameless(window, title='Hydra Torrent'):
    """Apply Win32 frameless styling to a pywebview window.

    - Removes the Windows title bar (WS_CAPTION)
    - Keeps resize capability (WS_THICKFRAME)
    - Eliminates the 1px DWM border (WM_NCCALCSIZE returns 0)
    - Enables drag from the custom HTML title bar (WM_NCHITTEST → HTCAPTION)
    - Injects JS resize overlays at all edges
    - Adds body.frameless class to show the custom HTML title bar

    Must be called AFTER the window's HWND exists (use in a background thread
    with a short delay, or from the window's shown/loaded event).
    """
    # We need to keep the callback alive for the lifetime of the window
    # so we store it on the window object itself.
    _old_proc = [0]

    def _wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_NCCALCSIZE and wparam:
            return 0  # client area = full window → no border
        if msg == WM_NCHITTEST:
            x = lparam & 0xFFFF
            if x > 32767:
                x -= 65536
            y = (lparam >> 16) & 0xFFFF
            if y > 32767:
                y -= 65536
            rect = ctypes.wintypes.RECT()
            GetWindowRect(hwnd, ctypes.byref(rect))
            local_y = y - rect.top
            local_x = x - rect.left
            win_w = rect.right - rect.left
            if local_y < TITLEBAR_HEIGHT and local_x < (win_w - TITLEBAR_CONTROLS_WIDTH):
                return HTCAPTION
            return HTCLIENT
        return CallWindowProcW(_old_proc[0], hwnd, msg, wparam, lparam)

    callback = WNDPROC(_wndproc)
    callback_ptr = ctypes.cast(callback, ctypes.c_void_p).value

    def _apply():
        time.sleep(2)  # wait for HWND to exist
        hwnd = 0
        for _ in range(20):            # up to ~10 more seconds on a slow start
            hwnd = _find_hwnd(title)
            if hwnd:
                break
            time.sleep(0.5)
        if not hwnd:
            return
        # Add thick frame (resize), remove caption (title bar)
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
        style |= WS_THICKFRAME
        style &= ~WS_CAPTION
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style)
        # Subclass the window proc
        _old_proc[0] = SetWindowLongPtrW(hwnd, GWLP_WNDPROC, callback_ptr)
        # Force redraw with new style
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0037)
        # SWP_FRAMECHANGED recalculates the non-client area but doesn't
        # trigger a real WM_SIZE, so the embedded WebView2 surface never
        # gets told to repaint - it can be left showing solid black
        # (background_color) even though the page is fully loaded and
        # live underneath. A 1px grow-then-shrink forces an actual size
        # change WebView2 has to repaint for, invisible to the user.
        rect = ctypes.wintypes.RECT()
        GetWindowRect(hwnd, ctypes.byref(rect))
        win_w = rect.right - rect.left
        win_h = rect.bottom - rect.top
        SWP_NOMOVE_NOZORDER = 0x0002 | 0x0004
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, win_w, win_h + 1, SWP_NOMOVE_NOZORDER)
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, win_w, win_h, SWP_NOMOVE_NOZORDER)

    def _on_loaded():
        window.evaluate_js('document.body.classList.add("frameless")')
        window.evaluate_js(_RESIZE_JS)

    # Store callback reference so it doesn't get garbage-collected
    window._frameless_callback = callback

    window.events.loaded += _on_loaded
    threading.Thread(target=_apply, daemon=True).start()
    threading.Thread(target=_watch_window_placement, daemon=True).start()
