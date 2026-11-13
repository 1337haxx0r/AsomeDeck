"""What the terminal widget claims from Qt: the keyboard, and its own size.

One test here, and it is the one a unit test of the encoder cannot make: that the keys
actually *arrive*. ``QWidget.event()`` spends Tab and Backtab on focus navigation before
``keyPressEvent`` is ever called, so an encoder can be perfectly correct about what Tab
means and still never be asked.

That failure needs a second focusable widget to show up at all. In a pane on its own the
navigation finds nowhere to go, fails, and the key falls through -- which is why this was
invisible in ``tools/probe_termview.py`` and only appeared in the real window, where focus
jumped to a button in the chrome and the shell got nothing.

The resize tests are here for the same reason in a different direction: Qt hands this
widget a size of zero on the way back from being hidden, and taking that at face value
resizes a live pseudo-console to two columns.

The scrollbar tests at the end pin the one thing about it that is easy to get backwards.
``_scroll`` counts lines *up from the bottom*; a ``QScrollBar`` counts down from the top.
Every assertion about ``value()`` here is really an assertion that the inversion is still
in place, because a bar wired straight to the offset would pass "it moves" and still run
the wrong way.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pyte")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QPushButton,
    QScrollBar,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from asomedeck.ui.term_view import PAD_LEFT, PAD_RIGHT, TerminalView  # noqa: E402


@pytest.fixture
def view_with_a_rival(app):
    """A terminal beside something else that would happily take the focus."""
    host = QWidget()
    layout = QVBoxLayout(host)
    view = TerminalView(parent=host)
    layout.addWidget(view)
    rival = QPushButton("steal me", host)
    rival.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    layout.addWidget(rival)
    host.show()
    view.setFocus(Qt.FocusReason.OtherFocusReason)
    QTest.qWait(30)
    yield view, rival
    host.close()


@pytest.mark.parametrize("label,key,mods,expected", [
    ("Tab", Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier, b"\t"),
    ("Shift+Tab", Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier, b"\x1b[Z"),
])
def test_tab_reaches_the_shell_instead_of_the_focus_chain(view_with_a_rival, label, key,
                                                          mods, expected):
    view, rival = view_with_a_rival
    sent: list[bytes] = []
    view.dataReady.connect(sent.append)

    QTest.keyClick(view, key, mods)

    assert sent == [expected], f"{label} produced {sent!r}"
    assert not rival.hasFocus(), f"{label} moved focus to the button instead"


def test_the_widget_declines_focus_navigation_outright():
    """The mechanism, stated directly: QWidget.event() only spends Tab on navigation if
    focusNextPrevChild agrees to it."""
    assert TerminalView.focusNextPrevChild is not QWidget.focusNextPrevChild


# --- resizing ---------------------------------------------------------------------------


def test_a_hidden_pane_keeps_its_grid_through_hide_and_show(app):
    """Zoom hides the other panes, and Qt makes that look like a resize to nothing.

    A hidden widget is laid out at zero extent, and Qt does not deliver the resize while it
    is hidden -- it arrives on show(), immediately ahead of the real one. grid_size() clamps
    a zero extent to its 2x1 floor, so acting on that first event would resize the emulator
    and the pseudo-console to two columns and straight back on every unzoom. pyte discards
    what no longer fits, so the shell would lose whatever was on screen.
    """
    host = QSplitter(Qt.Orientation.Horizontal)
    host.setChildrenCollapsible(False)
    view = TerminalView()
    host.addWidget(view)
    host.addWidget(QWidget())
    host.resize(900, 400)
    host.show()
    app.processEvents()

    before = (view.emulator.screen.columns, view.emulator.screen.lines)
    assert before[0] > 2, "the view never got a real size to begin with"
    announced: list[tuple[int, int]] = []
    view.resized.connect(lambda cols, rows: announced.append((cols, rows)))

    view.hide()
    app.processEvents()
    view.show()
    app.processEvents()

    assert (view.emulator.screen.columns, view.emulator.screen.lines) == before
    assert announced == [], f"the pty was resized behind a hidden pane: {announced}"
    host.close()


def test_a_real_resize_still_reaches_the_emulator(app):
    """The guard declines zero, not everything -- the ordinary path has to keep working."""
    host = QWidget()
    layout = QVBoxLayout(host)
    view = TerminalView(parent=host)
    layout.addWidget(view)
    host.resize(900, 400)
    host.show()
    app.processEvents()

    before = view.emulator.screen.columns
    host.resize(500, 400)
    app.processEvents()

    assert view.emulator.screen.columns != before
    assert view.emulator.screen.columns == view.grid_size()[0]
    host.close()


# --- the scrollback bar -----------------------------------------------------------------

#: Enough lines past the bottom of the grid that there is a range to move within, and few
#: enough that a failure prints a readable number.
SPILL = 40


def bar_of(view: TerminalView) -> QScrollBar:
    """The view's own bar, found the way a caller would rather than through a private.

    That it is a *child of the view* is half the design -- an overlay owns no layout, so
    showing and hiding it cannot reflow the grid -- and findChild asserts that much for
    free.
    """
    found = view.findChild(QScrollBar)
    assert found is not None, "the terminal has no scrollbar at all"
    return found


def _visible(view: TerminalView) -> str:
    """The text the grid is currently showing, which is what "held still" is about.

    Read the way the painter reads it -- the top row is `total_lines - rows - _scroll` --
    so a fix that moved the offset and the window by the same amount would not pass.
    """
    rows = view.emulator.screen.lines
    top = view.emulator.total_lines - rows - view.scroll_offset
    return view.emulator.text_for(top, top + rows - 1)


@pytest.fixture
def scrolled(app):
    """A shown terminal holding more history than its screen can show."""
    host = QWidget()
    layout = QVBoxLayout(host)
    view = TerminalView(parent=host)
    layout.addWidget(view)
    host.resize(900, 400)
    host.show()
    app.processEvents()
    rows = view.emulator.screen.lines
    view.feed("".join(f"line {n}\r\n" for n in range(rows + SPILL)))
    view.emulator.drain()
    app.processEvents()
    yield view
    host.close()


def test_a_terminal_with_nothing_above_the_fold_shows_no_bar(app):
    """One screenful is not scrollable, and a bar spanning nothing says otherwise."""
    view = TerminalView()

    assert not bar_of(view).isVisibleTo(view)


def test_the_bar_appears_with_the_history_and_spans_exactly_it(scrolled):
    bar = bar_of(scrolled)
    rows = scrolled.emulator.screen.lines

    assert bar.isVisibleTo(scrolled)
    assert bar.maximum() == scrolled.emulator.total_lines - rows
    assert bar.maximum() > 0, "the fixture never pushed anything into scrollback"
    assert bar.pageStep() == rows


def test_pinned_to_the_live_view_reads_as_the_bottom_of_the_bar(scrolled):
    """Offset 0 is the *bottom*. A bar fed the offset directly would sit at the top."""
    assert scrolled.scroll_offset == 0
    assert bar_of(scrolled).value() == bar_of(scrolled).maximum()


def test_the_bar_runs_the_opposite_way_to_the_offset(scrolled):
    bar = bar_of(scrolled)

    scrolled.scroll_by(5)

    assert scrolled.scroll_offset == 5
    assert bar.value() == bar.maximum() - 5


def test_dragging_the_bar_to_the_top_reaches_the_oldest_line(scrolled):
    bar = bar_of(scrolled)

    bar.setValue(0)

    assert scrolled.scroll_offset == bar.maximum()


def test_output_while_scrolled_up_leaves_the_same_lines_on_screen(scrolled):
    """The point of a scrollback: reading it does not require the machine to stop.

    Output used to snap the view back to the bottom, so scrolling up during a build
    lasted exactly until the next line arrived. What holds still here is the *text*, not
    the offset -- the offset has to climb to keep the text where it is.
    """
    scrolled.scroll_by(10)
    before = _visible(scrolled)

    scrolled.feed("".join(f"new {n}\r\n" for n in range(5)))
    scrolled.emulator.drain()

    assert _visible(scrolled) == before
    assert scrolled.scroll_offset == 15


def test_the_bar_follows_the_view_that_stayed_put(scrolled):
    """Held still against a growing history, the *handle* still has to move: there is
    more history above it than there was."""
    bar = bar_of(scrolled)
    scrolled.scroll_by(10)
    before = bar.maximum()

    scrolled.feed("".join(f"new {n}\r\n" for n in range(5)))
    scrolled.emulator.drain()

    assert bar.maximum() == before + 5
    assert bar.value() == bar.maximum() - scrolled.scroll_offset


def test_output_at_the_bottom_still_follows_the_tail(scrolled):
    """The other half, and the one that is easy to break while fixing the first: a view
    that was never scrolled up is following the output, and has to keep doing it."""
    assert scrolled.scroll_offset == 0

    scrolled.feed("something new\r\n")
    scrolled.emulator.drain()

    assert scrolled.scroll_offset == 0
    assert "something new" in _visible(scrolled)


def test_a_full_scrollback_takes_the_oldest_lines_with_it(app):
    """The one case that cannot hold still. Once the deque is evicting from the front as
    fast as it appends, the rows the view was showing stop existing -- so the offset
    clamps rather than climbing past a history that is no longer growing."""
    view = TerminalView(scrollback=20)
    view.feed("".join(f"line {n}\r\n" for n in range(200)))
    view.emulator.drain()
    view.scroll_by(10)
    assert view.scroll_offset == 10

    view.feed("".join(f"new {n}\r\n" for n in range(5)))
    view.emulator.drain()

    assert view.scroll_offset == 10
    assert view.scroll_offset <= bar_of(view).maximum()


def test_a_full_screen_program_starts_at_the_bottom(scrolled):
    """`vim` opened from a scrolled-back view. There is nothing to hold in the alternate
    screen -- no history is appended while it is up and the bar stands down -- so an
    offset carried into it would draw the program off screen with no way back but a
    keystroke."""
    scrolled.scroll_by(10)

    scrolled.feed("\x1b[?1049h")
    scrolled.emulator.drain()

    assert scrolled.emulator.screen.in_alt_screen, "the fixture never entered alt screen"
    assert scrolled.scroll_offset == 0


def test_a_keystroke_returns_to_the_bottom_and_the_bar_follows(scrolled):
    """The third writer of ``_scroll``, and the one a bar is most likely to be left out of.

    Typing while scrolled up jumps back to the prompt. That reset lived inline in
    keyPressEvent, where nothing told the bar about it.
    """
    bar = bar_of(scrolled)
    scrolled.scroll_by(10)

    QTest.keyClick(scrolled, Qt.Key.Key_A)

    assert scrolled.scroll_offset == 0
    assert bar.value() == bar.maximum()


def test_the_bar_stands_down_in_the_alternate_screen(scrolled):
    """History survives into the alternate screen, so total_lines alone would keep the bar
    up -- offering to scroll into the scrollback underneath a live full-screen program."""
    bar = bar_of(scrolled)
    assert bar.isVisibleTo(scrolled)

    scrolled.feed("\x1b[?1049h")
    scrolled.emulator.drain()
    assert scrolled.emulator.screen.in_alt_screen, "the fixture never entered alt screen"
    assert not bar.isVisibleTo(scrolled)

    scrolled.feed("\x1b[?1049l")
    scrolled.emulator.drain()

    assert bar.isVisibleTo(scrolled)


def test_the_grid_reserves_the_gutter_the_bar_is_drawn_in(scrolled):
    """The gutter is subtracted whether or not the bar is showing, which is what stops a
    bar appearing from resizing the pseudo-console underneath it."""
    cell_w, _cell_h = scrolled.cell_size()
    cols, _rows = scrolled.grid_size()

    assert cols == int(max(1, scrolled.width() - PAD_LEFT - PAD_RIGHT) // cell_w)


def test_hiding_the_bar_does_not_change_the_grid(scrolled):
    before = (scrolled.emulator.screen.columns, scrolled.emulator.screen.lines)

    scrolled.feed("\x1b[?1049h")
    scrolled.emulator.drain()
    app_grid = (scrolled.emulator.screen.columns, scrolled.emulator.screen.lines)

    assert not bar_of(scrolled).isVisibleTo(scrolled)
    assert app_grid == before, "the bar standing down reflowed the shell"


def test_shift_end_returns_to_the_tail_without_typing(scrolled):
    """The way back, now that output is not one. It must not reach the shell: a key
    pressed to move the view is not a character the program should receive."""
    sent = []
    scrolled.dataReady.connect(sent.append)
    scrolled.scroll_by(10)

    QTest.keyClick(scrolled, Qt.Key.Key_End, Qt.KeyboardModifier.ShiftModifier)

    assert scrolled.scroll_offset == 0
    assert sent == []


def test_shift_home_reaches_the_oldest_line(scrolled):
    """Clamped by `_set_scroll`, so the far end can be asked for without knowing it."""
    QTest.keyClick(scrolled, Qt.Key.Key_Home, Qt.KeyboardModifier.ShiftModifier)

    assert scrolled.scroll_offset == bar_of(scrolled).maximum()
