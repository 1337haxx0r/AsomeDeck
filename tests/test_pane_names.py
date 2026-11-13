"""Pane names, and the roster that makes them mean something to an agent.

A name is given to a pane in the window -- "designer", "tests" -- and never by the program
running inside it, which is what keeps it the same whatever agent that is. Three promises
rest on it, and this file holds each of them:

* **It survives.** Saved with the arrangement, like ``cwd``, so the pane in that slot
  tomorrow is still called what it was called today.
* **It is unique over a folder, and only over a folder.** ``panes.md`` lives in the
  checkout and lists every pane of every deck over it; two lines there both called
  ``designer`` would leave anybody reading it to guess. Two checkouts each having their
  own ``designer`` is the point.
* **The roster tells the truth.** It lists what is running, it is rewritten when that
  changes, and it is emptied when the window goes -- because the terminals go with it.

No shell is started. ``FakeTerminal`` answers for the renderer, as in
``test_pane_identity.py``, and ``LiveTerminal`` is the same thing reporting itself alive,
because the roster lists running panes and a fake that never runs lists nothing.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QInputDialog, QMessageBox  # noqa: E402

from asomedeck import board  # noqa: E402
from asomedeck.ui import term_pane  # noqa: E402
from asomedeck.ui.pane_tree import PaneTree  # noqa: E402

from .test_pane_identity import FakeTerminal  # noqa: E402


class LiveTerminal(FakeTerminal):
    """A fake body that reports a running shell, which is all the roster asks of one."""

    pid = 4242

    def is_alive(self) -> bool:
        return True


@pytest.fixture
def tree(app):
    widget = PaneTree()
    yield widget
    widget.deleteLater()


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(term_pane, "TerminalPane", LiveTerminal)


def _launch(window):
    """A running pane in the open deck."""
    pane = window.tree.add_pane()
    assert window._launch_into(pane, window.deck)
    return pane


def _roster(path) -> str:
    return board.roster_path(path).read_text(encoding="utf-8")


def _second_deck(window, path, name="Second"):
    """Another deck over ``path``, opened. Several decks may share a folder."""
    deck = window.db.add_deck(str(path), "gitbash", name=name)
    window._reload_rows()
    window._open_deck(deck.id)
    return deck


# --- the pane carries one -----------------------------------------------------------------

def test_a_pane_starts_with_no_name(tree):
    pane = tree.add_pane()
    assert pane.name == ""
    assert pane.name_label.isHidden()


def test_the_header_shows_a_name_once_it_has_one(tree):
    pane = tree.add_pane()
    pane.set_name("designer")
    assert not pane.name_label.isHidden()
    assert pane.name_label.text().lower() == "designer"

    pane.set_name("")
    assert pane.name_label.isHidden()


def test_the_caption_prefers_the_name_then_the_title_then_the_number(tree):
    pane = tree.add_pane()
    assert pane.caption == f"pane {pane.pane_id}"
    pane.session_title = "✳ Claude Code"
    assert pane.caption == "✳ Claude Code"
    pane.set_name("designer")
    assert pane.caption == "designer"


# --- it survives a restart ----------------------------------------------------------------

def _only_pane(saved: dict) -> dict:
    """The one pane node of a one-pane tree. The root is always a splitter."""
    (node,) = saved["children"]
    assert node["type"] == "pane"
    return node


def test_a_name_is_saved_with_the_arrangement(tree):
    pane = tree.add_pane()
    pane.set_name("designer")
    assert _only_pane(tree.to_tree())["name"] == "designer"


def test_no_name_writes_nothing(tree):
    """Absent rather than empty, which is how every layout from before names reads."""
    tree.add_pane()
    assert "name" not in _only_pane(tree.to_tree())


def test_a_name_comes_back_with_its_pane(tree, app):
    first = tree.add_pane()
    tree.add_pane()
    first.set_name("designer")
    saved = tree.to_tree()

    other = PaneTree()
    try:
        restored = other.restore(saved)
        assert [pane.name for pane in restored] == ["designer", ""]
        assert not restored[0].name_label.isHidden()
    finally:
        other.deleteLater()


def test_a_hand_edited_name_is_held_to_the_same_rules(tree):
    """A layout is a file somebody can edit. One that names a pane "3" -- which reads as a
    pane number -- gets no name, not one the dialog would have refused."""
    restored = tree.restore({"type": "pane", "launcher_id": "", "name": "3"})
    assert restored[0].name == ""


def test_a_hand_edited_name_is_kept_in_the_form_the_dialog_would_keep(tree):
    restored = tree.restore({"type": "pane", "launcher_id": "", "name": "Front End"})
    assert restored[0].name == "front-end"


# --- naming one in the window -------------------------------------------------------------

def test_naming_a_pane_names_it(window):
    pane = window.tree.add_pane()
    assert window._rename_pane(pane, "Designer") == ""
    assert pane.name == "designer"


def test_a_name_that_cannot_be_one_is_explained_and_not_kept(window):
    pane = window.tree.add_pane()
    problem = window._rename_pane(pane, "3d")
    assert "cannot be a pane name" in problem
    assert pane.name == ""


def test_a_name_in_use_in_the_same_deck_is_refused(window):
    first = window.tree.add_pane()
    second = window.tree.add_pane()
    window._rename_pane(first, "designer")

    problem = window._rename_pane(second, "designer")

    assert f"Pane {first.pane_id}" in problem
    assert second.name == ""


def test_a_name_in_use_by_another_deck_over_the_same_folder_is_refused(window, tmp_path):
    """The roster lives in the folder, and lists both decks' panes in one file."""
    first = window.tree.add_pane()
    window._rename_pane(first, "designer")

    _second_deck(window, tmp_path)
    second = window.tree.add_pane()

    assert window._rename_pane(second, "designer")
    assert second.name == ""


def test_another_folder_can_have_its_own(window, tmp_path):
    """The whole reason names are scoped to a folder: two checkouts, two designers."""
    first = window.tree.add_pane()
    window._rename_pane(first, "designer")

    elsewhere = tmp_path / "other-repo"
    elsewhere.mkdir()
    _second_deck(window, elsewhere)
    second = window.tree.add_pane()

    assert window._rename_pane(second, "designer") == ""
    assert second.name == "designer"


def test_keeping_the_same_name_is_not_a_clash_with_itself(window):
    pane = window.tree.add_pane()
    window._rename_pane(pane, "designer")
    assert window._rename_pane(pane, "Designer") == ""
    assert pane.name == "designer"


def test_clearing_a_name_frees_it(window):
    first = window.tree.add_pane()
    second = window.tree.add_pane()
    window._rename_pane(first, "designer")

    assert window._rename_pane(first, "") == ""
    assert first.name == ""
    assert window._rename_pane(second, "designer") == ""


def test_naming_a_pane_saves_the_arrangement(window):
    pane = window.tree.add_pane()
    window._save_timer.stop()
    window._rename_pane(pane, "designer")
    assert window._save_timer.isActive()


def test_the_tabs_and_the_status_bar_show_it(window):
    """The number stays in the status bar: claims are written under it, and once a
    program has titled the pane it is the one place left on screen that shows it."""
    pane = window.tree.add_pane()
    window._rename_pane(pane, "designer")

    assert window.tabs._tabs[id(pane)].label.text().lower() == "designer"
    focus = window.status.focus.text().lower()
    assert f"pane {pane.pane_id}" in focus
    assert "designer" in focus


# --- the dialog ---------------------------------------------------------------------------

def test_the_menu_row_names_the_pane_that_was_right_clicked(window, monkeypatch):
    first = window.tree.add_pane()
    window.tree.add_pane()          # active now, and not the one being named
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("designer", True))

    menu = window._build_pane_menu(first)
    next(a for a in menu.actions() if a.text() == "Name pane…").trigger()

    assert first.name == "designer"


def test_a_refused_name_is_asked_again_with_what_was_typed(window, monkeypatch):
    """Nearly always one keystroke from a good one, so the field keeps it."""
    pane = window.tree.add_pane()
    answers = iter([("3d", True), ("lead", True)])
    offered: list[str] = []

    def answer(*_args, **kwargs):
        offered.append(kwargs.get("text", ""))
        return next(answers)

    explained: list[str] = []
    monkeypatch.setattr(QInputDialog, "getText", answer)
    monkeypatch.setattr(QMessageBox, "information",
                        lambda _parent, _title, text: explained.append(text))

    window._name_pane(pane)

    assert offered == ["", "3d"]
    assert len(explained) == 1
    assert pane.name == "lead"


def test_cancelling_keeps_the_name_it_had(window, monkeypatch):
    pane = window.tree.add_pane()
    window._rename_pane(pane, "designer")
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("", False))

    window._name_pane(pane)

    assert pane.name == "designer"


# --- restoring into a folder that already has the name ------------------------------------

def test_a_restored_name_already_taken_over_the_folder_is_dropped(window, tmp_path):
    """PaneIds' rule, for names: a saved layout's name is a preference. Two decks over one
    folder can each have saved a designer while only one was open."""
    first = window.tree.add_pane()
    window._rename_pane(first, "designer")

    second = window.db.add_deck(str(tmp_path), "gitbash", name="Second")
    window.db.save_layout(second.id, {"type": "pane", "launcher_id": "", "name": "designer"})
    window._reload_rows()
    window._open_deck(second.id)

    assert [pane.name for pane in window.tree.panes] == [""]
    assert first.name == "designer"


def test_a_layout_repeating_a_name_keeps_the_first(window, tmp_path):
    (tmp_path / "x").mkdir()
    other = window.db.add_deck(str(tmp_path / "x"), "gitbash", name="Other")
    window.db.save_layout(other.id, {
        "type": "split", "orientation": "h", "sizes": [1, 1],
        "children": [
            {"type": "pane", "launcher_id": "", "name": "designer"},
            {"type": "pane", "launcher_id": "", "name": "designer"},
        ],
    })
    window._reload_rows()
    window._open_deck(other.id)

    assert [pane.name for pane in window.tree.panes] == ["designer", ""]


# --- the roster ---------------------------------------------------------------------------

def test_a_running_pane_is_listed_with_its_name(window, live, tmp_path):
    pane = _launch(window)
    window._rename_pane(pane, "designer")

    window.write_rosters()

    assert f"| {pane.pane_id} | designer | {window.deck.name} |" in _roster(tmp_path)


def test_one_roster_covers_every_deck_over_the_folder(window, live, tmp_path):
    here = _launch(window)
    _second_deck(window, tmp_path)
    there = _launch(window)

    window.write_rosters()

    text = _roster(tmp_path)
    assert f"| {here.pane_id} |" in text
    assert f"| {there.pane_id} |  | Second |" in text


def test_a_folder_with_nothing_running_gets_no_roster(window, tmp_path):
    """A deck that has only been looked at should not grow a .asomedeck for it."""
    window.tree.add_pane()
    window.write_rosters()
    assert not board.roster_path(tmp_path).exists()


def test_a_pane_that_has_not_started_is_not_listed(window, monkeypatch, tmp_path):
    monkeypatch.setattr(term_pane, "TerminalPane", FakeTerminal)
    _launch(window)
    window.write_rosters()
    assert not board.roster_path(tmp_path).exists()


def test_the_last_pane_closing_empties_the_roster(window, live, tmp_path):
    pane = _launch(window)
    window.write_rosters()

    window.tree.close_pane(pane)
    window.write_rosters()

    assert board.ROSTER_EMPTY.strip() in _roster(tmp_path)


def test_closing_a_pane_schedules_a_rewrite(window, live):
    """Every way a pane ends reaches paneRemoved, so that is where the roster listens."""
    pane = _launch(window)
    window._roster_timer.stop()
    window.tree.close_pane(pane)
    assert window._roster_timer.isActive()


def test_a_shell_starting_schedules_a_rewrite(window, live):
    pane = _launch(window)
    window._roster_timer.stop()
    pane.body.started.emit()
    assert window._roster_timer.isActive()


def test_naming_a_pane_schedules_a_rewrite(window):
    pane = window.tree.add_pane()
    window._roster_timer.stop()
    window._rename_pane(pane, "designer")
    assert window._roster_timer.isActive()


def test_closing_the_window_empties_every_roster(window, live, tmp_path):
    """The terminals end with the app, so a roster left listing them would be a list of
    panes that no longer exist -- the stale-claim problem the board already has, made by
    us instead of by an agent."""
    _launch(window)
    window.write_rosters()

    window.close()

    assert board.ROSTER_EMPTY.strip() in _roster(tmp_path)
