"""A pane's own directory: a terminal need not start where its deck does.

`test_pane_identity.py` covers the other half of `_launch_into` -- the environment a shell
is handed -- and shares this file's trick of swapping a fake body onto the renderer module
so nothing is actually spawned. What is covered here is the argument beside it: the cwd,
which until now was `deck.path` for every pane in the window and is now the pane's own
answer when it has one.

The stale-directory case is the one worth having. A layout outlives the folders it names,
and a shell spawned into a path that has been deleted does not start at all -- so a pane
restored with a directory that is gone has to come back as a working terminal in the
deck, not as an empty rectangle nobody can explain.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402

from asomedeck.ui import term_pane  # noqa: E402
from asomedeck.ui.pane_tree import PaneTree  # noqa: E402

from .test_pane_identity import FakeTerminal  # noqa: E402


@pytest.fixture
def fake_terminal(monkeypatch):
    monkeypatch.setattr(term_pane, "TerminalPane", FakeTerminal)


# --- what a pane launches into --------------------------------------------------------

def test_a_pane_with_no_directory_of_its_own_starts_in_the_deck(window, fake_terminal):
    """The default, and the behaviour every pane had before this existed."""
    pane = window.tree.add_pane()
    assert window._launch_into(pane, window.deck)
    assert pane.body.cwd == window.deck.path


def test_a_pane_starts_in_its_own_directory_when_it_has_one(window, fake_terminal, tmp_path):
    inner = tmp_path / "src"
    inner.mkdir()
    pane = window.tree.add_pane(cwd=str(inner))
    assert window._launch_into(pane, window.deck)
    assert pane.body.cwd == str(inner)


def test_the_footer_reports_where_the_pane_actually_went(window, fake_terminal, tmp_path):
    """Not the deck's path. `pane_title` compares a shell's self-reported directory
    against this one to decide whether the title is saying anything new, so a footer
    naming the wrong folder would silently change what every title means."""
    inner = tmp_path / "docs"
    inner.mkdir()
    pane = window.tree.add_pane(cwd=str(inner))
    window._launch_into(pane, window.deck)
    assert pane.cwd_text == str(inner)


def test_a_directory_that_is_gone_falls_back_to_the_deck(window, fake_terminal, tmp_path):
    pane = window.tree.add_pane(cwd=str(tmp_path / "deleted-last-week"))
    assert window._launch_into(pane, window.deck)
    assert pane.body.cwd == window.deck.path


def test_the_fallback_does_not_rewrite_what_the_pane_asked_for(window, fake_terminal,
                                                               tmp_path):
    """An unmounted share is back tomorrow. Overwriting the override with the fallback
    would turn one failed launch into a pane that has forgotten where it belongs."""
    wanted = str(tmp_path / "not-mounted-today")
    pane = window.tree.add_pane(cwd=wanted)
    window._launch_into(pane, window.deck)
    assert pane.cwd == wanted


# --- persistence ----------------------------------------------------------------------

def test_a_pane_directory_survives_a_round_trip(app, tmp_path):
    tree = PaneTree()
    tree.add_pane(cwd=str(tmp_path))
    tree.add_pane(orientation=Qt.Orientation.Vertical)

    restored = PaneTree()
    panes = restored.restore(tree.to_tree())
    assert [pane.cwd for pane in panes] == [str(tmp_path), ""]
    tree.deleteLater()
    restored.deleteLater()


def test_a_pane_in_its_deck_stores_no_directory_at_all(app):
    """Absence is the default, and it has to stay absent. A pane that recorded the
    deck's path would keep it after the deck was pointed somewhere else."""
    tree = PaneTree()
    tree.add_pane()
    assert "cwd" not in tree.to_tree()
    tree.deleteLater()


def test_a_layout_written_before_this_existed_still_loads(app):
    tree = PaneTree()
    panes = tree.restore({"type": "pane", "launcher_id": "gitbash", "pane_id": 0})
    assert panes[0].cwd == ""
    tree.deleteLater()


# --- the gesture ----------------------------------------------------------------------

def test_asking_for_a_directory_never_reuses_the_waiting_pane(window, fake_terminal,
                                                              tmp_path):
    """"Open a terminal in src" is a request for a terminal in src. Handing it the empty
    pane that happens to be sitting there would make the same menu item mean two things
    depending on what the grid looked like."""
    inner = tmp_path / "src"
    inner.mkdir()
    window._new_session(None, str(inner))
    before = window.tree.count()
    window._new_session(None, str(inner))
    assert window.tree.count() == before + 1
    assert window.tree.active.cwd == str(inner)


def test_asking_for_nothing_in_particular_still_reuses_it(window, fake_terminal):
    """The reuse this replaced is intact for the plain gesture: a fake body reports itself
    dead, so the pane stays empty and the next new session should land in it."""
    window._new_session(None)
    before = window.tree.count()
    window._new_session(None)
    assert window.tree.count() == before
