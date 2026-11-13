"""Grid alignment, checked in pixels.

The terminal is a fixed grid: column N starts at exactly ``PAD_LEFT + N * cell_w``, and
nothing may shift what follows it. A double-width character breaks that in two ways at
once, and both were live:

* pyte parks the glyph at the first column and leaves the second cell holding ``""``.
  Rendering that empty cell as a space drew twelve display columns of text into ten
  columns of grid -- everything to the right of any CJK character moved.
* the face Qt falls back to for CJK is not the terminal font, and its advance is not
  exactly two cells (17px against 18 here), so a wide glyph inside a batched run drifts
  the rest of the line even once the phantom space is gone.

Asserting on the drawn image is the point. Both faults are invisible to any test that
inspects the screen buffer, because the buffer was always right.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")
pytest.importorskip("pyte")

from asomedeck.ui import theme as th  # noqa: E402
from asomedeck.ui.term_view import PAD_LEFT, PAD_TOP, TerminalView  # noqa: E402

WIDE = "漢"      # a double-width han character
MARKER = "█"    # full block: fills its cell, so a column either has ink or does not

#: The cursor is ink too, and it sits one column past the text -- which is exactly the
#: column these tests assert is empty. Hidden rather than worked around, so a failure
#: means the glyphs moved and nothing else.
HIDE_CURSOR = "[?25l"


@pytest.fixture
def view(app):
    th.apply(app, th.Theme("dark"))
    widget = TerminalView(th.Theme("dark"))
    widget.resize(900, 260)
    widget.show()
    app.processEvents()
    yield widget
    widget.close()


def columns_with_ink(view: TerminalView, row: int = 0) -> set[int]:
    """Which grid columns contain drawn pixels, sampled at each column's centre."""
    image = view.grab().toImage()
    background = image.pixelColor(view.width() - 2, view.height() - 2)
    found = set()
    cell_w, cell_h = view.cell_size()
    for col in range(view.emulator.screen.columns):
        x = int(PAD_LEFT + col * cell_w + cell_w / 2)
        if x >= image.width():
            break
        for dy in range(2, cell_h - 1):
            y = PAD_TOP + row * cell_h + dy
            if y < image.height() and image.pixelColor(x, y) != background:
                found.add(col)
                break
    return found


def test_a_wide_character_does_not_shift_what_follows(view):
    """The regression, stated as geometry: the marker sits where the grid says.

    Two double-width characters occupy columns 2-5, so the block lands in column 6 and
    nothing may be drawn in column 7.
    """
    view.emulator.feed(f"{HIDE_CURSOR}ab{WIDE}{WIDE}{MARKER}")
    view.emulator.drain()
    view.repaint()

    ink = columns_with_ink(view)
    assert 6 in ink, f"the marker is not in its column; ink at {sorted(ink)}"
    assert 7 not in ink, f"something spilled past the marker; ink at {sorted(ink)}"


def test_the_same_line_without_wide_characters_agrees(view):
    """A control. Four narrow cells then the marker puts it in column 4."""
    view.emulator.feed(f"{HIDE_CURSOR}abcd{MARKER}")
    view.emulator.drain()
    view.repaint()

    ink = columns_with_ink(view)
    assert 4 in ink and 5 not in ink, f"ink at {sorted(ink)}"


def test_the_continuation_cell_is_not_painted_as_a_space(view):
    """pyte stores '' there, and a space is not the same thing as nothing."""
    view.emulator.feed(f"{HIDE_CURSOR}{WIDE}")
    view.emulator.drain()
    row = view.emulator.screen.buffer[0]
    assert row[0].data == WIDE
    assert row[1].data == "", "pyte's continuation cell is no longer empty"


def test_selection_copies_a_wide_character_once(view):
    """The buffer was always right; this pins that the fix did not disturb it."""
    view.emulator.feed(f"{HIDE_CURSOR}a{WIDE}b")
    view.emulator.drain()
    view._sel_anchor, view._sel_head = (0, 0), (0, 4)
    assert view.selected_text().startswith(f"a{WIDE}b")
