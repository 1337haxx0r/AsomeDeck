"""Pane numbers, and the promise the claims board rests on: no two panes share one.

`board.md` keys every claim on a pane's number, and the board lives in a folder rather
than in a pane tree. While one deck meant one folder those were the same scope and a
counter per tree was enough. They are about to stop being the same scope -- several
decks in the sidebar will be able to point at one checkout -- and a number that is only
unique within a tree stops being an identity at that moment: two agents would write claims
as "pane 3" into one file, and neither could tell which line was its own.

So the allocator is the window's, not the tree's. Most of this file is that one property
under the conditions that would break it: a second tree, a restore, a layout written when
numbers were per-tree, and a hand-edited one.

`PaneIds` itself needs no QApplication -- it is a set and a loop -- but it lives in a
module that imports Qt, so the whole file skips together where PySide6 is missing, as the
rest of the suite does.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from asomedeck.ui.pane_tree import PaneIds, PaneTree  # noqa: E402

# --- the allocator on its own ---------------------------------------------------------

def test_numbers_start_at_zero_and_do_not_repeat():
    ids = PaneIds()
    assert [ids.take() for _ in range(4)] == [0, 1, 2, 3]


def test_a_number_is_never_handed_out_twice():
    """The property the board needs. Nothing is released, so "taken once" is forever --
    a claim naming a pane that has exited is stale, and the reader is asked to spot that
    and delete it. Reissuing the number would make a stale line look like a live session's
    that never wrote it."""
    ids = PaneIds()
    seen = {ids.take() for _ in range(50)}
    assert len(seen) == 50


def test_a_saved_number_is_honoured_when_it_is_free():
    ids = PaneIds()
    assert ids.take(7) == 7


def test_a_saved_number_that_is_spoken_for_is_renumbered():
    """Two decks restoring layouts written when numbers were per-tree both ask for 0.
    A duplicate is worse than a change: _on_session_row finds a pane by number and would
    land on whichever one it met first."""
    ids = PaneIds()
    assert ids.take(0) == 0
    assert ids.take(0) != 0


def test_a_number_it_already_issued_is_not_reoffered_by_a_later_save():
    ids = PaneIds()
    ids.take()          # 0
    ids.take()          # 1
    assert ids.take(1) != 1


@pytest.mark.parametrize("junk", [None, True, False, -1, "seven", 2.0, [3]])
def test_anything_that_is_not_a_pane_number_gets_a_fresh_one(junk):
    """A hand-edited layout. `bool` is in the list because it is an `int` -- "pane_id":
    true would otherwise restore as pane 1, a real number arrived at from a value that is
    not one."""
    ids = PaneIds()
    assert ids.take(junk) == 0


def test_a_fresh_number_fills_the_lowest_gap():
    """Numbers are numbers rather than tokens so that they stay small enough to read. A
    gap only ever comes from a layout that named something nothing else reached."""
    ids = PaneIds()
    ids.take(4)
    assert ids.take() == 0


# --- trees sharing one -----------------------------------------------------------------

def test_a_tree_given_no_allocator_still_numbers_from_zero(app):
    """What every existing test of the tree assumes, and what a PaneTree used on its own
    should do. Sharing is the window's decision, not the tree's default."""
    tree = PaneTree()
    tree.add_pane()
    tree.add_pane()
    assert [pane.pane_id for pane in tree.panes] == [0, 1]
    tree.deleteLater()


def test_two_trees_sharing_an_allocator_never_collide(app):
    ids = PaneIds()
    first, second = PaneTree(None, ids), PaneTree(None, ids)
    first.add_pane()
    first.add_pane()
    second.add_pane()
    numbers = [p.pane_id for p in first.panes] + [p.pane_id for p in second.panes]
    assert len(set(numbers)) == len(numbers) == 3
    first.deleteLater()
    second.deleteLater()


def test_a_second_tree_restoring_the_same_saved_numbers_is_renumbered(app):
    """The case this exists for. Both layouts were written when numbers were per-tree, so
    both name 0 and 1; the panes they come back as still have to be six distinct things."""
    saved = {"type": "split", "orientation": "h",
             "children": [{"type": "pane", "pane_id": 0},
                          {"type": "pane", "pane_id": 1},
                          {"type": "pane", "pane_id": 2}]}
    ids = PaneIds()
    first, second = PaneTree(None, ids), PaneTree(None, ids)
    numbers = [p.pane_id for p in first.restore(saved)]
    numbers += [p.pane_id for p in second.restore(saved)]
    assert len(set(numbers)) == 6
    first.deleteLater()
    second.deleteLater()


def test_the_first_tree_to_restore_keeps_the_numbers_it_saved(app):
    """Renumbering is the fallback, not the rule. A window with one deck open should
    see exactly the numbers it shut down with."""
    saved = {"type": "split", "orientation": "h",
             "children": [{"type": "pane", "pane_id": 3},
                          {"type": "pane", "pane_id": 5}]}
    tree = PaneTree(None, PaneIds())
    assert [p.pane_id for p in tree.restore(saved)] == [3, 5]
    tree.deleteLater()


# --- the window's own ------------------------------------------------------------------

def test_panes_in_different_decks_do_not_share_a_number(window, tmp_path):
    """The window hands one allocator to every tree it builds. Two decks pointing at
    one checkout write to one board, and this is what keeps their claims apart."""
    other = tmp_path / "second"
    other.mkdir()
    second = window.db.add_deck(str(other), "")

    window.tree.add_pane()
    window.tree.add_pane()
    first_numbers = [pane.pane_id for pane in window.tree.panes]

    window._open_deck(second.id)
    window.tree.add_pane()
    second_numbers = [pane.pane_id for pane in window.tree.panes]

    assert not set(first_numbers) & set(second_numbers)


def test_the_shell_is_told_the_number_that_is_unique(window, tmp_path, monkeypatch):
    """End to end, because this is the only thing the property is *for*. The number
    reaches an agent one way -- ASOMEDECK_PANE_ID at spawn -- and what it writes into
    board.md is whatever it found there.

    Two decks and not two decks on one folder: `UNIQUE(path)` still forbids the second,
    so what is checked here is the allocator being shared across trees, which is the whole
    of what dropping that constraint will then rely on.
    """
    from asomedeck import board
    from asomedeck.ui import term_pane

    from .test_pane_identity import FakeTerminal
    monkeypatch.setattr(term_pane, "TerminalPane", FakeTerminal)

    other = tmp_path / "second"
    other.mkdir()
    second = window.db.add_deck(str(other), "")

    here = window.tree.add_pane()
    window._launch_into(here, window.deck)
    window._open_deck(second.id)
    there = window.tree.add_pane()
    window._launch_into(there, window.deck)

    assert here.body.env[board.PANE_VAR] != there.body.env[board.PANE_VAR]
