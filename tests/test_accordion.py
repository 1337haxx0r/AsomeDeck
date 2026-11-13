"""Decks expand to show the terminals inside them.

The sidebar is a QListView with a hand-painted delegate, so the hierarchy is flattened
into that list rather than modelled as a tree: a QTreeView brings its own indentation,
branch indicators and selection behaviour, all of which would fight the design for no gain.

Flattening has one consequence worth pinning, and it is the reason most of these tests
exist: a row index no longer means a deck index. Anything that assumed it did was
quietly wrong the moment a deck was expanded.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from asomedeck.ui.deck_model import (  # noqa: E402
    DeckListModel,
    DeckRow,
    DeckStore,
    PlaceholderRow,
    SessionRow,
)


@pytest.fixture
def model(app):
    rows = [DeckRow(name="one", path="C:/one", deck_id=1),
            DeckRow(name="two", path="C:/two", deck_id=2)]
    return DeckListModel(DeckStore(rows))


def kinds(model):
    return [type(model.row_at(model.index(i, 0))).__name__
            for i in range(model.rowCount())]


def test_collapsed_by_default(model):
    assert kinds(model) == ["DeckRow", "DeckRow"]


def test_expanding_inserts_the_terminals_under_their_deck(model):
    model.set_children(1, [SessionRow(deck_id=1, pane_id=0, title="✳ Claude Code"),
                           SessionRow(deck_id=1, pane_id=1)])
    model.set_expanded(1, True)

    assert kinds(model) == ["DeckRow", "SessionRow", "SessionRow", "DeckRow"]
    assert model.row_at(model.index(1, 0)).label == "✳ Claude Code"


def test_an_expanded_deck_with_nothing_running_says_so(model):
    """Rather than showing nothing, which reads as a broken expander."""
    model.set_expanded(2, True)
    assert kinds(model) == ["DeckRow", "DeckRow", "PlaceholderRow"]


def test_a_pane_without_a_title_falls_back_to_its_number(model):
    assert SessionRow(deck_id=1, pane_id=3).label == "pane 3"


def test_row_for_deck_survives_children_above_it(model):
    """The index of the second deck moves when the first one opens. Everything that
    repaints or selects a deck by position depends on this."""
    assert model.row_for_deck(2) == 1
    model.set_children(1, [SessionRow(deck_id=1, pane_id=0)])
    model.set_expanded(1, True)
    assert model.row_for_deck(2) == 2


def test_deck_at_refuses_a_session_row(model):
    """`_deck_menu` offers to remove "a deck"; on a terminal row it would have
    named the wrong thing and then removed it."""
    model.set_children(1, [SessionRow(deck_id=1, pane_id=0)])
    model.set_expanded(1, True)

    assert model.deck_at(model.index(0, 0)) is not None
    assert model.deck_at(model.index(1, 0)) is None
    assert isinstance(model.row_at(model.index(1, 0)), SessionRow)


def test_updating_terminals_in_place_does_not_reset_the_model(model):
    """A reset drops the selection. This runs on every uptime tick, once a second."""
    model.set_children(1, [SessionRow(deck_id=1, pane_id=0, state="live")])
    model.set_expanded(1, True)
    resets = []
    model.modelReset.connect(lambda: resets.append(1))

    model.set_children(1, [SessionRow(deck_id=1, pane_id=0, state="running",
                                      uptime="0m 05s")])

    assert resets == []
    assert model.row_at(model.index(1, 0)).state == "running"


def test_a_change_in_shape_does_reset(model):
    model.set_children(1, [SessionRow(deck_id=1, pane_id=0)])
    model.set_expanded(1, True)
    resets = []
    model.modelReset.connect(lambda: resets.append(1))

    model.set_children(1, [SessionRow(deck_id=1, pane_id=0),
                           SessionRow(deck_id=1, pane_id=1)])

    assert resets == [1]
    assert kinds(model) == ["DeckRow", "SessionRow", "SessionRow", "DeckRow"]


def test_removing_a_deck_forgets_what_was_under_it(model):
    model.set_children(1, [SessionRow(deck_id=1, pane_id=0)])
    model.set_expanded(1, True)

    model.replace(DeckStore([DeckRow(name="two", path="C:/two", deck_id=2)]))

    assert kinds(model) == ["DeckRow"]
    assert not model.is_expanded(1)


def test_the_placeholder_belongs_to_its_deck(model):
    model.set_expanded(1, True)
    row = model.row_at(model.index(1, 0))
    assert isinstance(row, PlaceholderRow) and row.deck_id == 1


def test_two_decks_sharing_a_basename_count_separately():
    """`api/src` and `web/src` are both called "src", and both are ordinary things to
    register. set_session_count matched on the name until now, so the second one's
    terminals were tallied against the first -- while set_git, directly above it, had
    always matched on the id.
    """
    rows = [DeckRow(name="src", path="C:/api/src", deck_id=1),
            DeckRow(name="src", path="C:/web/src", deck_id=2)]
    model = DeckListModel(DeckStore(rows))

    model.set_session_count(2, 4, 1)

    assert (rows[0].session_count, rows[0].live_count) == (0, 0)
    assert (rows[1].session_count, rows[1].live_count) == (4, 1)


def test_the_deck_is_found_by_id_not_by_position(model):
    """The crash. `_current_row` indexed `store.rows` with a *display* position, so
    expanding a deck pushed the one below it past the end of that list --
    ``IndexError`` out of `_refresh_header`, which the git poll calls every three
    seconds, which the excepthook turns into the app exiting."""
    model.set_children(1, [SessionRow(deck_id=1, pane_id=0)])
    model.set_expanded(1, True)

    assert model.row_for_deck(2) >= len(model.store.rows)   # the trap
    assert model.deck_row(2).name == "two"
    assert model.deck_row(99) is None


def test_the_count_is_decks_not_rows(model):
    """The sidebar header answers "how many decks". Expanding one with two terminals
    under it made it claim four."""
    model.set_children(1, [SessionRow(deck_id=1, pane_id=0),
                           SessionRow(deck_id=1, pane_id=1)])
    model.set_expanded(1, True)

    assert model.rowCount() == 4
    assert model.deck_count() == 2


def test_a_deck_that_is_not_open_repaints_without_resetting(model):
    """Its children are one placeholder line, not sessions. The in-place path looked for
    a row carrying a pane_id, found none, and reset the whole model on every refresh --
    dropping the sidebar selection each time."""
    model.set_children(2, [PlaceholderRow(2, "not open · 3 terminals saved")])
    model.set_expanded(2, True)
    resets, changed = [], []
    model.modelReset.connect(lambda: resets.append(1))
    model.dataChanged.connect(lambda a, b, _r: changed.append((a.row(), b.row())))

    model.set_children(2, [PlaceholderRow(2, "not open · 4 terminals saved")])

    assert resets == []
    assert changed == [(2, 2)]
    assert model.row_at(model.index(2, 0)).text.endswith("4 terminals saved")
