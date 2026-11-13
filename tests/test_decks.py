"""Several decks over one folder: the question "add" now has to ask.

Schema 3 dropped `UNIQUE(path)`, which turned `db.add_deck` from a function that could
answer for itself into one that cannot. While a folder held one deck, picking a folder
already in the sidebar could only be a slip, and handing back the row you already had was
kinder than an error. It is now a real request about half the time, and the other half has
not gone away -- so the window asks, and what these cover is that both answers are honest:
"add another" makes a second row and "switch to it" makes none.

The dialogs are driven rather than shown. `_answer` picks a button by its text without
letting QMessageBox reach a screen, which is the only way a modal is testable at all --
an unanswered one in a test run is a hang, not a dialog.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox  # noqa: E402

from asomedeck.git_status import GitState  # noqa: E402
from asomedeck.ui.main_window import _and_list, _spare_name  # noqa: E402


def _answer(monkeypatch, label: str) -> None:
    """Click the button with this text. `None` for a dialog dismissed without one."""
    monkeypatch.setattr(QMessageBox, "exec", lambda _box: 0)
    monkeypatch.setattr(
        QMessageBox, "clickedButton",
        lambda box: next((button for button in box.buttons()
                          if button.text().replace("&", "") == label), None))


def _pick(monkeypatch, path) -> None:
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(path))


def _names(window) -> list[str]:
    return [deck.name for deck in window.db.decks()]


def _state() -> GitState:
    """A repository on `main` with two changed paths. `changed` is derived from `files`,
    so the count has to come from real entries rather than a number."""
    return GitState(known=True, branch="main", files=(("M", "a.py"), ("??", "b.py")))


# --- the prose helpers ------------------------------------------------------------------

@pytest.mark.parametrize("names, said", [
    ([], ""),
    (["Api"], "Api"),
    (["Api", "Web"], "Api and Web"),
    (["Api", "Web", "Ledger"], "Api, Web and Ledger"),
])
def test_the_dialog_names_what_is_already_there(names, said):
    assert _and_list(names) == said


def test_a_spare_name_starts_at_two():
    assert _spare_name("/repo/api", ["api"]) == "api 2"


def test_a_spare_name_steps_past_what_is_taken():
    assert _spare_name("/repo/api", ["api", "api 2", "api 3"]) == "api 4"


def test_a_spare_name_is_not_fooled_by_casing():
    assert _spare_name("/repo/api", ["api", "API 2"]) == "api 3"


def test_a_spare_name_starts_from_the_folder_not_the_existing_row():
    """The row above may have been renamed to something a digit does not belong on."""
    assert _spare_name("/repo/api", ["Tuesday's experiment"]) == "api 2"


# --- adding a folder that is not there yet ----------------------------------------------

def test_a_new_folder_is_added_without_a_question(window, monkeypatch, tmp_path):
    fresh = tmp_path / "elsewhere"
    fresh.mkdir()
    _pick(monkeypatch, fresh)
    # No QMessageBox is patched: reaching one would hang, which is the assertion.
    window._add_deck()

    assert _names(window)[-1] == "elsewhere"
    assert window.deck.name == "elsewhere"


# --- adding a folder that is ------------------------------------------------------------

def test_add_another_makes_a_second_deck_and_opens_it(window, monkeypatch):
    _pick(monkeypatch, window.deck.path)
    _answer(monkeypatch, "Add another")
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Experiments", True))

    window._add_deck()

    assert len(window.db.decks()) == 2
    assert window.deck.name == "Experiments"
    assert len(window.db.decks_at(window.deck.path)) == 2


def test_the_second_deck_gets_its_own_terminals(window, monkeypatch):
    """The point of the whole thing. Two rows over one checkout are two workspaces, not
    two views of one -- separate trees, so separate panes."""
    first = window.deck.id
    window.tree.add_pane()

    _pick(monkeypatch, window.deck.path)
    _answer(monkeypatch, "Add another")
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Experiments", True))
    window._add_deck()

    assert window.deck.id != first
    assert window.tree.count() == 0
    assert window._trees[first].count() == 1


def test_switch_to_it_adds_nothing(window, monkeypatch):
    _pick(monkeypatch, window.deck.path)
    _answer(monkeypatch, "Switch to it")

    window._add_deck()

    assert len(window.db.decks()) == 1


def test_cancelling_the_question_adds_nothing(window, monkeypatch):
    _pick(monkeypatch, window.deck.path)
    _answer(monkeypatch, None)

    window._add_deck()

    assert len(window.db.decks()) == 1


@pytest.mark.parametrize("answer", [
    ("Experiments", False),   # cancelled at the name
    ("", True),               # emptied
    ("   ", True),            # whitespace only
])
def test_a_second_deck_with_no_name_is_not_made(window, monkeypatch, answer):
    """Two rows on one folder are told apart by name and nothing else -- the path line
    under them is identical by construction. An unnamed one is worse than none."""
    _pick(monkeypatch, window.deck.path)
    _answer(monkeypatch, "Add another")
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: answer)

    window._add_deck()

    assert len(window.db.decks()) == 1


def test_a_folder_spelled_differently_is_still_recognised(window, monkeypatch):
    """Otherwise a second row would appear with no question asked, which is the failure
    the old UNIQUE index existed to prevent and the reason decks_at is case-blind."""
    _pick(monkeypatch, window.deck.path.upper())
    _answer(monkeypatch, "Switch to it")

    window._add_deck()

    assert len(window.db.decks()) == 1


# --- what the two of them share ---------------------------------------------------------

def test_git_state_reaches_every_deck_on_the_folder(window):
    """One `git status` over one working tree describes both rows. Giving it only to the
    one that asked leaves its sibling blank beside it, which reads as "not a repository"
    rather than "nobody has opened this one yet"."""
    twin = window.db.add_deck(window.deck.path, "", name="Twin")
    window._reload_rows()

    window._on_git(window.deck.id, _state())

    row = window.model.deck_row(twin.id)
    assert (row.branch, row.changed, row.git_known) == ("main", 2, True)


def test_git_state_does_not_reach_a_deck_on_another_folder(window, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    stranger = window.db.add_deck(str(other), "")
    window._reload_rows()

    window._on_git(window.deck.id, _state())

    assert window.model.deck_row(stranger.id).git_known is False


def test_a_deck_alone_on_its_folder_is_its_own_only_sibling(window):
    assert window._siblings_of(window.deck.id) == [window.deck.id]
