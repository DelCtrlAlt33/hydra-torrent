"""PyWebView JS API and frameless window helpers for Hydra Torrent."""

import ctypes
import ctypes.wintypes
import threading
import time

import webview

# ── Win32 constants ──────────────────────────────────────────────────────────

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
GWL_STYLE = -16
GWLP_WNDPROC = -4
WS_THICKFRAME = 0x00040000
WS_CAPTION = 0x00C00000
HTCAPTION = 2
HTCLIENT = 1
TITLEBAR_HEIGHT = 33           # px — must match .titlebar height in index.html
TITLEBAR_CONTROLS_WIDTH = 138  # px — space reserved for min/max/close buttons

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

# ── Resize overlay JS (injected into page) ───────────────────────────────────

_RESIZE_JS = """
(function() {
    if (document.getElementById('resize-edges')) return;
    var B = 8;
    var edges = [
        {id:'r-left',   css:'left:0;top:0;width:'+B+'px;height:100%;cursor:ew-resize',              dir:'left'},
        {id:'r-right',  css:'right:0;top:0;width:'+B+'px;height:100%;cursor:ew-resize',             dir:'right'},
        {id:'r-top',    css:'left:0;top:0;width:100%;height:'+B+'px;cursor:ns-resize',              dir:'top'},
        {id:'r-bottom', css:'left:0;bottom:0;width:100%;height:'+B+'px;cursor:ns-resize',           dir:'bottom'},
        {id:'r-tl',     css:'left:0;top:0;width:'+B*2+'px;height:'+B*2+'px;cursor:nwse-resize',     dir:'topleft'},
        {id:'r-tr',     css:'right:0;top:0;width:'+B*2+'px;height:'+B*2+'px;cursor:nesw-resize',    dir:'topright'},
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
        if self._window:
            self._window.minimize()

    def maximize(self):
        if self._window:
            if self._window.maximized:
                self._window.restore()
            else:
                self._window.maximize()

    def close(self):
        if self._window:
            if self._hide_on_close:
                self._window.hide()
            else:
                self._window.destroy()


# ── ResizeAPI ────────────────────────────────────────────────────────────────

class ResizeAPI(WebViewAPI):
    """Extends WebViewAPI with edge-resize support via JS overlay divs."""

    def do_resize(self, dx, dy, direction):
        """Called from JS mousemove handler on edge overlay divs."""
        if not self._window:
            return
        x, y = self._window.x, self._window.y
        w, h = self._window.width, self._window.height

        if 'right' in direction:
            w += dx
        if 'bottom' in direction:
            h += dy
        if 'left' in direction:
            w -= dx
            x += dx
        if 'top' in direction:
            h -= dy
            y += dy

        w = max(w, 800)
        h = max(h, 500)
        self._window.resize(w, h)
        self._window.move(x, y)


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
        hwnd = ctypes.windll.user32.FindWindowW(None, title)
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

    def _on_loaded():
        window.evaluate_js('document.body.classList.add("frameless")')
        window.evaluate_js(_RESIZE_JS)

    # Store callback reference so it doesn't get garbage-collected
    window._frameless_callback = callback

    window.events.loaded += _on_loaded
    threading.Thread(target=_apply, daemon=True).start()
