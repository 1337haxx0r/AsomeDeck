"""The window answers for every command the registry declares.

commands.py can be checked on its own, and is. What it cannot check is the half that
lives in main_window.py: that each id has a slot behind it. A command declared and never
wired is exactly the failure this codebase keeps finding in itself -- code that is correct
and never invoked -- except that here it reaches the user as a menu entry that does
nothing when clicked.

``_build_actions`` indexes the slot table by id, so a missing one is a KeyError at
construction. This pins that, and pins that the shortcuts survived the move off the
KEY_* constants.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

from asomedeck.commands import COMMANDS, MENUS, by_id, in_menu  # noqa: E402


def test_every_command_has_an_action(window):
    assert set(window.command_actions) == {command.id for command in COMMANDS}


def test_the_shortcuts_arrived_intact(window):
    for command in COMMANDS:
        got = window.command_actions[command.id].shortcut().toString()
        assert got == command.shortcut, command.id


def test_the_sidebar_key_no_longer_takes_ctrl_b(window):
    """It was a WindowShortcut, so it fired wherever focus was -- including inside a pane,
    where Ctrl+B belongs to readline, vim and tmux."""
    assert by_id("view.sidebar").shortcut == "Ctrl+Shift+B"
    live = {a.shortcut().toString() for a in window.command_actions.values()}
    assert "Ctrl+B" not in live


def test_commands_grey_out_when_they_do_not_apply(window):
    """A disabled action does not fire its shortcut either, which is the point."""
    assert window.command_actions["pane.split.right"].isEnabled()
    window.deck = None
    window._refresh_commands()
    assert not window.command_actions["project.shell"].isEnabled()


def test_the_menus_are_the_registry(window):
    """Five dropdowns that were five styled labels with no `clicked.connect` behind them.

    Order matters as much as membership: the registry's order *is* the menu's order, so a
    command added to a group lands where it was written rather than wherever a second
    list happened to put it.
    """
    for menu in MENUS:
        rows = window.title_bar.menus[menu].actions()
        assert [a.text() for a in rows] == [c.label for c in in_menu(menu)], menu


def test_a_menu_row_is_the_same_object_as_the_shortcut(window):
    """Not a copy of it. One QAction means the row shows the chord that really fires it,
    clicking it runs the one handler, and _refresh_commands greying it greys the menu."""
    shell = window.command_actions["project.shell"]
    assert shell in window.title_bar.menus["Deck"].actions()
    split = window.command_actions["pane.split.right"]
    assert split in window.title_bar.menus["Terminal"].actions()

    window.deck = None
    window._refresh_commands()
    assert not shell.isEnabled()


def test_no_dropdown_opens_onto_nothing(window):
    for menu in MENUS:
        assert window.title_bar.menus[menu].actions(), menu


def test_the_zoom_command_acts_on_the_pane_you_are_in(window):
    """Like split and close, and for the same reason: `active` is the pane with focus."""
    tree = window.tree
    first = tree.add_pane()
    second = tree.add_pane()
    assert tree.active is second

    window.command_actions["pane.zoom"].trigger()
    assert tree.zoomed is second
    window.command_actions["pane.zoom"].trigger()
    assert tree.zoomed is None

    tree.set_active(first)
    window.command_actions["pane.zoom"].trigger()
    assert tree.zoomed is first


def test_the_zoom_chord_is_not_the_editor_s_redo(window):
    """Ctrl+Shift+Z is Windows' redo, and QPlainTextEdit claims it via ShortcutOverride.

    A window action bound to it would be dead whenever an editor pane had focus -- zoom
    working in a terminal and silently doing nothing in a file. That is this codebase's
    Ctrl+B complaint aimed at the editor instead of a shell.
    """
    assert by_id("pane.zoom").shortcut == "Ctrl+Alt+Z"
    live = {a.shortcut().toString() for a in window.command_actions.values()}
    assert "Ctrl+Shift+Z" not in live


def test_zooming_an_empty_tree_does_nothing(window):
    """A tree with no panes has nothing to zoom, and the chord still reaches the slot.

    `_has_tree` asks whether there is a tree, not whether anything is in it -- so greying
    does not cover this and the guard in the slot has to.
    """
    window.tree.clear()
    assert window.tree.active is None
    window._toggle_pane_zoom()          # must not raise
    assert window.tree.zoomed is None


def test_a_layout_saved_while_zoomed_still_describes_the_grid(window, app):
    """The end-to-end form of the trap, at the level it would have reached a user.

    A hidden splitter child reports a size of 0, and the layout write is debounced by more
    than a second -- so an unrelated drag or split a moment earlier lands the save *while*
    zoomed. `_apply_sizes` then rejects the list on the way back in, because it holds a
    zero, so the arrangement does not come back collapsed: it comes back with no
    proportions at all, and the user's split points are simply gone.
    """
    # Shown, and that is not incidental: a QSplitter only reports a hidden child as 0
    # once it has actually laid out, so this test passes against the bug it exists for if
    # the window is never on screen.
    window.resize(1200, 700)
    window.show()
    app.processEvents()
    tree = window.tree
    tree.add_pane()
    second = tree.add_pane(orientation=Qt.Orientation.Horizontal)
    tree._root.setSizes([800, 400])
    app.processEvents()
    grid = tree.to_tree()

    tree.toggle_zoom(second)
    app.processEvents()
    window.save_layouts()

    saved = window.db.load_layout(window.deck.id)
    assert saved == grid
    assert all(saved["sizes"]), f"a zero reached the database: {saved['sizes']}"
    window.hide()


def test_zooming_puts_the_keyboard_in_the_pane(window, app):
    """Qt gives click focus to the nearest ancestor that accepts it, which is the Pane and
    not the terminal inside it -- so without the window putting it back you enlarge a shell
    and then cannot type into it."""
    tree = window.tree
    pane = tree.add_pane()
    body = QWidget()
    body.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    pane.attach_body(body)
    window.show()
    app.processEvents()

    pane.zoom_button.click()
    app.processEvents()
    assert tree.zoomed is pane
    assert body.hasFocus(), "the keyboard was left outside the pane that was zoomed"
    window.hide()


def test_a_new_session_while_zoomed_returns_to_the_grid(window, app):
    """Otherwise Ctrl+Shift+D creates a pane behind the zoomed one and looks like a key
    that did nothing."""
    tree = window.tree
    tree.add_pane()
    second = tree.add_pane(orientation=Qt.Orientation.Horizontal)
    tree.toggle_zoom(second)
    assert tree.zoomed is second

    window._new_session(Qt.Orientation.Vertical)
    assert tree.zoomed is None
    assert tree.count() == 3
    assert not any(p.isHidden() for p in tree.panes)
