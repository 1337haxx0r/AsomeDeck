"""What the sidebar says is happening, in decks you are not looking at.

``activity.py`` answers "what is this pane doing now", which is the wrong question for a
row you glance at while working somewhere else: by the time you look, the turn that
finished there is over and the pane is back to being indistinguishable from a shell nobody
has touched. So the two states worth acting on are sticky -- set by an event, cleared by
looking -- and every pane is re-read on the tick, not just the visible deck's.

The tick used to walk ``self.tree`` alone, so a background deck's state froze at
whatever it was when you switched away and the sidebar reported it confidently for as long
as you stayed gone.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QWidget  # noqa: E402

from asomedeck.term import activity  # noqa: E402
from asomedeck.ui.pane_tree import PaneTree  # noqa: E402
from asomedeck.ui.widgets.status_dot import ATTENTION, DEAD, LIVE, WAITING  # noqa: E402


class FakeBody(QWidget):
    """Enough of a terminal for a pane to have a session. Everything a pane asks of its
    renderer is asked by getattr, which is what lets this stand in for one -- but it is
    still swapped into a layout, so it has to be a real widget."""

    pane_kind = "terminal"

    def __init__(self, alive: bool = True) -> None:
        super().__init__()
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


def _deck(window, name: str = "bg"):
    """A second registered deck with a tree of its own, and never made visible."""
    deck_id = window.db.add_deck(f"C:/{name}", "gitbash").id
    window._reload_rows()
    tree = PaneTree(window.theme)
    window._trees[deck_id] = tree
    return deck_id, tree


def _pane(tree, *, title: str = "", alive: bool = True):
    pane = tree.add_pane("#101010")
    pane.attach_body(FakeBody(alive))
    pane.session_title = title
    return pane


def _settle(window, pane, title: str, *, renames: int) -> None:
    """Drive a pane to rest at ``title`` after that many self-renames.

    Renames matter as well as recency: a spinner renames the pane on every frame, a launch
    renames it once, and both come to rest on the same mark.
    """
    pane.session_title = title
    pane.title_changes = renames
    pane.title_changed_at = 0.0            # long settled by the clock below
    window._tick_states(now=activity.SETTLE_SECONDS + 10.0)


def _row(window, deck_id):
    return window.model.deck_row(deck_id)


# --- the tick reaches decks you are not in -----------------------------------------


def test_a_background_deck_is_still_re_read(window):
    """The frozen-state bug. _tick_states walked the visible tree only, so these panes
    were never asked again."""
    deck_id, tree = _deck(window)
    pane = _pane(tree, title="✳ done")
    assert window.tree is not tree              # genuinely not the visible one

    window._tick_states(now=activity.SETTLE_SECONDS + 10.0)
    assert pane.activity_state == WAITING


# --- a finished turn ------------------------------------------------------------------


def test_a_turn_that_ran_and_stopped_lights_the_deck(window):
    deck_id, tree = _deck(window)
    pane = _pane(tree)
    _settle(window, pane, "✳ What you can do", renames=6)

    assert pane.finished is True
    assert pane.session_state == WAITING
    assert _row(window, deck_id).status == WAITING


def test_a_program_that_merely_started_up_ready_does_not(window):
    """Claude Code names itself "✳ Claude Code" once at launch and rests there -- the same
    mark, held just as still, as the one a finished turn ends on. Without the rename count
    restoring four saved sessions announced four finished turns."""
    deck_id, tree = _deck(window)
    pane = _pane(tree)
    _settle(window, pane, "✳ Claude Code", renames=1)

    assert pane.finished is False
    assert _row(window, deck_id).status == LIVE


def test_resting_where_it_already_rested_does_not_re_arm(window):
    """The edge, not the level. On the level the flag would come back the tick after it
    was cleared and the dot would never return to blue."""
    deck_id, tree = _deck(window)
    pane = _pane(tree)
    _settle(window, pane, "✳ done", renames=6)

    pane.clear_alerts()
    window._tick_states(now=activity.SETTLE_SECONDS + 20.0)
    assert pane.finished is False
    assert _row(window, deck_id).status == LIVE


# --- the bell -------------------------------------------------------------------------


def test_a_bell_outranks_a_finished_turn(window):
    """One is asking; the other is only reporting."""
    deck_id, tree = _deck(window)
    pane = _pane(tree)
    _settle(window, pane, "✳ done", renames=6)
    window._on_bell(pane)

    assert _row(window, deck_id).status == ATTENTION


def test_an_unseen_bell_survives_the_process_exiting(window):
    """Count and status disagree here on purpose: nothing is running, and the dot is the
    only thing left saying the run wanted you."""
    deck_id, tree = _deck(window)
    pane = _pane(tree)
    window._on_bell(pane)
    pane.body._alive = False
    window._tick_states(now=100.0)

    row = _row(window, deck_id)
    assert (row.session_count, row.status) == (0, ATTENTION)


# --- clearing by looking --------------------------------------------------------------


def test_looking_at_the_pane_clears_it(window, monkeypatch):
    deck_id, tree = _deck(window)
    pane = _pane(tree)
    _settle(window, pane, "✳ done", renames=6)
    assert _row(window, deck_id).status == WAITING

    # Make it the visible, focused, active pane -- all four conditions _observed wants.
    monkeypatch.setattr(window.stack, "currentWidget", lambda: tree)
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    tree.set_active(pane)
    window._tick_states(now=activity.SETTLE_SECONDS + 20.0)

    assert pane.finished is False
    assert _row(window, deck_id).status == LIVE


def test_a_bell_in_the_pane_you_are_watching_is_not_hoarded(window, monkeypatch):
    """You have already had the notification. Keeping the flag would leave an amber dot on
    the deck you are staring at, cleared by the next tick -- a blink, not a signal."""
    deck_id, tree = _deck(window)
    pane = _pane(tree)
    monkeypatch.setattr(window.stack, "currentWidget", lambda: tree)
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    tree.set_active(pane)

    window._on_bell(pane)
    assert pane.attention is False


# --- one dot for the whole deck ----------------------------------------------------


def test_the_loudest_pane_decides_the_row(window):
    deck_id, tree = _deck(window)
    quiet = _pane(tree)
    loud = _pane(tree)
    _settle(window, quiet, "npm run dev", renames=3)
    assert _row(window, deck_id).status == LIVE

    window._on_bell(loud)
    assert _row(window, deck_id).status == ATTENTION


def test_a_deck_with_nothing_running_stays_dark(window):
    deck_id, tree = _deck(window)
    _pane(tree, alive=False)
    window._tick_states(now=100.0)

    assert _row(window, deck_id).status == DEAD
