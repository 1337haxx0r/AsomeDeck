"""Labels that carry design-system typography QSS cannot express.

Qt stylesheets have no ``text-transform``: the declaration parses and is dropped without
a warning, so a DS eyebrow styled purely in QSS renders lowercase and nobody finds out
until they compare against the design. Casing therefore lives here, next to the font role
that goes with it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QWidget

from ..theme import Theme


class RoleLabel(QLabel):
    """A label bound to a DS type role, applying that role's font and casing."""

    def __init__(self, role: str, text: str = "", theme: Theme | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._role = role
        self._theme = theme or Theme()
        self.setFont(self._theme.font(role))
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        super().setText(Theme.text_for(self._role, text))

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setFont(theme.font(self._role))


class EyebrowLabel(RoleLabel):
    """Small tracked uppercase section heading: DECKS, SESSION, CHANGED FILES."""

    def __init__(self, text: str = "", theme: Theme | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__("eyebrow", text, theme, parent)
        self.setObjectName("Eyebrow")


class BrandLabel(RoleLabel):
    """The ASOMEDECK wordmark in the title bar."""

    def __init__(self, text: str = "AsomeDeck", theme: Theme | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__("brand", text, theme, parent)
        self.setObjectName("BrandText")


class LinkLabel(RoleLabel):
    """A role label that is also a control.

    The sidebar's "change" affordance sat next to the shell name looking exactly like a
    button and was a bare ``QLabel`` with nothing connected -- so the shell it offered to
    change could not be changed, by it or by anything else. A ``QPushButton`` would bring
    button chrome this footer does not have, so the click lives on the label instead and
    the design is left alone.
    """

    clicked = Signal()

    def __init__(self, role: str, text: str = "", theme: Theme | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(role, text, theme, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)
