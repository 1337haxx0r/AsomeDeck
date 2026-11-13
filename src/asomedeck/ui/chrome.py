"""The design-system chrome around the pane grid (spec 14).

Title bar, deck sidebar, deck header, session tab strip, session panel and status
bar. Everything here is presentation: it renders state owned elsewhere and emits intent,
so nothing in this module knows how a terminal is launched or captured.

Two rules hold throughout, both from spec 14.2-14.3:

* No field claims something we cannot observe. Context percentage, turn counts and
  running-versus-waiting were cut because docking cannot see them; where a value is not
  yet wired the row is omitted rather than shown empty or plausible.
* No hex literals. Colour comes from ``Theme`` so a theme switch actually switches.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QAction, QCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QStackedWidget,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, platform_
from ..commands import MENUS, by_id, chord_text, in_menu
from .deck_model import (
    DeckListModel,
    DeckRow,
    PlaceholderRow,
    SessionRow,
)
from .delegates import DeckDelegate
from .file_tree import IS_DIR_ROLE, PATH_ROLE, FileTree
from .theme import GEOM, Theme, styled
from .widgets.labels import BrandLabel, EyebrowLabel, LinkLabel, RoleLabel
from .widgets.status_dot import DEAD, LIVE, StatusDot

#: Rail furniture. Small enough to fit GEOM["rail_w"] with its margins.
RAIL_DOT = 10
RAIL_BUTTON = 22


def _row(*widgets, spacing: int = 0, margins=(0, 0, 0, 0)) -> QWidget:
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    for widget in widgets:
        if widget is None:
            layout.addStretch(1)
        else:
            layout.addWidget(widget)
    return holder


def _sep(theme: Theme) -> QLabel:
    label = QLabel("|")
    label.setObjectName("StatusSep")
    label.setFont(theme.font("statusBar"))
    return label


class WindowButton(QPushButton):
    """A title-bar button that reports its own hover state.

    QSS has a ``:hover`` pseudo-state and this could rely on it. It does not, because the
    state that reaches the stylesheet could not be demonstrated: hovering the close button
    left it unchanged, and ``underMouse()`` stayed False through every way of asking --
    a synthetic QEnterEvent, WA_UnderMouse set by hand, QTest.mouseMove, and moving the
    physical cursor onto it.

    So the state is kept here, where it can be set and seen. ``set_hovered`` drives a
    dynamic property that the stylesheet matches on, which is the same mechanism
    ``pane_tree.Pane`` already uses for its focus ring, and for the same reason: Qt does
    not restyle on a property change by itself, so the repolish is explicit and therefore
    testable. The ``:hover`` rules are kept alongside; where they work they agree.
    """

    def __init__(self, text: str, name: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName(name)
        self.setFlat(True)
        self.setFixedWidth(46)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.set_hovered(False)

    def set_hovered(self, on: bool) -> None:
        self.setProperty("hovered", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().enterEvent(event)
        self.set_hovered(True)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().leaveEvent(event)
        self.set_hovered(False)


class TitleBar(QWidget):
    """Brand, menus, theme toggle, search hint, window buttons (spec 14.7)."""

    minimizeRequested = Signal()
    maximizeRequested = Signal()
    closeRequested = Signal()
    themeToggled = Signal()
    dragRequested = Signal()
    #: The search chip was clicked. It was a QLabel promising a Ctrl+K that no handler
    #: anywhere in the app answered; a promise of a control is a control.
    paletteRequested = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        styled(self, "TitleBar")
        self._theme = theme

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 0, 0)
        layout.setSpacing(20)

        mark = QWidget()
        styled(mark, "BrandMark")
        mark.setFixedSize(11, 11)
        layout.addWidget(_row(mark, BrandLabel(theme=theme), spacing=9))

        menus = QWidget()
        menu_layout = QHBoxLayout(menus)
        menu_layout.setContentsMargins(0, 0, 0, 0)
        menu_layout.setSpacing(18)
        self.menus: dict[str, QMenu] = {}
        for name in MENUS:
            button = QPushButton(name)
            button.setObjectName("MenuButton")
            button.setFont(theme.font("menu"))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFlat(True)
            menu = QMenu(button)
            menu.setFont(theme.font("menu"))
            # setMenu rather than clicked.connect(popup): Qt then owns the open/close
            # cycle, so a second click on the same button shuts the dropdown instead of
            # closing it and immediately reopening it.
            button.setMenu(menu)
            self.menus[name] = menu
            menu_layout.addWidget(button)
        layout.addWidget(menus)
        layout.addStretch(1)

        # theme.mode, not the literal "dark" it used to hold: the config decides which
        # theme a launch starts in, and a chip that always opened saying "dark" was
        # wrong on every light-theme launch.
        self.theme_button = QPushButton(theme.mode)
        self.theme_button.setObjectName("Chip")
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.clicked.connect(self.themeToggled)
        layout.addWidget(self.theme_button)

        self.search_chip = QPushButton("search")
        self.search_chip.setObjectName("Chip")
        self.search_chip.setFont(theme.font("statusBar"))
        self.search_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_chip.setToolTip("Commands, decks, sessions and files")
        self.search_chip.clicked.connect(self.paletteRequested)
        layout.addWidget(self.search_chip)
        layout.addSpacing(14)

        # Only where the window is frameless, which is to say only on Windows. macOS draws
        # its own traffic lights in a real title bar above this row, and a second set of
        # minimise/zoom/close glyphs inside the window would be both redundant and a lie --
        # they would sit at the wrong end, in the wrong order, under the wrong metaphor.
        #
        # The rest of the bar stays: this widget is not only a title bar, it is where the
        # five menus, the theme chip and the search chip live. Deleting it on macOS would
        # take the app's menus with it.
        self.window_buttons: QWidget | None = None
        if platform_.IS_WINDOWS:
            buttons = QWidget()
            button_layout = QHBoxLayout(buttons)
            button_layout.setContentsMargins(0, 0, 0, 0)
            button_layout.setSpacing(0)
            for text, name, signal in (
                ("—", "WinButton", self.minimizeRequested),
                ("□", "WinButton", self.maximizeRequested),
                ("✕", "WinClose", self.closeRequested),
            ):
                button = WindowButton(text, name)
                button.clicked.connect(signal)
                button_layout.addWidget(button)
            layout.addWidget(buttons)
            self.window_buttons = buttons
        else:
            # The 14px spacing above assumed a button block was about to follow it and butt
            # up against the frame. With nothing there the search chip would sit hard
            # against the window edge.
            layout.addSpacing(4)

    def set_actions(self, actions: dict[str, QAction]) -> None:
        """Fill the five dropdowns from the registry.

        These are the window's own QActions -- the same objects its shortcuts fire, not
        copies. So a menu entry shows the chord that actually triggers it, activating it
        runs the one handler, and ``_refresh_commands`` greying an action greys the menu
        row with no second call. Restating any of that here is what the registry exists
        to prevent.
        """
        for name, menu in self.menus.items():
            menu.clear()
            for command in in_menu(name):
                menu.addAction(actions[command.id])

    def set_search_hint(self, chord: str) -> None:
        """Label the chip with the chord that really opens the palette.

        Passed in rather than written here, because config.py can rebind it by command id
        and a chip that names a key nobody bound is the same lie the hardcoded ``Ctrl+K``
        was -- only harder to notice, because it would once have been true.
        """
        native = chord_text(chord)
        self.search_chip.setText(f"{native}   search" if native else "search")

    def set_theme_label(self, text: str) -> None:
        self.theme_button.setText(text)

    # --- window dragging -------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Drag the window by its title bar.

        Handled here rather than by answering HTCAPTION from the hit-test. Windows routes
        presses on a caption as non-client, so Qt would never see them and every button up
        here would stop working. startSystemMove hands off to the same Windows move loop,
        so Aero snap and drag-to-maximise behave natively either way.

        Unconditional, and safe for a reason worth stating: a press that lands on a button
        or a menu never reaches this method, because those widgets accept it. Only presses
        on the bar itself and on its labels -- which do not -- arrive here. A list of
        widgets to exclude was written for this and never connected to anything; it is
        gone, because the propagation *is* the exclusion.
        """
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragRequested.emit()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().mouseDoubleClickEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.maximizeRequested.emit()


class RailDot(QPushButton):
    """A deck, reduced to a dot you can still click.

    A QPushButton and not the StatusDot widget beside it: StatusDot sets
    WA_TransparentForMouseEvents, because in a pane header it is a readout and a click
    there belongs to the pane. On a rail the dot *is* the control.
    """

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.setObjectName("RailDot")
        self.setFlat(True)
        self.setFixedSize(RAIL_DOT, RAIL_DOT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(label)
        self.set_state(active=False, live=False)

    def set_state(self, *, active: bool, live: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        self.setProperty("live", "true" if live else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_accent(self, fill: str, hover: str) -> None:
        """Paint this dot in its deck's own colour, collapsed or not.

        A widget-level sheet, which Qt prefers over the application one whatever the
        selectors say -- so the hover has to be restated here or the app's
        ``#RailDot:hover`` never wins again and the dot stops answering the pointer. The
        radius and the 10px box are not restated: those rules do not conflict, so they
        still arrive from the application sheet.
        """
        self.setStyleSheet(
            f"#RailDot {{ background: {fill}; }}"
            f"#RailDot:hover {{ background: {hover}; }}"
            if fill else ""
        )


class RailButton(QPushButton):
    """The chevron that collapses or expands a panel."""

    def __init__(self, text: str, tip: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("RailButton")
        self.setFlat(True)
        self.setFixedSize(RAIL_BUTTON, RAIL_BUTTON)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tip)


class SidebarTab(QPushButton):
    """One word in the sidebar header that switches what the panel is showing.

    A ``QTabBar`` would bring a frame, a close affordance and its own metrics into a
    46px header that already holds a count and two buttons. This is the eyebrow type the
    header used to hold, made clickable and given an underline when it is the live one --
    the casing applied in Python, because QSS drops ``text-transform`` silently.
    """

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(label.upper(), parent)
        self.setObjectName("SidebarTab")
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.set_on(False)

    def set_on(self, on: bool) -> None:
        self.setProperty("on", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class SidePanel(QWidget):
    """A panel that shrinks to a rail instead of vanishing.

    Two states, one widget. ``full`` and ``rail`` are both built up front and swapped by a
    QStackedLayout, so collapsing costs a repaint rather than a rebuild and nothing has to
    be re-wired on the way back.

    The width is the awkward part. It is pinned in the stylesheet -- ``min-width`` and
    ``max-width`` both -- so ``setMaximumWidth`` would be arguing with QSS and QSS wins.
    A dynamic property with a matching rule is the way to change it, the same mechanism
    WindowButton uses for hover, and for the same reason: Qt does not restyle on a property
    change by itself, so the repolish is explicit. ``updateGeometry`` after it, because the
    size hint is cached and the rule alone will not move the layout.
    """

    collapsedChanged = Signal(bool)

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        styled(self, name)
        self._collapsed = False

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self.full = QWidget()
        self.rail = QWidget()
        self._stack.addWidget(self.full)
        self._stack.addWidget(self.rail)
        self.set_collapsed(False)

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        self.setProperty("collapsed", "true" if self._collapsed else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.updateGeometry()
        self._stack.setCurrentWidget(self.rail if self._collapsed else self.full)
        self.collapsedChanged.emit(self._collapsed)

    def toggle(self) -> None:
        self.set_collapsed(not self._collapsed)


class Sidebar(SidePanel):
    """Deck registry: header, delegate-painted rows, default-launcher footer.

    Collapsed it keeps the two things the panel is actually for: adding a deck, and
    switching to one. Everything else is detail that the pane chrome and the header repeat
    anyway.
    """

    deckSelected = Signal(object)   # DeckRow
    #: A terminal under an expanded deck was clicked. (deck_id, pane_id)
    sessionSelected = Signal(int, int)
    addDeckRequested = Signal()
    #: The "+" dropdown's other entries: a deck over a repository not cloned yet, and a deck
    #: with no folder under it.
    cloneDeckRequested = Signal()
    newDeckRequested = Signal()
    #: About to open, so the window can check Git is still where it was.
    addMenuOpening = Signal()
    #: A file was picked in Files mode. Carries an absolute path.
    fileActivated = Signal(str)
    #: "decks" or "files", so the window can remember which one was up.
    modeChanged = Signal(str)
    #: "change" in the footer was picked. The window owns the menu, because only it knows
    #: which deck is open and which shells are installed.
    launcherChangeRequested = Signal()

    def __init__(self, model: DeckListModel, theme: Theme,
                 parent: QWidget | None = None) -> None:
        super().__init__("Sidebar", parent)
        self._theme = theme
        self._model = model
        self._dots: list[RailDot] = []
        #: Remembered so apply_theme can rebuild the rail without the window telling it
        #: again which deck is open -- it is the same answer either way.
        self._active_deck_id: int | None = None
        #: What is on screen, and what the user last asked for. They differ only while a
        #: deck with no folder is open: Files is not available then, but the preference
        #: has to survive it -- forcing the tab back and forgetting why would rewrite
        #: `sidebar_mode` in the registry and lose the choice for every other deck too.
        self._mode = ""
        self._wanted = ""
        self._files_ok = True

        #: One menu behind both "+" buttons -- the header's and the rail's. They are the
        #: same control at two sizes, and two menus would be two places to add the next
        #: entry to, one of which would be missed. A QMenu is not owned by the button it
        #: is set on, so the panel holds it.
        self._add_menu = QMenu(self)
        self._add_menu.setFont(theme.font("menu"))
        # Off by default on a QMenu, and the greyed clone entry is what needs it: a disabled row
        # with no reason attached is a dead end.
        self._add_menu.setToolTipsVisible(True)
        self._add_menu.aboutToShow.connect(self.addMenuOpening)
        for command_id, signal in (("project.add", self.addDeckRequested),
                                   ("deck.clone", self.cloneDeckRequested),
                                   ("deck.new", self.newDeckRequested)):
            # Labels from the registry rather than spelled again here, so the dropdown,
            # the File menu and the palette cannot drift into naming one thing twice.
            action = QAction(by_id(command_id).label, self)
            action.triggered.connect(signal)
            self._add_menu.addAction(action)
            if command_id == "deck.clone":
                self._clone_action = action

        layout = QVBoxLayout(self.full)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        styled(header, "SidebarHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 0, 12, 0)
        header_layout.setSpacing(6)
        self.tab_decks = SidebarTab("Decks")
        self.tab_files = SidebarTab("Files")
        self.tab_decks.clicked.connect(lambda: self.set_mode("decks"))
        self.tab_files.clicked.connect(lambda: self.set_mode("files"))
        header_layout.addWidget(self.tab_decks)
        header_layout.addWidget(self.tab_files)
        header_layout.addStretch(1)
        self.count_label = RoleLabel("deckMeta", str(model.deck_count()), theme)
        header_layout.addWidget(self.count_label)
        header_layout.addWidget(self._add_button())
        self.collapse_button = RailButton("«", "Collapse (Ctrl+B)")
        self.collapse_button.clicked.connect(self.toggle)
        header_layout.addWidget(self.collapse_button)
        layout.addWidget(header)

        self.view = QListView()
        self.view.setObjectName("DeckList")
        self.view.setModel(model)
        self.delegate = DeckDelegate(theme, parent=self.view)
        self.view.setItemDelegate(self.delegate)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setMouseTracking(True)
        self.view.setUniformItemSizes(False)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.clicked.connect(self._on_clicked)
        # For the expander's own hover -- see eventFilter. On the viewport, because that
        # is the widget the pointer is actually over inside a scroll area.
        self.view.viewport().installEventFilter(self)

        self.files = FileTree(theme)
        self.files.fileActivated.connect(self.fileActivated)

        self._modes = QStackedWidget()
        self._modes.addWidget(self.view)
        self._modes.addWidget(self.files)
        layout.addWidget(self._modes, 1)

        footer = QWidget()
        styled(footer, "SidebarFooter")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(16, 12, 16, 14)
        footer_layout.setSpacing(10)
        footer_layout.addWidget(EyebrowLabel("Default shell & command", theme))
        picker = QWidget()
        styled(picker, "AgentPicker")
        picker_layout = QHBoxLayout(picker)
        picker_layout.setContentsMargins(11, 9, 11, 9)
        picker_layout.setSpacing(10)
        swatch = QWidget()
        styled(swatch, "BrandMark")
        swatch.setFixedSize(7, 7)
        picker_layout.addWidget(swatch)
        self.launcher_label = RoleLabel("panelVal", "Git Bash", theme)
        picker_layout.addWidget(self.launcher_label)
        picker_layout.addStretch(1)
        self.launcher_change = LinkLabel("panelKey", "change", theme)
        self.launcher_change.clicked.connect(self.launcherChangeRequested)
        picker_layout.addWidget(self.launcher_change)
        footer_layout.addWidget(picker)
        layout.addWidget(footer)
        #: Kept so Files mode can drop it: the default shell belongs to the deck
        #: registry above it, and under a file tree it is a control for something not
        #: on screen.
        self._footer = footer

        self._build_rail(theme)
        self.set_mode("decks")

        if model.rowCount():
            self.view.setCurrentIndex(model.index(0, 0))

    def _add_button(self, tooltip: str = "") -> QPushButton:
        """A "+" that opens the add menu rather than doing one of the things on it.

        It used to be the folder picker directly, which was right while a deck was a
        folder. A deck with no folder has no picker to open, so the button that makes
        decks has to ask which kind first.
        """
        button = QPushButton("+")
        button.setObjectName("IconButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            button.setToolTip(tooltip)
        # setMenu rather than clicked.connect(popup), for the same reason the title bar
        # does it: Qt then owns the open/close cycle, so a second click on the button
        # shuts the dropdown instead of closing it and immediately reopening it.
        button.setMenu(self._add_menu)
        return button

    def _build_rail(self, theme: Theme) -> None:
        rail = QVBoxLayout(self.rail)
        rail.setContentsMargins(0, 8, 0, 12)
        rail.setSpacing(10)

        expand = RailButton("»", "Expand (Ctrl+B)")
        expand.clicked.connect(self.toggle)
        rail.addWidget(expand, 0, Qt.AlignmentFlag.AlignHCenter)

        rail.addWidget(self._add_button("New deck"), 0,
                       Qt.AlignmentFlag.AlignHCenter)

        self._dot_box = QVBoxLayout()
        self._dot_box.setContentsMargins(0, 6, 0, 0)
        self._dot_box.setSpacing(10)
        rail.addLayout(self._dot_box)
        rail.addStretch(1)
        _ = theme

    def refresh_rail(self, active_deck_id: int | None = None) -> None:
        """Rebuild the dots from the model.

        Rebuilt rather than diffed: a handful of decks, and a stale dot pointing at a
        removed deck is a click that opens the wrong thing.
        """
        while self._dot_box.count():
            item = self._dot_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._dots = []
        self._active_deck_id = active_deck_id
        for row in self._model.store.rows:
            dot = RailDot(row.name)
            dot.set_state(active=row.deck_id == active_deck_id,
                          live=row.session_count > 0)
            if row.accent:
                dot.set_accent(self._theme.tint(row.accent).name(),
                               self._theme.hex("text"))
            dot.clicked.connect(lambda _c=False, r=row: self.deckSelected.emit(r))
            self._dot_box.addWidget(dot, 0, Qt.AlignmentFlag.AlignHCenter)
            self._dots.append(dot)

    def apply_theme(self, theme: Theme) -> None:
        """Re-resolve everything the panel paints itself rather than via the app sheet.

        Only the rail dots, so far: a deck colour is stored once and means two different
        values depending on the mode, so a toggle has to ask Theme.tint again. The rows
        themselves go through the delegate, which the window re-themes separately.
        """
        self._theme = theme
        self.refresh_rail(self._active_deck_id)

    def set_clone_available(self, available: bool, why: str = "") -> None:
        """Grey the clone entry, and say why. ``why`` shows as its tooltip."""
        self._clone_action.setEnabled(available)
        self._clone_action.setToolTip(why)

    def set_launcher(self, label: str, command: str = "") -> None:
        self.launcher_label.setText(f"{label} → {command}" if command else label)

    @property
    def mode(self) -> str:
        """"decks" or "files"."""
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Switch what the panel shows. Unknown names fall back to decks.

        This is the *asked for* mode, which is what `modeChanged` reports and what the
        window stores. What ends up on screen may be the deck list anyway -- see
        `set_files_available`.
        """
        # Anything that is not "files" is the deck list, which is what makes the rename
        # safe here: `sidebar_mode` in app_meta holds whatever the last run wrote, and on
        # a machine that ran an older build that is the string "projects". It falls
        # through to "decks" rather than leaving the panel showing neither.
        mode = "files" if mode == "files" else "decks"
        if mode == self._wanted:
            return
        self._wanted = mode
        self._show(mode if mode == "decks" or self._files_ok else "decks")
        self.modeChanged.emit(mode)

    def set_files_available(self, available: bool) -> None:
        """Whether the deck on screen has a folder for the tree to show.

        A deck with no folder has no tree, and a Files tab that switches to a blank root
        is a worse answer than one that is visibly not for this deck. The tab stays in
        place rather than being hidden: dropping it would slide the tab beside it sideways
        on every deck switch.

        The user's choice is remembered rather than overwritten, so a Files-mode session
        that passes through a folderless deck is back in Files mode when it leaves.
        """
        if available == self._files_ok:
            return
        self._files_ok = available
        self.tab_files.setEnabled(available)
        self.tab_files.setToolTip("" if available else "This deck has no folder")
        self._show(self._wanted if available or self._wanted == "decks" else "decks")

    def _show(self, mode: str) -> None:
        """Put a mode on screen. Says nothing about what was asked for.

        The footer goes with Files mode. It sets the *default shell* for new terminals,
        which under a file tree is a control for something not on screen -- and the
        space it frees is the bottom third of the tree.
        """
        if mode == self._mode:
            return
        self._mode = mode
        showing_files = mode == "files"
        self._modes.setCurrentWidget(self.files if showing_files else self.view)
        self.tab_decks.set_on(not showing_files)
        self.tab_files.set_on(showing_files)
        self._footer.setVisible(not showing_files)
        # The count is "how many decks", which is not the question Files mode answers.
        self.count_label.setVisible(not showing_files)

    def refresh_count(self) -> None:
        # The header count is built from an empty model at construction, so it is wrong
        # until the decks actually load.
        #
        # deck_count rather than rowCount: the accordion put terminals in the same
        # list, and this label answers "how many decks", so expanding one with four
        # terminals under it used to make the sidebar claim five decks.
        self.count_label.setText(str(self._model.deck_count()))

    def _on_clicked(self, index) -> None:
        """Route a click by what was clicked, not merely which row.

        A deck row does two things depending on where it is hit: the chevron toggles
        the accordion, anywhere else switches deck. They share a row, so the position
        has to be consulted -- ``clicked`` only carries the index.
        """
        row = self._model.row_at(index)
        if row is None:
            return
        if isinstance(row, SessionRow):
            self.sessionSelected.emit(row.deck_id, row.pane_id)
            return
        if isinstance(row, PlaceholderRow):
            return
        if self._on_chevron(index, self.view.viewport().mapFromGlobal(QCursor.pos())):
            self._model.set_expanded(row.deck_id,
                                     not self._model.is_expanded(row.deck_id))
            return
        self.deckSelected.emit(row)

    def _chevron_box(self, index: QModelIndex) -> QRect:
        """Where that row's expander is drawn, in viewport coordinates.

        The clamp is the whole point of this existing. ``visualRect`` answers with the
        delegate's size hint -- a flat 272, the sidebar's width -- but QListView narrows
        the rect it hands ``paint`` to the viewport, which is that width less the list's
        padding and whatever the scrollbar takes. Asking the delegate with the unclamped
        rect put the target 27px right of the box on screen, and only once enough decks
        were registered to bring the scrollbar out -- so it read as "sometimes it just
        misses", and a two-deck fixture had no disagreement to show.
        """
        rect = self.view.visualRect(index)
        rect.setWidth(min(rect.width(), self.view.viewport().width()))
        option = QStyleOptionViewItem()
        option.rect = rect
        return self.delegate.chevron_rect(option)

    def _on_chevron(self, index: QModelIndex, point) -> bool:
        """Whether a viewport point lands on that row's expander."""
        return self._chevron_box(index).contains(point)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        """Give the expander the hover the sidebar's "+" gets for free.

        A QPushButton knows when the pointer is on it; a delegate is told only that some
        row is hovered, and this control is an 18px corner of a row four lines tall. So
        the moves are watched here and the delegate is told which row is hot -- otherwise
        the box would light up for a click that selects the deck rather than expanding it.

        Repaints only on a crossing, not on every move: ``set_hot_row`` reports whether
        anything changed, and the answer is no for all but two pixels of a traverse.
        """
        if watched is self.view.viewport():
            if event.type() == QEvent.Type.MouseMove:
                point = event.position().toPoint()
                index = self.view.indexAt(point)
                hot = (index.isValid()
                       and isinstance(self._model.row_at(index), DeckRow)
                       and self._on_chevron(index, point))
                self._set_hot_row(index.row() if hot else -1)
            elif event.type() in (QEvent.Type.Leave, QEvent.Type.Wheel):
                # Wheel too: the rows move under a stationary pointer, so the row that
                # was hot is no longer the one being pointed at and no move says so.
                self._set_hot_row(-1)
        return super().eventFilter(watched, event)

    def _set_hot_row(self, row: int) -> None:
        if not self.delegate.set_hot_row(row):
            return
        # The whole viewport rather than the two rows: repainting a 272px column a few
        # times a second costs nothing next to remembering which row was hot last.
        self.view.viewport().update()
        self.view.viewport().setCursor(Qt.CursorShape.PointingHandCursor if row >= 0
                                       else Qt.CursorShape.ArrowCursor)

    def current_row(self) -> DeckRow | None:
        return self._model.deck_at(self.view.currentIndex())

    def selected_file(self) -> str:
        """The file the tree has selected, or "" for a directory, nothing, or no tree.

        A path and not an index, because the caller is opening a file and has no business
        knowing how this panel indexes one.
        """
        index = self.files.currentIndex()
        if not index.isValid() or bool(index.data(IS_DIR_ROLE)):
            return ""
        return str(index.data(PATH_ROLE) or "")


class DeckHeader(QWidget):
    """Active deck identity and its primary actions."""

    newSessionRequested = Signal()
    #: The branch chip was clicked. The window answers with the branch list, because it
    #: owns the deck path and this header only ever knew the one name it was handed.
    branchesRequested = Signal()
    #: Show the file tree's current selection in the editor pane.
    openInEditorRequested = Signal()
    #: Everything that applies to the open deck. The window answers this with the
    #: Deck menu itself rather than a second list -- see MainWindow._deck_settings.
    settingsRequested = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        styled(self, "DeckHeader")
        self._theme = theme

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 0, 18, 0)
        layout.setSpacing(14)

        identity = QWidget()
        identity_layout = QVBoxLayout(identity)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.setSpacing(3)

        self.title = RoleLabel("headerTitle", "-", theme)
        self.title.setObjectName("DeckTitle")
        self.path = RoleLabel("headerMeta", "", theme)
        self.path.setObjectName("DeckPath")
        identity_layout.addWidget(_row(self.title, self.path, spacing=10))

        # The branch is lifted out of the meta run and made a control. It was the one
        # thing on this line the user has a question about -- "what else is there?" --
        # and the answer already exists in the repository; everything beside it (a count
        # of changed paths, a count of panes) is a readout with nothing behind it.
        self.branch = LinkLabel("headerMeta", "", theme)
        self.branch.setObjectName("BranchChip")
        self.branch.setToolTip("Branches in this repository")
        self.branch.clicked.connect(self.branchesRequested)
        #: Drawn rather than joined into `meta`, so the gap either side of it is the
        #: layout's and stays even; a leading "   |   " inside the text would space one
        #: side in mono characters and the other in layout pixels.
        self._branch_sep = RoleLabel("headerMeta", "|", theme)
        self._branch_sep.setObjectName("DeckMeta")
        self.meta = RoleLabel("headerMeta", "", theme)
        self.meta.setObjectName("DeckMeta")
        # Its own row rather than _row(), for the alignment. A plain addWidget hands each
        # child the full height of the cell, which for two bare labels is invisible and
        # for a bordered chip is a 30px box around 11px of text.
        meta_row = QWidget()
        meta_layout = QHBoxLayout(meta_row)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(10)
        for widget in (self.branch, self._branch_sep, self.meta):
            meta_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        meta_layout.addStretch(1)
        identity_layout.addWidget(meta_row)
        layout.addWidget(identity)
        layout.addStretch(1)

        # Kept as attributes, not loop-locals. Both were styled, both carried a
        # pointing-hand cursor, and neither had a `clicked.connect` behind it -- which is
        # a button that looks like it works, the one thing this chrome is not allowed to
        # do. `settings` is also the anchor the Deck menu pops up under.
        self.open_in_editor = QPushButton("Open in editor")
        self.settings = QPushButton("Deck settings")
        for button, signal in ((self.open_in_editor, self.openInEditorRequested),
                               (self.settings, self.settingsRequested)):
            button.setObjectName("GhostButton")
            button.setFont(theme.font("button"))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(signal)
            layout.addWidget(button)

        new_session = QPushButton("+  New session")
        new_session.setObjectName("AccentButton")
        new_session.setFont(theme.font("buttonAccent"))
        new_session.setCursor(Qt.CursorShape.PointingHandCursor)
        new_session.clicked.connect(self.newSessionRequested)
        layout.addWidget(new_session)

    def set_deck(self, row: DeckRow | None, sessions: int,
                    branch: str = "", diff: str = "") -> None:
        if row is None:
            self.title.setText("No deck")
            self.path.setText("")
            self.meta.setText("")
            self.set_branch("")
            self._set_browsable(False)
            return
        self.title.setText(row.name)
        self.path.setText(row.path_text)
        self._set_browsable(bool(row.path))
        # Empty branch means git has not answered, or this is not a repository. Either
        # way the run is omitted rather than rendered blank, which would read as clean.
        self.set_branch(branch)
        parts = [part for part in (diff,) if part]
        parts.append("1 pane" if sessions == 1 else f"{sessions} panes")
        self.meta.setText("   |   ".join(parts))

    def set_branch(self, branch: str) -> None:
        """The chip, and the separator that only exists to sit beside it.

        Hidden rather than blanked: an empty bordered box next to a lone "|" is chrome
        for something that is not there, and a deck with no folder has no repository to
        ask. Both widgets go together for that reason.
        """
        self.branch.setText(f"{branch}  ▾" if branch else "")
        self.branch.setVisible(bool(branch))
        self._branch_sep.setVisible(bool(branch))

    def _set_browsable(self, on: bool) -> None:
        """Whether there is a tree behind "Open in editor".

        With nothing selected the button falls back to showing the file tree, and for a
        deck with no folder that is a tab which is itself unavailable -- a button whose
        only remaining behaviour is to point at something switched off. Disabled says the
        same thing without the round trip.
        """
        self.open_in_editor.setEnabled(on)
        self.open_in_editor.setToolTip("" if on else "This deck has no folder to browse")


class SessionPanel(SidePanel):
    """Focused session detail (spec 14.2).

    Turns, tool calls and the context meter are deliberately absent: they are internal to
    the agent process and unobservable from outside it. PID and shell take their place,
    which are exact.
    """

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__("SessionPanel", parent)
        self._theme = theme

        layout = QVBoxLayout(self.full)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QWidget()
        styled(header, "PanelSection")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 0, 16, 0)
        header_layout.setSpacing(8)
        header_layout.addWidget(EyebrowLabel("Session", theme))
        self.session_label = RoleLabel("panelVal", "-", theme)
        header_layout.addWidget(self.session_label)
        header_layout.addStretch(1)
        self.collapse_button = RailButton("»", "Collapse (Ctrl+Alt+B)")
        self.collapse_button.clicked.connect(self.toggle)
        header_layout.addWidget(self.collapse_button)
        header.setFixedHeight(GEOM["tabstrip_h"])
        layout.addWidget(header)

        rail = QVBoxLayout(self.rail)
        rail.setContentsMargins(0, 8, 0, 12)
        rail.setSpacing(10)
        expand = RailButton("«", "Expand (Ctrl+Alt+B)")
        expand.clicked.connect(self.toggle)
        rail.addWidget(expand, 0, Qt.AlignmentFlag.AlignHCenter)
        self.rail_dot = StatusDot(DEAD, theme, 8)
        rail.addWidget(self.rail_dot, 0, Qt.AlignmentFlag.AlignHCenter)
        rail.addStretch(1)

        detail = QWidget()
        styled(detail, "PanelSection")
        self._detail_layout = QVBoxLayout(detail)
        self._detail_layout.setContentsMargins(16, 16, 16, 18)
        self._detail_layout.setSpacing(13)
        self._rows: dict[str, RoleLabel] = {}
        # Launcher and Shell would carry the same value for a shell session, so the
        # fourth row is the command instead -- four distinct observable facts.
        for key in ("Launcher", "Command", "Uptime", "PID"):
            value = RoleLabel("panelVal", "-", theme)
            value.setObjectName("PanelVal")
            key_label = RoleLabel("panelKey", key, theme)
            key_label.setObjectName("PanelKey")
            self._detail_layout.addWidget(_row(key_label, None, value))
            self._rows[key] = value
        layout.addWidget(detail)

        files = QWidget()
        styled(files, "PanelSection")
        files_layout = QVBoxLayout(files)
        files_layout.setContentsMargins(16, 16, 16, 18)
        files_layout.setSpacing(11)
        files_layout.addWidget(EyebrowLabel("Changed files", theme))
        self.files_placeholder = RoleLabel("panelKey", "not a git repository", theme)
        self.files_placeholder.setObjectName("PanelKey")
        self.files_placeholder.setWordWrap(True)
        files_layout.addWidget(self.files_placeholder)
        self._files_layout = QVBoxLayout()
        self._files_layout.setSpacing(7)
        files_layout.addLayout(self._files_layout)
        layout.addWidget(files)

        sessions = QWidget()
        sessions_layout = QVBoxLayout(sessions)
        sessions_layout.setContentsMargins(16, 16, 16, 18)
        sessions_layout.setSpacing(12)
        sessions_layout.addWidget(EyebrowLabel("Sessions in deck", theme))
        self._cards_layout = QVBoxLayout()
        self._cards_layout.setSpacing(8)
        sessions_layout.addLayout(self._cards_layout)
        layout.addWidget(sessions)
        layout.addStretch(1)

    def set_session(self, label: str, launcher: str, uptime: str, pid: str,
                    command: str) -> None:
        self.session_label.setText(label)
        self._rows["Launcher"].setText(launcher)
        self._rows["Command"].setText(command)
        self._rows["Uptime"].setText(uptime)
        self._rows["PID"].setText(pid)

    def clear_session(self) -> None:
        self.session_label.setText("-")
        for value in self._rows.values():
            value.setText("-")

    def set_files(self, files: list[tuple[str, str]], known: bool) -> None:
        """Changed files from git. Not from any terminal -- see git_status.py."""
        while self._files_layout.count():
            item = self._files_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not known:
            self.files_placeholder.setText("not a git repository")
            self.files_placeholder.show()
            return
        if not files:
            self.files_placeholder.setText("working tree clean")
            self.files_placeholder.show()
            return
        self.files_placeholder.hide()
        for mark, path in files:
            mark_label = RoleLabel("panelVal", mark, self._theme)
            mark_label.setFixedWidth(14)
            name = RoleLabel("panelVal", path, self._theme)
            name.setObjectName("PanelVal")
            self._files_layout.addWidget(_row(mark_label, name, None, spacing=8))

    def set_cards(self, entries: list[tuple[str, str, bool]]) -> None:
        """entries: (label, secondary, alive)."""
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for label, secondary, alive in entries:
            card = QWidget()
            styled(card, "SessionCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(11, 10, 11, 10)
            card_layout.setSpacing(6)
            dot = StatusDot(LIVE if alive else DEAD, self._theme)
            title = RoleLabel("paneTitle", label, self._theme)
            card_layout.addWidget(_row(dot, title, None, spacing=8))
            detail = RoleLabel("panelKey", secondary, self._theme)
            detail.setObjectName("PanelKey")
            card_layout.addWidget(detail)
            self._cards_layout.addWidget(card)


class StatusBar(QWidget):
    """Counts we can actually stand behind (spec 14.3).

    The DS shows "N running / N awaiting input". Neither is observable under docking, so
    this reports sessions and decks instead -- both exact.
    """

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        styled(self, "StatusBar")
        self._theme = theme
        self.setFixedHeight(GEOM["statusbar_h"])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 0, 18, 0)
        layout.setSpacing(14)

        self.dot = StatusDot(DEAD, theme)
        layout.addWidget(self.dot)
        self.counts = RoleLabel("statusBar", "0 sessions · 0 decks", theme)
        layout.addWidget(self.counts)
        layout.addWidget(_sep(theme))
        self.shells = RoleLabel("statusBar", "", theme)
        layout.addWidget(self.shells)
        layout.addStretch(1)
        self.focus = RoleLabel("statusBar", "", theme)
        layout.addWidget(self.focus)
        layout.addWidget(_sep(theme))
        # Read from the registry rather than written out again. It was a literal, and a
        # literal is wrong twice over: it goes stale when the chord is rebound in
        # config.json, and it names the Windows spelling on a Mac.
        self.hint = RoleLabel(
            "statusBar", f"{chord_text(by_id('pane.split.right').shortcut)} splits", theme)
        layout.addWidget(self.hint)
        layout.addWidget(_sep(theme))
        # Dimmer than its neighbours on purpose: it is here to be read once, when
        # somebody is writing down what they were running.
        self.version = RoleLabel("statusBar", f"v{__version__}", theme)
        self.version.setObjectName("StatusSep")
        layout.addWidget(self.version)

    def set_counts(self, sessions: int, decks: int) -> None:
        """Live sessions, not panes. An empty pane is not a session."""
        noun = "session" if sessions == 1 else "sessions"
        self.counts.setText(f"{sessions} {noun} · {decks} decks")
        self.dot.set_state(LIVE if sessions else DEAD)

    def set_shells(self, text: str) -> None:
        self.shells.setText(text)

    def set_focus_label(self, text: str) -> None:
        self.focus.setText(text)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(super().sizeHint().width(), GEOM["statusbar_h"])


class SessionTab(QWidget):
    """One tab: state dot, label, launcher, close.

    Also the way back from a hidden pane. A hidden pane keeps its tab precisely so that
    clicking it can put the pane back -- ``PaneTree.set_active`` shows it -- which is why
    the strip is not filtered down to the visible panes.
    """

    activated = Signal(object)
    closeRequested = Signal(object)

    def __init__(self, pane, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        styled(self, "SessionTab")
        #: The pane itself, not its id. Ids are unique only within one deck's tree, so
        #: keying on them made two decks' "pane 0" the same tab.
        self.pane = pane
        self._theme = theme
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(13, 0, 11, 0)
        layout.setSpacing(9)
        self.dot = StatusDot(DEAD, theme, 7)
        layout.addWidget(self.dot)
        self.label = RoleLabel("tab", f"pane {pane.pane_id}", theme)
        layout.addWidget(self.label)
        self.meta = RoleLabel("tabMeta", "", theme)
        layout.addWidget(self.meta)
        close = QPushButton("✕")
        close.setObjectName("MenuButton")
        close.setFlat(True)
        close.setFixedSize(16, 16)
        close.clicked.connect(lambda: self.closeRequested.emit(self))
        layout.addWidget(close)

    def set_state(self, label: str, meta: str, alive: bool, selected: bool,
                  hidden: bool = False) -> None:
        self.label.setText(label)
        self.meta.setText(meta)
        self.dot.set_state(LIVE if alive else DEAD)
        self.setProperty("selected", "true" if selected else "false")
        self.setProperty("hidden", "true" if hidden else "false")
        self.setToolTip("Hidden — click to bring it back" if hidden else "")
        # The dot is left alone deliberately. It reports whether the shell is alive, and a
        # hidden pane's shell is very much alive -- that is the whole difference between
        # hiding a pane and closing it. Dimming the dot would say the opposite.
        #
        # The two labels are repolished as well as the tab carrying the property, and that
        # is not belt and braces: the rule dimming them is a descendant selector, and Qt
        # evaluates one of those when the *child* is polished. Pane.set_zoomed measured the
        # same thing -- repolishing only the ancestor leaves the rule matched and unpainted.
        for widget in (self, self.label, self.meta):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().mousePressEvent(event)
        self.activated.emit(self)


class SessionTabStrip(QWidget):
    """Tabs for the panes in the active deck.

    Composed from widgets rather than subclassing QTabBar as spec 14.5 suggests. A DS tab
    carries an animated dot, two text runs at different families and a close button, and
    QTabBar owns its own layout and paint path -- overriding paintEvent to place all of
    that means reimplementing the widget anyway, without control of the geometry. These
    tabs also do not own pages: which pane shows a session is the tree's business, not the
    tab strip's.
    """

    tabActivated = Signal(object)  # Pane
    tabClosed = Signal(object)     # Pane
    newSessionRequested = Signal()

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        styled(self, "TabStrip")
        self._theme = theme
        self._tabs: dict[int, SessionTab] = {}   # keyed by id(pane)
        self.setFixedHeight(GEOM["tabstrip_h"])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 5, 12, 0)
        layout.setSpacing(4)
        self._tab_layout = QHBoxLayout()
        self._tab_layout.setContentsMargins(0, 0, 0, 0)
        self._tab_layout.setSpacing(4)
        layout.addLayout(self._tab_layout)

        # The sidebar's "+" and this one are the same glyph a few hundred pixels apart,
        # and they were two different controls: a bordered box that outlines in the accent
        # against a bare label whose only hover was dim -> text. #IconButton is the one
        # with the affordance, so both wear it. What they do stays different -- that "+"
        # opens a menu because a deck can be two things, and this one opens a terminal,
        # which is one thing and needs no question asked first.
        add = QPushButton("+")
        add.setObjectName("IconButton")
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        command = by_id("session.new")
        add.setToolTip(f"{command.label}   {chord_text(command.shortcut)}".rstrip())
        add.clicked.connect(self.newSessionRequested)
        layout.addWidget(add)
        layout.addStretch(1)

        self.layout_hint = RoleLabel(
            "statusBar",
            f"{chord_text(by_id('pane.split.right').shortcut)}  "
            f"{chord_text(by_id('pane.split.down').shortcut)}",
            theme)
        layout.addWidget(self.layout_hint)

    def sync(self, panes: list, active) -> None:
        """panes: (pane, label, meta, alive, hidden), in tree order. ``active`` is a Pane.

        Keyed by the pane object. Pane ids restart per deck, so keying by id left one
        deck's tabs standing while another's were shown.
        """
        wanted = {id(pane) for pane, *_ in panes}
        for key in list(self._tabs):
            if key not in wanted:
                tab = self._tabs.pop(key)
                self._tab_layout.removeWidget(tab)
                # Hidden now, not at the next event-loop turn: deleteLater alone leaves the
                # orphan painted over the strip until Qt gets round to it.
                #
                # hide() and not setParent(None), which put a blank window on the desktop.
                # The insertWidget below runs for every tab on every sync, and a layout asks
                # for the show later, on a queue. Reparenting hides a widget without counting
                # as having been asked to, so that show went ahead, on what was by then a
                # widget with no parent: a real window, with the tab's place in the strip read
                # as a place on the screen. First opening a restored deck syncs the old deck's
                # tabs and removes them in one turn, so each of them did this, for about 200 ms
                # over the title bar. An explicit hide is one the queued show respects.
                tab.hide()
                tab.deleteLater()

        for position, (pane, label, meta, alive, hidden) in enumerate(panes):
            tab = self._tabs.get(id(pane))
            if tab is None:
                tab = SessionTab(pane, self._theme)
                tab.activated.connect(lambda t: self.tabActivated.emit(t.pane))
                tab.closeRequested.connect(lambda t: self.tabClosed.emit(t.pane))
                self._tabs[id(pane)] = tab
            self._tab_layout.insertWidget(position, tab)
            tab.set_state(label, meta, alive, pane is active, hidden)
