"""The terminal widget: a grid of cells, painted by Qt.

This is what replaced xterm.js in a QWebEngineView. The web view worked -- its probe
passed every check -- and cost twelve processes and 572 MB to show three terminals, where
this costs eight and 210 MB. That is what a render process per pane buys you.

What it owns: the font metrics that define a cell, the paint, the keyboard, the mouse, the
selection, and the scrollback viewport. What it does not own: the escape sequences (see
term/emulator.py) and the shell (see term/conpty.py). It never touches a pty directly --
it emits the bytes it wants written and lets its owner decide.

The one deliberate rounding decision, because it caused a real bug in the chrome already
(see theme.py's ``_px``): a cell's width is measured in float and floored once, and every
subsequent x is an integer multiple of it. Rounding per-column instead accumulates a
sub-pixel error across eighty columns and the cursor drifts off the character it is on.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import QApplication, QScrollBar, QWidget

from .. import platform_
from ..term import keys
from ..term.emulator import DEFAULT_SCROLLBACK, Emulator, private
from .theme import FONT_MONO, FONT_MONO_FALLBACK, GEOM, TYPE, Theme

log = logging.getLogger(__name__)

#: Padding inside the widget, carried over from what the old page reserved, so the grid
#: does not start flush against the pane's focus ring.
PAD_LEFT = 8
PAD_TOP = 6

#: The right gutter belongs to the scrollbar, and it is reserved whether or not the bar
#: is showing. A bar that took its width from the grid only while visible would resize
#: the emulator *and the pseudo-console* the first time scrollback passed one screenful,
#: and again every time it crossed back -- the reflow that resizeEvent below and
#: tools/probe_zoom.py both exist to prevent. The price is one column, permanently, in
#: every pane; the two spare pixels keep text off the handle's rounded edge.
PAD_RIGHT = GEOM["scrollbar_w"] + 2

#: The cursor blinks at the rate Windows uses. Off entirely while output is streaming
#: would be nicer still, but a terminal that stops blinking looks hung.
BLINK_MS = 530

#: pyte's names for the sixteen ANSI colours, in the order SGR numbers them.
ANSI_NAMES = ("black", "red", "green", "brown", "blue", "magenta", "cyan", "white")
ANSI_BRIGHT = ("brightblack", "brightred", "brightgreen", "brightbrown",
               "brightblue", "brightmagenta", "brightcyan", "brightwhite")

#: DECTCEM. Reset by ``?25l``, which every full-screen program does while it redraws.
MODE_CURSOR_VISIBLE = private(25)

#: The clipboard chords, named so the right-click menu can label its rows with the keys
#: that actually work rather than a second set of literals that would drift from these.
#: They are not in commands.py because they are handled by the focused view, not the window.
#:
#: Shifted on Windows, and not negotiable there: ``Ctrl+C`` is SIGINT and ``Ctrl+V`` is a
#: control byte, so the terminal convention is the shifted forms -- see ``keyPressEvent``.
#:
#: On macOS the shift is not only unnecessary but wrong. Cmd is not a control key, so
#: ``Cmd+C`` is free to be copy exactly as it is in every other Mac application, and
#: physical Ctrl+C keeps SIGINT to itself. Written as "Ctrl+…" because that is the spelling
#: QKeySequence maps to Cmd on macOS -- these strings are rendered as ``⌘C``/``⌘V``.
#: tests/test_commands.py forbids a bare ``Ctrl+<letter>`` in the *window's* registry for
#: exactly the reason that does not apply here: this chord is Cmd, and it never reaches a
#: shell.
if platform_.IS_MACOS:
    KEY_COPY = "Ctrl+C"
    KEY_PASTE = "Ctrl+V"
else:
    KEY_COPY = "Ctrl+Shift+C"
    KEY_PASTE = "Ctrl+Shift+V"


def term_palette(theme: Theme) -> dict[str, QColor]:
    """Design tokens mapped onto the sixteen ANSI colours.

    Lifted wholesale from what ``xterm_pane.xterm_theme`` fed xterm.js, because the
    mapping was already made and already matches the design system. The names are pyte's:
    it reports ``Char.fg`` as a colour *name* for the basic sixteen and as six hex digits
    for anything from 256-colour or truecolor.

    Note ``brown``. pyte follows the VT100 naming where SGR 33 is brown rather than
    yellow, and reports that name -- a palette keyed on "yellow" silently loses it.
    """
    hexes = {
        "black": theme.hex("bg"),
        "red": theme.hex("del"),
        "green": theme.hex("add"),
        "brown": theme.hex("accent"),
        "blue": theme.hex("tool"),
        "magenta": "#c08fd0",
        "cyan": theme.hex("tool"),
        "white": theme.hex("dim2"),
        "brightblack": theme.hex("borderStrong"),
        "brightred": theme.hex("del"),
        "brightgreen": theme.hex("add"),
        "brightbrown": theme.hex("accent"),
        "brightblue": theme.hex("tool"),
        "brightmagenta": "#c08fd0",
        "brightcyan": theme.hex("tool"),
        "brightwhite": theme.hex("text"),
    }
    palette = {name: QColor(value) for name, value in hexes.items()}
    palette["default"] = QColor(theme.hex("text"))
    palette["defaultbg"] = QColor(theme.hex("term"))
    # pyte spells these too, for `SGR 39` / `SGR 49`.
    palette["white_default"] = QColor(theme.hex("text"))
    return palette


class TerminalView(QWidget):
    """One terminal's screen, keyboard and mouse."""

    #: Bytes for the shell. The view never writes to a pty itself.
    dataReady = Signal(bytes)
    #: The grid changed size, in cells.
    resized = Signal(int, int)
    titleChanged = Signal(str)
    #: The program rang the bell. Forwarded as well as beeped: a beep is gone the instant
    #: it happens, and the pane it came from is the thing worth knowing. Whoever holds the
    #: sidebar turns this into a state that survives until it is looked at.
    bellRang = Signal()
    #: Right-click, in global coordinates, when the menu is ours to show. Emitted rather
    #: than acted on: half the rows a terminal menu wants -- split, close -- belong to the
    #: pane tree, and a view that built them would have to know what contains it.
    contextMenuRequested = Signal(object)

    def __init__(self, theme: Theme | None = None, scrollback: int = DEFAULT_SCROLLBACK,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme or Theme()
        self._palette = term_palette(self._theme)

        self.emulator = Emulator(80, 24, scrollback, self)
        self.emulator.updated.connect(self._on_updated)
        self.emulator.titleChanged.connect(self.titleChanged)
        self.emulator.bell.connect(self._on_bell)
        # Replies to the shell's own questions leave by the same door as keystrokes, so
        # there is exactly one path from this widget to the pty.
        self.emulator.respond.connect(lambda text: self.dataReady.emit(text.encode()))

        #: Lines scrolled up from the live view. 0 means pinned to the bottom, which is
        #: where a keystroke puts us again.
        self._scroll = 0
        #: How long the history was at the last repaint. `_on_updated` needs the
        #: *difference* -- see there -- and the emulator only reports that something
        #: changed, not by how much.
        self._history = len(self.emulator.screen.history)
        #: An overlay rather than a laid-out sibling, so showing and hiding it changes no
        #: geometry. It never takes focus -- a scrollbar that could would swallow the
        #: keystrokes the pane exists to deliver -- and it keeps the arrow pointer the
        #: view's own IBeam would otherwise lend it.
        self._scrollbar = QScrollBar(Qt.Orientation.Vertical, self)
        self._scrollbar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scrollbar.setCursor(Qt.CursorShape.ArrowCursor)
        self._scrollbar.valueChanged.connect(self._on_scrollbar)
        self._sync_scrollbar()
        #: Selection in absolute row coordinates, so it survives the screen scrolling
        #: underneath it. None when there is no selection.
        self._sel_anchor: tuple[int, int] | None = None
        self._sel_head: tuple[int, int] | None = None
        self._selecting = False

        self._cursor_on = True
        self._blink = QTimer(self)
        self._blink.setInterval(BLINK_MS)
        self._blink.timeout.connect(self._toggle_cursor)
        self._blink.start()

        self._font = QFont()
        self._font_cache: dict[tuple[bool, bool, bool, bool], QFont] = {}
        self._cell_w = 8.0
        self._cell_h = 16
        self._baseline = 12
        self._apply_font()

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        # We paint every pixel of our rect, so Qt need not clear it first.
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)

    # --- font and geometry -----------------------------------------------------------

    @staticmethod
    def _mono(family: str, pixels: int) -> QFont:
        font = QFont(family)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        # Pixels, not points. theme.Theme.font sizes every other role with setPixelSize,
        # and this used to call setPointSizeF on the same figure -- which at 96 DPI made
        # the terminal roughly a third larger than that number means anywhere else.
        font.setPixelSize(pixels)
        return font

    def _apply_font(self) -> None:
        _family, size, _weight, _spacing = TYPE["terminal"]
        pixels = max(1, int(round(size)))
        font = self._mono(FONT_MONO, pixels)
        # Qt substitutes silently when the family is missing, and a proportional
        # substitute makes every column a different width. Ask for the fallback by name.
        metrics = QFontMetricsF(font)
        if abs(metrics.horizontalAdvance("i") - metrics.horizontalAdvance("W")) > 0.5:
            font = self._mono(FONT_MONO_FALLBACK, pixels)
            metrics = QFontMetricsF(font)

        self._font = font
        self._font_cache = {}
        # Measured once, floored once. See the module docstring.
        self._cell_w = metrics.horizontalAdvance("W")
        self._cell_h = max(1, int(round(metrics.height())))
        self._baseline = int(round(metrics.ascent()))

    def cell_size(self) -> tuple[float, int]:
        return self._cell_w, self._cell_h

    def grid_size(self) -> tuple[int, int]:
        """The grid the current widget size affords, in cells."""
        usable_w = max(1, self.width() - PAD_LEFT - PAD_RIGHT)
        usable_h = max(1, self.height() - PAD_TOP)
        cols = max(2, int(usable_w // self._cell_w))
        rows = max(1, usable_h // self._cell_h)
        return cols, rows

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(int(self._cell_w * 80) + PAD_LEFT + PAD_RIGHT,
                     self._cell_h * 24 + PAD_TOP)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        # Ahead of the zero-extent guard below, because the bar is an overlay and no
        # layout will ever place it for us -- declining the event still has to leave it
        # somewhere sane.
        bar_w = GEOM["scrollbar_w"]
        self._scrollbar.setGeometry(self.width() - bar_w, 0, bar_w, self.height())
        if event.size().width() <= 0 or event.size().height() <= 0:
            # A hidden widget is laid out at zero extent, and Qt does not deliver the
            # resize while it is hidden -- it arrives on show(), immediately ahead of the
            # real size. grid_size() clamps a zero extent to its 2x1 floor, so acting on
            # that event would resize the emulator *and the pseudo-console* to two columns
            # and back on every unzoom. pyte discards what no longer fits, so a full-screen
            # program loses its frame permanently and the header's cols x rows readout
            # flickers through 2x1. Declining leaves the grid alone; the real event that
            # follows finds it unchanged and does nothing, which is the correct answer for
            # a pane that was hidden and shown at the same size.
            return
        cols, rows = self.grid_size()
        if (cols, rows) != (self.emulator.screen.columns, self.emulator.screen.lines):
            self.emulator.resize(cols, rows)
            self.resized.emit(cols, rows)

    # --- theme -----------------------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        """Recolour a running terminal. Every cell re-resolves on the next paint."""
        self._theme = theme
        self._palette = term_palette(theme)
        self._apply_font()
        cols, rows = self.grid_size()
        if (cols, rows) != (self.emulator.screen.columns, self.emulator.screen.lines):
            self.emulator.resize(cols, rows)
            self.resized.emit(cols, rows)
        self.update()

    # --- painting --------------------------------------------------------------------

    def _colour(self, name: str, *, background: bool) -> QColor:
        if name == "default":
            return self._palette["defaultbg" if background else "default"]
        found = self._palette.get(name)
        if found is not None:
            return found
        # pyte hands back six hex digits for 256-colour and truecolor.
        if len(name) == 6:
            try:
                return QColor(f"#{name}")
            except ValueError:
                pass
        return self._palette["defaultbg" if background else "default"]

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setFont(self._font)
        background = self._palette["defaultbg"]
        painter.fillRect(event.rect(), background)

        screen = self.emulator.screen
        cols, rows = screen.columns, screen.lines
        top = self.emulator.total_lines - rows - self._scroll
        selection = self._selection_span()

        for view_row in range(rows):
            absolute = top + view_row
            if absolute < 0:
                continue
            row = self.emulator.line(absolute)
            y = PAD_TOP + view_row * self._cell_h
            if y > event.rect().bottom() or y + self._cell_h < event.rect().top():
                continue
            self._paint_row(painter, row, absolute, y, cols, selection, background)

        self._paint_cursor(painter, screen, rows)
        painter.end()

    def _styled_font(self, bold: bool, italic: bool, underline: bool,
                     strike: bool) -> QFont:
        """A font variant, cached. Building one per run showed up in profiles."""
        key = (bold, italic, underline, strike)
        font = self._font_cache.get(key)
        if font is None:
            font = QFont(self._font)
            font.setBold(bold)
            font.setItalic(italic)
            font.setUnderline(underline)
            font.setStrikeOut(strike)
            self._font_cache[key] = font
        return font

    def _paint_row(self, painter, row, absolute: int, y: int, cols: int,
                   selection, background) -> None:
        """One line, drawn as runs of identical styling rather than cell by cell.

        A cell-at-a-time loop is one drawText per column -- eighty to two hundred per row,
        which shows up immediately under streaming output. Ordinary shell output changes
        style a handful of times per line, so batching takes that to a handful of calls.
        """
        selected_bg = QColor(self._theme.hex("sel"))

        # Resolved once per cell, then walked twice: backgrounds fill by colour regardless
        # of glyph, text batches by style and has to respect character width.
        cells = []
        for col in range(cols):
            char = row[col]
            fg = self._colour(char.fg, background=False)
            bg = self._colour(char.bg, background=True)
            if char.reverse:
                fg, bg = bg, fg
            if self._is_selected(selection, absolute, col):
                bg = selected_bg
            cells.append((char.data, fg, bg,
                          (char.bold, char.italics, char.underscore, char.strikethrough)))

        # --- backgrounds, batched by colour ---
        start = 0
        for col in range(1, cols + 1):
            if col < cols and cells[col][2].rgb() == cells[start][2].rgb():
                continue
            colour = cells[start][2]
            if colour.rgb() != background.rgb():
                x = PAD_LEFT + start * self._cell_w
                width = (col - start) * self._cell_w
                painter.fillRect(QRect(int(x), y, int(width) + 1, self._cell_h), colour)
            start = col

        # --- text ---
        segments: list[tuple[int, QColor, tuple, str]] = []
        run_start = 0
        run_text: list[str] = []
        run_key = None
        run_fg: QColor | None = None
        run_flags: tuple = ()

        def close_run() -> None:
            nonlocal run_text, run_key
            if run_text:
                segments.append((run_start, run_fg, run_flags, "".join(run_text)))
            run_text, run_key = [], None

        col = 0
        while col < cols:
            data, fg, _bg, flags = cells[col]
            if data == "":
                # The continuation cell of a double-width character. pyte parks the glyph
                # at the first column and leaves this one empty -- emitting a space for it
                # is what pushed every following character one cell to the right.
                col += 1
                continue

            wide = col + 1 < cols and cells[col + 1][0] == ""
            if wide:
                # Drawn alone, at its exact column. The fallback face Qt picks for CJK is
                # not the terminal font, and its advance is not exactly two cells --
                # measured 17px against 18 here -- so inside a batched run the error would
                # accumulate across the rest of the line rather than stopping at the glyph.
                close_run()
                painter.setFont(self._styled_font(*flags))
                painter.setPen(fg)
                painter.drawText(int(PAD_LEFT + col * self._cell_w), y + self._baseline,
                                 data)
                col += 2
                continue

            key = (fg.rgb(), flags)
            if key != run_key:
                close_run()
                run_start, run_key, run_fg, run_flags = col, key, fg, flags
            run_text.append(data or " ")
            col += 1
        close_run()

        for seg_start, seg_fg, seg_flags, text in segments:
            if not text.strip():
                continue
            painter.setFont(self._styled_font(*seg_flags))
            painter.setPen(seg_fg)
            painter.drawText(int(PAD_LEFT + seg_start * self._cell_w),
                             y + self._baseline, text)

    def _paint_cursor(self, painter, screen, rows: int) -> None:
        """The cursor, if there is anywhere to draw one.

        Not drawn while scrolled back: the cursor belongs to the live screen, and painting
        it at the same grid position on a scrollback line points at the wrong character.
        """
        if self._scroll != 0:
            return
        if MODE_CURSOR_VISIBLE not in screen.mode:      # DECTCEM, reset by ?25l
            return
        if screen.cursor.hidden or screen.cursor.y >= rows:
            return

        x = PAD_LEFT + screen.cursor.x * self._cell_w
        y = PAD_TOP + screen.cursor.y * self._cell_h
        rect = QRect(int(x), y, max(1, int(self._cell_w)), self._cell_h)
        accent = QColor(self._theme.hex("accent"))

        # Unfocused draws a hollow box, which is the convention for "this terminal is
        # running but is not where your typing goes".
        if not self.hasFocus():
            painter.setPen(accent)
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
            return
        if not self._cursor_on:
            return

        painter.fillRect(rect, accent)
        char = screen.buffer[screen.cursor.y][screen.cursor.x]
        if char.data and char.data != " ":
            painter.setFont(self._font)
            painter.setPen(QColor(self._theme.hex("term")))
            painter.drawText(int(x), y + self._baseline, char.data)

    def _toggle_cursor(self) -> None:
        self._cursor_on = not self._cursor_on
        screen = self.emulator.screen
        x = PAD_LEFT + screen.cursor.x * self._cell_w
        y = PAD_TOP + screen.cursor.y * self._cell_h
        self.update(QRect(int(x) - 1, y, int(self._cell_w) + 3, self._cell_h))

    def _on_updated(self) -> None:
        """Output arrived. Hold whatever the reader is looking at.

        This used to snap ``_scroll`` back to 0 on any output while scrolled up, on the
        grounds that it is what every terminal does. It is not: xterm, iTerm and Windows
        Terminal all leave a scrolled-back view where it is and follow the tail only when
        you are already at the bottom -- scrolling on output is an option, and one that
        defaults off, because the thing you scrolled up to read is usually the thing a
        running command is burying. What they *do* snap for is a keystroke, and
        ``keyPressEvent`` already does.

        Holding still is not the same as leaving ``_scroll`` alone. It counts lines up
        from the bottom, and the bottom moves: every line pushed into history puts another
        line between the view and it, so an untouched offset walks the text up and off the
        top at exactly the speed the command is producing it. Adding what history gained
        keeps the same rows under the eye.

        Once the scrollback is full that gain is zero while lines still arrive -- the
        deque evicts from the front as fast as it appends -- so the view does drift then.
        Nothing can be done about that from here: the rows it was showing are gone.
        """
        history = len(self.emulator.screen.history)
        grew = history - self._history
        self._history = history
        # Not in the alternate screen, where there is nothing to hold: `index` declines to
        # append while a full-screen program is up, `_sync_scrollbar` hides the bar, and a
        # `vim` started from a scrolled-back view would otherwise open off screen.
        held = 0 if self.emulator.screen.in_alt_screen else self._scroll
        if held:
            held += grew
        self.emulator.screen.dirty.clear()
        # Through the setter even when the offset does not move: history grows with every
        # line of output, so the bar's range is stale after any drain that added one.
        self._set_scroll(held)
        self.update()

    def _on_bell(self) -> None:
        QApplication.beep()
        self.bellRang.emit()

    # --- selection -------------------------------------------------------------------

    def _cell_at(self, pos) -> tuple[int, int]:
        col = int(max(0, (pos.x() - PAD_LEFT)) // self._cell_w)
        row = int(max(0, (pos.y() - PAD_TOP)) // self._cell_h)
        rows = self.emulator.screen.lines
        absolute = self.emulator.total_lines - rows - self._scroll + row
        return absolute, min(col, self.emulator.screen.columns - 1)

    def _selection_span(self):
        if self._sel_anchor is None or self._sel_head is None:
            return None
        first, last = sorted([self._sel_anchor, self._sel_head])
        return first, last

    @staticmethod
    def _is_selected(span, row: int, col: int) -> bool:
        if span is None:
            return False
        (r0, c0), (r1, c1) = span
        if row < r0 or row > r1:
            return False
        if r0 == r1:
            return c0 <= col < c1
        if row == r0:
            return col >= c0
        if row == r1:
            return col < c1
        return True

    def selected_text(self) -> str:
        span = self._selection_span()
        if span is None:
            return ""
        (r0, c0), (r1, c1) = span
        cols = self.emulator.screen.columns
        lines = []
        for row in range(r0, r1 + 1):
            data = self.emulator.line(row)
            start = c0 if row == r0 else 0
            end = c1 if row == r1 else cols
            lines.append("".join(data[x].data for x in range(start, min(end, cols))).rstrip())
        return "\n".join(lines)

    def clear_selection(self) -> None:
        if self._sel_anchor is not None:
            self._sel_anchor = self._sel_head = None
            self.update()

    @property
    def has_selection(self) -> bool:
        """Whether anything is selected, without building the string to find out.

        A menu greying its Copy row asks this once per open; ``selected_text`` would walk
        every selected cell to answer the same question.
        """
        return self._sel_anchor is not None

    @property
    def has_scrollback(self) -> bool:
        """Whether there is history worth clearing.

        The same condition ``_sync_scrollbar`` uses to stand the bar down, and for the
        same reason: on the alternate screen the history that exists is the one from
        before ``vim`` started, and it has nothing to do with what is on screen.
        """
        return (self.emulator.total_lines > self.emulator.screen.lines
                and not self.emulator.screen.in_alt_screen)

    def select_all(self) -> None:
        """Select the scrollback and the live screen as one range."""
        last = max(0, self.emulator.total_lines - 1)
        self._sel_anchor = (0, 0)
        # The head column is exclusive -- see _is_selected -- so the width, not width - 1.
        self._sel_head = (last, self.emulator.screen.columns)
        self.update()

    def clear_scrollback(self) -> None:
        """Throw away the history, keeping what is on screen.

        The selection goes with it because selection is in absolute row coordinates and
        every row above the live screen just stopped existing -- keeping it would leave a
        highlight over whichever lines happened to land at those numbers. ``_set_scroll``
        is the single writer, so the bar re-syncs on the way out.
        """
        self.emulator.clear_history()
        self._history = 0
        self.clear_selection()
        self._set_scroll(0)
        self.update()

    # --- mouse -----------------------------------------------------------------------

    def _mouse_cell(self, pos) -> tuple[int, int]:
        """1-based (col, row) within the *visible* grid, for mouse reporting."""
        col = int(max(0, pos.x() - PAD_LEFT) // self._cell_w) + 1
        row = int(max(0, pos.y() - PAD_TOP) // self._cell_h) + 1
        return min(col, self.emulator.screen.columns), min(row, self.emulator.screen.lines)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if self.emulator.mouse_tracking and not (
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            # Shift is the universal "let me select even though the program wants the
            # mouse" override, and programs rely on us honouring it.
            button = keys.MOUSE_BUTTONS.get(event.button())
            if button is not None:
                col, row = self._mouse_cell(event.position())
                data = keys.encode_mouse(button, col, row, pressed=True,
                                         modifiers=event.modifiers(),
                                         sgr=self.emulator.mouse_sgr)
                if data:
                    self.dataReady.emit(data)
                    return
        if event.button() == Qt.MouseButton.LeftButton:
            self._selecting = True
            self._sel_anchor = self._sel_head = self._cell_at(event.position())
            self.update()
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.paste()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._selecting:
            self._sel_head = self._cell_at(event.position())
            self.update()
            return
        if self.emulator.reports_motion and self.emulator.mouse_tracking:
            col, row = self._mouse_cell(event.position())
            data = keys.encode_mouse(32, col, row, pressed=True, motion=False,
                                     modifiers=event.modifiers(),
                                     sgr=self.emulator.mouse_sgr)
            if data:
                self.dataReady.emit(data)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._selecting:
            self._selecting = False
            return
        if self.emulator.mouse_tracking:
            button = keys.MOUSE_BUTTONS.get(event.button())
            if button is not None:
                col, row = self._mouse_cell(event.position())
                data = keys.encode_mouse(button, col, row, pressed=False,
                                         modifiers=event.modifiers(),
                                         sgr=self.emulator.mouse_sgr)
                if data:
                    self.dataReady.emit(data)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Select the word under the cursor."""
        row, col = self._cell_at(event.position())
        line = self.emulator.line(row)
        cols = self.emulator.screen.columns

        def is_word(index: int) -> bool:
            char = line[index].data
            return bool(char) and not char.isspace()

        if not is_word(min(col, cols - 1)):
            return
        start = col
        while start > 0 and is_word(start - 1):
            start -= 1
        end = col
        while end < cols - 1 and is_word(end + 1):
            end += 1
        self._sel_anchor, self._sel_head = (row, start), (row, end + 1)
        self.update()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Right-click, when the right button is ours to claim.

        It is not ours inside ``vim`` or ``less``: a program that asked for mouse tracking
        gets every button, with ``Shift`` as the override the user reaches for when they
        want the app instead. That is xterm's convention and the one ``mousePressEvent``
        already honours for selection, so the two agree about who owns the mouse.

        Windows delivers this on the *release*, by which point the press and release
        reports have already gone to the program -- so declining here is the whole of what
        pass-through needs.
        """
        if self.emulator.mouse_tracking and not (
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            event.ignore()
            return
        event.accept()
        self.contextMenuRequested.emit(event.globalPos())

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt override
        steps = event.angleDelta().y() // 120
        if not steps:
            return
        if self.emulator.mouse_tracking and self.emulator.screen.in_alt_screen:
            # In the alternate screen there is no scrollback to reach, and the program
            # (less, vim) wants the wheel itself.
            button = keys.MOUSE_WHEEL_UP if steps > 0 else keys.MOUSE_WHEEL_DOWN
            col, row = self._mouse_cell(event.position())
            for _ in range(abs(steps)):
                data = keys.encode_mouse(button, col, row, pressed=True,
                                         sgr=self.emulator.mouse_sgr)
                if data:
                    self.dataReady.emit(data)
            return
        self.scroll_by(steps * 3)

    def scroll_by(self, lines: int) -> None:
        self._set_scroll(self._scroll + lines)

    @property
    def scroll_offset(self) -> int:
        return self._scroll

    def _set_scroll(self, value: int) -> None:
        """The only writer for ``_scroll``, so the bar cannot fall out of step with it.

        Three places used to assign it directly -- the wheel, output arriving, and a
        keystroke going to the shell -- and a scrollbar has to hear about all three.
        Clamping moved in here with them, which is why ``scroll_by`` no longer does its
        own.
        """
        limit = max(0, self.emulator.total_lines - self.emulator.screen.lines)
        value = min(limit, max(0, value))
        changed = value != self._scroll
        self._scroll = value
        self._sync_scrollbar()
        if changed:
            self.update()

    def _sync_scrollbar(self) -> None:
        """Point the bar at the history the emulator currently holds.

        Two details are the whole correctness of it. **The value is inverted**:
        ``_scroll`` counts lines up from the bottom and a ``QScrollBar`` counts down from
        the top, so a bar handed ``_scroll`` runs backwards. And the value is set with
        signals blocked, because ``valueChanged`` comes straight back in through
        ``_on_scrollbar``.

        Hidden in the alternate screen, which is not cosmetic. ``DeckScreen.index``
        declines to append to history while a full-screen program is up, but the history
        from *before* it started is still there and ``total_lines`` still counts it -- so
        a visible bar would offer to scroll into the scrollback underneath a live ``vim``.
        ``wheelEvent`` already refuses that, but only when the program also asked for
        mouse tracking; this refuses on the alternate screen alone.
        """
        rows = self.emulator.screen.lines
        limit = max(0, self.emulator.total_lines - rows)
        bar = self._scrollbar
        bar.setVisible(limit > 0 and not self.emulator.screen.in_alt_screen)
        bar.setRange(0, limit)
        bar.setPageStep(rows)
        # The wheel's own step, so dragging and rolling move by the same amount.
        bar.setSingleStep(3)
        blocked = bar.blockSignals(True)
        bar.setValue(limit - self._scroll)
        bar.blockSignals(blocked)

    def _on_scrollbar(self, value: int) -> None:
        self._set_scroll(self._scrollbar.maximum() - value)

    # --- keyboard --------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        key, mods = Qt.Key(event.key()), event.modifiers()

        # Terminal clipboard conventions, and they must never reach the shell. On Windows
        # Ctrl+C has to stay SIGINT so copy and paste are the shifted forms; on macOS the
        # command modifier is Cmd, which is not a control key at all, so the plain chord is
        # both correct and native. keys.COMMAND_MOD is the same enum member either way --
        # Qt reports Cmd as ControlModifier -- so only the Shift half differs.
        clipboard = keys.COMMAND_MOD | Qt.KeyboardModifier.ShiftModifier
        if platform_.IS_MACOS:
            clipboard = keys.COMMAND_MOD
        if mods & clipboard == clipboard:
            if key == Qt.Key.Key_C:
                self.copy()
                return
            if key in (Qt.Key.Key_V, Qt.Key.Key_Insert):
                self.paste()
                return

        if mods & Qt.KeyboardModifier.ShiftModifier:
            if key == Qt.Key.Key_PageUp:
                self.scroll_by(self.emulator.screen.lines - 1)
                return
            if key == Qt.Key.Key_PageDown:
                self.scroll_by(-(self.emulator.screen.lines - 1))
                return
            # The way back to the tail, and it needs its own key now that output no longer
            # provides one. The alternatives are dragging the bar or typing at the shell,
            # and typing to look at something is a character you did not mean to send.
            if key == Qt.Key.Key_End:
                self._set_scroll(0)
                return
            if key == Qt.Key.Key_Home:
                self._set_scroll(self.emulator.total_lines)
                return

        data = keys.encode_key(key, mods, event.text(),
                               app_cursor=self.emulator.app_cursor_keys)
        if data is None:
            super().keyPressEvent(event)
            return
        self.clear_selection()
        self._set_scroll(0)
        self.dataReady.emit(data)
        event.accept()

    # --- clipboard -------------------------------------------------------------------

    def copy(self) -> None:
        text = self.selected_text()
        if text:
            QApplication.clipboard().setText(text)

    def paste(self) -> None:
        text = QApplication.clipboard().text()
        if not text:
            return
        self.dataReady.emit(
            keys.encode_paste(text, bracketed=self.emulator.bracketed_paste)
        )

    # --- focus -----------------------------------------------------------------------

    def focusNextPrevChild(self, forward: bool) -> bool:  # noqa: N802 - Qt override
        """Refuse to hand Tab to the focus chain. A terminal owns Tab.

        ``QWidget.event()`` intercepts Tab and Backtab *before* keyPressEvent and spends
        them on focus navigation, and it does that first, so the encoder never sees them.
        Returning False declines, and the key falls through to keyPressEvent.

        This was invisible in a standalone pane, because with nothing else focusable the
        navigation failed and Tab fell through anyway. In the real window there is a
        sidebar and a row of buttons, so focus jumped to a QPushButton and the shell got
        nothing: no tab completion, and Shift+Tab never reached the program in the pane.
        """
        return False

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().focusInEvent(event)
        self._cursor_on = True
        self._blink.start()
        self.update()

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().focusOutEvent(event)
        self._blink.stop()
        self.update()

    # --- output ----------------------------------------------------------------------

    def feed(self, text: str) -> None:
        self.emulator.feed(text)
