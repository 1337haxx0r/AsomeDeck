"""A tab leaving the strip must leave, and not turn up as a window of its own.

Opening a restored deck for the first time put one blank white window on the desktop for each
tab of the deck being left, over the title bar. Nothing about that needs a real window system:
it is a widget going visible with no parent, and that reads the same offscreen.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from asomedeck.ui.chrome import SessionTab, SessionTabStrip  # noqa: E402
from asomedeck.ui.theme import Theme  # noqa: E402


class _Pane:
    """All ``sync`` asks of a pane is to exist and to have a number."""

    pane_id = 1


def _windows_made_of_tabs(app) -> list[SessionTab]:
    return [widget for widget in app.topLevelWidgets()
            if isinstance(widget, SessionTab) and widget.isVisible()]


def test_a_tab_removed_before_its_queued_show_stays_hidden(app):
    """The mechanism, on the strip alone.

    Every ``sync`` re-inserts every tab, and a layout asks for the show later, on a queue.
    So a tab removed in the same turn as a ``sync`` that kept it still has that show coming.
    """
    strip = SessionTabStrip(Theme("dark"))
    strip.show()
    pane = _Pane()
    row = [(pane, "pane 1", "", True, False)]
    strip.sync(row, pane)
    app.processEvents()

    strip.sync(row, pane)
    strip.sync([], None)
    app.processEvents()

    assert _windows_made_of_tabs(app) == []
    strip.close()


def test_first_opening_a_restored_deck_leaves_no_tab_behind_as_a_window(app, window, tmp_path):
    """The way it was seen. ``_tree_for`` restores before the stack changes page, so the
    restore's own refresh still syncs the old deck's tabs, and the refresh after it removes
    them: the same turn, every time, for any deck with a saved layout."""
    folder = tmp_path / "second"
    folder.mkdir()
    second = window.db.add_deck(str(folder), "")
    window.db.save_layout(second.id, {"type": "pane", "launcher_id": ""})

    window.tree.add_pane()
    window.tree.add_pane()
    window.show()
    app.processEvents()

    window._open_deck(second.id)
    app.processEvents()

    assert _windows_made_of_tabs(app) == []
