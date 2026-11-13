"""The sidebar's "+" is a dropdown, not the folder picker.

It was `clicked -> _add_deck` while a deck was a folder, and that was the whole of it:
one kind of deck, one thing the button could mean. A deck with no folder has no picker to
open, so the button that makes decks has to ask which kind first -- otherwise the only way
to reach `deck.new` is the File menu or the palette, and the control that visibly makes
decks does not offer half of what a deck can be.

The rail carries the same "+" collapsed, and it is the same menu object: what these tests
mostly hold is that the two stay one control.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QFileDialog, QInputDialog, QPushButton  # noqa: E402

from asomedeck.commands import by_id  # noqa: E402


def _buttons(window) -> list[QPushButton]:
    """Both "+"s: the header's and the rail's, in construction order."""
    return window.sidebar.findChildren(QPushButton, "IconButton")


def _entry(window, label: str):
    for action in window.sidebar._add_menu.actions():
        if action.text() == label:
            return action
    raise AssertionError(f"no {label!r} on the add menu: "
                         f"{[a.text() for a in window.sidebar._add_menu.actions()]}")


# --- the menu itself --------------------------------------------------------------------

def test_both_plus_buttons_open_the_one_menu(window):
    """Not two menus that happen to agree today. The next entry goes on one object."""
    buttons = _buttons(window)
    assert len(buttons) == 2
    assert [button.menu() for button in buttons] == [window.sidebar._add_menu] * 2


def test_the_entries_are_named_by_the_registry(window):
    """So the dropdown, the File menu and the palette cannot drift into naming one
    thing twice. Registry order too: adding a folder is still the common case."""
    assert [action.text() for action in window.sidebar._add_menu.actions()] == [
        by_id("project.add").label, by_id("deck.clone").label, by_id("deck.new").label]


# --- what the entries do ----------------------------------------------------------------

def test_add_deck_still_opens_the_folder_picker(window, monkeypatch, tmp_path):
    """The old behaviour of the button, one click further in."""
    second = tmp_path / "second"
    second.mkdir()
    asked = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        lambda *a, **k: (asked.append(True), str(second))[1])

    _entry(window, by_id("project.add").label).trigger()

    assert asked
    assert window.deck.path == str(second)


def test_new_empty_deck_makes_one_with_no_folder(window, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Scratch", True))

    _entry(window, by_id("deck.new").label).trigger()

    assert window.deck.path == ""
    assert window.deck.name == "Scratch"


def test_no_picker_is_opened_on_the_way_to_an_empty_deck(window, monkeypatch):
    """The point of the second entry: it is not the first one with the dialog skipped."""
    def refuse(*a, **k):
        raise AssertionError("a folder picker was opened for a deck with no folder")

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", refuse)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Scratch", True))

    _entry(window, by_id("deck.new").label).trigger()


# --- the button is no longer a shortcut for one of them ---------------------------------

def test_clicking_plus_does_not_add_a_deck_by_itself(window, monkeypatch):
    """A button that both opens a menu and does something is a click that does two
    things. `setMenu` leaves `clicked` firing, so the old connection had to go."""
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        lambda *a, **k: pytest.fail("the picker opened on a bare click"))
    before = [deck.id for deck in window.db.decks()]

    for button in _buttons(window):
        button.clicked.emit()

    assert [deck.id for deck in window.db.decks()] == before


# --- the third "+", in the tab strip ----------------------------------------------------
#
# Same glyph a few hundred pixels away, and it was a different control: a bare label whose
# only hover was dim -> text, beside a bordered box that outlines in the accent. Now they
# wear the one style. What they *do* stays different, and these hold that line -- a deck
# can be two things and has to ask which, a terminal is one thing and must not.


def _tab_plus(window):
    buttons = window.tabs.findChildren(QPushButton, "IconButton")
    assert len(buttons) == 1, "the tab strip should carry exactly one +"
    return buttons[0]


def test_the_tab_strips_plus_wears_the_same_style_as_the_sidebars(window):
    assert _tab_plus(window).objectName() == _buttons(window)[0].objectName()


def test_the_tab_strips_plus_is_flat_no_longer(window):
    """setFlat asks the platform style not to draw a frame, and #IconButton is a frame.
    Left on, the button takes the border in some styles and not in others."""
    assert not _tab_plus(window).isFlat()


def test_the_tab_strips_plus_did_not_grow_a_menu(window):
    """The dropdown is the sidebar's answer to "a deck can be two things". A terminal is
    one thing, and a menu asking which would be a click added to the commonest gesture
    in the window."""
    assert _tab_plus(window).menu() is None


def test_the_tab_strips_plus_still_opens_a_terminal_on_a_bare_click(window):
    opened = []
    window.tabs.newSessionRequested.connect(lambda: opened.append(True))

    _tab_plus(window).clicked.emit()

    assert opened == [True]


def test_the_tab_strips_plus_names_the_command_it_runs(window):
    """It has no label, so the tooltip is the only place the chord can be advertised --
    and it comes from the registry, so a rebound key cannot leave it lying."""
    assert by_id("session.new").label in _tab_plus(window).toolTip()
