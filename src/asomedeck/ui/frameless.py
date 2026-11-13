"""Frameless window support (spec 14.7).

A frameless window has no non-client area, so Windows stops generating the hit-test
results that drive resizing, snapping and title-bar dragging. They have to be synthesised.

The constraint that shapes this module is measured, not theoretical.
``tools/probe_frameless.py`` found that with a terminal flush to the frame **all eight
edges and corners belong to the terminal**, and with a 6px gutter **none do**. Windows
sends WM_NCHITTEST to the window under the cursor, so where that window is the terminal
this code never runs at all. The gutter is not padding for looks -- it is the only reason
any of this executes, which is why ``reserve_gutter`` is not optional on whatever holds
the panes.

No ctypes here: message decoding lives in ``winapi``, which is all that remains of the
platform layer now that no foreign windows are involved.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QPoint, Qt
from PySide6.QtWidgets import QWidget

from .. import winapi as win32
from .theme import GEOM

log = logging.getLogger(__name__)


def reserve_gutter(widget: QWidget, gutter: int | None = None) -> None:
    """Keep a grabbable strip around a widget that would otherwise touch the frame.

    Left, right and bottom only. The top of the pane area is covered by the tab strip,
    which is an ordinary Qt widget and routes hit-tests to us already.
    """
    size = GEOM["resize_gutter"] if gutter is None else gutter
    layout = widget.layout()
    if layout is not None:
        layout.setContentsMargins(size, 0, size, size)
    else:
        widget.setContentsMargins(size, 0, size, size)


class FramelessHelper(QObject):
    """Synthesises the hit-test results a frameless window no longer receives."""

    def __init__(self, window: QWidget, border: int | None = None) -> None:
        super().__init__(window)
        self._window = window
        self._border = GEOM["resize_gutter"] if border is None else border

    @property
    def border(self) -> int:
        return self._border

    def hit_test(self, global_x: int, global_y: int) -> int | None:
        """Resolve a screen point to a hit-test code, or None to let Qt answer."""
        window = self._window
        pos = window.mapFromGlobal(QPoint(global_x, global_y))
        x, y = pos.x(), pos.y()
        w, h = window.width(), window.height()
        if not (0 <= x < w and 0 <= y < h):
            return None

        # A maximised window has no resizable edge to offer, and claiming one would put a
        # resize cursor on a border the user cannot actually drag.
        edge = 0 if (window.isMaximized() or window.isFullScreen()) else self._border
        if not edge:
            return None
        left, right = x < edge, x >= w - edge
        top, bottom = y < edge, y >= h - edge
        if top:
            return win32.HTTOPLEFT if left else win32.HTTOPRIGHT if right else win32.HTTOP
        if bottom:
            return (win32.HTBOTTOMLEFT if left else
                    win32.HTBOTTOMRIGHT if right else win32.HTBOTTOM)
        if left:
            return win32.HTLEFT
        if right:
            return win32.HTRIGHT
        # The caption is deliberately not reported here. Dragging is handled in Qt via
        # startSystemMove, which enters the same Windows move loop -- so Aero snap still
        # works -- while leaving the title bar an ordinary widget whose buttons receive
        # ordinary clicks. Answering HTCAPTION would make Windows route those presses as
        # non-client and Qt would never see them.
        #
        # This is also why there is no list of caption exclusions. One was written here,
        # alongside a list of interactive widgets in TitleBar, and neither was ever
        # connected to the other -- because nothing over the caption is ever claimed,
        # there is nothing for an exclusion to take back. Both are now gone rather than
        # joined up, which would have been ceremony around a code path that cannot run.
        return None

    def native_event(self, message_ptr) -> tuple[bool, int] | None:
        """Answer the two messages a frameless window owns. None means "not ours"."""
        message, wparam, x, y = win32.decode_native_message(message_ptr)
        if message == win32.WM_NCCALCSIZE:
            # Returning 0 with wParam set collapses the non-client area to nothing while
            # leaving the window an ordinary resizable, snappable top-level window --
            # which is what keeps Aero snap and the maximise animation working.
            return (True, 0) if wparam else None
        if message == win32.WM_NCHITTEST:
            code = self.hit_test(x, y)
            if code is not None:
                return True, code
        return None


#: Windows consults these before it will move or size a window, independently of what
#: WM_NCHITTEST answers. Qt's FramelessWindowHint clears WS_THICKFRAME, which is why a
#: correct hit-test still produced a window that could not be resized at all.
WS_THICKFRAME = 0x00040000
WS_MAXIMIZEBOX = 0x00010000
WS_MINIMIZEBOX = 0x00020000
WS_SYSMENU = 0x00080000
NATIVE_BEHAVIOUR_STYLES = WS_THICKFRAME | WS_MAXIMIZEBOX | WS_MINIMIZEBOX | WS_SYSMENU


def enable_frameless(window: QWidget) -> FramelessHelper:
    """Turn a window frameless and return the helper that keeps it usable."""
    window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    helper = FramelessHelper(window)
    log.info("frameless enabled with a %dpx resize border", helper.border)
    return helper


def restore_native_behaviours(window: QWidget) -> None:
    """Put back the styles Windows needs, after the window has a handle.

    The frame is still hidden -- WM_NCCALCSIZE takes care of that. This only restores the
    capabilities: sizing, maximise, minimise and the system menu. Must run after the HWND
    exists, so showEvent rather than the constructor.
    """
    hwnd = int(window.winId())
    style = win32.add_window_styles(hwnd, NATIVE_BEHAVIOUR_STYLES)
    log.info("restored native window behaviours; style=0x%08X", style)


def start_system_move(window: QWidget) -> None:
    """Hand a title-bar drag to Windows, so Aero snap behaves natively."""
    handle = window.windowHandle()
    if handle is not None:
        handle.startSystemMove()
