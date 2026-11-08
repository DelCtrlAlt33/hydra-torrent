"""
Pure window-placement maths for the frameless desktop window. No Win32 or
pywebview imports on purpose, so it can be unit-tested anywhere.

Why this exists: on a 125%-scaled display the window was seen parked at
x = 26214 (and, while "maximized", at (-26214, 26214)) - that is the
+/-32768 "hidden window" placeholder coordinate divided by the display scale,
used as if it were a real position. The window was visible to Windows but not
on any screen, so it looked like Hydra had vanished and the min/max buttons
seemed dead. These helpers decide when a window is effectively off-screen and
where to put it back, and compute edge-resize rectangles without drift.

All rectangles are (left, top, right, bottom) in the same coordinate space the
caller reads them in (GetWindowRect / GetSystemMetrics).
"""

MIN_SIZE = (800, 500)       # matches the min_size given to the window
MIN_VISIBLE = (100, 50)     # at least this much of the window must be on a screen


def fix_placement(win, virtual, work, maximized=False,
                  min_size=MIN_SIZE, min_visible=MIN_VISIBLE):
    """Return None if the window is fine, else the (x, y, w, h) to move it to.

    win      - the window's current rectangle
    virtual  - the bounding rectangle of all screens
    work     - the primary screen's work area (screen minus taskbar)
    maximized - a maximized window is legitimately a bit larger than the work
                area (it overshoots by its border), so only an off-screen
                maximized window is treated as broken, never an oversized one.
    """
    wl, wt, wr, wb = win
    vl, vt, vr, vb = virtual
    kl, kt, kr, kb = work

    w, h = wr - wl, wb - wt
    work_w, work_h = kr - kl, kb - kt

    overlap_w = min(wr, vr) - max(wl, vl)
    overlap_h = min(wb, vb) - max(wt, vt)
    offscreen = overlap_w < min_visible[0] or overlap_h < min_visible[1]
    too_big = (w > work_w or h > work_h) and not maximized

    if not offscreen and not too_big:
        return None

    # Never larger than the usable screen, never smaller than the minimum
    # (unless the screen itself is smaller than the minimum).
    new_w = min(max(w, min_size[0]), work_w)
    new_h = min(max(h, min_size[1]), work_h)

    if offscreen:
        x = kl + (work_w - new_w) // 2
        y = kt + (work_h - new_h) // 2
    else:
        # Visible but too big: shrink in place, then nudge so it fits.
        x = min(max(wl, kl), kr - new_w)
        y = min(max(wt, kt), kb - new_h)
    return x, y, new_w, new_h


def resize_rect(win, dx, dy, direction, min_size=MIN_SIZE):
    """Return the (x, y, w, h) after dragging an edge/corner by (dx, dy).

    Dragging the left or top edge moves the origin only by what the size
    actually changed, so a window already at its minimum size stops instead of
    sliding across the screen (the old code moved the origin by the full drag
    even when the size was clamped).
    """
    wl, wt, wr, wb = win
    x, y = wl, wt
    w, h = wr - wl, wb - wt

    if 'right' in direction:
        w = max(w + dx, min_size[0])
    if 'bottom' in direction:
        h = max(h + dy, min_size[1])
    if 'left' in direction:
        new_w = max(w - dx, min_size[0])
        x += w - new_w
        w = new_w
    if 'top' in direction:
        new_h = max(h - dy, min_size[1])
        y += h - new_h
        h = new_h
    return x, y, w, h
