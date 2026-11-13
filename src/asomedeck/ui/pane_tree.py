"""QSplitter tree of terminal panes (spec 11, milestone 3).

Qt owns all layout. A pane is a ``Pane`` frame wrapping one terminal renderer, and the
tree is a nest of ``QSplitter`` s whose leaves are those frames. Splitting reshapes the
tree; closing a pane collapses any splitter left holding a single child, so the
arrangement never accumulates one-child splitters that add a handle and nothing else.

Every pane owns a shell on a pseudo-console, rendered by an ordinary QWidget. There is no
foreign window in the tree any more, and no web view either, which is why reparenting a
pane is now an ordinary Qt operation rather than the delicate one it used to be.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import board
from .theme import GEOM, Theme, styled
from .widgets.labels import RoleLabel
from .widgets.status_dot import DEAD, LIVE, StatusDot

log = logging.getLogger(__name__)

#: Matches the design system's 1px pane gutters.
HANDLE_WIDTH = 1

#: The zoom button's two faces. Arrows rather than a box because the box is the window's
#: own maximise (chrome.py), and two controls a few pixels apart meaning different things
#: should not look the same.
ZOOM_GLYPH = "⤢"
UNZOOM_GLYPH = "⤡"
ZOOM_TIP = "Zoom pane (Ctrl+Alt+Z)"
UNZOOM_TIP = "Back to the grid (Ctrl+Alt+Z)"

#: Splitters are told to distribute space by handing every child the same large number;
#: Qt normalizes against the real extent.
_EQUAL = 1 << 20


class PaneIds:
    """The numbers panes are known by, handed out once each and never again.

    A counter, which is very nearly all it is. What makes it an object rather than the
    ``_next_id`` integer it replaces is *scope*: an integer on ``PaneTree`` can only be
    unique within one tree, and these numbers have to be unique across every tree in the
    window.

    The reason is ``board.py``. A pane's number is what it claims files under -- handed to
    the shell as ``ASOMEDECK_PANE_ID``, written into ``board.md`` as "pane 3" -- and the
    board belongs to a *working tree*, not to a pane tree. One deck per folder kept
    those the same thing, so a per-tree counter was enough. Two decks over one checkout
    are two trees writing claims into one file, and two panes both called "pane 3" make
    that file unreadable: an agent cannot tell which claim is its own, which is the first
    question the board asks anybody who opens it.

    So the window owns one of these and hands it to every tree it builds. Unique per
    window is stricter than the board needs -- only trees over the same folder can
    actually collide -- and it is stricter deliberately, because it then needs to know
    nothing about which trees those are. A tree built without one gets its own, so a
    ``PaneTree`` on its own still numbers from zero.

    **Nothing is ever released.** Closing a pane does not return its number. Within a tree
    that is already the rule ``test_pane_ids_are_stable_across_closes`` pins; across the
    window it is what the board needs, because a claim naming a pane that has exited is
    *stale*, and the board's whole instruction to its reader is to notice that and delete
    the line. Handing the number to a new session would turn a stale claim into one that
    appears to belong to a pane that never wrote it -- worse than a stale claim, and worse
    than no claim at all.

    What this cannot cover is two AsomeDeck processes over one folder: nothing is shared
    between them, and what would share it is the broker process ``db.py`` and
    ``conpty.py`` both name as the v2 question. The board is a convention, and this makes
    it legible within one window -- the only scope anything else in this app is legible
    in either.
    """

    def __init__(self) -> None:
        #: Every number ever handed out, including those whose panes have since closed.
        #: It only grows, which is the "never again" above.
        self._issued: set[int] = set()

    def take(self, wanted: object = None) -> int:
        """The next number, preferring ``wanted`` where it can be honoured.

        ``wanted`` is a saved layout's ``pane_id``, and it is a preference and not a
        demand -- the argument the old ``_restored_id`` made, widened from one tree to the
        window. A duplicated id is worse than a changed one: ``_on_session_row`` finds a
        pane by number and would land on whichever one it met first.

        Two decks restoring layouts written when numbers were per-tree will both ask for
        0, 1, 2. The first is honoured and the second renumbered, and the renumbering
        sticks the moment that deck's layout is next saved.

        Anything that is not a whole number at or above zero is ignored, which covers a
        hand-edited layout. ``bool`` is excluded by name because it is an ``int``, and
        ``"pane_id": true`` would otherwise restore as pane 1 -- a real pane number,
        arrived at from a value that is not one.

        The fresh number is the lowest never issued rather than one past the highest.
        Gaps only ever come from a layout that named a number nothing else reached, and
        leaving them unfilled would push the numbers a user actually reads upward for no
        gain -- they are numbers instead of tokens precisely so they stay small.
        """
        if (isinstance(wanted, int) and not isinstance(wanted, bool)
                and wanted >= 0 and wanted not in self._issued):
            self._issued.add(wanted)
            return wanted
        fresh = 0
        while fresh in self._issued:
            fresh += 1
        self._issued.add(fresh)
        return fresh


class PaneHeader(QWidget):
    """The pane's title bar, and the only place a double-click means "zoom".

    A class rather than a bare QWidget for one reason: the gesture has to be scoped to this
    strip. Handling it on ``Pane`` would catch double-clicks anywhere in the pane, including
    the terminal, where a double-click selects a word -- so reading a path would zoom the
    pane out from under you.

    Neither override consumes an ordinary press. QWidget ignores those, so a single click
    still propagates to ``Pane.mousePressEvent`` and clicking the header -- with either
    button -- goes on activating the pane. The menu relies on that: its lower rows are the
    window's own actions, which aim at whichever pane is active.
    """

    doubleClicked = Signal()
    #: Right-click on the strip, in global coordinates. Scoped here for the reason the
    #: double-click is: on ``Pane`` it would fire inside the terminal as well, where the
    #: right button may belong to the program running there.
    contextMenuRequested = Signal(object)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().mouseDoubleClickEvent(event)
        self.doubleClicked.emit()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Unconditionally ours, unlike the terminal's.

        ``TerminalView`` has to ask ``emulator.mouse_tracking`` first, because a program
        that claimed the mouse is entitled to the right button. Nothing runs in a title
        bar, so there is nobody to defer to.
        """
        event.accept()
        self.contextMenuRequested.emit(event.globalPos())


class Pane(QWidget):
    """One leaf of the tree: a focus ring around one terminal.

    The ring is a permanent 1px border that only ever changes colour. Toggling a border
    on and off would change the content rect and reflow the terminal on every focus
    change -- less catastrophic now than under docking, but still visible churn.
    """

    activated = Signal(object)
    #: The pane's own close button was pressed. The tree does not act on it: ending the
    #: shell belongs to whoever owns the session, which is the window.
    closeRequested = Signal(object)
    #: The zoom button or a double-click on the header. Unlike closeRequested the tree
    #: *does* act on this one, because zoom has no owner outside the tree -- it is which
    #: siblings are hidden, and nothing else knows or needs to.
    zoomRequested = Signal(object)
    #: Right-click on the header, as ``(pane, global point)``. Forwarded rather than acted
    #: on for the reason closeRequested is: half the rows the menu wants are the window's
    #: commands, and a tree that built them would have to know what contains it.
    menuRequested = Signal(object, object)

    def __init__(self, bg_color: str = "#1e1e1e", theme: Theme | None = None,
                 launcher_id: str = "", cwd: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bg_color = bg_color
        #: When this pane's shell started, for the uptime readout. Kept on the pane
        #: rather than keyed by pid, because the pid only exists once the page has
        #: loaded and the shell has actually spawned.
        self.started_at: float | None = None
        #: What the program in this pane last called itself, via OSC 0/2. Empty until it
        #: says something. Kept on the pane because the header, the tab strip and the
        #: session panel all show it and none of them owns it.
        self.session_title = ""
        #: When the title last changed, for telling a spinner from a resting state. None
        #: until the program says anything, which is not the same as "long ago".
        self.title_changed_at: float | None = None
        #: Renames since this pane last came to rest. How often, where ``title_changed_at``
        #: is how recently -- together they separate a turn that ran and stopped from a
        #: program that came up ready. Zeroed each time the pane settles.
        self.title_changes = 0
        #: Last state the dot was told to show, so a tick that changes nothing repaints
        #: nothing.
        self.session_state = DEAD
        #: The raw reading from ``activity.state_for``, before the two flags below
        #: overrule it. Kept apart from ``session_state`` because "the turn just finished"
        #: has to be caught as an edge, and ``session_state`` is the *displayed* state,
        #: which those flags themselves set -- watching it for transitions would mean
        #: watching its own output.
        self.activity_state = DEAD
        #: Something happened here that you have not seen yet: the program rang the bell,
        #: or a turn finished. Set by an event, cleared by looking at the pane. They are
        #: what lets a deck row you are not currently in still say something useful.
        self.attention = False
        self.finished = False
        #: Which launcher filled this pane. Persisted, so a restored layout knows what to
        #: relaunch into each slot (spec 9, layout.tree_json).
        self.launcher_id = launcher_id
        #: Where this pane's shell was told to start, or "" for the deck's own folder.
        #: The empty string is the meaningful value: it means "wherever this deck is",
        #: so a deck that is later pointed somewhere else takes its panes with it,
        #: while a pane that was deliberately sent elsewhere stays where it was put.
        #: Persisted alongside ``launcher_id`` and for the same reason -- the two together
        #: are the whole description of a terminal a restored layout can bring back.
        self.cwd = cwd
        self._theme = theme or Theme()
        self._active = False
        self._zoomed = False
        #: Mirrors ``PaneTree``'s hidden set onto the pane, the way ``_zoomed`` mirrors the
        #: zoom. The tree decides; this is what everything drawing a pane can read without
        #: asking which tree holds it.
        self._hidden = False
        #: Stable for the pane's lifetime. List positions shift when panes close, so
        #: anything that names a pane -- logs, tree shapes, tests -- needs this instead.
        self.pane_id = 0
        #: What the person calls this pane -- "designer", "tests" -- or "" for nothing.
        #: Given in the window and never by the program inside, which is what makes it
        #: work the same whatever agent is running here. Already in ``board.clean_name``
        #: form; the window checks it is unique among the panes over its folder, because
        #: only the window knows which those are. Persisted, like ``cwd``.
        self.name = ""
        #: What a restored layout said this pane held, until the window fills it. Only
        #: an editor pane sets it; a terminal pane is described by ``launcher_id``.
        self.restore_kind = ""
        self.restore_path = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)  # the ring lives here, always
        layout.setSpacing(0)

        self.header = self._build_header()
        layout.addWidget(self.header)

        #: A placeholder until the renderer is attached. The pane exists before its
        #: shell does, because the layout is restored first and filled afterwards.
        self.body: QWidget = QWidget(self)
        layout.addWidget(self.body, 1)

        self.footer = self._build_footer()
        layout.addWidget(self.footer)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._apply_ring()

    def _build_header(self) -> QWidget:
        """Spec 14.2 pane chrome. Every field here is directly observable."""
        bar = PaneHeader()
        bar.doubleClicked.connect(lambda: self.zoomRequested.emit(self))
        bar.contextMenuRequested.connect(lambda point: self.menuRequested.emit(self, point))
        styled(bar, "PaneHeader")
        bar.setFixedHeight(GEOM["pane_header_h"])
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(9)

        self.dot = StatusDot(DEAD, self._theme, 6)
        row.addWidget(self.dot)
        # Before the title and apart from it. The title is the program's and changes
        # every turn; the name is the person's and does not, so it is the one thing in
        # this strip that reads the same each time you look.
        self.name_label = RoleLabel("paneTitle", "", self._theme)
        self.name_label.setObjectName("PaneName")
        self.name_label.hide()
        row.addWidget(self.name_label)
        self.title_label = RoleLabel("paneTitle", "-", self._theme)
        self.title_label.setObjectName("PaneTitle")
        row.addWidget(self.title_label)
        self.shell_chip = QLabel("")
        self.shell_chip.setObjectName("ShellChip")
        self.shell_chip.setFont(self._theme.font("deckMeta"))
        row.addWidget(self.shell_chip)
        self.cmd_label = RoleLabel("paneCmd", "", self._theme)
        self.cmd_label.setObjectName("PaneCmd")
        row.addWidget(self.cmd_label)
        row.addStretch(1)
        self.geometry_label = RoleLabel("paneCmd", "", self._theme)
        self.geometry_label.setObjectName("PaneGeometry")
        row.addWidget(self.geometry_label)
        self.zoom_button = QPushButton(ZOOM_GLYPH)
        self.zoom_button.setObjectName("PaneZoom")
        self.zoom_button.setFlat(True)
        self.zoom_button.setCursor(Qt.CursorShape.PointingHandCursor)
        # A clicked QPushButton keeps the keyboard by default, so Space would toggle the
        # zoom again and the terminal you just enlarged would not be the thing you are
        # typing into. PaneClose can afford that policy because the pane is going away.
        self.zoom_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.zoom_button.setToolTip(ZOOM_TIP)
        self.zoom_button.clicked.connect(lambda: self.zoomRequested.emit(self))
        row.addWidget(self.zoom_button)
        close = QPushButton("✕")
        close.setObjectName("PaneClose")
        close.setFlat(True)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip("Close pane (Ctrl+Shift+W)")
        close.clicked.connect(lambda: self.closeRequested.emit(self))
        row.addWidget(close)
        return bar

    def _build_footer(self) -> QWidget:
        bar = QWidget()
        styled(bar, "PaneFooter")
        bar.setFixedHeight(GEOM["pane_footer_h"])
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(12)
        self.pid_label = RoleLabel("paneFooter", "", self._theme)
        row.addWidget(self.pid_label)
        self.cwd_label = RoleLabel("paneFooter", "", self._theme)
        row.addWidget(self.cwd_label)
        row.addStretch(1)
        # Spec 14.2 cuts the context meter as unobservable and gives its space to uptime.
        self.uptime_label = RoleLabel("paneFooter", "", self._theme)
        row.addWidget(self.uptime_label)
        return bar

    def set_session_info(self, *, title: str = "", shell: str = "", cmd: str = "",
                         pid: str = "", cwd: str = "", alive: bool = False) -> None:
        self.title_label.setText(title or "empty")
        self.shell_chip.setText(shell)
        self.shell_chip.setVisible(bool(shell))
        self.cmd_label.setText(cmd)
        self.pid_label.setText(pid)
        self.cwd_label.setText(cwd)
        self.dot.set_state(LIVE if alive else DEAD)
        self.session_state = self.activity_state = LIVE if alive else DEAD

    def set_name(self, name: str) -> None:
        """Show a name in the header, or take it away. The caller has validated it."""
        self.name = name
        self.name_label.setText(name)
        self.name_label.setVisible(bool(name))

    @property
    def caption(self) -> str:
        """What to call this pane wherever panes are listed: tabs, cards, the sidebar.

        The name when there is one, because it is the thing the person chose to tell
        panes apart by. Then what the program says it is, then the number -- the order
        every one of those lists already used, with the name put in front of it.
        """
        return self.name or self.session_title or f"pane {self.pane_id}"

    @property
    def cwd_text(self) -> str:
        return self.cwd_label.text()

    @property
    def kind(self) -> str:
        """"terminal" or "editor" -- whatever the attached body says it is.

        Asked of the body rather than stored, so it cannot drift out of step with what
        the pane is actually holding, and read by name rather than ``isinstance`` because
        main_window imports both renderers lazily: a missing terminal backend has to
        surface as a dialog, which it cannot do if importing it is a precondition for
        asking a pane what it is.
        """
        return getattr(self.body, "pane_kind", "terminal")

    @property
    def file_path(self) -> str:
        """The file an editor pane is showing, or "" for anything else."""
        return str(getattr(self.body, "file_path", "") or "")

    def set_session_state(self, state: str) -> None:
        """Show a session state on the dot, if it is not already showing it."""
        if state == self.session_state:
            return
        self.session_state = state
        self.dot.set_state(state)

    def note_bell(self) -> None:
        """The program rang. Remember it until somebody looks at this pane."""
        self.attention = True

    def clear_alerts(self) -> bool:
        """Forget what was waiting to be seen. True if there was anything to forget."""
        if not (self.attention or self.finished):
            return False
        self.attention = self.finished = False
        return True

    def set_uptime(self, text: str) -> None:
        self.uptime_label.setText(text)

    def set_geometry_label(self, text: str) -> None:
        self.geometry_label.setText(text)

    @property
    def bg_color(self) -> str:
        return self._bg_color

    @property
    def has_session(self) -> bool:
        """Whether a shell is actually running in this pane."""
        alive = getattr(self.body, "is_alive", None)
        return bool(alive()) if callable(alive) else False

    def attach_body(self, widget: QWidget) -> None:
        """Swap the placeholder for the real renderer."""
        self.layout().replaceWidget(self.body, widget)
        self.body.deleteLater()
        self.body = widget
        widget.setParent(self)
        widget.show()

    def focus_body(self) -> None:
        focus = getattr(self.body, "focus_terminal", None)
        focus() if callable(focus) else self.body.setFocus()

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self._apply_ring()

    @property
    def zoomed(self) -> bool:
        return self._zoomed

    def set_zoomed(self, zoomed: bool) -> None:
        """Show, on the pane itself, that it is the one filling the tree.

        The button has to change: it is the same button for both directions, and one that
        still says "zoom" while already zoomed is a control lying about what it does.

        The button is repolished as well as the pane that carries the property, and that is
        not belt and braces. The rule lighting it is ``Pane[zoomed="true"] #PaneZoom``, and
        Qt evaluates a descendant selector when the *child* is polished -- repolishing only
        the ancestor leaves the rule matched and unpainted. Measured: the glyph stayed
        ``#6d6d6a`` through every zoom until the button itself was unpolished.
        """
        if zoomed == self._zoomed:
            return
        self._zoomed = zoomed
        self.zoom_button.setText(UNZOOM_GLYPH if zoomed else ZOOM_GLYPH)
        self.zoom_button.setToolTip(UNZOOM_TIP if zoomed else ZOOM_TIP)
        self.setProperty("zoomed", "true" if zoomed else "false")
        for widget in (self, self.zoom_button):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    @property
    def hidden(self) -> bool:
        return self._hidden

    def set_hidden(self, hidden: bool) -> None:
        """Record that the tree has taken this pane off the grid.

        The pane itself is not visible while this is true, so unlike ``set_zoomed`` there
        is nothing here to repaint. It is set anyway because the *tab* for this pane is
        very much on screen, and the chrome that draws it reads the pane rather than
        hunting for the tree that owns it.
        """
        if hidden == self._hidden:
            return
        self._hidden = hidden
        self.setProperty("hidden", "true" if hidden else "false")

    def _apply_ring(self) -> None:
        # A dynamic property, so ui/theme.py owns the colour and this owns only the
        # state. Qt does not restyle on a property change by itself.
        for widget in (self, self.header, self.footer):
            widget.setProperty("active", "true" if self._active else "false")
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().mousePressEvent(event)
        self.activated.emit(self)

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().focusInEvent(event)
        self.activated.emit(self)


class PaneTree(QWidget):
    """The splitter tree and its active-pane bookkeeping."""

    paneActivated = Signal(object)   # Pane
    paneRemoved = Signal(object)     # Pane
    paneCloseRequested = Signal(object)  # Pane
    emptied = Signal()
    #: Which pane now fills the tree, or None for the grid. Deliberately not folded into
    #: layoutChanged: that signal means "what to_tree() returns is different", and zoom
    #: changes nothing it serialises. Wiring zoom into it would save the layout on every
    #: keypress and, worse, imply zoom was part of the arrangement.
    zoomChanged = Signal(object)        # Pane | None
    #: The hidden set changed. A separate signal from layoutChanged for exactly the reason
    #: zoomChanged is one: hiding a pane changes nothing to_tree() serialises, and folding
    #: it in would save the layout on every hide and imply hiding was part of the
    #: arrangement rather than a view of it.
    hiddenChanged = Signal()
    #: A right-click on some pane's header, as ``(pane, global point)``. Passed straight
    #: through from the pane for the reason paneCloseRequested is: the rows below the
    #: separator are the window's own commands.
    paneMenuRequested = Signal(object, object)
    #: The arrangement changed and what to_tree() would return is now different: a pane
    #: added, a pane closed, or a splitter dragged. Everything to_tree() serialises is
    #: covered by one of the three, which is why this is one signal rather than three --
    #: the only listener is "persist the layout", and it does not care which happened.
    layoutChanged = Signal()

    def __init__(self, theme: Theme | None = None, ids: PaneIds | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme or Theme()
        self._panes: list[Pane] = []
        self._active: Pane | None = None
        #: Shared with every other tree in the window when the window builds it, so that
        #: two decks over one folder cannot both hold a "pane 3" to claim files as. Its
        #: own when nobody supplies one, which is what keeps a standalone tree -- and the
        #: whole of ``tests/test_pane_tree.py`` -- numbering from zero.
        self._ids = ids if ids is not None else PaneIds()
        #: The pane filling the tree, or None. Transient by design -- a focus mode, not
        #: part of the arrangement, so it is never persisted and never restored.
        self._zoomed: Pane | None = None
        #: Panes the user has taken off the grid. Their shells keep running -- that is the
        #: entire difference from closing one -- and their tabs stay in the strip, which is
        #: how they come back. Transient like the zoom, and never serialised.
        self._hidden: set[Pane] = set()
        #: True while panes are being shown and hidden, by zoom or by hide. Hiding the pane
        #: that holds focus makes Qt hand focus to the next visible widget, and that
        #: focusChanged is one we caused: reacting to it re-enters set_active, which moves
        #: the zoom again while the first move is still half done. Measured without it, one
        #: tab click produced three nested activations and three full chrome refreshes.
        self._reflowing = False
        #: Splitter -> the sizes it had before it was collapsed onto one child. Two jobs:
        #: putting the proportions back on unzoom, and standing in for sizes() while
        #: zoomed, because a hidden child reports 0 and _apply_sizes rejects any list with
        #: a zero in it -- so a save taken mid-zoom does not come back collapsed, it comes
        #: back with no proportions at all.
        #:
        #: Insertion order is leaf-to-root and is load-bearing: unzoom walks it backwards.
        self._zoom_sizes: dict[QSplitter, list[int]] = {}
        #: The same record, kept for the same two jobs, for panes hidden rather than zoomed
        #: away. Both are needed: a pane can be hidden in a tree that is not zoomed, and
        #: _serialise has to find real proportions either way.
        #:
        #: Insertion order is leaf-to-root here too, and read backwards for the same
        #: reason.
        self._hidden_sizes: dict[QSplitter, list[int]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._root = self._new_splitter(Qt.Orientation.Horizontal)
        self._root.setObjectName("PaneGrid")
        layout.addWidget(self._root)

        # Focus lands on the terminal widget *inside* a pane, and Qt does not tell an
        # ancestor about it -- Pane.focusInEvent only fires when the Pane itself is the
        # focus target, which it never is once a body is attached. So the pane the user is
        # typing in was not the active one, and everything keyed on `active` acted
        # somewhere else: splitting split the last pane created, and Ctrl+Shift+W closed
        # it. Watching the application's own focus signal catches any focusable child,
        # including whatever a future body puts inside itself.
        instance = QApplication.instance()
        if instance is not None:
            instance.focusChanged.connect(self._on_focus_changed)


    # --- queries -------------------------------------------------------------------

    @property
    def panes(self) -> list[Pane]:
        return list(self._panes)

    @property
    def active(self) -> Pane | None:
        return self._active

    def count(self) -> int:
        return len(self._panes)

    @property
    def hidden(self) -> list[Pane]:
        """The panes off the grid, in tree order."""
        return [pane for pane in self._panes if pane in self._hidden]

    def hidden_count(self) -> int:
        return len(self._hidden)

    def is_hidden(self, pane: Pane) -> bool:
        return pane in self._hidden

    def can_hide(self, pane: Pane | None) -> bool:
        """Whether this pane can come off the grid.

        The last visible one cannot. Hiding it would leave a grid with nothing on it to
        right-click, so the menu that did it could not undo it -- and a window in a state
        only one control can get out of is not worth the row.
        """
        if pane is None or pane not in self._panes or pane in self._hidden:
            return False
        return sum(1 for candidate in self._panes if candidate not in self._hidden) > 1

    def describe(self) -> str:
        """Bracketed tree shape by pane id, e.g. ``H[0 V[1 2]]``. For tests and logging."""
        return self._describe(self._root)

    def _describe(self, widget: QWidget) -> str:
        if isinstance(widget, QSplitter):
            axis = "H" if widget.orientation() == Qt.Orientation.Horizontal else "V"
            inner = " ".join(self._describe(widget.widget(i)) for i in range(widget.count()))
            return f"{axis}[{inner}]"
        if isinstance(widget, Pane) and widget in self._panes:
            return str(widget.pane_id)
        return "?"

    # --- mutation ------------------------------------------------------------------

    def add_pane(self, bg_color: str = "#1e1e1e",
                 orientation: Qt.Orientation | None = None,
                 launcher_id: str = "", cwd: str = "") -> Pane:
        """Add a pane, splitting the active one. The first pane becomes the whole tree.

        ``cwd`` is carried, not acted on. The tree does not start shells -- the window
        does -- so this is the same kind of parameter ``launcher_id`` is: something a
        pane is labelled with at birth, so that whoever fills it later knows what was
        asked for.
        """
        # Before anything reads the tree. Splitting while zoomed would put the new pane
        # behind the zoomed one, so Ctrl+Shift+D would appear to do nothing -- and the
        # sizes recorded for the zoom would describe splitters that no longer hold what
        # they held.
        self.set_zoom(None)
        pane = Pane(bg_color, self._theme, launcher_id, cwd, parent=self)
        pane.pane_id = self._ids.take()
        pane.activated.connect(self.set_active)
        pane.closeRequested.connect(self.paneCloseRequested)
        pane.zoomRequested.connect(self.toggle_zoom)
        pane.menuRequested.connect(self.paneMenuRequested)

        if not self._panes:
            self._root.addWidget(pane)
        else:
            target = self._active or self._panes[-1]
            splitter = self._splitter_of(target)
            wanted = orientation if orientation is not None else splitter.orientation()
            index = splitter.indexOf(target)

            if splitter.count() == 1:
                # A lone child can just change axis. Nesting here would add a splitter that
                # owns one thing and means nothing.
                splitter.setOrientation(wanted)
                splitter.addWidget(pane)
            elif splitter.orientation() == wanted:
                splitter.insertWidget(index + 1, pane)
            else:
                inner = self._new_splitter(wanted)
                # replaceWidget hands `target` back with a null parent. That used to be
                # dangerous -- destroying a container destroyed the foreign window inside
                # it -- and is now an ordinary Qt reparent.
                splitter.replaceWidget(index, inner)
                inner.addWidget(target)
                inner.addWidget(pane)
                splitter = inner

            self._equalize(splitter)

        self._panes.append(pane)
        # The splitters just changed shape, so anything recorded about their proportions
        # describes a tree that no longer exists. Clearing it costs the hidden panes their
        # exact restoration and keeps them hidden, which is the half that matters: adding a
        # pane is not a reason to put back the ones somebody put away.
        self._hidden_sizes.clear()
        if self._hidden:
            self._apply_visibility()
        self.set_active(pane)
        log.info("added pane %d, tree=%s", pane.pane_id, self.describe())
        self.layoutChanged.emit()
        return pane

    def close_pane(self, pane: Pane) -> None:
        """Remove an *empty* pane and collapse whatever that leaves behind.

        The caller owns the session: closing the shell is its job, not the tree's.
        """
        if pane not in self._panes:
            return
        # First, and not only when the zoomed pane is the one going: _collapse deletes
        # splitters, and a _zoom_sizes key pointing at a deleted C++ object is a crash the
        # next time anything walks it. Closing a pane also changes the arrangement, which
        # is worth seeing.
        self.set_zoom(None)
        # And the hidden bookkeeping for the same reason, which is not only tidiness: a
        # _hidden_sizes key pointing at a splitter _collapse has deleted is a fault the
        # next time anything walks the dict.
        self._hidden.discard(pane)
        pane.set_hidden(False)
        self._hidden_sizes.clear()
        splitter = self._splitter_of(pane)
        self._panes.remove(pane)
        pane.setParent(None)
        pane.deleteLater()
        self._collapse(splitter)
        if self._hidden:
            if len(self._hidden) == len(self._panes):
                # Closing the last *visible* pane would leave a grid with nothing on it and
                # every survivor hidden. can_hide refuses to create that state, so closing
                # must not create it round the back: the panes that are left come back.
                self.show_all_panes()
            else:
                self._apply_visibility()

        if self._active is pane:
            self._active = None
            if self._panes:
                self.set_active(self._panes[-1])
        self.paneRemoved.emit(pane)
        log.info("closed pane %d, tree=%s", pane.pane_id, self.describe())
        self.layoutChanged.emit()
        if not self._panes:
            self.emptied.emit()

    def _on_focus_changed(self, _old, new) -> None:
        if self._reflowing:
            # Our own doing: hiding the focused pane moved the keyboard. Following it
            # would move the zoom, or the hide, mid-move.
            return
        pane = self._pane_owning(new)
        if pane is not None:
            self.set_active(pane)

    def _pane_owning(self, widget) -> Pane | None:
        """Walk up from a focused widget to the pane containing it, if it is ours.

        Every tree is connected to the same application signal, so this is also what keeps
        one deck's tree from claiming a focus event belonging to another's.
        """
        node = widget
        while node is not None:
            if isinstance(node, Pane) and node in self._panes:
                return node
            node = node.parentWidget()
        return None

    def set_active(self, pane: Pane | None) -> None:
        if pane is not None and pane not in self._panes:
            return
        # A hidden pane cannot be the active one -- focus_body() on a hidden widget does
        # nothing, so selecting its tab would look broken. Showing it here is the whole of
        # the restore gesture: the tab strip already routes a click into set_active, so it
        # became the way back without a line of new wiring.
        if pane is not None and pane in self._hidden:
            self.show_pane(pane)
        # Zoom follows the selection. The tab strip stays visible while zoomed and is the
        # obvious way to change pane, but the pane it selects is hidden -- focus_body()
        # on a hidden widget does nothing, so the tab would look broken. Moving the zoom
        # keeps the strip working as a switcher and keeps the mode you asked for.
        if self._zoomed is not None and pane is not None and pane is not self._zoomed:
            # set_zoom activates what it zooms, so it has already done the rest of this
            # call. Falling through would repeat it and emit paneActivated a second time,
            # which is a second full chrome refresh for one tab click.
            self.set_zoom(pane)
            return
        self._active = pane
        for candidate in self._panes:
            candidate.set_active(candidate is pane)
        if pane is not None:
            self.paneActivated.emit(pane)

    def focus_active(self) -> None:
        if self._active is not None:
            self._active.focus_body()

    # --- zoom ------------------------------------------------------------------------

    @property
    def zoomed(self) -> Pane | None:
        """The pane currently filling the tree, or None for the grid."""
        return self._zoomed

    def toggle_zoom(self, pane: Pane | None) -> None:
        """Zoom this pane, or return to the grid if it is already the zoomed one."""
        if pane is None or pane not in self._panes:
            return
        self.set_zoom(None if pane is self._zoomed else pane)

    def set_zoom(self, pane: Pane | None) -> None:
        """Give one pane the whole tree by hiding its siblings, or put them all back.

        Hiding rather than reparenting. The pane stays exactly where it is in the splitter
        tree, which is what keeps ``to_tree`` serialising the real arrangement and keeps
        ``_splitter_of`` finding a real parent -- a pane lifted out into a stacked layout
        has neither, so the next split would land at the root instead of beside its
        neighbour.

        Always unzooms first, even when zooming somewhere else. Restoring from a
        half-collapsed tree is a special case nobody would maintain, and the show/hide is
        cheap.
        """
        if pane is not None and pane not in self._panes:
            return
        # Zoom implies visible. Reachable in one click: a hidden pane keeps its tab, and
        # selecting it while another pane is zoomed arrives here through set_active.
        if pane is not None and pane in self._hidden:
            self.show_pane(pane)

        previous, self._zoomed = self._zoomed, pane
        self._reflowing = True
        try:
            # Root outward, which is why _zoom_sizes is walked backwards. Giving an inner
            # splitter its children back while the root is still collapsed lays them out
            # at the zoomed width and corrects them a moment later -- a pty resize each
            # way for panes that never moved. Measured on H[0 V[1 2]]: leaf-first takes
            # both panes in the zoomed branch through 149 columns and back to 74;
            # root-first resizes each of them once, to the width they are keeping.
            for splitter, sizes in reversed(list(self._zoom_sizes.items())):
                for index in range(splitter.count()):
                    child = splitter.widget(index)
                    # setVisible against the predicate, not show(). A pane the user hid is
                    # off the grid for a reason that has nothing to do with the zoom, and
                    # showing it here is precisely the fight _has_visible_pane settles.
                    child.setVisible(self._has_visible_pane(child))
                # Absolute pixels from before, and that is fine: QSplitter treats sizes as
                # hints and normalises them, so a window resized while zoomed comes back
                # proportional rather than short.
                splitter.setSizes(sizes)
            self._zoom_sizes.clear()

            if pane is not None:
                # Walk to the root hiding everything off the path. A pane two splitters
                # deep has siblings at both levels, and leaving the outer ones showing
                # would zoom it to its own branch rather than to the tree.
                node: QWidget = pane
                while node is not self._root:
                    splitter = self._splitter_of(node)
                    self._zoom_sizes[splitter] = list(splitter.sizes())
                    for index in range(splitter.count()):
                        child = splitter.widget(index)
                        child.show() if child is node else child.hide()
                    node = splitter
        finally:
            self._reflowing = False

        if previous is not None:
            previous.set_zoomed(False)
        if pane is not None:
            pane.set_zoomed(True)
            # Zoomed implies active. Everything keyed on `active` -- split, close, the
            # status bar's focus label -- would otherwise be aiming at a pane that is not
            # on screen, which is reachable by clicking an inactive pane's zoom button.
            # Terminates: set_active only moves the zoom when the pane is not already the
            # zoomed one, and by here it is.
            if pane is not self._active:
                self.set_active(pane)

        if previous is not pane:
            self.zoomChanged.emit(pane)

    # --- hiding ----------------------------------------------------------------------
    #
    # Zoom's opposite: instead of one pane taking the grid, one pane leaves it and the
    # rest take its space. The shell goes on running, which is the whole point -- closing
    # the pane is the gesture that ends it, and a watcher you do not want to look at is
    # not a watcher you want to kill.
    #
    # Coming back is the tab strip. A hidden pane keeps its tab, and set_active shows it
    # again, so there is no second control to find and no state the window can be left in
    # with no way out.

    def hide_pane(self, pane: Pane) -> None:
        """Take one pane off the grid, leaving its shell running."""
        if not self.can_hide(pane):
            return
        # Before anything reads the tree, and for add_pane's reason: while zoomed the
        # splitters are collapsed onto one child, so the sizes recorded below would
        # describe the zoom rather than the grid the user arranged.
        self.set_zoom(None)

        node: QWidget = pane
        while node is not self._root:
            splitter = self._splitter_of(node)
            # setdefault, not assignment: the first hide under a splitter is the one that
            # saw the grid. A second would be reading proportions the first already skewed,
            # because a hidden child reports no size.
            self._hidden_sizes.setdefault(splitter, list(splitter.sizes()))
            node = splitter

        self._hidden.add(pane)
        pane.set_hidden(True)
        self._apply_visibility()
        if self._active is pane:
            self.set_active(self._next_visible(pane))
        log.info("hid pane %d, %d hidden, tree=%s", pane.pane_id, len(self._hidden),
                 self.describe())
        self.hiddenChanged.emit()

    def show_pane(self, pane: Pane) -> None:
        """Put one hidden pane back on the grid."""
        if pane not in self._hidden:
            return
        self._hidden.discard(pane)
        pane.set_hidden(False)
        self._apply_visibility()
        self._restore_hidden_sizes()
        log.info("showed pane %d, %d hidden", pane.pane_id, len(self._hidden))
        self.hiddenChanged.emit()

    def show_all_panes(self) -> None:
        """Put every hidden pane back, in one go and one signal."""
        if not self._hidden:
            return
        for pane in list(self._hidden):
            pane.set_hidden(False)
        self._hidden.clear()
        self._apply_visibility()
        self._restore_hidden_sizes()
        log.info("showed every hidden pane, tree=%s", self.describe())
        self.hiddenChanged.emit()

    def toggle_hidden(self, pane: Pane | None) -> None:
        """Hide this pane, or put it back if it is already hidden."""
        if pane is None or pane not in self._panes:
            return
        self.show_pane(pane) if pane in self._hidden else self.hide_pane(pane)

    def _next_visible(self, pane: Pane) -> Pane | None:
        """The pane after this one that is still on the grid, wrapping.

        Tree order rather than the last pane created, which is what close_pane settles
        for. Closing is destructive and rare; hiding is meant to be flicked through, and
        landing next door is what makes hiding several in a row feel like moving along a
        row rather than being thrown back to the end of it.
        """
        if not self._panes:
            return None
        start = self._panes.index(pane) if pane in self._panes else -1
        for offset in range(1, len(self._panes) + 1):
            candidate = self._panes[(start + offset) % len(self._panes)]
            if candidate not in self._hidden:
                return candidate
        return None

    def _has_visible_pane(self, node: QWidget) -> bool:
        """Whether anything under here is still meant to be on screen.

        The one predicate both reasons a widget is off the grid go through. Two
        independent show/hide passes fight, and the bug has a name: unzoom called show()
        on every child of every splitter it had collapsed, which put a pane the user had
        deliberately hidden back on the grid.

        A splitter is worth showing only for its contents, so a branch whose panes are all
        hidden is hidden itself. Otherwise it keeps its share of the parent and hiding a
        pane inside a nested branch reclaims nothing.
        """
        if isinstance(node, QSplitter):
            return any(self._has_visible_pane(node.widget(i)) for i in range(node.count()))
        return isinstance(node, Pane) and node not in self._hidden

    def _apply_visibility(self) -> None:
        """Show or hide every splitter child to match the hidden set.

        Guarded by _reflowing for set_zoom's reason: hiding the pane that holds focus
        makes Qt move the keyboard, and following that focusChanged would re-enter
        set_active in the middle of this.
        """
        self._reflowing = True
        try:
            self._apply_to(self._root)
        finally:
            self._reflowing = False

    def _apply_to(self, splitter: QSplitter) -> None:
        # Root outward, which is the order set_zoom argues for at length: an inner
        # splitter given its children back while the root is still collapsed lays them out
        # at the wrong width and corrects them a moment later, a pty resize each way for
        # panes that never moved.
        for index in range(splitter.count()):
            child = splitter.widget(index)
            child.setVisible(self._has_visible_pane(child))
            if isinstance(child, QSplitter):
                self._apply_to(child)

    def _restore_hidden_sizes(self) -> None:
        """Put the proportions back, once nothing is hidden any more.

        Only then, and that is a deliberate simplification rather than an oversight. A
        record is taken when the *first* pane under a splitter goes; applying it while a
        second is still hidden would hand space to a widget that is not on screen, and
        reconciling it against what remains means guessing which of those sizes the user
        has dragged since. Waiting until the set empties gives back exactly the grid that
        was there before any of it started, which is the thing they are asking for.
        """
        if self._hidden:
            return
        for splitter, sizes in reversed(list(self._hidden_sizes.items())):
            # A stale count means the tree changed shape under the record. add_pane and
            # close_pane both clear the dict for that, so this is the belt to their braces.
            if splitter.count() == len(sizes):
                splitter.setSizes(sizes)
        self._hidden_sizes.clear()

    # --- persistence (spec 9) --------------------------------------------------------

    def to_tree(self) -> dict:
        """Serialise the arrangement: splitters, proportions, launcher and id per pane.

        Deliberately not the sessions. A session is a live process holding a pty inside
        our window tree and cannot outlive us; what survives a restart is the shape, so
        reopening restores the workspace and relaunches into it. Arrangement back,
        scrollback gone -- see db.py.

        The id is here because the number stopped being ours alone. It names a pane in the
        header, in the tab strip and in the log, and ``board.env_for`` now hands it to the
        shell as well -- so a claim written on the board yesterday means the pane that is
        in that slot today, and not whichever one the counter reached first.
        """
        return self._serialise(self._root)

    def _serialise(self, widget: QWidget) -> dict:
        if isinstance(widget, QSplitter):
            # The zoomed reading of a collapsed splitter is [0, ..., everything], because
            # a hidden child reports no size, and the save is debounced by more than a
            # second so it lands while still zoomed. The damage is quieter than it looks:
            # _apply_sizes rejects any list holding a zero, so the layout does not come
            # back collapsed, it comes back with no proportions at all and Qt distributes
            # evenly. What the user arranged is what was there before the zoom, so that is
            # what is written.
            sizes = (self._zoom_sizes.get(widget) or self._hidden_sizes.get(widget)
                     or widget.sizes())
            return {
                "type": "split",
                "orientation": ("h" if widget.orientation() == Qt.Orientation.Horizontal
                                else "v"),
                "sizes": list(sizes),
                "children": [self._serialise(widget.widget(i))
                             for i in range(widget.count())],
            }
        launcher = widget.launcher_id if isinstance(widget, Pane) else ""
        node = {"type": "pane", "launcher_id": launcher}
        if isinstance(widget, Pane):
            node["pane_id"] = widget.pane_id
            # Written only when there is an override to write. A node with no "cwd" is
            # how an older layout already reads, and how a pane that simply sits in its
            # deck should keep reading -- absence is the default, not a stored copy
            # of a path that would then have to be kept in step with the deck's.
            if widget.cwd:
                node["cwd"] = widget.cwd
            # The same rule: absent unless there is one, which is how every layout
            # written before names existed already reads.
            if widget.name:
                node["name"] = widget.name
        # An editor pane persists as its path. Reopening a file is cheap and exact,
        # unlike a session, so this one *can* come back as it was.
        if isinstance(widget, Pane) and widget.kind == "editor" and widget.file_path:
            node["kind"] = "editor"
            node["path"] = widget.file_path
        return node

    def restore(self, tree: dict, bg_color: str = "#1e1e1e") -> list[Pane]:
        """Rebuild an arrangement from ``to_tree``. Returns the panes, in tree order.

        Panes come back empty; the caller launches into them. Restoring the shape and
        relaunching are separate steps because the second one is the expensive, visible
        one and the user may not want it for fifteen panes.
        """
        self.clear()
        node = tree if isinstance(tree, dict) else {"type": "pane", "launcher_id": ""}
        created: list[Pane] = []
        if node.get("type") == "split":
            self._root.setOrientation(_orientation(node.get("orientation")))
            for child in node.get("children", []):
                self._root.addWidget(self._rebuild(child, bg_color, created))
            _apply_sizes(self._root, node.get("sizes"))
        else:
            self._root.addWidget(self._rebuild(node, bg_color, created))
        self._panes = created
        if created:
            self.set_active(created[0])
        log.info("restored layout with %d pane(s), tree=%s", len(created), self.describe())
        return created

    def _rebuild(self, node: dict, bg_color: str, created: list[Pane]) -> QWidget:
        if node.get("type") == "split":
            splitter = self._new_splitter(_orientation(node.get("orientation")))
            for child in node.get("children", []):
                splitter.addWidget(self._rebuild(child, bg_color, created))
            _apply_sizes(splitter, node.get("sizes"))
            return splitter
        pane = Pane(bg_color, self._theme, node.get("launcher_id", ""),
                    str(node.get("cwd", "") or ""), parent=self)
        pane.restore_kind = str(node.get("kind", "") or "")
        pane.restore_path = str(node.get("path", "") or "")
        pane.pane_id = self._ids.take(node.get("pane_id"))
        # Through the same rule the dialog applies, so a hand-edited layout cannot hand a
        # pane a name the window would have refused. Whether it is still *free* is a
        # question about other trees, which only the window can answer.
        pane.set_name(board.clean_name(node.get("name", "")) or "")
        pane.activated.connect(self.set_active)
        pane.closeRequested.connect(self.paneCloseRequested)
        pane.zoomRequested.connect(self.toggle_zoom)
        pane.menuRequested.connect(self.paneMenuRequested)
        created.append(pane)
        return pane

    def clear(self) -> None:
        """Drop every pane. The caller is responsible for their shells."""
        self.set_zoom(None)
        self._hidden.clear()
        self._hidden_sizes.clear()
        self._panes = []
        self._active = None
        while self._root.count():
            child = self._root.widget(0)
            child.setParent(None)
            child.deleteLater()

    # --- tree plumbing -------------------------------------------------------------

    def _new_splitter(self, orientation: Qt.Orientation) -> QSplitter:
        splitter = QSplitter(orientation)
        splitter.setHandleWidth(HANDLE_WIDTH)
        # Collapsible children can be dragged to zero extent, stranding a terminal the user
        # can then neither reach nor drag back.
        splitter.setChildrenCollapsible(False)
        # Every splitter, not just the root: a nested split is where most dragging
        # happens, and proportions are half of what to_tree() records.
        splitter.splitterMoved.connect(lambda _pos, _index: self.layoutChanged.emit())
        return splitter

    def _splitter_of(self, widget: QWidget) -> QSplitter:
        node = widget.parentWidget()
        while node is not None and not isinstance(node, QSplitter):
            node = node.parentWidget()
        return node if isinstance(node, QSplitter) else self._root

    def _equalize(self, splitter: QSplitter) -> None:
        if splitter.count():
            splitter.setSizes([_EQUAL] * splitter.count())

    def _collapse(self, splitter: QSplitter) -> None:
        """Splice out a splitter left holding one child. Cascades toward the root."""
        if splitter is self._root:
            return
        parent = self._splitter_of(splitter)
        index = parent.indexOf(splitter)
        if index < 0:
            return

        if splitter.count() == 1:
            child = splitter.widget(0)
            parent.replaceWidget(index, child)
        elif splitter.count() == 0:
            splitter.setParent(None)
        else:
            return

        splitter.deleteLater()
        self._equalize(parent)
        self._collapse(parent)


def _orientation(value: str | None) -> Qt.Orientation:
    return Qt.Orientation.Vertical if value == "v" else Qt.Orientation.Horizontal


def _apply_sizes(splitter: QSplitter, sizes) -> None:
    """Restore proportions, ignoring a stale count from an edited or older layout."""
    if isinstance(sizes, list) and len(sizes) == splitter.count() and all(sizes):
        splitter.setSizes([int(v) for v in sizes])
