"""Custom-painted list rows (spec 14.5).

QSS cannot style a multi-line list item: it has one foreground colour, one font and no
way to place a rail, a dot, a chip and three text runs at different weights inside one
row. The DS deck row needs all of that, so it is painted.

Everything here reads its colours and fonts from ``Theme``. A hex literal in this file
would be a second source of truth and would not follow a theme switch.

The small text -- path, branch, diff, relative time, child rows -- is painted in ``dim``
rather than the ``label``/``dimmer`` the DS nominates. Measured against the panel those
two land at 3.4:1 and 4.8:1 in the dark theme, and both collapse to 3.7:1 in the light
theme where they are the same hex; ``dim`` clears the 4.5:1 floor in both (5.4 and 4.7).
The one line still under it is the "clean" git state, which keeps its own token.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from .deck_model import ROW_ROLE, DeckRow, PlaceholderRow, SessionRow
from .theme import GEOM, RADIUS, Theme

RAIL_W = 3
RAIL_GAP = 11
ROW_RADIUS = 5
LINE_GAP = 7
DOT_D = 6
CHIP_PAD_X = 6
CHIP_H = 18


class DeckDelegate(QStyledItemDelegate):
    """One sidebar row: rail, name, live dot and count, path, branch chip, diff, time."""

    def __init__(self, theme: Theme, compact: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._compact = compact
        #: Which row's expander the pointer is inside, or -1. The view cannot tell the
        #: delegate this -- ``State_MouseOver`` is per row, and the expander is one 18px
        #: corner of a row four lines tall -- so the sidebar tracks it and sets it here.
        self._hot_row = -1

    def set_hot_row(self, row: int) -> bool:
        """Mark the row whose expander is under the pointer. True if that changed."""
        if row == self._hot_row:
            return False
        self._hot_row = row
        return True

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme

    def set_compact(self, compact: bool) -> None:
        self._compact = compact

    # --- metrics ---------------------------------------------------------------------

    @property
    def _pad(self) -> int:
        return GEOM["row_pad_compact"] if self._compact else GEOM["row_pad"]

    #: Where the expand chevron lives on a deck row, measured from the right edge.
    #: Square, and the same 18px as a branch chip is tall -- it is drawn as a chip.
    CHEVRON_W = 18

    #: A child row is one line of text plus breathing room, not a deck card. It is a
    #: floor rather than the height: the row is sized off the font so that raising the
    #: metadata role cannot quietly clip the descenders.
    CHILD_H = 26
    #: How far a child is indented past the deck's rail.
    CHILD_INDENT = 18

    def chevron_rect(self, option: QStyleOptionViewItem) -> QRect:
        """The hit target that toggles a deck open, in viewport coordinates.

        Exposed because the sidebar has to tell a click on the chevron from a click on the
        row: one expands, the other switches deck, and they are the same row.
        """
        rect = option.rect
        # Centred on the name line rather than hung from the padding: it shares that
        # line with the live dot and the session count, and now that it has a border
        # a couple of pixels out of true is visible where a bare glyph hid it.
        name_h = QFontMetrics(self._theme.font("deckName")).height()
        return QRect(rect.right() - self._pad - self.CHEVRON_W,
                     rect.top() + self._pad + (name_h - self.CHEVRON_W) // 2,
                     self.CHEVRON_W, self.CHEVRON_W)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        row = index.data(ROW_ROLE)
        if isinstance(row, (SessionRow, PlaceholderRow)):
            line_h = max(QFontMetrics(self._theme.font("deckMeta")).height(),
                         QFontMetrics(self._theme.font("sessionName")).height())
            return QSize(GEOM["sidebar_w"], max(self.CHILD_H, line_h + LINE_GAP))
        name_h = QFontMetrics(self._theme.font("deckName")).height()
        path_h = QFontMetrics(self._theme.font("deckPath")).height()
        height = self._pad * 2 + name_h + LINE_GAP + path_h
        if row is not None and self._has_meta(row):
            height += LINE_GAP + max(CHIP_H, QFontMetrics(self._theme.font("deckMeta")).height())
        return QSize(GEOM["sidebar_w"], height)

    @staticmethod
    def _has_meta(row: DeckRow) -> bool:
        # With git unknown the only meta left is the relative time; the branch chip and
        # diff stat are omitted rather than drawn empty.
        return bool(row.git_known or row.when)

    # --- paint -----------------------------------------------------------------------

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex) -> None:
        row = index.data(ROW_ROLE)
        if isinstance(row, (SessionRow, PlaceholderRow)):
            self._paint_child(painter, option, row)
            return
        if not isinstance(row, DeckRow):
            super().paint(painter, option, index)
            return

        t = self._theme
        # The state flags live on QStyle, not on the option class.
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = option.rect.adjusted(0, 0, 0, -3)
        if selected:
            painter.setBrush(t.c("sel"))
        elif hovered:
            painter.setBrush(t.c("hover"))
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(Qt.PenStyle.NoPen)
        if selected or hovered:
            painter.drawRoundedRect(rect, ROW_RADIUS, ROW_RADIUS)

        # The rail is two things at once: which row is selected, and which deck this is.
        # A deck that has been given a colour keeps its rail whether or not it is the
        # open one -- that is the whole point of giving it one. The text below already
        # reserves RAIL_W + RAIL_GAP unconditionally, so a rail appearing reflows nothing.
        if selected or row.accent:
            rail = QRect(rect.left() + self._pad, rect.top() + self._pad,
                         RAIL_W, rect.height() - self._pad * 2)
            painter.setBrush(t.tint(row.accent))
            painter.drawRoundedRect(rail, 2, 2)

        left = rect.left() + self._pad + RAIL_W + RAIL_GAP
        right = rect.right() - self._pad
        y = rect.top() + self._pad

        # The chevron sits where the name line would otherwise run to, so the text has to
        # be told about it rather than drawn under it.
        model = index.model()
        expandable = hasattr(model, "is_expanded")
        if expandable:
            self._paint_chevron(painter, option, model.is_expanded(row.deck_id),
                                self._hot_row == index.row())
            right -= self.CHEVRON_W + 7

        y = self._paint_name_line(painter, row, left, right, y, selected)
        y += LINE_GAP
        y = self._paint_path(painter, row, left, right, y)
        if self._has_meta(row):
            y += LINE_GAP
            self._paint_meta(painter, row, left, right, y)

        painter.restore()

    def _paint_child(self, painter: QPainter, option: QStyleOptionViewItem, row) -> None:
        """One line under a deck: a state dot, a name, and what it is running."""
        t = self._theme
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if hovered and isinstance(row, SessionRow):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(t.c("hover"))
            painter.drawRoundedRect(rect.adjusted(self._pad, 1, -self._pad, -2),
                                    ROW_RADIUS, ROW_RADIUS)

        left = rect.left() + self._pad + RAIL_W + RAIL_GAP + self.CHILD_INDENT
        middle = rect.center().y()

        if isinstance(row, PlaceholderRow):
            painter.setFont(t.font("deckMeta"))
            painter.setPen(t.c("dim"))
            painter.drawText(QRect(left, rect.top(), rect.width() - left, rect.height()),
                             int(Qt.AlignmentFlag.AlignVCenter), row.text)
            painter.restore()
            return

        dot = QRect(left - 12, middle - 3, 6, 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(t.dot_color(row.state))
        painter.drawEllipse(dot)

        font = t.font("deckMeta")
        painter.setFont(font)
        metrics = QFontMetrics(font)
        right = rect.right() - self._pad
        tail = f"{row.uptime}" if row.uptime else ""
        tail_w = metrics.horizontalAdvance(tail) if tail else 0
        if tail:
            painter.setPen(t.c("dim"))
            painter.drawText(QRect(right - tail_w, rect.top(), tail_w, rect.height()),
                             int(Qt.AlignmentFlag.AlignVCenter), tail)

        name_font = t.font("sessionName")
        name_metrics = QFontMetrics(name_font)
        painter.setFont(name_font)
        label_w = max(10, (right - tail_w - 8) - left)
        painter.setPen(t.c("dim2") if row.state == "dead" else t.c("text"))
        painter.drawText(QRect(left, rect.top(), label_w, rect.height()),
                         int(Qt.AlignmentFlag.AlignVCenter),
                         name_metrics.elidedText(row.label, Qt.TextElideMode.ElideRight,
                                                 label_w))
        painter.restore()

    def _paint_chevron(self, painter: QPainter, option: QStyleOptionViewItem,
                       expanded: bool, hovered: bool) -> None:
        """The expander, wearing the sidebar's "+": chip border, chip radius, dim2 glyph.

        A bare glyph beside the live dot and the count read as a third piece of deck
        metadata rather than as the one control on the row. The box is what says it is
        pressable, and it is the box #IconButton already uses a few rows above -- the
        two are the panel's own controls, so they should look like each other.

        The hover is the pointer's, not the row's: lighting up whenever the row is
        hovered would promise expansion for a click on the deck name, which selects the
        deck instead. Sidebar watches the pointer and calls ``set_hot_row`` for it,
        because ``State_MouseOver`` only says "somewhere in this row".
        """
        t = self._theme
        box = self.chevron_rect(option)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(t.c("accent") if hovered else t.c("chip"))
        painter.drawRoundedRect(box, RADIUS["chip"], RADIUS["chip"])
        painter.setFont(t.font("deckMeta"))
        painter.setPen(t.c("accent") if hovered else t.c("dim2"))
        painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter),
                         "▾" if expanded else "▸")

    def _paint_name_line(self, painter: QPainter, row: DeckRow,
                         left: int, right: int, y: int, selected: bool) -> int:
        t = self._theme
        font = t.font("deckName")
        painter.setFont(font)
        metrics = QFontMetrics(font)
        height = metrics.height()

        count = str(row.session_count)
        count_font = t.font("deckMeta")
        count_w = QFontMetrics(count_font).horizontalAdvance(count)
        cluster_w = DOT_D + 5 + count_w

        painter.setPen(t.c("text") if row.session_count else t.c("dim"))
        name_rect = QRect(left, y, right - left - cluster_w - 8, height)
        painter.drawText(name_rect, int(Qt.AlignmentFlag.AlignVCenter),
                         metrics.elidedText(row.name, Qt.TextElideMode.ElideRight,
                                            name_rect.width()))

        # Whatever the panes under this deck rolled up to. The expression here used to
        # be `"live" if row.live_count else ("dead" if not row.session_count else "live")`,
        # whose two branches both yield "live" whenever there is a session -- so the busy
        # tally main_window computed for it never changed a pixel, and the dot was a
        # restatement of the count sitting next to it.
        # Unguarded by the session count on purpose: rollup() already answers "nothing
        # running" with dead, and a pane that rang the bell and then exited is exactly the
        # case where the count is zero and the dot is the only thing left saying so.
        dot_colour: QColor = t.dot_color(row.status)
        dot_y = y + (height - DOT_D) // 2
        painter.setBrush(dot_colour)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRect(right - cluster_w, dot_y, DOT_D, DOT_D))

        painter.setFont(count_font)
        painter.setPen(t.c("dim2") if row.session_count else t.c("dim"))
        painter.drawText(QRect(right - count_w, y, count_w, height),
                         int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                         count)
        _ = selected
        return y + height

    def _paint_path(self, painter: QPainter, row: DeckRow,
                    left: int, right: int, y: int) -> int:
        t = self._theme
        font = t.font("deckPath")
        painter.setFont(font)
        painter.setPen(t.c("dim"))
        metrics = QFontMetrics(font)
        rect = QRect(left, y, right - left, metrics.height())
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter),
                         metrics.elidedText(row.path_text, Qt.TextElideMode.ElideLeft,
                                            rect.width()))
        return y + metrics.height()

    def _paint_meta(self, painter: QPainter, row: DeckRow,
                    left: int, right: int, y: int) -> None:
        t = self._theme
        font = t.font("deckMeta")
        painter.setFont(font)
        metrics = QFontMetrics(font)
        height = max(CHIP_H, metrics.height())
        x = left

        if row.git_known and row.branch:
            text_w = metrics.horizontalAdvance(row.branch)
            chip = QRect(x, y + (height - CHIP_H) // 2, text_w + CHIP_PAD_X * 2, CHIP_H)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(t.c("chip"))
            painter.drawRoundedRect(chip, 3, 3)
            painter.setPen(t.c("dim"))
            painter.drawText(chip, int(Qt.AlignmentFlag.AlignCenter), row.branch)
            x = chip.right() + 7

            diff = row.diff_text
            if diff:
                painter.setPen(t.c("clean") if row.clean else t.c("dim"))
                diff_w = metrics.horizontalAdvance(diff)
                painter.drawText(QRect(x, y, diff_w, height),
                                 int(Qt.AlignmentFlag.AlignVCenter), diff)

        if row.when:
            when_w = metrics.horizontalAdvance(row.when)
            painter.setPen(t.c("dim"))
            painter.drawText(QRect(right - when_w, y, when_w, height),
                             int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                             row.when)
