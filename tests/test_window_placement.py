"""
window_placement.py is pure maths (no Win32), so these run on any OS.

The numbers in the first tests are the real ones seen on an affected laptop: a
1536x864 screen (1920x1080 at 125% scaling) where the window was found parked
at x=26214, and, once "maximized", at (-26214, 26214): +/-32768 divided by the
display scale, visible to Windows but on no screen.
"""
import pytest

from window_placement import fix_placement, resize_rect

SCREEN = (0, 0, 1536, 864)
WORK = (0, 0, 1536, 824)          # minus a 40px taskbar


def test_window_parked_far_off_the_right_edge_is_brought_back():
    win = (26214, 0, 26214 + 1212, 882)
    x, y, w, h = fix_placement(win, SCREEN, WORK)
    assert (w, h) == (1212, 824)                       # fits the usable screen
    assert 0 <= x and x + w <= 1536 and 0 <= y and y + h <= 824


def test_maximized_window_at_the_bogus_coordinates_is_brought_back():
    win = (-26214, 26214, -26214 + 989, 26214 + 612)
    x, y, w, h = fix_placement(win, SCREEN, WORK, maximized=True)
    assert 0 <= x and x + w <= 1536 and 0 <= y and y + h <= 824


def test_normal_window_fully_on_screen_is_left_alone():
    assert fix_placement((98, 85, 98 + 1100, 85 + 700), SCREEN, WORK) is None


def test_properly_maximized_window_is_left_alone():
    # Windows maximizes a frameless window slightly past the work area.
    assert fix_placement((-6, -6, 1543, 871), SCREEN, WORK, maximized=True) is None


def test_visible_but_taller_than_the_screen_is_shrunk_in_place():
    x, y, w, h = fix_placement((240, 0, 240 + 1212, 882), SCREEN, WORK)
    assert h == 824 and y == 0
    assert x == 240 and w == 1212                      # position kept, not re-centered


def test_a_sliver_that_is_barely_on_screen_counts_as_off_screen():
    # 40px of the window still overlaps the screen: effectively unreachable.
    win = (1536 - 40, 100, 1536 - 40 + 1100, 800)
    assert fix_placement(win, SCREEN, WORK) is not None


def test_window_on_a_second_monitor_to_the_left_is_fine():
    virtual = (-1920, 0, 1536, 1080)
    assert fix_placement((-1800, 100, -700, 800), virtual, WORK) is None


def test_never_larger_than_a_screen_smaller_than_the_minimum():
    x, y, w, h = fix_placement((26214, 0, 27426, 882), (0, 0, 700, 420), (0, 0, 700, 400))
    assert (w, h) == (700, 400)


# ---------------------------------------------------------------------------
# resize_rect
# ---------------------------------------------------------------------------

WIN = (100, 100, 1100, 700)       # 1000 x 600


def test_drag_right_edge_grows_width_only():
    assert resize_rect(WIN, 50, 0, 'right') == (100, 100, 1050, 600)


def test_drag_left_edge_moves_origin_by_exactly_the_size_change():
    assert resize_rect(WIN, -50, 0, 'left') == (50, 100, 1050, 600)


def test_drag_left_edge_at_minimum_width_does_not_slide_the_window():
    # 800 wide already: dragging the left edge further in must not move x.
    win = (100, 100, 900, 700)
    assert resize_rect(win, 200, 0, 'left') == (100, 100, 800, 600)


def test_drag_top_edge_at_minimum_height_does_not_slide_the_window():
    win = (100, 100, 1100, 600)       # 500 tall already
    assert resize_rect(win, 0, 150, 'top') == (100, 100, 1000, 500)


def test_corner_drag_changes_both_axes():
    assert resize_rect(WIN, 20, 30, 'bottomright') == (100, 100, 1020, 630)
    assert resize_rect(WIN, -20, -30, 'topleft') == (80, 70, 1020, 630)


@pytest.mark.parametrize("direction", ["left", "right", "top", "bottom",
                                       "topleft", "bottomright", "bottomleft"])
def test_result_never_smaller_than_the_minimum(direction):
    _, _, w, h = resize_rect((0, 0, 800, 500), 400, 400, direction)
    _, _, w2, h2 = resize_rect((0, 0, 800, 500), -400, -400, direction)
    assert min(w, w2) >= 800 and min(h, h2) >= 500
