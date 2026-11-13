"""The per-deck accent picker: a row of colours that lives inside a QMenu.

Hand-painted rather than assembled from QPushButtons, for the same reason the sidebar rows
are: a styled button brings its own frame, focus rect and padding into a 20px cell, and
four of them in a row inside a menu do not line up with the menu's own items. One widget,
one paintEvent, one hit test.

It is put into a menu through ``QWidgetAction``, which has one consequence worth knowing
before reading ``MainWindow._deck_menu``: clicking a widget action does *not* dismiss
the menu the way clicking an item does, so the caller closes it after acting on ``picked``.

The first cell is Default -- a hollow ring, not a colour -- and it emits "". Colour is
stored per deck as one hex and painted through ``Theme.tint``, so what a swatch shows
here is exactly what the sidebar rail will show in the mode that is up.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from ..theme import ACCENTS, Theme

#: The painted box for one colour, and the gap between two of them.
CELL = 20
GAP = 7
#: Menu items are indented past their (absent) checkmark column; matching that padding is
#: what makes the row read as belonging to the items under it rather than floating.
PAD_X = 12
PAD_Y = 7
RADIUS = 4


class AccentSwatchRow(QWidget):
    """Default plus ``theme.ACCENTS``, with a tick on whichever one is current."""

    #: The chosen colour as a hex, or "" for Default. The receiver decides what that
    #: means; this widget does not keep the choice, because the row it belongs to is
    #: rebuilt from the database either way.
    picked = Signal(str)

    def __init__(self, theme: Theme, current: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AccentSwatches")
        self._theme = theme
        self._current = current
        #: "" first, so Default is always in the same place whatever the palette holds.
        self._values: list[str] = ["", *ACCENTS]
        self._hovered = -1

        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self.sizeHint())

    # --- state ---------------------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    # --- geometry ------------------------------------------------------------------

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        cells = len(self._values)
        return QSize(PAD_X * 2 + cells * CELL + (cells - 1) * GAP, PAD_Y * 2 + CELL)

    def _cell_rect(self, index: int) -> QRect:
        return QRect(PAD_X + index * (CELL + GAP), PAD_Y, CELL, CELL)

    def _index_at(self, point: QPoint) -> int:
        for index in range(len(self._values)):
            if self._cell_rect(index).contains(point):
                return index
        return -1

    # --- input ---------------------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        index = self._index_at(event.position().toPoint())
        if index != self._hovered:
            self._hovered = index
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._hovered = -1
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        # Release and not press: a press that slid off the swatch before letting go is a
        # cancelled click everywhere else in the app, and a colour is not worth being the
        # one control that disagrees.
        if event.button() != Qt.MouseButton.LeftButton:
            return
        index = self._index_at(event.position().toPoint())
        if index >= 0:
            self.picked.emit(self._values[index])

    def event(self, event) -> bool:
        """Hex under the pointer, per swatch.

        A single setToolTip cannot do this -- the whole row is one widget, so the tip has
        to be placed from the cell the pointer is actually over.
        """
        if event.type() == QEvent.Type.ToolTip:
            index = self._index_at(event.pos())
            if index < 0:
                QToolTip.hideText()
            else:
                value = self._values[index]
                QToolTip.showText(event.globalPos(), value or "Default", self)
            return True
        return super().event(event)

    # --- paint ---------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        theme = self._theme

        for index, value in enumerate(self._values):
            rect = self._cell_rect(index)
            if value:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(theme.tint(value))
                painter.drawRoundedRect(rect, RADIUS, RADIUS)
            else:
                # Default is drawn as an outline, because a filled cell in the theme's own
                # accent would look like a fifth colour to choose rather than the way out
                # of choosing one.
                painter.setPen(QPen(theme.c("borderStrong"), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), RADIUS, RADIUS)

            if value == self._current:
                self._paint_tick(painter, rect, value)
            if index == self._hovered:
                painter.setPen(QPen(theme.c("text"), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect.adjusted(-2, -2, 1, 1), RADIUS + 2, RADIUS + 2)

    def _paint_tick(self, painter: QPainter, rect: QRect, value: str) -> None:
        """The check on the current swatch.

        In `onAccent` over a colour and in `text` over the hollow Default cell -- onAccent
        is the token for "drawn on top of the accent", and over the menu background it is
        the near-invisible one of the two.
        """
        pen = QPen(self._theme.c("onAccent" if value else "text"), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        left = rect.left() + rect.width() * 0.28
        middle = rect.left() + rect.width() * 0.44
        right = rect.left() + rect.width() * 0.74
        top = rect.top() + rect.height() * 0.34
        bottom = rect.top() + rect.height() * 0.64
        painter.drawPolyline([
            QPoint(int(left), int(rect.top() + rect.height() * 0.52)),
            QPoint(int(middle), int(bottom)),
            QPoint(int(right), int(top)),
        ])
