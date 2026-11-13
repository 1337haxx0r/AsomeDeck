"""Per-deck accent colour: the picker, what it stores, and what it paints.

The colour is stored once and drawn twice -- as the rail down a sidebar row, and as the
deck's dot when the panel is collapsed to a rail -- through two different mechanisms, a
QPainter and a widget-level stylesheet. Both are asserted here, because a colour that
reaches the database and then paints nothing looks exactly like a colour that was never
picked.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QEvent, QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QMenu,
    QStyle,
    QStyleOptionViewItem,
    QWidgetAction,
)

from asomedeck.ui import theme as th  # noqa: E402
from asomedeck.ui.deck_model import (  # noqa: E402
    DeckListModel,
    DeckRow,
    DeckStore,
)
from asomedeck.ui.delegates import DeckDelegate  # noqa: E402
from asomedeck.ui.widgets.swatches import CELL, GAP, PAD_X, AccentSwatchRow  # noqa: E402

# --- the swatch row -------------------------------------------------------------------


def _click(row: AccentSwatchRow, index: int, offset: int = 0) -> None:
    point = QPoint(PAD_X + index * (CELL + GAP) + CELL // 2 + offset, CELL)
    row.mouseReleaseEvent(QMouseEvent(
        QEvent.Type.MouseButtonRelease, point, row.mapToGlobal(point),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    ))


def test_the_row_offers_default_first_then_every_preset(app):
    row = AccentSwatchRow(th.Theme("dark"))
    assert row._values == ["", *th.ACCENTS]


def test_clicking_a_swatch_emits_its_hex(app):
    row = AccentSwatchRow(th.Theme("dark"))
    seen: list[str] = []
    row.picked.connect(seen.append)

    _click(row, 2)
    assert seen == [th.ACCENTS[1]]


def test_clicking_the_first_cell_clears_rather_than_picks(app):
    # Default is the way out of having a colour, so it emits "" -- the same value the
    # database stores for "use the theme accent".
    row = AccentSwatchRow(th.Theme("dark"), current=th.ACCENTS[0])
    seen: list[str] = []
    row.picked.connect(seen.append)

    _click(row, 0)
    assert seen == [""]


def test_clicking_between_two_swatches_emits_nothing(app):
    row = AccentSwatchRow(th.Theme("dark"))
    seen: list[str] = []
    row.picked.connect(seen.append)

    _click(row, 0, offset=CELL // 2 + GAP // 2)
    assert seen == []


def test_the_swatches_show_what_the_rail_will_show(app):
    """A swatch painted in the stored hex would promise a colour the light theme then
    substitutes, so the row paints through the same Theme.tint the delegate does."""
    light = th.Theme("light")
    row = AccentSwatchRow(light)
    pixmap = QPixmap(row.sizeHint())
    pixmap.fill(light.c("panel"))
    painter = QPainter(pixmap)
    row.render(painter, QPoint(0, 0))
    painter.end()

    # A few pixels into the second cell -- the first preset -- clear of its rounded corner.
    sample = pixmap.toImage().pixelColor(PAD_X + (CELL + GAP) + CELL // 2, CELL // 2 + 7)
    assert sample.name() == th.ACCENT_LIGHT[th.ACCENTS[0]]
    assert sample.name() != th.ACCENTS[0]


# --- the sidebar rail -----------------------------------------------------------------


def _rail_colour(theme: th.Theme, row: DeckRow, *, selected: bool) -> str:
    """Paint one deck row through the real delegate and read the rail's pixel."""
    model = DeckListModel(DeckStore([row]))
    delegate = DeckDelegate(theme)
    index = model.index(0, 0)

    option = QStyleOptionViewItem()
    option.rect = QRect(QPoint(0, 0), delegate.sizeHint(option, index))
    if selected:
        option.state |= QStyle.StateFlag.State_Selected

    pixmap = QPixmap(option.rect.size())
    pixmap.fill(theme.c("panel"))
    painter = QPainter(pixmap)
    delegate.paint(painter, option, index)
    painter.end()
    # A pixel inside the 3px rail: past the row padding, halfway down so the rounded ends
    # are not what gets sampled.
    return pixmap.toImage().pixelColor(delegate._pad + 1, option.rect.height() // 2).name()


def test_a_deck_without_a_colour_paints_no_rail_until_selected(app):
    theme = th.Theme("dark")
    row = DeckRow(name="alpha", path="C:/dev/alpha", deck_id=1)
    # Unselected and uncoloured, the rail's pixels belong to the row background.
    assert _rail_colour(theme, row, selected=False) != theme.hex("accent")
    assert _rail_colour(theme, row, selected=True) == theme.hex("accent")


def test_a_deck_with_a_colour_keeps_its_rail_unselected(app):
    # The point of giving a deck a colour is telling it apart from the others, which is
    # a question about the rows you are *not* on.
    theme = th.Theme("dark")
    row = DeckRow(name="alpha", path="C:/dev/alpha", deck_id=1, accent=th.ACCENTS[2])
    assert _rail_colour(theme, row, selected=False) == th.ACCENTS[2]
    assert _rail_colour(theme, row, selected=True) == th.ACCENTS[2]


def test_the_rail_takes_the_light_twin_on_the_light_theme(app):
    light = th.Theme("light")
    row = DeckRow(name="alpha", path="C:/dev/alpha", deck_id=1, accent=th.ACCENTS[0])
    assert _rail_colour(light, row, selected=False) == th.ACCENT_LIGHT[th.ACCENTS[0]]


# --- the window ------------------------------------------------------------------------


def _open_deck_menu(window) -> QMenu:
    """The real menu for the one registered deck, built and not shown.

    ``_build_deck_menu`` exists for exactly this: QMenu.exec runs a modal event loop,
    which in an unattended run is a hang and not a menu.
    """
    menu, _custom, _rename, _remove = window._build_deck_menu(window.model.store.rows[0])
    return menu


def test_the_context_menu_carries_the_swatch_row(window):
    menu = _open_deck_menu(window)

    widget_actions = [a for a in menu.actions() if isinstance(a, QWidgetAction)]
    assert len(widget_actions) == 1
    assert isinstance(widget_actions[0].defaultWidget(), AccentSwatchRow)

    labels = [a.text() for a in menu.actions() if a.text()]
    assert "Custom colour…" in labels
    assert any(label.startswith("Remove ") for label in labels)


def test_the_menu_still_names_the_deck_it_will_remove(window):
    # The one thing this menu did before the swatches arrived, and the reason it names the
    # row rather than "this deck": a right-click on a terminal used to offer it too.
    _menu, _custom, _rename, remove = window._build_deck_menu(
        window.model.store.rows[0])
    assert remove.text() == f"Remove {window.model.store.rows[0].name} from AsomeDeck"


def test_the_swatch_row_opens_on_the_colour_the_deck_already_has(window):
    deck = window.db.decks()[0]
    window._set_deck_accent(deck.id, th.ACCENTS[3])

    menu = _open_deck_menu(window)
    swatches = next(a.defaultWidget() for a in menu.actions() if isinstance(a, QWidgetAction))
    assert swatches._current == th.ACCENTS[3]


def test_picking_a_swatch_from_the_menu_stores_the_colour(window):
    menu = _open_deck_menu(window)
    swatches = next(a.defaultWidget() for a in menu.actions() if isinstance(a, QWidgetAction))

    swatches.picked.emit(th.ACCENTS[2])

    assert window.db.decks()[0].accent == th.ACCENTS[2]


def test_setting_an_accent_stores_it_and_reaches_the_row(window):
    deck = window.db.decks()[0]
    window._set_deck_accent(deck.id, th.ACCENTS[1])

    assert window.db.decks()[0].accent == th.ACCENTS[1]
    assert window.model.store.rows[0].accent == th.ACCENTS[1]
    # The open deck is held as a Deck instance beside the database; leaving it stale
    # means the next thing to read it disagrees with what the sidebar is painting.
    assert window.deck.accent == th.ACCENTS[1]


def test_clearing_an_accent_goes_back_to_the_theme(window):
    deck = window.db.decks()[0]
    window._set_deck_accent(deck.id, th.ACCENTS[1])
    window._set_deck_accent(deck.id, "")

    assert window.db.decks()[0].accent == ""
    assert window.model.store.rows[0].accent == ""


def _collapsed_rail(window):
    """The sidebar's dots, with the panel actually collapsed.

    _refresh_rail is a no-op while the panel is expanded -- the dots are not on screen and
    rebuilding them on every model change would be work nobody can see. _toggle_panel is
    what builds them on the way in, so that is the path the test takes.
    """
    if not window.sidebar.collapsed:
        window._toggle_panel(window.sidebar)
    return window.sidebar._dots


def test_the_rail_dot_wears_the_deck_colour(window):
    deck = window.db.decks()[0]
    window._set_deck_accent(deck.id, th.ACCENTS[2])

    sheet = _collapsed_rail(window)[0].styleSheet()
    assert th.ACCENTS[2] in sheet
    # Restated on purpose: a widget sheet outranks the application one whatever the
    # selectors say, so a bare background rule would kill the app's #RailDot:hover.
    assert ":hover" in sheet


def test_a_deck_without_a_colour_leaves_the_dot_to_the_stylesheet(window):
    # No override, no widget sheet -- otherwise the dot stops following #RailDot[active].
    assert _collapsed_rail(window)[0].styleSheet() == ""


def test_a_theme_toggle_re_tints_the_rail_dot(window):
    deck = window.db.decks()[0]
    window._set_deck_accent(deck.id, th.ACCENTS[0])
    assert th.ACCENTS[0] in _collapsed_rail(window)[0].styleSheet()

    window._toggle_theme()

    # The stored hex has not changed; what it means in the light theme has.
    sheet = window.sidebar._dots[0].styleSheet()
    assert th.ACCENT_LIGHT[th.ACCENTS[0]] in sheet
    assert th.ACCENTS[0] not in sheet
