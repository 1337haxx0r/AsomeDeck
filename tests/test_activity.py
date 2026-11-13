"""When a pane may claim to be busy.

Spec 14.3 cut RUNNING and WAITING because docking could not see them. Owning the byte
stream makes them observable: the program sets its own title through OSC 0/2 and we parse
it, so this is the program talking rather than the app guessing.

Each state is claimed for a different reason, and the ordering is the interesting part.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from asomedeck.term import activity  # noqa: E402
from asomedeck.ui.widgets.status_dot import (  # noqa: E402
    ATTENTION,
    DEAD,
    LIVE,
    RUNNING,
    WAITING,
)


def test_nothing_running_is_dead():
    assert activity.state_for(alive=False, title="✳ anything",
                              since_title_change=0.0) == DEAD


def test_a_title_that_just_moved_means_work():
    """A program animating its own title is doing something, whatever the glyph is.

    This is the only state claimed from observation rather than the program's word, and
    it is what survives a spinner set changing between releases.
    """
    assert activity.state_for(alive=True, title="● What you can do",
                              since_title_change=0.2) == RUNNING


def test_a_settled_ready_mark_means_waiting():
    """Observed: Claude Code shows this at startup and when a turn finishes."""
    assert activity.state_for(alive=True, title="✳ What you can do",
                              since_title_change=9.0) == WAITING


def test_a_spinner_frame_of_the_ready_glyph_is_not_read_as_ready():
    """The ordering that makes ✳ safe to read at all.

    That glyph appears in the spinner sequence as well as at rest, so a frame of it
    mid-spin would say "waiting" -- except a frame does not last, and the ready reading
    only applies to a title that has held still.
    """
    assert activity.state_for(alive=True, title="✳ What you can do",
                              since_title_change=0.1) == RUNNING


def test_a_shell_that_never_names_itself_claims_nothing():
    """None is not "a long time ago". A shell that sets no title is not idle, it is
    simply not saying -- which is what LIVE means."""
    assert activity.state_for(alive=True, title="", since_title_change=None) == LIVE


def test_a_settled_ordinary_title_claims_nothing():
    assert activity.state_for(alive=True, title="npm run dev",
                              since_title_change=30.0) == LIVE


def test_only_running_counts_as_busy_for_the_deck_tally():
    assert activity.busy(RUNNING)
    for state in (LIVE, WAITING, DEAD):
        assert not activity.busy(state)


@pytest.mark.parametrize("elapsed,expected", [
    (0.0, RUNNING), (1.4, RUNNING), (1.6, WAITING), (60.0, WAITING),
])
def test_the_settle_window_is_the_only_thing_separating_the_two(elapsed, expected):
    assert activity.state_for(alive=True, title="✳ done",
                              since_title_change=elapsed) == expected


# --- what the sidebar shows, which is not what the pane is doing ---------------------


def test_a_finished_turn_outranks_whatever_the_pane_is_doing_now():
    """The point of the sticky flags.

    By the time you look at another deck, the turn that finished there is over and the
    pane is sitting at LIVE -- indistinguishable from a shell nobody has touched. The one
    thing worth telling you has already scrolled past.
    """
    assert activity.displayed(LIVE, attention=False, finished=True) == WAITING


def test_the_bell_outranks_a_finished_turn():
    """A program that rang is asking; a finished turn is only reporting."""
    assert activity.displayed(RUNNING, attention=True, finished=True) == ATTENTION


def test_an_unseen_alert_survives_the_process_exiting():
    """A run that rang and then exited is the case you would most want kept."""
    assert activity.displayed(DEAD, attention=True, finished=False) == ATTENTION


def test_with_nothing_unseen_the_live_reading_stands():
    for state in (LIVE, DEAD, RUNNING):
        assert activity.displayed(state, attention=False, finished=False) == state


def test_a_prompt_you_have_already_seen_is_just_idle():
    """Green is earned by a turn ending unseen, not by a program resting. A program rests
    for as long as you leave it there, so passing the raw reading through would keep the
    dot green forever after you had looked -- the one thing the flags exist to avoid."""
    assert activity.displayed(WAITING, attention=False, finished=False) == LIVE


# --- one dot for a whole deck -----------------------------------------------------


def test_a_deck_with_no_panes_is_dead():
    assert activity.rollup([]) == DEAD
    assert activity.rollup([DEAD, DEAD]) == DEAD


def test_the_loudest_pane_wins():
    assert activity.rollup([DEAD, LIVE, WAITING]) == WAITING
    assert activity.rollup([WAITING, RUNNING, ATTENTION]) == ATTENTION


def test_running_collapses_into_live_on_a_deck_row():
    """Across a list of decks, "working" and "idle" are the same answer: nothing is
    asking for you. Spending a third colour on the difference would dilute the two that
    are worth a glance. Expand the deck and the session rows still separate them."""
    assert activity.rollup([RUNNING]) == LIVE
    assert activity.rollup([RUNNING, LIVE]) == LIVE
