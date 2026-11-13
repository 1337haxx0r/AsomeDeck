"""The pane header's right-click menu, and the two things it has to get right.

The first is which pane it means. Four of its six rows are the window's own ``QAction``
objects, and those act on whichever pane is *active* -- so a menu opened on one pane while
another was active would split, zoom or close the wrong terminal. The press that opens the
menu already activates the pane, but relying on that makes four rows depend on the order a
platform happens to deliver two mouse events in, so ``_build_pane_menu`` makes it explicit
and these tests pin it without ever sending a press.

The second is that hiding is offered only when it can be done. The last visible pane
cannot go: hiding it would leave a grid with nothing on it to right-click, so the menu
that emptied it could not undo it. Greying is functional here rather than cosmetic --
there is no chord to fall back on, because hiding deliberately has no ``Command``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QContextMenuEvent  # noqa: E402

from asomedeck.ui.pane_tree import PaneTree  # noqa: E402

HIDE = "Hide pane"


@pytest.fixture
def tree(app):
    """A tree with nobody listening, for the plumbing half.

    Deliberately not ``window.tree``. A pane in a real window is wired to ``_pane_menu``,
    which ``exec``s -- so delivering a right-click to one would open a modal menu and hang
    the run rather than test it. That the window is wired to it at all is asserted below,
    by spying on the slot instead of letting it run.
    """
    widget = PaneTree()
    yield widget
    widget.deleteLater()


def _rows(menu) -> list[str]:
    return [action.text() for action in menu.actions() if action.text()]


def _row(menu, text: str):
    return next(action for action in menu.actions() if action.text() == text)


def _right_click(pane) -> None:
    """Deliver a real context-menu event to a pane's header."""
    header = pane.header
    point = QPoint(20, 8)
    header.contextMenuEvent(QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, point, header.mapToGlobal(point),
        Qt.KeyboardModifier.NoModifier,
    ))


# --- the right-click reaches the window --------------------------------------------------

def test_right_clicking_a_header_asks_for_a_menu(tree):
    pane = tree.add_pane()
    seen: list = []
    pane.menuRequested.connect(lambda p, point: seen.append(p))

    _right_click(pane)

    assert seen == [pane]


def test_the_tree_passes_the_request_straight_through(tree):
    """The tree does not act on it, for the reason it does not act on paneCloseRequested:
    the rows below the separator are the window's commands, not the tree's business."""
    pane = tree.add_pane()
    seen: list = []
    tree.paneMenuRequested.connect(lambda p, point: seen.append(p))

    _right_click(pane)

    assert seen == [pane]


def test_a_restored_pane_is_wired_up_too(tree):
    """restore() builds panes through _rebuild rather than add_pane, and a signal wired in
    only one of the two is a menu that works until you reopen the deck."""
    tree.add_pane()
    tree.add_pane()
    panes = tree.restore(tree.to_tree())
    seen: list = []
    tree.paneMenuRequested.connect(lambda p, point: seen.append(p))

    _right_click(panes[1])

    assert seen == [panes[1]]


def test_the_window_opens_the_menu_on_the_pane_that_was_clicked(window, monkeypatch):
    """The last link, with only the modal half stubbed out.

    ``_pane_menu`` ``exec``s, and a modal event loop under test is a hang and not a menu --
    which is the whole reason the build is split from the show. Replacing the builder
    leaves the signal chain, the connection made in ``_tree_for`` and the slot itself all
    running for real.
    """
    built: list = []
    shown: list = []

    class _Stub:
        def exec(self, point):
            shown.append(point)

    def _build(pane):
        built.append(pane)
        return _Stub()

    monkeypatch.setattr(window, "_build_pane_menu", _build)
    pane = window.tree.add_pane()

    _right_click(pane)

    assert built == [pane]
    assert len(shown) == 1


# --- the menu the window builds ----------------------------------------------------------

def test_the_menu_offers_hiding_and_the_pane_commands(window):
    pane = window.tree.add_pane()
    menu = window._build_pane_menu(pane)

    assert _rows(menu) == ["Name pane…", HIDE, "Show hidden panes", "Split right",
                           "Split down", "New session in folder…", "Toggle pane zoom",
                           "Close pane"]


def test_the_pane_rows_are_the_window_s_own_actions(window):
    """Not copies -- the trick _build_terminal_menu explains. It is what makes a chord
    rebound in config.json show up here, and what lets _refresh_commands grey the row."""
    pane = window.tree.add_pane()
    menu = window._build_pane_menu(pane)

    for command_id, label in (("pane.split.right", "Split right"),
                              ("pane.split.down", "Split down"),
                              ("session.new.in", "New session in folder…"),
                              ("pane.zoom", "Toggle pane zoom"),
                              ("pane.close", "Close pane")):
        assert _row(menu, label) is window.command_actions[command_id]


def test_the_menu_acts_on_the_pane_it_was_opened_on(window):
    """The shared rows aim at `active`, so opening the menu has to move it."""
    tree = window.tree
    first = tree.add_pane()
    second = tree.add_pane()
    assert tree.active is second

    window._build_pane_menu(first)

    assert tree.active is first


def test_hiding_from_the_menu_hides_that_pane(window):
    tree = window.tree
    first = tree.add_pane()
    tree.add_pane()

    _row(window._build_pane_menu(first), HIDE).trigger()

    assert tree.is_hidden(first) is True
    assert first.isHidden() is True


# --- what the menu refuses to offer ------------------------------------------------------

def test_hiding_is_dead_on_the_only_pane(window):
    pane = window.tree.add_pane()

    assert not _row(window._build_pane_menu(pane), HIDE).isEnabled()


def test_hiding_is_dead_on_the_last_visible_pane(window):
    tree = window.tree
    first = tree.add_pane()
    second = tree.add_pane()
    tree.hide_pane(first)

    assert not _row(window._build_pane_menu(second), HIDE).isEnabled()


def test_hiding_is_alive_once_there_are_two_panes(window):
    tree = window.tree
    first = tree.add_pane()
    tree.add_pane()

    assert _row(window._build_pane_menu(first), HIDE).isEnabled()


def test_show_hidden_is_dead_until_something_is_hidden(window):
    tree = window.tree
    first = tree.add_pane()
    tree.add_pane()
    assert not _row(window._build_pane_menu(first), "Show hidden panes").isEnabled()

    tree.hide_pane(first)

    assert _row(window._build_pane_menu(tree.active), "Show hidden panes (1)").isEnabled()


def test_show_hidden_counts_them(window):
    tree = window.tree
    first = tree.add_pane()
    second = tree.add_pane()
    tree.add_pane()
    tree.hide_pane(first)
    tree.hide_pane(second)

    menu = window._build_pane_menu(tree.active)

    assert "Show hidden panes (2)" in _rows(menu)


def test_show_hidden_brings_them_all_back(window):
    tree = window.tree
    first = tree.add_pane()
    second = tree.add_pane()
    third = tree.add_pane()
    tree.hide_pane(first)
    tree.hide_pane(second)

    _row(window._build_pane_menu(third), "Show hidden panes (2)").trigger()

    assert tree.hidden_count() == 0
    assert [p.isHidden() for p in tree.panes] == [False, False, False]


# --- the chrome that says so -------------------------------------------------------------

def test_a_hidden_pane_says_so_in_its_sub_text(window):
    """In words, not only in colour. A dimmed tab reads as a dead session, which is the
    opposite of what hiding does -- the shell is still running."""
    tree = window.tree
    first = tree.add_pane()
    tree.add_pane()
    assert "hidden" not in window._pane_label(first)

    tree.hide_pane(first)

    assert window._pane_label(first).endswith("· hidden")


def test_a_hidden_pane_keeps_its_tab(window):
    """The tab is the way back, so filtering the strip down to visible panes would strand
    the pane it names."""
    tree = window.tree
    first = tree.add_pane()
    tree.add_pane()

    tree.hide_pane(first)
    window._refresh_chrome()

    tab = window.tabs._tabs[id(first)]
    assert tab.property("hidden") == "true"
    assert tab.toolTip()


def test_the_tab_stops_saying_hidden_when_the_pane_comes_back(window):
    tree = window.tree
    first = tree.add_pane()
    tree.add_pane()
    tree.hide_pane(first)
    window._refresh_chrome()

    tree.show_pane(first)
    window._refresh_chrome()

    assert window.tabs._tabs[id(first)].property("hidden") == "false"


def test_hiding_a_pane_does_not_schedule_a_layout_save(window):
    """_on_hidden_changed is not _schedule_save: hiding changes nothing to_tree
    serialises, which is the whole argument for hiddenChanged being its own signal."""
    tree = window.tree
    first = tree.add_pane()
    tree.add_pane()
    saves: list = []
    window._schedule_save = lambda: saves.append(1)

    tree.hide_pane(first)

    assert saves == []
