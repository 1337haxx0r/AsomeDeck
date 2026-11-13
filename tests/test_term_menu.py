"""The terminal's right-click menu, and the two rules that make it correct.

The first is about ownership of the mouse. A program that asked for tracking -- ``vim``,
``less`` -- gets every button, and ``Shift`` is the override the user reaches for when
they want the app instead. That is xterm's convention and the one selection already
honours, so a menu that ignored it would make the two halves of this widget disagree about
whose button it is.

The second is about where the rows come from. Split and close are the window's own
``QAction`` objects rather than copies, so a chord rebound in config.json shows up here and
a command greyed by the registry greys the row. Copy and paste cannot be, because they are
handled by whichever view has focus and not by the window -- so the test that matters for
them is that the labels still name the keys that actually work.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pyte")

from PySide6.QtCore import QObject, QPoint, Qt, Signal  # noqa: E402
from PySide6.QtGui import QContextMenuEvent  # noqa: E402

from asomedeck.ui import theme as th  # noqa: E402
from asomedeck.ui.term_view import KEY_COPY, KEY_PASTE, TerminalView  # noqa: E402

#: DECSET 1000. What a program sends when it wants the mouse.
TRACK_ON = "\x1b[?1000h"
TRACK_OFF = "\x1b[?1000l"

#: DECSET 1049. The alternate screen, which is what a full-screen program runs on.
ALT_ON = "\x1b[?1049h"


@pytest.fixture
def view(app):
    th.apply(app, th.Theme("dark"))
    widget = TerminalView(th.Theme("dark"))
    widget.resize(900, 260)
    widget.show()
    app.processEvents()
    yield widget
    widget.close()


def _right_click(view: TerminalView, *, shift: bool = False) -> list:
    """Deliver a real context-menu event and report what the view emitted."""
    seen: list = []
    view.contextMenuRequested.connect(seen.append)
    point = QPoint(40, 20)
    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, point, view.mapToGlobal(point),
        Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier,
    )
    view.contextMenuEvent(event)
    return seen


def _fill(view: TerminalView, lines: int = 120) -> None:
    view.feed("".join(f"line {n}\r\n" for n in range(lines)))
    view.emulator.drain()


def _rows(menu) -> list[str]:
    return [action.text() for action in menu.actions() if action.text()]


def _row(menu, text: str):
    return next(action for action in menu.actions() if action.text() == text)


# --- who owns the right button ---------------------------------------------------------

def test_right_click_asks_for_a_menu(view):
    assert len(_right_click(view)) == 1


def test_a_program_that_wants_the_mouse_keeps_the_right_button(view):
    """Not cosmetic: inside ``less`` the right button is a command, and a menu appearing
    over it swallows the click the program was waiting for."""
    view.feed(TRACK_ON)
    view.emulator.drain()
    assert view.emulator.mouse_tracking

    assert _right_click(view) == []


def test_shift_takes_the_button_back(view):
    view.feed(TRACK_ON)
    view.emulator.drain()

    assert len(_right_click(view, shift=True)) == 1


def test_the_menu_returns_when_the_program_lets_the_mouse_go(view):
    view.feed(TRACK_ON)
    view.emulator.drain()
    view.feed(TRACK_OFF)
    view.emulator.drain()

    assert len(_right_click(view)) == 1


# --- selection --------------------------------------------------------------------------

def test_select_all_takes_the_scrollback_with_it(view):
    _fill(view, 60)

    view.select_all()

    text = view.selected_text()
    assert "line 0" in text
    assert "line 59" in text


def test_select_all_on_an_empty_terminal_selects_nothing_visible(view):
    view.select_all()
    assert view.has_selection
    assert view.selected_text().strip() == ""


# --- clearing the scrollback -------------------------------------------------------------

def test_clearing_the_scrollback_keeps_the_screen(view):
    _fill(view)
    assert len(view.emulator.history) > 0
    on_screen = view.emulator.line(view.emulator.total_lines - 2)

    view.clear_scrollback()

    assert len(view.emulator.history) == 0
    assert view.emulator.line(view.emulator.total_lines - 2) == on_screen
    assert view.scroll_offset == 0


def test_clearing_drops_a_selection_that_pointed_into_it(view):
    """Selection is in absolute row coordinates. Keeping it would leave the highlight over
    whichever lines happened to land on those numbers once the history went."""
    _fill(view)
    view.select_all()

    view.clear_scrollback()

    assert not view.has_selection


def test_a_fresh_terminal_has_nothing_to_clear(view):
    assert not view.has_scrollback


def test_the_alternate_screen_hides_a_scrollback_that_is_not_its_own(view):
    """The history from before ``vim`` started is still counted and has nothing to do with
    what is on screen -- the same reason the scrollbar stands down there."""
    _fill(view)
    assert view.has_scrollback

    view.feed(ALT_ON)
    view.emulator.drain()

    assert not view.has_scrollback


# --- the menu the window builds ----------------------------------------------------------

def test_the_menu_offers_the_clipboard_and_the_pane(window):
    menu = window._build_terminal_menu(TerminalView(window.theme))

    assert _rows(menu) == ["Copy", "Paste", "Select all", "Clear scrollback",
                           "Split right", "Split down", "Close pane"]


def test_copy_is_dead_until_something_is_selected(window):
    target = TerminalView(window.theme)
    assert not _row(window._build_terminal_menu(target), "Copy").isEnabled()

    target.feed("hello\r\n")
    target.emulator.drain()
    target.select_all()

    assert _row(window._build_terminal_menu(target), "Copy").isEnabled()


def test_clear_scrollback_is_dead_until_there_is_some(window):
    target = TerminalView(window.theme)
    assert not _row(window._build_terminal_menu(target), "Clear scrollback").isEnabled()

    _fill(target, 200)

    assert _row(window._build_terminal_menu(target), "Clear scrollback").isEnabled()


def test_the_pane_rows_are_the_window_s_own_actions(window):
    """Not copies. It is what makes a chord rebound in config.json show up here, and what
    lets ``_refresh_commands`` grey the row -- the trick TitleBar.set_actions already uses.
    """
    menu = window._build_terminal_menu(TerminalView(window.theme))

    for command_id, label in (("pane.split.right", "Split right"),
                              ("pane.split.down", "Split down"),
                              ("pane.close", "Close pane")):
        assert _row(menu, label) is window.command_actions[command_id]


def test_the_clipboard_rows_name_the_keys_that_work(window):
    """Ctrl+C is SIGINT and stays SIGINT, so the chords are the shifted forms -- and this
    menu is where somebody finds that out."""
    menu = window._build_terminal_menu(TerminalView(window.theme))

    assert _row(menu, "Copy").shortcut().toString() == KEY_COPY
    assert _row(menu, "Paste").shortcut().toString() == KEY_PASTE


def test_the_clipboard_rows_do_not_claim_their_chords_window_wide(window):
    """The menu outlives the ``exec`` that showed it -- it is parented to the window -- so
    a row left at the default context would leave its chord bound on the window."""
    menu = window._build_terminal_menu(TerminalView(window.theme))

    for label in ("Copy", "Paste"):
        assert _row(menu, label).shortcutContext() == Qt.ShortcutContext.WidgetShortcut


def test_the_rows_act_on_the_view_they_were_built_for(window):
    target = TerminalView(window.theme)
    target.feed("hello\r\n")
    target.emulator.drain()

    _row(window._build_terminal_menu(target), "Select all").trigger()

    assert target.has_selection


# --- the wiring between the two -----------------------------------------------------------

class _Body(QObject):
    """A pane body that reports a right-click and nothing else.

    The point of the getattr surface ``_wire_reports`` uses is that a body answers only
    what it has; a stub that answered everything would test the opposite of the property
    that matters.
    """

    contextMenuRequested = Signal(object)


def test_a_body_that_reports_a_right_click_gets_a_menu(window, monkeypatch):
    """The connection nothing else covers: the view emits and the window builds, but the
    two only meet in _wire_reports -- which is a getattr away from silently not happening.
    """
    asked: list = []
    monkeypatch.setattr(window, "_terminal_menu",
                        lambda view, point: asked.append((view, point)))
    body = _Body()
    window._wire_reports(object(), body)

    point = QPoint(3, 4)
    body.contextMenuRequested.emit(point)

    assert asked == [(body, point)]


def test_a_body_with_no_such_signal_is_left_alone(window):
    """An editor pane has none and keeps QPlainTextEdit's own menu. Reaching for the
    signal unconditionally would be an AttributeError on every file opened."""
    window._wire_reports(object(), QObject())
