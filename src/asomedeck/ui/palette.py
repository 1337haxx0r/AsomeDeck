"""One box over the window that reaches everything in it.

The title bar has advertised ``search decks & sessions`` since the chrome was built, as
a bare ``QLabel`` with no handler behind it anywhere in the app. This is that promise, and
it is deliberately wider than the promise was: commands, decks, open sessions and the
files of the active deck all filter in the same list, because by the time the command
registry and the file tree both exist, three of the four cost nothing extra.

Two things are kept out of here on purpose.

**No knowledge of what a row does.** A :class:`Row` carries a callable the window built.
The palette can open a deck, split a pane and load a file without importing any of
them, and adding a fifth kind of row is a change in ``main_window`` alone.

**No second list of commands.** Command rows come from the registry, with the registry's
own ``enabled`` deciding whether they appear at all -- so a palette entry cannot outlive
the command behind it, and cannot claim a chord that config.py has rebound.

Ranking is a pure function of two strings and is tested as one. What is *not* pure --
which rows exist, and what they do -- is the window's, and stays there.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from .theme import Theme, styled

#: The kinds a row can be, in the order ties break. Commands first because the palette is
#: the only way to reach the ones with no chord; files last because a deck has one of
#: everything else and thousands of them.
KINDS: tuple[str, ...] = ("command", "session", "deck", "file")

#: How many rows are ever shown. Past this a list is not being read, it is being scrolled
#: past -- and the answer to too many matches is another keystroke, not more rows.
SHOWN = 50

PALETTE_ROLE = Qt.ItemDataRole.UserRole + 1

CARD_W = 660
CARD_TOP = 96
ROW_H = 30
ROW_PAD = 10
TAG_W = 58
GAP = 10
SCRIM_ALPHA = 190


@dataclass(frozen=True, slots=True)
class Row:
    """One thing the palette can offer, and what happens when it is chosen.

    ``search`` is extra text a query may match, beyond the label -- a file's
    deck-relative path, so ``ui/pal`` finds ``palette.py``. It is *extra*: a match in
    the label always outranks one found only here, or typing ``pa`` in this codebase would
    put every file under ``packaging/`` above ``palette.py``.
    """

    kind: str
    label: str
    detail: str
    run: Callable[[], None] = field(compare=False)
    search: str = ""


def _subsequence(query: str, text: str) -> bool:
    """Whether every character of ``query`` appears in ``text``, in order.

    What makes ``spd`` find "Split down". Tried last, so a real substring match never
    loses to a scattered one.
    """
    position = 0
    for char in query:
        position = text.find(char, position) + 1
        if position == 0:
            return False
    return True


def score(query: str, text: str) -> int | None:
    """How well ``text`` answers ``query``; lower is better, ``None`` is no match.

    The order encodes what a person means by typing: what they typed is how the thing
    starts, then how a word inside it starts, then anywhere inside it, then merely present
    in order. Mid-word ranks below word-start because "pane" should find "Close pane"
    before "Toggle session panel", and both contain the letters.
    """
    if not query:
        return 0
    query, text = query.lower(), text.lower()
    if text.startswith(query):
        return 0
    position = text.find(query)
    if position > 0:
        return 1 if not text[position - 1].isalnum() else 2
    return 3 if _subsequence(query, text) else None


def _match(query: str, row: Row) -> int | None:
    """How well a row answers a query, counting its label first and its path second.

    The penalty is what keeps ``pa`` finding ``palette.py`` before every file under
    ``packaging/``: both start with the letters, but only one of them is *named* that.
    """
    matched = score(query, row.label)
    if not row.search or row.search == row.label:
        return matched
    through_path = score(query, row.search)
    if through_path is None:
        return matched
    through_path += 1
    return through_path if matched is None else min(matched, through_path)


def rank(rows: Iterable[Row], query: str, limit: int = SHOWN) -> list[Row]:
    """The rows that match, best first, cut to ``limit``.

    Ties break by kind, then by how much label there is around the match, then by name --
    so the same query always produces the same list. A palette whose top row moves between
    two equally good matches is one you cannot learn to type blind, which is most of what a
    palette is for.

    Length is in there because scoring alone cannot separate "Close pane" from "Toggle
    session panel": ``pane`` starts a word in both. The shorter label is the one where
    what you typed is more of what the row is -- which is why it counts only while there
    is something typed. With an empty box there is no match to be more or less of, and
    ordering a hundred files by how short their names are is noise.
    """
    scored = []
    for row in rows:
        matched = _match(query, row)
        if matched is not None:
            scored.append((matched, KINDS.index(row.kind),
                           len(row.label) if query else 0, row.label.lower(), row))
    scored.sort(key=lambda item: item[:4])
    return [item[4] for item in scored[:limit]]


class PaletteDelegate(QStyledItemDelegate):
    """A row: kind tag, label, and a dim detail on the right.

    Painted rather than styled, for the reason ``delegates.py`` states -- QSS gives a list
    item one font and one colour, and this row wants three of each.
    """

    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        _ = option, index
        return QSize(CARD_W, ROW_H)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index: QModelIndex) -> None:
        row = index.data(PALETTE_ROLE)
        if not isinstance(row, Row):
            super().paint(painter, option, index)
            return
        t = self._theme
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(t.c("sel") if selected else t.c("hover"))
            painter.drawRoundedRect(rect.adjusted(2, 1, -2, -1), 4, 4)

        left = rect.left() + ROW_PAD
        right = rect.right() - ROW_PAD

        painter.setFont(t.font("eyebrow"))
        painter.setPen(t.c("label"))
        painter.drawText(QRect(left, rect.top(), TAG_W, rect.height()),
                         int(Qt.AlignmentFlag.AlignVCenter), row.kind.upper()[:7])
        left += TAG_W + GAP

        detail_w = 0
        if row.detail:
            metrics = QFontMetrics(t.font("paneCmd"))
            # Half the row at most: the label is what was typed at, so a long path gives
            # way to it rather than pushing it off the left.
            detail_w = min(metrics.horizontalAdvance(row.detail), rect.width() // 2)
            painter.setFont(t.font("paneCmd"))
            painter.setPen(t.c("dimmer"))
            # Elided from the left: the end of a path is the part that identifies it.
            painter.drawText(
                QRect(right - detail_w, rect.top(), detail_w, rect.height()),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                metrics.elidedText(row.detail, Qt.TextElideMode.ElideLeft, detail_w),
            )
            detail_w += GAP

        painter.setFont(t.font("menu"))
        painter.setPen(t.c("text") if selected else t.c("dim2"))
        label_rect = QRect(left, rect.top(), max(0, right - detail_w - left), rect.height())
        painter.drawText(label_rect, int(Qt.AlignmentFlag.AlignVCenter),
                         QFontMetrics(t.font("menu")).elidedText(
                             row.label, Qt.TextElideMode.ElideRight, label_rect.width()))
        painter.restore()


class Palette(QWidget):
    """The overlay: a dimmed backdrop over the window, and one card near the top.

    A child of the window rather than a popup window of its own. This window is frameless
    and does its own hit-testing, and a second native window over it is a second thing to
    keep positioned across a move, a maximise and a DPI change -- where a child widget is
    placed by the same resize that places everything else.
    """

    #: Emitted after the overlay hides, whether or not a row was chosen. The window uses
    #: it to put focus back where it was, which is inside a terminal.
    closed = Signal()

    def __init__(self, theme: Theme, parent: QWidget) -> None:
        super().__init__(parent)
        self._theme = theme
        self._rows: list[Row] = []
        self.setObjectName("PaletteScrim")
        self.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, CARD_TOP, 0, 0)

        self.card = QFrame(self)
        styled(self.card, "PaletteCard")
        self.card.setFixedWidth(CARD_W)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(1, 1, 1, 1)
        card_layout.setSpacing(0)

        self.input = QLineEdit(self.card)
        self.input.setObjectName("PaletteInput")
        self.input.setFont(theme.font("menu"))
        self.input.setPlaceholderText("Search commands, decks, sessions and files")
        self.input.textChanged.connect(self._refresh)
        self.input.installEventFilter(self)
        card_layout.addWidget(self.input)

        self.list = QListWidget(self.card)
        self.list.setObjectName("PaletteList")
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setMouseTracking(True)
        self.list.setUniformItemSizes(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.delegate = PaletteDelegate(theme, self.list)
        self.list.setItemDelegate(self.delegate)
        self.list.itemClicked.connect(lambda _item: self._activate())
        card_layout.addWidget(self.list)

        # Aligned per item, not per layout: setAlignment on the layout positions the
        # layout inside the widget and leaves the card free to stretch, which made the
        # card the height of the whole window with its field floating in the middle.
        layout.addWidget(self.card, 0,
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        parent.installEventFilter(self)

    # --- opening and closing ----------------------------------------------------------

    def open_with(self, rows: Sequence[Row]) -> None:
        """Show the overlay over a fresh set of rows.

        Rebuilt on every open rather than cached: which sessions are running and which
        commands apply are both true only at the moment the palette was asked for.
        """
        self._rows = list(rows)
        self.setGeometry(self.parentWidget().rect())
        self.input.clear()
        self._refresh()
        self.show()
        self.raise_()
        self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)

    @property
    def is_open(self) -> bool:
        """Whether the overlay is up.

        ``isHidden`` and not ``isVisible``: a child widget is not "visible" while any
        ancestor is hidden, which is true of every window a test builds and never true of
        the running app. Asking whether *this* widget was hidden is the question actually
        being asked, in both.
        """
        return not self.isHidden()

    def dismiss(self) -> None:
        if not self.is_open:
            return
        self.hide()
        self.closed.emit()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.delegate.apply_theme(theme)
        self.input.setFont(theme.font("menu"))
        self.update()

    # --- rows -------------------------------------------------------------------------

    def _refresh(self) -> None:
        self.list.clear()
        for row in rank(self._rows, self.input.text()):
            item = QListWidgetItem(row.label, self.list)
            item.setData(PALETTE_ROLE, row)
        if self.list.count():
            self.list.setCurrentRow(0)
        # As tall as its contents, up to a point: an empty palette must not leave a tall
        # blank box, and a full one must not run off the bottom of the window.
        visible = min(self.list.count(), 10)
        self.list.setFixedHeight(visible * ROW_H + (6 if visible else 0))

    def _activate(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        row = item.data(PALETTE_ROLE)
        # Hidden first: what a row does may open a deck or close a pane, and the
        # overlay must not still be sitting over the window it has just changed.
        self.dismiss()
        row.run()

    def _step(self, delta: int) -> None:
        count = self.list.count()
        if count:
            self.list.setCurrentRow((self.list.currentRow() + delta) % count)

    # --- keyboard and geometry --------------------------------------------------------

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            if self.is_open:
                self.setGeometry(self.parentWidget().rect())
            return False
        if watched is self.input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.dismiss()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._activate()
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                # Handled here because the list never takes focus: the text field keeps it
                # so typing goes on filtering while the selection moves.
                self._step(1 if key == Qt.Key.Key_Down else -1)
                return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        """A click on the backdrop dismisses; a click on the card does not.

        Tested against the card's rectangle rather than trusting the card to swallow it.
        A QFrame does not accept presses, so a click on its border or on the strip beside
        the field falls through to this widget exactly as a click on the backdrop does --
        and dismissing the palette because somebody clicked its own edge is not a thing
        any of them meant.
        """
        super().mousePressEvent(event)
        if not self.card.geometry().contains(event.position().toPoint()):
            self.dismiss()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Dim what is behind, from the theme's own background token.

        Painted rather than given a stylesheet colour because it needs an alpha the token
        map does not carry -- and a hex literal here would be a second source of truth
        that does not follow a theme switch.
        """
        _ = event
        colour = QColor(self._theme.c("bg"))
        colour.setAlpha(SCRIM_ALPHA)
        QPainter(self).fillRect(self.rect(), colour)
