"""Chrome that actually paints what the stylesheet says.

Both cases here were live bugs, and both were invisible in the way that matters: the QSS
was correct, matched the widget, and was then silently not drawn. Nothing errors, nothing
logs, the colour is simply the one underneath.

* A plain ``QWidget`` ignores its ``background:`` rule without ``WA_StyledBackground``.
  Every container in the chrome was affected -- the title bar kept the window's colour no
  matter what its token said.
* ``#WinClose`` matched only its own ``:hover`` rule, so the close button had no base
  styling at all and only looked right because the widget sets flat and a fixed width in
  code.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from asomedeck.commands import MENUS  # noqa: E402
from asomedeck.platform_ import IS_WINDOWS  # noqa: E402
from asomedeck.ui import theme as th  # noqa: E402
from asomedeck.ui.chrome import Sidebar, TitleBar, WindowButton  # noqa: E402
from asomedeck.ui.deck_model import DeckListModel, DeckRow, DeckStore  # noqa: E402
from asomedeck.ui.main_window import fit_buttons  # noqa: E402

#: Minimise, maximise and close are drawn in-window only where we drew the frame --
#: which is Windows. macOS has real traffic lights in a native title bar instead.
WINDOWS_CHROME = pytest.mark.skipif(
    not IS_WINDOWS,
    reason="the in-window buttons exist only where we draw the frame",
)


@pytest.fixture
def title_bar(app):
    theme = th.Theme("dark")
    th.apply(app, theme)
    bar = TitleBar(theme)
    bar.resize(900, th.GEOM["titlebar_h"])
    bar.show()
    yield bar, theme
    bar.close()


def test_the_title_bar_paints_its_own_token(title_bar):
    """Not the window's. This is the WA_StyledBackground trap."""
    bar, theme = title_bar
    painted = bar.grab().toImage().pixelColor(bar.width() // 2, 4).name()
    assert painted.lower() == theme.hex("titlebar").lower()


def test_containers_are_marked_styled(title_bar):
    bar, _ = title_bar
    assert bar.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)


@WINDOWS_CHROME
@pytest.mark.parametrize("name,token", [("WinClose", None), ("WinButton", "chip")])
def test_window_buttons_change_on_hover(title_bar, name, token):
    """The close button hovers red, the others lift to `chip`.

    `chip` rather than the generic `hover` token: that one is a lift from the darker list
    backgrounds it was chosen against, and lands *darker* than the title bar.
    """
    bar, theme = title_bar
    button = next(b for b in bar.findChildren(WindowButton) if b.objectName() == name)
    expected = th.CLOSE_HOVER if token is None else theme.hex(token)
    spot = button.mapTo(bar, QPoint(6, 20))

    button.set_hovered(False)
    rest = bar.grab().toImage().pixelColor(spot.x(), spot.y()).name()
    button.set_hovered(True)
    hovered = bar.grab().toImage().pixelColor(spot.x(), spot.y()).name()

    assert rest != hovered, f"{name} did not change on hover"
    assert hovered.lower() == expected.lower()


def test_a_press_on_the_bar_drags(title_bar):
    """Dragging the bar moves the window, on both platforms.

    macOS has a real title bar above this row, but the row is still a grabbable strip and
    ``startSystemMove`` is cross-platform Qt, so this half of the old test stays live
    everywhere.
    """
    bar, _ = title_bar
    drags = []
    bar.dragRequested.connect(lambda: drags.append(1))

    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=QPoint(bar.width() // 2, 10))
    assert drags == [1]


@WINDOWS_CHROME
def test_a_press_on_a_button_does_not_drag(title_bar):
    """The title bar emits dragRequested unconditionally, which is safe only because a
    press on a button never reaches it. A list of widgets to exclude was written for this
    and never connected to anything; this is what replaced it -- the propagation itself,
    pinned, so a future child that does not accept its presses shows up as a window that
    drags when you click it.
    """
    bar, _ = title_bar
    drags = []
    bar.dragRequested.connect(lambda: drags.append(1))

    close = next(b for b in bar.findChildren(WindowButton) if b.objectName() == "WinClose")
    QTest.mousePress(close, Qt.MouseButton.LeftButton, pos=QPoint(4, 10))
    assert drags == []


@WINDOWS_CHROME
def test_hover_state_is_reset_by_leaving(title_bar):
    bar, _ = title_bar
    button = bar.findChildren(WindowButton)[0]
    button.set_hovered(True)
    assert button.property("hovered") == "true"
    button.set_hovered(False)
    assert button.property("hovered") == "false"


def test_the_window_buttons_follow_the_frame(title_bar):
    """Minimise, maximise and close are drawn in-window only where we drew the frame.

    On macOS the window has a native title bar with real traffic lights above this row, so
    a second set inside the window would be redundant and misplaced. What must *not* happen
    is the rest of the bar going with them: this widget is also where the five menus, the
    theme chip and the search chip live, and it is the menus that would be silently lost.
    """
    bar, _ = title_bar
    buttons = bar.findChildren(WindowButton)
    if IS_WINDOWS:
        assert [b.objectName() for b in buttons] == ["WinButton", "WinButton", "WinClose"]
        assert bar.window_buttons is not None
    else:
        assert buttons == []
        assert bar.window_buttons is None
    # Either way the bar is still the menu bar.
    assert set(bar.menus) == set(MENUS)
    assert bar.theme_button is not None
    assert bar.search_chip is not None


# --- dialogs ----------------------------------------------------------------------------

def test_a_long_dialog_button_is_not_squeezed_below_its_text(app):
    """The stylesheet's ``min-width: 96px`` is a floor for bare OK buttons, and Qt treats
    it as the minimum *size hint* -- so a wider label is free to be compressed back to it
    and elided from both ends. "Quit and close terminals" reached the screen as
    "it and close termir".
    """
    from PySide6.QtWidgets import QMessageBox

    theme = th.Theme("dark")
    th.apply(app, theme)
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.NoIcon)
    box.setText("2 terminals still running.")
    box.setInformativeText("Quitting ends the shells and anything they are running.")
    go = box.addButton("Quit and close terminals", QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(go)

    fit_buttons(box)
    box.show()
    app.processEvents()
    try:
        assert go.width() >= go.sizeHint().width(), "the label is being clipped"
    finally:
        box.close()


def test_the_change_control_is_a_control(app):
    """The sidebar footer offered "change" next to the shell name and it did nothing.

    It was a bare RoleLabel: no signal, no connection, no handler -- so the shell it
    advertised could not be changed by it or by anything else, and `db.set_launcher` had
    no caller outside the tests. That is the half of the Git-Bash bug that made it
    unrecoverable rather than merely wrong, so what is pinned here is that the click
    arrives at all.
    """
    theme = th.Theme("dark")
    th.apply(app, theme)
    sidebar = Sidebar(DeckListModel(DeckStore([])), theme)
    seen = []
    sidebar.launcherChangeRequested.connect(lambda: seen.append(True))
    QTest.mouseClick(sidebar.launcher_change, Qt.MouseButton.LeftButton)
    sidebar.close()
    assert seen == [True]


# --- collapsible side panels -------------------------------------------------------

def _laid_out(app, panel):
    """Put a panel in a real layout and let it settle, so width means something."""
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(panel)
    row.addWidget(QWidget(), 1)          # something stretchy to take the rest
    host.resize(1000, 400)
    host.show()
    app.processEvents()
    return host


@pytest.fixture
def sidebar(app):
    from asomedeck.ui.chrome import Sidebar
    from asomedeck.ui.deck_model import DeckListModel, DeckRow, DeckStore

    theme = th.Theme("dark")
    th.apply(app, theme)
    rows = [DeckRow(name="one", path="C:/one", deck_id=1, session_count=2),
            DeckRow(name="two", path="C:/two", deck_id=2)]
    panel = Sidebar(DeckListModel(DeckStore(rows)), theme)
    host = _laid_out(app, panel)
    yield panel, app
    host.close()


def test_collapsing_actually_narrows_the_panel(sidebar):
    """The width is pinned in QSS, so this is the assertion that matters.

    setMaximumWidth would be arguing with `min-width` in the stylesheet and losing; the
    property and its rule are what move it, and only after an explicit repolish.
    """
    panel, app = sidebar
    full = panel.width()
    assert full >= th.GEOM["sidebar_w"] - 2, f"did not start at full width: {full}"

    panel.set_collapsed(True)
    app.processEvents()
    narrow = panel.width()

    assert narrow <= th.GEOM["rail_w"] + 2, f"still {narrow}px wide when collapsed"
    assert narrow < full


def test_expanding_restores_the_full_width(sidebar):
    panel, app = sidebar
    full = panel.width()
    panel.set_collapsed(True)
    app.processEvents()
    panel.set_collapsed(False)
    app.processEvents()
    assert abs(panel.width() - full) <= 2


def test_the_rail_offers_one_dot_per_deck(sidebar):
    panel, _app = sidebar
    panel.refresh_rail(active_deck_id=2)

    assert len(panel._dots) == 2
    assert panel._dots[0].property("live") == "true"     # session_count 2
    assert panel._dots[1].property("live") == "false"
    assert panel._dots[1].property("active") == "true"   # deck_id 2
    assert panel._dots[0].property("active") == "false"


def test_clicking_a_rail_dot_switches_deck(sidebar):
    """The rail keeps the sidebar's actual job. A dot that only decorates would not."""
    panel, _app = sidebar
    panel.refresh_rail(active_deck_id=1)
    picked = []
    panel.deckSelected.connect(picked.append)

    panel._dots[1].click()

    assert [row.deck_id for row in picked] == [2]


def test_the_rail_dots_survive_a_deck_going_away(sidebar):
    """Rebuilt rather than diffed: a stale dot is a click that opens the wrong deck."""
    panel, _app = sidebar
    panel.refresh_rail()
    panel._model.store.rows.pop()
    panel.refresh_rail()
    assert len(panel._dots) == 1


# --- the deck expander ------------------------------------------------------------------

def _move_to(panel, point):
    """One mouse move over the deck list, at a point in viewport coordinates."""
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    viewport = panel.view.viewport()
    QApplication.sendEvent(viewport, QMouseEvent(
        QEvent.Type.MouseMove, QPointF(point),
        QPointF(viewport.mapToGlobal(point)), Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))


def _chevron(panel, row: int):
    """Where that row's expander is, in viewport coordinates."""
    return panel._chevron_box(panel._model.index(row, 0))


def test_the_expander_lights_up_only_for_the_pointer_on_it(sidebar):
    """It is drawn as a button now, so it has to behave as one.

    The delegate is told which row is hot because a view reports only "some part of this
    row is hovered", and this control is an 18px corner of a four-line row. Lighting it
    from the row's own hover would promise expansion for a click on the deck name, which
    selects the deck instead.
    """
    panel, _app = sidebar

    _move_to(panel, _chevron(panel, 1).center())
    assert panel.delegate._hot_row == 1
    assert panel.view.viewport().cursor().shape() == Qt.CursorShape.PointingHandCursor

    _move_to(panel, _chevron(panel, 1).center() - QPoint(80, 0))
    assert panel.delegate._hot_row == -1
    assert panel.view.viewport().cursor().shape() == Qt.CursorShape.ArrowCursor


def test_a_scroll_drops_the_hot_expander(sidebar):
    """The rows move under a pointer that never moved, and no move event says so."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication

    panel, _app = sidebar
    _move_to(panel, _chevron(panel, 0).center())
    assert panel.delegate._hot_row == 0

    viewport = panel.view.viewport()
    at = QPointF(_chevron(panel, 0).center())
    QApplication.sendEvent(viewport, QWheelEvent(
        at, QPointF(viewport.mapToGlobal(at.toPoint())), QPoint(0, -40), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False))

    assert panel.delegate._hot_row == -1


def test_the_expander_sits_on_the_name_line(sidebar):
    """Level with the live dot and the count it shares that line with.

    Hung from the row padding instead it sat a few pixels high -- invisible while it was
    a bare glyph, obvious once it wore a border.
    """
    from PySide6.QtGui import QFontMetrics

    panel, _app = sidebar
    box = _chevron(panel, 0)
    line_top = panel.view.visualRect(panel._model.index(0, 0)).top() + th.GEOM["row_pad"]
    name_h = QFontMetrics(panel._theme.font("deckName")).height()

    above = box.top() - line_top
    below = (line_top + name_h) - (box.top() + box.height())
    assert above >= 0 and abs(above - below) <= 1, f"{above} above, {below} below"


def test_the_expander_target_is_where_it_is_drawn(app):
    """The two disagreed by 27px, but only for a registry long enough to scroll.

    ``visualRect`` answers with the delegate's size hint -- a flat sidebar width -- while
    QListView narrows the rect it hands ``paint`` to the viewport, which is that width
    less the list's padding and whatever the scrollbar takes. Hit-testing the unclamped
    rect aimed 27px right of the box on screen, and a two-deck fixture has no scrollbar
    and so no disagreement to catch it.
    """
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QStyleOptionViewItem

    theme = th.Theme("dark")
    th.apply(app, theme)
    rows = [DeckRow(name=f"deck{i}", path=f"D:/p{i}", deck_id=i) for i in range(1, 14)]
    panel = Sidebar(DeckListModel(DeckStore(rows)), theme)
    host = _laid_out(app, panel)
    try:
        painted = []
        drawn_by = panel.delegate.paint

        def spy(painter, option, index):
            if index.row() == 0:
                painted.append(QRect(option.rect))
            drawn_by(painter, option, index)

        panel.delegate.paint = spy
        panel.view.viewport().render(QPixmap(panel.view.viewport().size()))

        assert panel.view.verticalScrollBar().isVisible(), "no scrollbar, nothing to catch"
        assert painted, "the first row never reached the delegate"
        option = QStyleOptionViewItem()
        option.rect = painted[0]

        assert (panel._chevron_box(panel._model.index(0, 0))
                == panel.delegate.chevron_rect(option))
    finally:
        host.close()
