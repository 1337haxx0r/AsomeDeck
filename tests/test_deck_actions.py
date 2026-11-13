"""The deck header's two buttons, and the commands behind them.

Both were styled `GhostButton`s with a pointing-hand cursor and no ``clicked.connect``
anywhere -- a control that looks like it works, which is the one thing spec 14 does not
allow this chrome to do. Two of the commands they reach are the other half of the same
pattern one level down: ``db.rename_deck`` and ``db.reorder`` have existed since the
schema did, are correct, and had no caller outside their own tests.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QInputDialog  # noqa: E402

from asomedeck.ui import file_tree as ft  # noqa: E402


@pytest.fixture
def two_decks(window, tmp_path):
    """A second registered folder, so order is a thing that can be wrong."""
    second = tmp_path / "second"
    second.mkdir()
    window.db.add_deck(str(second), "gitbash")
    window._reload_rows()
    window._refresh_chrome()      # what greys "Move down" back on, as a real open would
    return window


def _names(window):
    return [deck.name for deck in window.db.decks()]


# --- rename ---------------------------------------------------------------------------

def test_rename_changes_the_label_and_not_the_folder(window, monkeypatch, tmp_path):
    """The name is this app's label for a directory. Two checkouts of one repository are
    both called `app` on disk and need not be here."""
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Work", True))

    window.command_actions["project.rename"].trigger()

    assert _names(window) == ["Work"]
    assert window.deck.name == "Work"
    assert window.deck.path == str(tmp_path)
    assert window.header.title.text() == "Work"


@pytest.mark.parametrize("answer", [
    ("Work", False),      # cancelled
    ("", True),           # emptied
    ("   ", True),        # whitespace only
])
def test_a_rename_that_says_nothing_does_nothing(window, monkeypatch, answer):
    before = _names(window)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: answer)

    window.command_actions["project.rename"].trigger()

    assert _names(window) == before


# --- reorder --------------------------------------------------------------------------

def test_moving_a_deck_down_reorders_the_sidebar(two_decks):
    window = two_decks
    first, second = _names(window)

    window.command_actions["project.move.down"].trigger()

    assert _names(window) == [second, first]
    # The sidebar is what sort_order is for; a reordered database and a stale list is the
    # same bug as not reordering at all.
    assert [row.name for row in window.model.store.rows] == [second, first]


def test_moving_back_up_restores_the_order(two_decks):
    window = two_decks
    before = _names(window)
    window.command_actions["project.move.down"].trigger()
    window._open_deck(window.deck.id)   # still the same deck, now second
    window.command_actions["project.move.up"].trigger()
    assert _names(window) == before


def test_the_ends_of_the_list_grey_out(two_decks):
    """"Move up" on the first deck is not unavailable in some subtle sense -- it is a
    button that would visibly do nothing."""
    window = two_decks
    window._refresh_commands()
    assert not window.command_actions["project.move.up"].isEnabled()
    assert window.command_actions["project.move.down"].isEnabled()

    window._open_deck(window.db.decks()[-1].id)
    window._refresh_commands()
    assert window.command_actions["project.move.up"].isEnabled()
    assert not window.command_actions["project.move.down"].isEnabled()


def test_a_move_that_cannot_happen_leaves_the_order_alone(two_decks):
    window = two_decks
    before = _names(window)
    window._move_deck(-1)      # already first
    assert _names(window) == before


# --- the header buttons ---------------------------------------------------------------

def test_open_in_editor_opens_what_the_tree_has_selected(window, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    window.sidebar.set_mode("files")
    window.sidebar.files.set_root(str(tmp_path))
    index = _index_for(window.sidebar.files, target.name)
    window.sidebar.files.setCurrentIndex(index)

    window.header.open_in_editor.click()

    editor = window._editor_pane(window.tree)
    assert editor is not None
    assert editor.file_path.endswith("notes.txt")


def test_open_in_editor_with_nothing_chosen_shows_the_tree(window):
    """Not a consolation prize: the selection lives in a panel that may be on the other
    tab or collapsed, so "nothing to open" and "you cannot see what you would open" are
    the same situation from the user's side."""
    window.sidebar.set_mode("decks")
    window.sidebar.set_collapsed(True)

    window.header.open_in_editor.click()

    assert window.sidebar.mode == "files"
    assert not window.sidebar.collapsed
    assert window._editor_pane(window.tree) is None


def test_a_directory_is_not_a_file_to_open(window, tmp_path):
    (tmp_path / "sub").mkdir()
    window.sidebar.files.set_root(str(tmp_path))
    window.sidebar.files.setCurrentIndex(_index_for(window.sidebar.files, "sub"))
    assert window.sidebar.selected_file() == ""


def test_deck_settings_opens_the_deck_menu(window):
    """The Deck menu itself, not a second list built to match it."""
    window.header.settings.click()
    try:
        assert window.title_bar.menus["Deck"].isVisible()
    finally:
        window.title_bar.menus["Deck"].hide()


def _index_for(tree: ft.FileTree, name: str):
    """A top-level row by name. The model pages its children in, so the root is asked to
    fetch before anything is there to find."""
    model = tree.tree_model
    if model.canFetchMore():
        model.fetchMore()
    for row in range(model.rowCount()):
        index = model.index(row, 0)
        if index.data() == name:
            return index
    raise AssertionError(f"no row named {name}")


# --- rename, from the row rather than from the window -----------------------------------

def _menu_for(window, deck_id: int):
    """The context menu for one deck, built and not shown.

    ``_build_deck_menu`` is split from ``_deck_menu`` for exactly this: QMenu.exec
    runs a modal event loop, which in an unattended run is a hang and not a menu.
    """
    row = window.model.deck_row(deck_id)
    return window._build_deck_menu(row)


def test_the_context_menu_renames_the_row_it_was_opened_on(two_decks, monkeypatch):
    """The whole point of the change. ``_rename_current_deck`` acted on ``self.deck``,
    so a menu opened on one row would have renamed whichever deck happened to be open."""
    window = two_decks
    other = window.db.decks()[1]
    assert window.deck.id != other.id
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Renamed", True))

    _menu, _custom, rename, _remove = _menu_for(window, other.id)
    rename.trigger()

    assert window.db.deck_by_id(other.id).name == "Renamed"
    # The open deck is untouched, and is still the one that is open.
    assert window.deck.id != other.id
    assert window.deck.name == window.db.deck_by_id(window.deck.id).name


def test_the_dialog_opens_on_the_clicked_row_s_name(two_decks, monkeypatch):
    """Prefilled from the row, not from ``self.deck`` -- which is the same bug one step
    earlier: the right deck renamed to the wrong deck's name, unedited."""
    window = two_decks
    other = window.db.decks()[1]
    seen: list[str] = []

    def spy(*_args, **kwargs):
        seen.append(kwargs.get("text", ""))
        return ("", False)

    monkeypatch.setattr(QInputDialog, "getText", spy)
    _menu, _custom, rename, _remove = _menu_for(window, other.id)
    rename.trigger()

    assert seen == [other.name]


def test_renaming_the_open_deck_still_moves_the_header(two_decks, monkeypatch):
    window = two_decks
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Still Mine", True))

    _menu, _custom, rename, _remove = _menu_for(window, window.deck.id)
    rename.trigger()

    assert window.deck.name == "Still Mine"
    assert window.header.title.text() == "Still Mine"


def test_f2_renames_whatever_the_list_has_selected(two_decks, monkeypatch):
    """Widget-scoped and not a Command: F2 is a function key keys.py encodes, so a window
    shortcut would take it away from every shell in the app."""
    window = two_decks
    other = window.db.decks()[1]
    window.sidebar.view.setCurrentIndex(
        window.model.index(window.model.row_for_deck(other.id), 0))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("By Keyboard", True))

    window._rename_selected_deck()

    assert window.db.deck_by_id(other.id).name == "By Keyboard"
    assert window.rename_shortcut.context() == Qt.ShortcutContext.WidgetShortcut


def test_the_shell_submenu_changes_the_row_and_not_the_open_deck(two_decks):
    """``_change_launcher`` was window-scoped too, and for the same reason could not be
    offered on a row."""
    window = two_decks
    other = window.db.decks()[1]
    before = window.db.deck_by_id(window.deck.id).launcher_id

    menu, *_rest = _menu_for(window, other.id)
    submenu = next(a.menu() for a in menu.actions() if a.menu() is not None)
    actions = submenu.actions()
    if len(actions) < 2:
        pytest.skip("only one shell installed, so there is nothing to change to")
    next(a for a in actions if not a.isChecked()).trigger()

    assert window.db.deck_by_id(other.id).launcher_id != other.launcher_id
    assert window.db.deck_by_id(window.deck.id).launcher_id == before
