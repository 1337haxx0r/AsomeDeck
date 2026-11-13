"""Tree-shape logic for the pane splitter tree (spec 11, milestone 3).

These need a QApplication and therefore a window station, unlike the rest of the suite,
which is deliberately headless. They skip rather than fail where one is unavailable so a
headless CI run still reports the win32 tests honestly.

No terminal is ever launched here: panes stay empty, so this covers the tree algebra --
nesting, collapsing, active tracking -- and not what fills a pane. The terminal itself is
covered by ``tools/probe_termview.py`` and by ``python -m asomedeck --smoke``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QSplitter, QWidget  # noqa: E402

from asomedeck.ui import theme as th  # noqa: E402
from asomedeck.ui.pane_tree import PaneTree  # noqa: E402

H = Qt.Orientation.Horizontal
V = Qt.Orientation.Vertical


@pytest.fixture
def tree(app):
    widget = PaneTree()
    yield widget
    widget.deleteLater()


def test_first_pane_becomes_the_whole_tree(tree):
    tree.add_pane()
    assert tree.describe() == "H[0]"
    assert tree.count() == 1


def test_same_axis_split_stays_flat(tree):
    tree.add_pane()
    tree.add_pane(orientation=H)
    tree.add_pane(orientation=H)
    # Three side-by-side panes are one splitter with three children, never nested ones.
    assert tree.describe() == "H[0 1 2]"


def test_cross_axis_split_nests_only_the_target(tree):
    tree.add_pane()
    tree.add_pane(orientation=H)
    tree.add_pane(orientation=V)
    assert tree.describe() == "H[0 V[1 2]]"


def test_lone_child_changes_axis_instead_of_nesting(tree):
    tree.add_pane()
    tree.add_pane(orientation=V)
    # The root held one pane, so it can just become vertical. Nesting here would create a
    # splitter that owns one thing and means nothing.
    assert tree.describe() == "V[0 1]"


def test_closing_a_pane_collapses_the_splitter_it_emptied(tree):
    tree.add_pane()
    tree.add_pane(orientation=H)
    third = tree.add_pane(orientation=V)
    assert tree.describe() == "H[0 V[1 2]]"

    tree.close_pane(third)
    # The V splitter is down to one child, so it is spliced out rather than left as a
    # single-child node that contributes only a handle.
    assert tree.describe() == "H[0 1]"
    assert tree.count() == 2


def test_collapse_cascades_through_nested_splitters(tree):
    tree.add_pane()
    tree.add_pane(orientation=H)
    tree.add_pane(orientation=V)
    fourth = tree.add_pane(orientation=H)
    assert tree.describe() == "H[0 V[1 H[2 3]]]"

    tree.close_pane(fourth)
    assert tree.describe() == "H[0 V[1 2]]"


def test_closing_down_to_one_pane_leaves_a_bare_root(tree):
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.close_pane(second)
    assert tree.describe() == "H[0]"
    assert tree.panes == [first]


def test_emptying_the_tree_signals_once(tree):
    seen = []
    tree.emptied.connect(lambda: seen.append(True))
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.close_pane(first)
    assert seen == []
    tree.close_pane(second)
    assert seen == [True]
    assert tree.count() == 0
    assert tree.active is None


def test_new_pane_becomes_active_and_others_deactivate(tree):
    first = tree.add_pane()
    assert tree.active is first
    second = tree.add_pane(orientation=H)
    assert tree.active is second
    assert first._active is False
    assert second._active is True


def test_closing_the_active_pane_promotes_a_survivor(tree):
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    assert tree.active is second
    tree.close_pane(second)
    assert tree.active is first


def test_pane_ids_are_stable_across_closes(tree):
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    third = tree.add_pane(orientation=H)
    tree.close_pane(second)
    # List positions shifted; ids did not. Anything that names a pane depends on this.
    assert [p.pane_id for p in tree.panes] == [first.pane_id, third.pane_id] == [0, 2]
    assert tree.describe() == "H[0 2]"


def test_split_targets_the_active_pane_not_the_newest(tree):
    first = tree.add_pane()
    tree.add_pane(orientation=H)
    tree.set_active(first)
    tree.add_pane(orientation=H)
    # Pane 2 lands beside pane 0, because that is where focus was.
    assert tree.describe() == "H[0 2 1]"


def test_closing_an_unknown_pane_is_a_no_op(tree):
    first = tree.add_pane()
    tree.close_pane(first)
    tree.close_pane(first)
    assert tree.count() == 0


# --- layout persistence (spec 9) -------------------------------------------------------


def test_round_trip_preserves_the_shape(tree, app):
    tree.add_pane(launcher_id="git-bash")
    tree.add_pane(orientation=H, launcher_id="git-bash")
    tree.add_pane(orientation=V, launcher_id="pwsh")
    before = tree.describe()
    assert before == "H[0 V[1 2]]"

    saved = tree.to_tree()
    restored = PaneTree()
    restored.restore(saved)
    assert restored.describe() == "H[0 V[1 2]]"
    assert [p.launcher_id for p in restored.panes] == ["git-bash", "git-bash", "pwsh"]


def test_round_trip_of_a_single_pane(tree, app):
    tree.add_pane(launcher_id="git-bash")
    restored = PaneTree()
    restored.restore(tree.to_tree())
    assert restored.describe() == "H[0]"
    assert restored.count() == 1


def test_restored_panes_are_empty_and_awaiting_a_launch(tree, app):
    tree.add_pane(launcher_id="git-bash")
    restored = PaneTree()
    panes = restored.restore(tree.to_tree())
    # The shape persists; the sessions cannot. Panes come back ready to launch into.
    assert all(not p.has_session for p in panes)


def test_restore_replaces_whatever_was_there(tree, app):
    tree.add_pane()
    tree.add_pane(orientation=H)
    tree.restore({"type": "pane", "launcher_id": "pwsh"})
    assert tree.count() == 1
    assert tree.describe() == "H[2]"


def test_restore_survives_a_layout_with_stale_sizes(tree, app):
    # An edited or older layout can carry a size list that no longer matches the children.
    restored = PaneTree()
    restored.restore({
        "type": "split", "orientation": "h", "sizes": [1, 2, 3],
        "children": [{"type": "pane", "launcher_id": "a"},
                     {"type": "pane", "launcher_id": "b"}],
    })
    assert restored.count() == 2


def test_restore_tolerates_junk(tree, app):
    tree.restore({})
    assert tree.count() == 1


# --- pane ids survive a restart ---------------------------------------------------------
#
# The number names a pane in the header, the tab strip and the log, and board.env_for hands
# it to the shell as ASOMEDECK_PANE_ID -- so a claim written on the board has to still mean
# the same slot tomorrow.


def test_a_pane_keeps_its_number_across_a_restore(tree, app):
    tree.add_pane()
    tree.add_pane(orientation=H)
    tree.add_pane(orientation=V)
    before = [p.pane_id for p in tree.panes]

    restored = PaneTree()
    restored.restore(tree.to_tree())
    assert [p.pane_id for p in restored.panes] == before


def test_a_pane_added_after_a_restore_does_not_collide(tree, app):
    tree.add_pane()
    tree.add_pane(orientation=H)

    restored = PaneTree()
    restored.restore(tree.to_tree())
    fresh = restored.add_pane(orientation=H)
    ids = [p.pane_id for p in restored.panes]
    assert fresh.pane_id not in ids[:-1]
    assert len(set(ids)) == len(ids)


def test_a_layout_from_before_ids_were_saved_still_opens(tree, app):
    # Every layout already in a state.db looks like this. The counter answers, as it did.
    restored = PaneTree()
    restored.restore({
        "type": "split", "orientation": "h",
        "children": [{"type": "pane", "launcher_id": "a"},
                     {"type": "pane", "launcher_id": "b"}],
    })
    assert len({p.pane_id for p in restored.panes}) == 2


def test_a_hand_edited_layout_cannot_produce_two_panes_with_one_number(tree, app):
    """A duplicate is worse than a changed id: _on_session_row picks a pane by number and
    would land on whichever one it met first."""
    restored = PaneTree()
    restored.restore({
        "type": "split", "orientation": "h",
        "children": [{"type": "pane", "pane_id": 4},
                     {"type": "pane", "pane_id": 4},
                     {"type": "pane", "pane_id": "seven"},
                     {"type": "pane", "pane_id": -1},
                     {"type": "pane", "pane_id": True}],
    })
    ids = [p.pane_id for p in restored.panes]
    assert ids[0] == 4
    assert len(set(ids)) == len(ids)
    assert all(isinstance(i, int) and i >= 0 for i in ids)


def test_clear_empties_the_tree(tree, app):
    tree.add_pane()
    tree.add_pane(orientation=H)
    tree.clear()
    assert tree.count() == 0
    assert tree.active is None
    assert tree.describe() == "H[]"


def test_orientation_survives_the_round_trip(tree, app):
    tree.add_pane()
    tree.add_pane(orientation=V)
    restored = PaneTree()
    restored.restore(tree.to_tree())
    assert restored.describe() == "V[0 1]"


# --- the active pane is the one you are typing in -------------------------------------

def _focusable_body(pane):
    """Stand in for a terminal: something inside the pane that takes focus itself."""
    from PySide6.QtWidgets import QWidget

    body = QWidget()
    body.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    pane.attach_body(body)
    return body


def test_focus_inside_a_pane_makes_that_pane_active(app, tree):
    """The regression, and it was worse than it looked.

    Focus lands on the widget *inside* the pane, and Qt tells no ancestor about it --
    Pane.focusInEvent only fires when the Pane itself is the focus target, which it never
    is once a body is attached. So `active` stayed on whichever pane was created last:
    splitting split the wrong pane, and Ctrl+Shift+W closed a pane the user was not
    looking at.
    """
    tree.resize(600, 400)
    tree.show()
    first = tree.add_pane()
    body = _focusable_body(first)
    last = tree.add_pane(orientation=H)
    last_body = _focusable_body(last)
    app.processEvents()

    # Focus each in turn rather than leaning on creation order: showing the tree gives
    # focus to the first focusable widget it finds, which is itself the behaviour under
    # test and would make the starting point depend on the fix.
    last_body.setFocus(Qt.FocusReason.MouseFocusReason)
    app.processEvents()
    assert tree.active is last

    body.setFocus(Qt.FocusReason.MouseFocusReason)
    app.processEvents()
    assert tree.active is first


def test_a_split_lands_under_the_focused_pane(app, tree):
    tree.resize(600, 400)
    tree.show()
    first = tree.add_pane()
    body = _focusable_body(first)
    tree.add_pane(orientation=H)
    app.processEvents()

    body.setFocus(Qt.FocusReason.MouseFocusReason)
    app.processEvents()
    tree.add_pane(orientation=V)

    # The new pane nests under the focused one, not beside the last created.
    assert tree.describe().startswith("H[V["), tree.describe()


def test_one_tree_does_not_claim_another_trees_focus(app, tree):
    """Every tree listens to the same application-wide signal."""
    other = PaneTree()
    other.resize(400, 300)
    other.show()
    mine = tree.add_pane()
    _focusable_body(mine)
    theirs = other.add_pane()
    other_body = _focusable_body(theirs)
    app.processEvents()

    other_body.setFocus(Qt.FocusReason.MouseFocusReason)
    app.processEvents()

    assert tree.active is mine, "the other tree's focus changed this tree's active pane"
    assert other.active is theirs
    other.deleteLater()


# --- closing a pane from the pane itself ----------------------------------------------

def test_the_pane_asks_to_be_closed_rather_than_closing_itself(tree):
    """The tree does not own the shell; the window does. A pane that removed itself here
    would drop the widget and leave the pty running."""
    pane = tree.add_pane()
    asked = []
    tree.paneCloseRequested.connect(asked.append)

    pane.closeRequested.emit(pane)

    assert asked == [pane]
    assert pane in tree.panes, "the tree must not act on the request itself"


def test_restored_panes_can_also_ask_to_close(tree):
    """The rebuild path is a second place the signal has to be connected, and the kind
    that is easy to miss because the tree looks identical either way."""
    tree.add_pane()
    tree.add_pane(orientation=H)
    restored = tree.restore(tree.to_tree())
    asked = []
    tree.paneCloseRequested.connect(asked.append)

    restored[0].closeRequested.emit(restored[0])

    assert asked == [restored[0]]


# --- telling somebody the arrangement changed -------------------------------------------

def test_the_tree_says_when_its_shape_changes(tree):
    """One signal for three events -- a pane added, a pane closed, a splitter dragged --
    because everything to_tree() serialises is one of the three, and its only listener is
    "persist this", which does not care which happened."""
    changes = []
    tree.layoutChanged.connect(lambda: changes.append(tree.describe()))

    first = tree.add_pane()
    tree.add_pane(orientation=H)
    assert len(changes) == 2

    tree.close_pane(first)
    assert len(changes) == 3


def test_dragging_any_splitter_counts(tree):
    """Nested ones too: a nested split is where most dragging happens, and proportions are
    half of what to_tree() records."""
    tree.add_pane()
    tree.add_pane(orientation=H)
    tree.add_pane(orientation=V)
    changes = []
    tree.layoutChanged.connect(lambda: changes.append(1))

    for splitter in tree.findChildren(QSplitter):
        splitter.splitterMoved.emit(40, 1)

    assert len(changes) == len(tree.findChildren(QSplitter)) >= 2



# --- zoom ------------------------------------------------------------------------------
#
# Zoom hides siblings rather than reparenting the pane, so most of what is asserted here is
# about what zoom must *not* disturb: the shape, the saved proportions, and the splitter
# objects the tree is built from.


@pytest.fixture
def shown_tree(tree, app):
    """A tree with real geometry, for the assertions that only mean something in pixels."""
    tree.resize(900, 600)
    tree.show()
    app.processEvents()
    yield tree, app
    tree.hide()


def test_zoom_hides_the_siblings_and_leaves_the_shape_alone(tree):
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.add_pane(orientation=V)
    assert tree.describe() == "H[0 V[1 2]]"

    tree.set_zoom(second)
    # Shape is not visibility. to_tree() and every collapse rule still see three panes in
    # the arrangement the user built; only one of them is on screen.
    assert tree.describe() == "H[0 V[1 2]]"
    assert tree.zoomed is second
    assert first.isHidden() is True
    assert second.isHidden() is False


def test_zoom_hides_siblings_at_every_level(tree):
    tree.add_pane()
    tree.add_pane(orientation=H)
    third = tree.add_pane(orientation=V)
    assert tree.describe() == "H[0 V[1 2]]"

    tree.set_zoom(third)
    # The pane is two splitters deep. Hiding only its own sibling would zoom it to its
    # branch and leave pane 0 beside it.
    assert [p.isHidden() for p in tree.panes] == [True, True, False]


def test_unzoom_restores_visibility(tree):
    tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.set_zoom(second)
    tree.set_zoom(None)
    assert tree.zoomed is None
    assert not any(p.isHidden() for p in tree.panes)


def test_toggle_zoom_goes_both_ways(tree):
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.toggle_zoom(second)
    assert tree.zoomed is second
    tree.toggle_zoom(second)
    assert tree.zoomed is None
    # Toggling a *different* pane while zoomed moves the zoom rather than clearing it.
    tree.toggle_zoom(second)
    tree.toggle_zoom(first)
    assert tree.zoomed is first


def test_the_zoomed_pane_actually_fills_the_tree(shown_tree):
    tree, app = shown_tree
    tree.add_pane()
    second = tree.add_pane(orientation=H)
    app.processEvents()
    shared = second.width()

    tree.set_zoom(second)
    app.processEvents()
    # The point of the feature, and the thing a stray size hint could silently cost us.
    # setChildrenCollapsible(False) governs dragging, not visibility.
    assert second.width() > shared
    assert second.width() >= tree.width() - 4


def test_unzoom_puts_the_proportions_back(shown_tree):
    tree, app = shown_tree
    tree.add_pane()
    second = tree.add_pane(orientation=H)
    root = tree._root
    root.setSizes([600, 300])
    app.processEvents()
    before = root.sizes()

    tree.set_zoom(second)
    app.processEvents()
    tree.set_zoom(None)
    app.processEvents()
    assert root.sizes() == before


def test_zoom_does_not_reach_the_saved_layout(shown_tree):
    """The regression this whole design is arranged around.

    A hidden child reports a size of 0, and the layout save is debounced by more than a
    second -- so it lands while still zoomed. Serialising what the splitter says at that
    moment writes [0, everything], which _apply_sizes then rejects on the way back in
    because it holds a zero: the arrangement does not return collapsed, it returns with no
    proportions at all, and the user's split points are gone.
    """
    tree, app = shown_tree
    tree.add_pane(launcher_id="git-bash")
    second = tree.add_pane(orientation=H, launcher_id="pwsh")
    tree._root.setSizes([600, 300])
    app.processEvents()
    before = tree.to_tree()

    tree.set_zoom(second)
    app.processEvents()
    assert tree._root.sizes() != before["sizes"], "the splitter did not actually collapse"
    assert tree.to_tree() == before


def test_zoom_does_not_announce_a_layout_change(tree):
    """layoutChanged means to_tree() now returns something different, and zoom does not."""
    seen = []
    tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.layoutChanged.connect(lambda: seen.append(True))
    tree.set_zoom(second)
    tree.set_zoom(None)
    assert seen == []


def test_zoom_reports_itself(tree):
    seen = []
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.zoomChanged.connect(seen.append)
    tree.set_zoom(second)
    tree.set_zoom(first)
    tree.set_zoom(None)
    assert seen == [second, first, None]
    # Setting the same zoom twice is not a change.
    tree.set_zoom(first)
    tree.set_zoom(first)
    assert seen == [second, first, None, first]


def test_splitting_while_zoomed_returns_to_the_grid(tree):
    tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.set_zoom(second)

    third = tree.add_pane(orientation=V)
    assert tree.zoomed is None
    assert not any(p.isHidden() for p in tree.panes)
    # And it landed beside the pane it split, not at the root -- which is what would break
    # if the pane had been lifted out of the splitter tree instead of hidden.
    assert tree.describe() == "H[0 V[1 2]]"
    assert third in tree.panes


def test_closing_the_zoomed_pane_returns_to_the_grid(tree):
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.set_zoom(second)
    tree.close_pane(second)
    assert tree.zoomed is None
    assert tree.panes == [first]
    assert first.isHidden() is False


def test_closing_a_hidden_pane_is_safe(tree):
    """The tab strip stays visible while zoomed, and its close button can reach a pane you
    cannot see.

    The ordering matters more than the outcome: _collapse deletes splitters, and a zoom
    holding recorded sizes against one of them would be holding a deleted C++ object.
    """
    def build(zoom_the_last: bool):
        built = PaneTree()
        first = built.add_pane()
        built.add_pane(orientation=H)
        last = built.add_pane(orientation=V)
        if zoom_the_last:
            built.set_zoom(last)
        built.close_pane(first)
        return built

    zoomed, control = build(True), build(False)
    assert zoomed.zoomed is None
    assert zoomed.count() == 2
    # Whatever closing that pane does to the tree, zoom must not change it.
    assert zoomed.describe() == control.describe()
    assert not any(p.isHidden() for p in zoomed.panes)
    zoomed.deleteLater()
    control.deleteLater()


def test_activating_a_hidden_pane_moves_the_zoom(tree):
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.set_zoom(second)

    tree.set_active(first)
    # Rather than unzooming: the tab strip is the switcher while zoomed, and a tab that
    # focuses a hidden widget looks like a tab that does nothing.
    assert tree.zoomed is first
    assert tree.active is first
    assert first.isHidden() is False
    assert second.isHidden() is True


def test_zooming_the_only_pane_is_harmless(tree):
    only = tree.add_pane()
    tree.set_zoom(only)
    # Nothing to hide; it holds the flag so the button and the toggle stay symmetric.
    assert tree.zoomed is only
    assert only.isHidden() is False
    tree.set_zoom(None)
    assert tree.zoomed is None
    assert tree.describe() == "H[0]"


def test_zoom_declines_a_pane_that_is_not_ours(tree):
    first = tree.add_pane()
    other = PaneTree()
    stranger = other.add_pane()
    tree.set_zoom(stranger)
    assert tree.zoomed is None
    tree.set_zoom(first)
    tree.toggle_zoom(stranger)
    assert tree.zoomed is first
    other.deleteLater()


def test_restoring_leaves_nothing_zoomed(tree, app):
    tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.set_zoom(second)
    saved = tree.to_tree()

    tree.restore(saved)
    assert tree.zoomed is None
    assert not any(p.isHidden() for p in tree.panes)


def test_the_pane_shows_that_it_is_zoomed(tree):
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.set_zoom(second)
    assert second.zoomed is True
    assert second.property("zoomed") == "true"
    assert second.zoom_button.text() != first.zoom_button.text()
    tree.set_zoom(None)
    assert second.zoomed is False
    assert second.property("zoomed") == "false"
    assert second.zoom_button.text() == first.zoom_button.text()


def test_the_pane_asks_the_tree_to_zoom_it(tree):
    """The header button and the double-click both go straight to the tree.

    Unlike closeRequested, which the window has to answer because it owns the session.
    """
    tree.add_pane()
    second = tree.add_pane(orientation=H)
    second.zoom_button.click()
    assert tree.zoomed is second
    second.header.doubleClicked.emit()
    assert tree.zoomed is None


# Four things that were wrong once and are invisible when they are wrong again: a bystander
# pane reflowing, one click doing the work of three, a button that never lights up, and the
# keyboard being left somewhere you cannot type.


class _Body(QWidget):
    """Stands in for a terminal: records the grid each real resize would ask the pty for.

    Carries TerminalView's own two filters -- decline a zero extent, and say nothing when
    the grid has not actually changed -- so what it records is what a pty would be told.
    """

    def __init__(self):
        super().__init__()
        self.grids: list[int] = []
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        if event.size().width() <= 0 or event.size().height() <= 0:
            return
        cols = max(2, event.size().width() // 8)
        if not self.grids or self.grids[-1] != cols:
            self.grids.append(cols)


@pytest.fixture
def wired_tree(tree, app):
    """H[0 V[1 2]] at a real size, every pane holding a body that records resizes."""
    tree.resize(1200, 700)
    panes = [tree.add_pane(), tree.add_pane(orientation=H), tree.add_pane(orientation=V)]
    bodies = []
    for pane in panes:
        body = _Body()
        pane.attach_body(body)
        bodies.append(body)
    tree.show()
    app.processEvents()
    assert tree.describe() == "H[0 V[1 2]]"
    yield tree, panes, bodies, app
    tree.hide()


def test_unzooming_does_not_resize_a_pane_that_never_moved(wired_tree):
    """Restoring has to go root outward, and nothing else says so.

    Handing an inner splitter its children back while the root is still collapsed lays
    them out at the zoomed width first and corrects them immediately after -- so a pane
    that was only ever a bystander gets its pseudo-console resized to the zoomed width and
    back. Measured leaf-first on this shape: pane 2 went to 149 columns and back to 74.
    """
    tree, panes, bodies, app = wired_tree
    tree.set_zoom(panes[1])
    app.processEvents()
    for body in bodies:
        body.grids.clear()

    tree.set_zoom(None)
    app.processEvents()

    settled = bodies[2].grids[-1] if bodies[2].grids else None
    assert bodies[2].grids in ([], [settled]), (
        f"pane 2 was resized on the way back: {bodies[2].grids}")
    for index, body in enumerate(bodies):
        assert len(body.grids) <= 1, f"pane {index} reflowed more than once: {body.grids}"


def test_moving_the_zoom_activates_once(wired_tree):
    """Hiding the focused pane makes Qt move focus, and that is a focusChanged we caused.

    Following it re-enters set_active, which moves the zoom again mid-move. Measured
    without the guard: three activations, and three full chrome refreshes, for one click
    on one tab.
    """
    tree, panes, bodies, app = wired_tree
    bodies[1].setFocus()
    app.processEvents()
    tree.set_zoom(panes[1])
    app.processEvents()

    seen = []
    tree.paneActivated.connect(lambda p: seen.append(p.pane_id))
    tree.set_active(panes[0])
    app.processEvents()

    assert seen == [panes[0].pane_id], f"one activation became {len(seen)}"
    assert tree.zoomed is panes[0]
    assert tree.active is panes[0]


def test_zooming_an_inactive_pane_makes_it_the_active_one(wired_tree):
    """Its button is clickable while another pane has focus, and everything keyed on
    `active` -- split, close, the status bar -- would then be aiming off screen."""
    tree, panes, bodies, app = wired_tree
    tree.set_active(panes[0])
    assert tree.active is panes[0]

    panes[2].zoom_button.click()
    assert tree.zoomed is panes[2]
    assert tree.active is panes[2]


def test_the_zoom_button_actually_changes_colour(wired_tree):
    """`Pane[zoomed="true"] #PaneZoom` is a descendant selector, and Qt evaluates one when
    the *child* is polished. Repolishing only the pane leaves it matched and unpainted --
    measured as #6d6d6a through every zoom until the button itself was unpolished."""
    tree, panes, bodies, app = wired_tree
    th.apply(app, th.Theme("dark"))
    button = panes[1].zoom_button

    resting = button.palette().color(button.foregroundRole()).name()
    tree.set_zoom(panes[1])
    app.processEvents()
    lit = button.palette().color(button.foregroundRole()).name()

    assert lit != resting, f"the glyph stayed {resting} while zoomed"
    assert lit == th.Theme("dark").hex("accent")


def test_the_zoom_button_does_not_keep_the_keyboard(wired_tree):
    """A focused QPushButton answers Space by clicking itself, so the chord that zoomed
    the pane would unzoom it again -- and the terminal you enlarged is not what you are
    typing into."""
    tree, panes, bodies, app = wired_tree
    assert panes[0].zoom_button.focusPolicy() == Qt.FocusPolicy.NoFocus


# --- hiding ------------------------------------------------------------------------------
#
# Zoom's opposite, built on the same machinery: one pane leaves the grid and the rest take
# its space. So most of what is asserted here is that the two compose -- above all that
# unzooming does not undo a hide, which is the bug a single visibility predicate exists to
# prevent -- plus the two rules hiding needs on its own: the last visible pane cannot go,
# and the shape the user arranged is untouched by any of it.


def test_hiding_a_pane_takes_it_off_the_grid_and_leaves_the_shape_alone(tree):
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    assert tree.describe() == "H[0 1]"

    tree.hide_pane(first)

    # Shape is not visibility, exactly as it is not for zoom: to_tree() and every collapse
    # rule still see the two panes the user arranged.
    assert tree.describe() == "H[0 1]"
    assert tree.count() == 2
    assert first.isHidden() is True
    assert second.isHidden() is False
    assert tree.is_hidden(first) is True
    assert tree.hidden == [first]


def test_a_hidden_pane_keeps_its_place_in_the_pane_list(tree):
    """The tab strip is built from tree order, and a hidden pane keeps its tab -- so a
    pane that came back would land somewhere else in the strip if this drifted."""
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    third = tree.add_pane(orientation=H)

    tree.hide_pane(second)

    assert tree.panes == [first, second, third]


def test_the_last_visible_pane_cannot_be_hidden(tree):
    """An empty grid has nothing on it to right-click, so the menu that emptied it could
    not undo it."""
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.hide_pane(first)

    assert tree.can_hide(second) is False
    tree.hide_pane(second)
    assert second.isHidden() is False
    assert tree.hidden_count() == 1


def test_a_lone_pane_cannot_be_hidden(tree):
    only = tree.add_pane()
    assert tree.can_hide(only) is False
    tree.hide_pane(only)
    assert only.isHidden() is False


def test_an_already_hidden_pane_cannot_be_hidden_twice(tree):
    first = tree.add_pane()
    tree.add_pane(orientation=H)
    tree.hide_pane(first)
    assert tree.can_hide(first) is False


def test_hiding_every_pane_in_a_branch_hides_the_branch(tree):
    """A splitter is worth showing only for its contents. One left holding nothing visible
    still claims its share of the parent, so hiding inside a nested branch would reclaim
    no space at all."""
    tree.add_pane()
    second = tree.add_pane(orientation=H)
    third = tree.add_pane(orientation=V)
    assert tree.describe() == "H[0 V[1 2]]"
    inner = tree._splitter_of(second)

    tree.hide_pane(second)
    assert inner.isHidden() is False       # third is still in there
    tree.hide_pane(third)
    assert inner.isHidden() is True

    tree.show_all_panes()
    assert inner.isHidden() is False


def test_showing_a_pane_puts_it_back(tree):
    first = tree.add_pane()
    tree.add_pane(orientation=H)

    tree.hide_pane(first)
    tree.show_pane(first)

    assert first.isHidden() is False
    assert tree.hidden_count() == 0
    assert first.hidden is False


def test_toggle_hidden_goes_both_ways(tree):
    first = tree.add_pane()
    tree.add_pane(orientation=H)

    tree.toggle_hidden(first)
    assert tree.is_hidden(first) is True
    tree.toggle_hidden(first)
    assert tree.is_hidden(first) is False


def test_show_all_panes_brings_back_every_one(tree):
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.add_pane(orientation=H)
    tree.hide_pane(first)
    tree.hide_pane(second)

    tree.show_all_panes()

    assert tree.hidden_count() == 0
    assert [p.isHidden() for p in tree.panes] == [False, False, False]


def test_hiding_moves_the_active_pane_to_a_visible_one(tree):
    """focus_active() on a hidden widget does nothing, so leaving the hidden pane active
    would leave the keyboard nowhere."""
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.set_active(first)

    tree.hide_pane(first)

    assert tree.active is second


def test_hiding_an_inactive_pane_leaves_the_active_one_alone(tree):
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.set_active(second)

    tree.hide_pane(first)

    assert tree.active is second


def test_activating_a_hidden_pane_shows_it(tree):
    """The whole restore gesture. The tab strip routes a click into set_active, so this
    one rule is what makes a hidden pane's tab the way back."""
    first = tree.add_pane()
    tree.add_pane(orientation=H)
    tree.hide_pane(first)

    tree.set_active(first)

    assert first.isHidden() is False
    assert tree.active is first
    assert tree.hidden_count() == 0


# --- hiding: what it must not disturb ----------------------------------------------------


def test_hiding_does_not_announce_a_layout_change(tree):
    """layoutChanged means "to_tree() would return something different". Hiding changes
    nothing it serialises, and wiring it in would save the layout on every hide."""
    tree.add_pane()
    second = tree.add_pane(orientation=H)
    changes = []
    tree.layoutChanged.connect(lambda: changes.append(1))

    tree.hide_pane(second)
    tree.show_pane(second)

    assert changes == []


def test_hiding_announces_itself_once(tree):
    tree.add_pane()
    second = tree.add_pane(orientation=H)
    seen = []
    tree.hiddenChanged.connect(lambda: seen.append(1))

    tree.hide_pane(second)
    tree.hide_pane(second)      # already hidden: nothing changed, nothing said
    tree.show_pane(second)
    tree.show_pane(second)

    assert len(seen) == 2


def test_show_all_panes_says_it_once_for_all_of_them(tree):
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.add_pane(orientation=H)
    tree.hide_pane(first)
    tree.hide_pane(second)
    seen = []
    tree.hiddenChanged.connect(lambda: seen.append(1))

    tree.show_all_panes()

    assert len(seen) == 1


def test_a_layout_saved_while_hidden_still_describes_the_grid(shown_tree):
    """A hidden child reports size 0, and _apply_sizes rejects any list holding a zero --
    so without the recorded sizes a save taken while a pane is hidden comes back with no
    proportions at all and the split points the user dragged are gone."""
    tree, app = shown_tree
    first = tree.add_pane()
    tree.add_pane(orientation=H)
    tree._root.setSizes([600, 300])
    app.processEvents()
    before = tree.to_tree()

    tree.hide_pane(first)
    app.processEvents()

    saved = tree.to_tree()
    assert saved == before
    assert all(saved["sizes"])


def test_hiding_restores_the_proportions_when_everything_is_back(shown_tree):
    tree, app = shown_tree
    first = tree.add_pane()
    tree.add_pane(orientation=H)
    tree._root.setSizes([600, 300])
    app.processEvents()
    before = tree._root.sizes()

    tree.hide_pane(first)
    app.processEvents()
    tree.show_pane(first)
    app.processEvents()

    assert tree._root.sizes() == before


def test_a_hidden_pane_gives_its_space_to_the_others(shown_tree):
    """Only means anything in pixels: a hidden QSplitter child does not report 0 until the
    tree has actually laid out."""
    tree, app = shown_tree
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    app.processEvents()
    whole = tree._root.width()

    tree.hide_pane(first)
    app.processEvents()

    assert tree._root.sizes()[0] == 0
    assert second.width() > whole * 0.9


# --- hiding: composing with zoom ---------------------------------------------------------


def test_unzooming_does_not_bring_back_a_hidden_pane(tree):
    """The bug the single visibility predicate exists to prevent. Unzoom used to call
    show() on every child of every splitter it had collapsed, which put a pane the user
    had deliberately hidden back on the grid."""
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    third = tree.add_pane(orientation=H)
    tree.hide_pane(first)

    tree.set_zoom(second)
    tree.set_zoom(None)

    assert first.isHidden() is True
    assert tree.is_hidden(first) is True
    assert second.isHidden() is False
    assert third.isHidden() is False


def test_zooming_a_hidden_pane_shows_it(tree):
    """Reachable in one click: a hidden pane keeps its tab, and selecting it while another
    pane is zoomed arrives in set_zoom through set_active."""
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.hide_pane(first)
    tree.set_zoom(second)

    tree.set_zoom(first)

    assert tree.is_hidden(first) is False
    assert first.isHidden() is False
    assert tree.zoomed is first


def test_hiding_the_zoomed_pane_returns_to_the_grid(tree):
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.set_zoom(first)

    tree.hide_pane(first)

    assert tree.zoomed is None
    assert first.isHidden() is True
    assert second.isHidden() is False


def test_selecting_a_hidden_pane_while_zoomed_moves_the_zoom_to_it(tree):
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    third = tree.add_pane(orientation=H)
    tree.hide_pane(third)
    tree.set_zoom(first)

    tree.set_active(third)

    assert tree.is_hidden(third) is False
    assert tree.zoomed is third
    assert second.isHidden() is True


# --- hiding: surviving the tree changing shape -------------------------------------------


def test_closing_a_hidden_pane_leaves_nothing_behind(tree):
    """_collapse deletes splitters, and a _hidden_sizes key pointing at a deleted C++
    object is a fault the next time anything walks the dict."""
    tree.add_pane()
    second = tree.add_pane(orientation=H)
    third = tree.add_pane(orientation=V)
    tree.hide_pane(third)

    tree.close_pane(third)

    assert tree.hidden_count() == 0
    assert tree._hidden_sizes == {}
    # The splitter that held second and third is gone; walking the tree must still work.
    assert tree.describe() == "H[0 1]"
    assert second.isHidden() is False


def test_closing_the_sibling_of_a_hidden_pane_keeps_it_hidden(tree):
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    third = tree.add_pane(orientation=H)
    tree.hide_pane(first)

    tree.close_pane(second)

    assert tree.is_hidden(first) is True
    assert first.isHidden() is True
    assert third.isHidden() is False
    assert tree._hidden_sizes == {}


def test_closing_the_last_visible_pane_brings_the_hidden_ones_back(tree):
    """can_hide refuses to leave a grid with nothing on it, so closing must not arrive at
    that state round the back -- there would be no pane left to right-click."""
    first = tree.add_pane()
    second = tree.add_pane(orientation=H)
    tree.hide_pane(first)

    tree.close_pane(second)

    assert tree.hidden_count() == 0
    assert first.isHidden() is False


def test_splitting_while_a_pane_is_hidden_keeps_it_hidden(tree):
    """Adding a pane is not a reason to put back the ones somebody put away."""
    first = tree.add_pane()
    tree.add_pane(orientation=H)

    tree.hide_pane(first)
    fresh = tree.add_pane(orientation=H)

    assert first.isHidden() is True
    assert tree.is_hidden(first) is True
    assert fresh.isHidden() is False


def test_clearing_the_tree_forgets_what_was_hidden(tree):
    first = tree.add_pane()
    tree.add_pane(orientation=H)
    tree.hide_pane(first)

    tree.clear()

    assert tree.hidden_count() == 0
    assert tree._hidden_sizes == {}


def test_a_restored_layout_comes_back_with_everything_showing(tree):
    """Hiding is a focus mode, not part of the arrangement -- so it is never written and
    never comes back."""
    first = tree.add_pane()
    tree.add_pane(orientation=H)
    tree.hide_pane(first)
    saved = tree.to_tree()

    panes = tree.restore(saved)

    assert tree.hidden_count() == 0
    assert [p.isHidden() for p in panes] == [False, False]


def test_a_hidden_pane_mirrors_the_state_onto_itself(tree):
    """The chrome that lists panes reads the pane rather than hunting for its tree."""
    first = tree.add_pane()
    tree.add_pane(orientation=H)

    tree.hide_pane(first)
    assert first.hidden is True
    assert first.property("hidden") == "true"

    tree.show_pane(first)
    assert first.hidden is False
    assert first.property("hidden") == "false"
