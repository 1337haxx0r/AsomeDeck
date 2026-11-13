"""A deck with no folder: what it keeps, and what it has to be stopped from doing.

`test_decks.py` covers several decks over one folder. This is the other end of the same
column: a deck over none -- terminals, a layout, a shell and a colour, with no working
tree underneath. The row is stored the way every other one is, with `path` empty, which
is why almost everything here is a guard rather than a feature. Four call sites would
otherwise take the empty string and do something with it:

* `db.add_deck` cleans a path, and `os.path.abspath("")` is the *process* working
  directory (`test_db.py` holds that one, where the function is).
* `board.ensure` joins `.asomedeck` onto it, and would seed the directory wherever the
  app was launched from.
* a pty chdirs only `if cwd`, so an empty one inherits the app's -- `/` from a bundle.
* `decks_at("")` answers with every other pathless deck, which would make them all
  siblings and spread one folder's git state across decks that have none.

None of those fail loudly. Each would work, quietly, on the wrong directory.

No shell is started: `_launch_into` looks the renderer up on the module at call time, so
`FakeTerminal` answers instead and records what it was handed -- the same trick
`test_pane_identity.py` uses, and imported from there rather than written twice.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QInputDialog  # noqa: E402

from asomedeck import board  # noqa: E402
from asomedeck.ui import term_pane  # noqa: E402
from asomedeck.ui.main_window import SCRATCH_NAME, _unused_name  # noqa: E402

from .test_pane_identity import FakeTerminal  # noqa: E402


def _named(monkeypatch, text: str, chose: bool = True) -> None:
    """Answer the name dialog. `chose=False` is the one dismissed with Cancel."""
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (text, chose))


def _new_scratch(window, monkeypatch, name: str = "Scratch"):
    _named(monkeypatch, name)
    window._new_deck()
    return window.deck


@pytest.fixture
def scratch(window, monkeypatch):
    """The fixture's window with a second, folderless deck open."""
    _new_scratch(window, monkeypatch)
    return window


@pytest.fixture
def fake_terminal(monkeypatch):
    monkeypatch.setattr(term_pane, "TerminalPane", FakeTerminal)


# --- the suggested name -----------------------------------------------------------------

def test_the_first_one_is_offered_the_plain_name():
    """Not `_spare_name`, which numbers unconditionally because it is answering a
    different question -- "a second name for this folder"."""
    assert _unused_name("Scratch", ["api", "web"]) == "Scratch"


def test_a_taken_name_is_numbered():
    assert _unused_name("Scratch", ["Scratch"]) == "Scratch 2"


def test_the_numbering_steps_past_what_is_taken():
    assert _unused_name("Scratch", ["scratch", "Scratch 2"]) == "Scratch 3"


# --- making one -------------------------------------------------------------------------

def test_a_new_deck_has_no_folder_and_is_opened(scratch):
    assert scratch.deck.name == "Scratch"
    assert scratch.deck.path == ""
    assert [deck.path for deck in scratch.db.decks()][-1] == ""


def test_it_gets_its_own_terminals(window, monkeypatch):
    """The same promise a second deck on one folder makes: a row is a workspace."""
    first = window.deck.id
    window.tree.add_pane()

    _new_scratch(window, monkeypatch)

    assert window.deck.id != first
    assert window.tree.count() == 0
    assert window._trees[first].count() == 1


def test_cancelling_makes_nothing(window, monkeypatch):
    before = len(window.db.decks())
    _named(monkeypatch, "Scratch", chose=False)
    window._new_deck()
    assert len(window.db.decks()) == before


def test_an_empty_name_makes_nothing(window, monkeypatch):
    """Two decks with no folder are told apart by name and nothing else -- the line
    under them is empty for both by construction."""
    before = len(window.db.decks())
    _named(monkeypatch, "   ")
    window._new_deck()
    assert len(window.db.decks()) == before


# --- what its panes launch into ---------------------------------------------------------

def test_a_pane_starts_at_home(scratch, fake_terminal):
    """Not "", which a pty would take as "stay wherever the app is" -- `/` for a bundle
    opened from the Dock."""
    pane = scratch.tree.add_pane()
    assert scratch._launch_into(pane, scratch.deck)
    assert pane.body.cwd == os.path.expanduser("~")


def test_a_pane_with_a_directory_of_its_own_still_gets_it(scratch, fake_terminal, tmp_path):
    inner = tmp_path / "notes"
    inner.mkdir()
    pane = scratch.tree.add_pane(cwd=str(inner))
    assert scratch._launch_into(pane, scratch.deck)
    assert pane.body.cwd == str(inner)


# --- the board --------------------------------------------------------------------------

def test_no_board_is_prepared(scratch, fake_terminal, monkeypatch):
    """Nothing is lost by that: the board exists because two agents in one working tree
    cannot see each other, and this deck has no tree for them to share. Asserted on the
    call rather than on the filesystem, because the directory it would have been written
    to is wherever the test runner happens to be standing."""
    asked: list[str] = []
    monkeypatch.setattr(board, "ensure",
                        lambda path, name="": asked.append(str(path)))

    pane = scratch.tree.add_pane()
    assert scratch._launch_into(pane, scratch.deck)

    assert asked == []
    assert board.BOARD_VAR not in pane.body.env


def test_the_pane_still_knows_which_pane_and_deck_it_is(scratch, fake_terminal):
    """The board is what goes missing, not the identity beside it."""
    pane = scratch.tree.add_pane()
    assert scratch._launch_into(pane, scratch.deck)
    assert pane.body.env[board.PANE_VAR] == str(pane.pane_id)
    assert pane.body.env[board.DECK_VAR] == scratch.deck.name


# --- what it is not a sibling of --------------------------------------------------------

def test_two_folderless_decks_share_nothing(window, monkeypatch):
    """`decks_at("")` would answer with both. Git state is written across siblings, and
    a folder neither of them has is not a folder they have in common."""
    first = _new_scratch(window, monkeypatch, "Scratch").id
    second = _new_scratch(window, monkeypatch, "Other").id

    assert window._siblings_of(first) == [first]
    assert window._siblings_of(second) == [second]


def test_a_folder_deck_still_finds_its_siblings(window, monkeypatch):
    """The guard is on the empty path, not on the lookup."""
    folder = window.deck.id
    _new_scratch(window, monkeypatch)
    assert window._siblings_of(folder) == [folder]


def test_git_is_not_polled(scratch):
    """`watch` handed an empty path says nothing and does nothing: it starts its
    three-second timer and lets every `refresh` return early forever."""
    assert not scratch.git._timer.isActive()


def test_git_starts_again_on_the_way_back(window, monkeypatch):
    """The other half, and the one that fails silently. `watch` is a no-op when handed
    the deck and path it already holds, so a `stop` that remembered what it had been
    watching would leave the repository unpolled for the rest of the run."""
    folder = window.deck.id
    _new_scratch(window, monkeypatch)
    assert not window.git._timer.isActive()

    window._open_deck(folder)

    assert window.git._timer.isActive()


# --- what the chrome says ---------------------------------------------------------------

def test_the_row_says_it_has_no_folder(scratch):
    row = scratch._current_row()
    assert row.path == ""
    assert row.path_text == "no folder"


def test_the_branch_chip_is_absent(scratch):
    row = scratch._current_row()
    assert not row.git_known
    assert row.diff_text == ""


def test_open_in_editor_is_disabled(scratch):
    """With nothing selected the button falls back to showing the file tree, and that
    tab is itself unavailable here."""
    assert not scratch.header.open_in_editor.isEnabled()


def test_the_button_comes_back_with_a_folder(window, monkeypatch):
    folder = window.deck.id
    _new_scratch(window, monkeypatch)
    window._open_deck(folder)
    assert window.header.open_in_editor.isEnabled()


def test_the_palette_offers_no_files(scratch):
    assert not [row for row in scratch._palette_rows() if row.kind == "file"]


def test_the_palette_still_offers_the_deck_itself(window, monkeypatch):
    _new_scratch(window, monkeypatch, "Scratch")
    window._open_deck(window.db.decks()[0].id)
    assert "Scratch" in {row.label for row in window._palette_rows() if row.kind == "deck"}


# --- the Files tab ----------------------------------------------------------------------

def test_files_mode_is_unavailable(scratch):
    assert not scratch.sidebar.tab_files.isEnabled()
    assert scratch.sidebar.mode == "decks"


def test_files_mode_is_left_rather_than_shown_empty(window, monkeypatch):
    window.sidebar.set_mode("files")
    _new_scratch(window, monkeypatch)
    assert window.sidebar.mode == "decks"


def test_the_choice_survives_the_deck_that_could_not_honour_it(window, monkeypatch):
    """Forcing the tab back and forgetting why would rewrite `sidebar_mode` in the
    registry, and lose Files mode for every deck that does have a folder."""
    folder = window.deck.id
    window.sidebar.set_mode("files")
    _new_scratch(window, monkeypatch)

    window._open_deck(folder)

    assert window.sidebar.mode == "files"
    assert window.sidebar.tab_files.isEnabled()


def test_the_stored_mode_is_what_the_user_asked_for(window, monkeypatch):
    """Not what a folderless deck forced on screen while it was open."""
    window.sidebar.set_mode("files")
    _new_scratch(window, monkeypatch)
    assert window.db.get_meta("sidebar_mode", "") == "files"


def test_the_default_name_is_the_one_the_dialog_offers(window):
    """A constant rather than a literal in three places -- the dialog, the tests here
    and the numbering that follows it."""
    assert SCRATCH_NAME == "Scratch"
