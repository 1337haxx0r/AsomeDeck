"""What a pane header says about the body inside it.

The renderer in a pane is duck-typed -- term_pane.py's whole surface is reached by
``getattr``, which is what let an xterm.js pane be swapped for a painted one by changing
an import. So the window asks a body what it can report rather than assuming, and these
are the two halves of that: a body that answers gets its answer shown, and a body that
does not is still allowed in the pane.

The field this fills was blank in every header in the app. ``Pane.set_geometry_label``
existed, was correct, and had no caller -- the recurring failure this codebase keeps
finding in itself.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Signal  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402


class Reporter(QWidget):
    """A body that reports its grid, and nothing else. What a terminal looks like from
    the window's side."""

    resized = Signal(int, int)


def test_a_pane_shows_the_grid_its_body_reports(window):
    """Driven by the body's own `resized` -- the signal the view already emitted to tell
    the pty its size in cells, which until now had exactly one listener."""
    pane = window.tree.add_pane()
    assert pane.geometry_label.text() == ""

    body = Reporter()
    window._wire_reports(pane, body)
    body.resized.emit(120, 34)

    assert pane.geometry_label.text() == "120×34"


def test_the_header_keeps_up_with_a_drag(window):
    """The size is not set once at launch: dragging a splitter re-emits, and the header
    is the only place in the window that says how big the grid actually is."""
    pane = window.tree.add_pane()
    body = Reporter()
    window._wire_reports(pane, body)

    body.resized.emit(120, 34)
    body.resized.emit(80, 24)

    assert pane.geometry_label.text() == "80×24"


def test_a_body_that_reports_nothing_is_still_allowed(window):
    """A hard attribute access here would quietly end the swappable-renderer contract."""
    pane = window.tree.add_pane()
    window._wire_reports(pane, QWidget())
    assert pane.geometry_label.text() == ""
