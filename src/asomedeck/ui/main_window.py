"""The main window: decks, switching, and the design-system chrome.

Each deck owns a ``PaneTree``; they live in a ``QStackedWidget`` and switching sets
the current page. Every pane runs a shell on a pseudo-console, rendered by Qt.

What persists across a restart is the *arrangement*, and reopening refills it with fresh
shells. See db.py for why the sessions themselves cannot come back.
"""

from __future__ import annotations

import logging
import os
import time
from functools import partial

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from .. import __version__, board, files, git_clone, platform_, shells
from ..commands import COMMANDS, by_id
from ..config import Config
from ..db import Database, Deck
from ..git_status import Branch, GitBranches, GitState, GitStatus
from ..term import activity
from . import frameless as fl
from . import theme as th
from .chrome import DeckHeader, SessionPanel, SessionTabStrip, Sidebar, StatusBar, TitleBar
from .clone_dialog import CloneDialog
from .deck_model import (
    DeckListModel,
    DeckStore,
    SessionRow,
    rows_from_db,
)
from .deck_model import (
    PlaceholderRow as DeckPlaceholder,
)
from .editor import EditorPane
from .palette import Palette, Row
from .pane_tree import Pane, PaneIds, PaneTree
from .term_view import KEY_COPY, KEY_PASTE
from .widgets.status_dot import LIVE, WAITING
from .widgets.swatches import AccentSwatchRow

log = logging.getLogger(__name__)

UPTIME_TICK_MS = 1000

#: How long the layout waits after a change before being written.
#:
#: Dragging a splitter emits continuously, so writing on every emission would put a
#: transaction per mouse-move on the database. Long enough to coalesce a drag, short
#: enough that a crash costs at most this much of an arrangement -- which is the whole
#: point: save_layouts used to run only from closeEvent, so a crash lost every split and
#: close since the last clean exit while the recovery dialog said arrangements were
#: preserved.
LAYOUT_SAVE_DEBOUNCE_MS = 1200

#: How long ``panes.md`` waits after a change before being rewritten. Much shorter than the
#: layout's, because an agent may be reading it the moment its pane starts -- and restoring
#: a deck starts a dozen shells in half a second, which this turns into one write rather
#: than twelve.
ROSTER_DEBOUNCE_MS = 200

#: Rename, from the deck list. Widget-scoped and not a registry command, which is the
#: whole reason it can be a bare key at all: a Command becomes a WindowShortcut and fires
#: wherever focus is, and F2 is a function key keys.py encodes and a shell is entitled to
#: receive. Bound to the sidebar list, it exists only while that list has focus -- the
#: same scoping editor.py gives Ctrl+S, for the same reason.
KEY_RENAME = "F2"

#: How much of a path the session panel can show. Measured against its 264px at the
#: panelVal size, not guessed: past this the value runs under the key beside it, which
#: is what an unelided absolute path did.
PANEL_PATH_CHARS = 30

#: What a deck with no folder is called before anybody renames it. Only a default in an
#: editable dialog -- it is offered, and numbered by `_unused_name` when it is taken.
SCRATCH_NAME = "Scratch"

#: ``app_meta`` key: where the last clone went. Window state rather than a preference, by
#: config.py's test: losing it resets a field and annoys nobody.
CLONE_FOLDER = "clone_folder"

#: How many branches the chip's dropdown will show. A long-lived repository has hundreds
#: of them and a menu that tall is unusable; --sort=-committerdate puts the ones anybody
#: is still working on at the top, so what the cap drops is what nobody was looking for.
BRANCH_MENU_LIMIT = 25


def _label_shortcut(action: QAction, chord: str) -> None:
    """Show a chord beside a menu row without letting the row bind it.

    Two Qt details, and both of them bite. A ``QMenu`` built with the window as its parent
    outlives the ``exec`` that showed it, so an action left at the default
    ``WindowShortcut`` context would leave its chord registered on the window afterwards --
    which for ``F2`` means taking a function key away from every shell in the app, the one
    thing this menu must not do. ``WidgetShortcut`` scopes it to the menu, where it is
    live only while the menu is open and is what the user is pressing anyway.

    And since Qt 5.10 a context menu hides shortcut text unless asked, so without the
    third line the chord is set, scoped, and never shown -- which is the whole reason it
    is being set on a row that is not allowed to fire it.
    """
    action.setShortcut(QKeySequence(chord))
    action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
    action.setShortcutVisibleInContextMenu(True)


def _count_panes(node) -> int:
    """How many terminals a saved arrangement will bring back.

    Editor panes are in the same tree and are deliberately not counted: the sidebar line
    this feeds says "N terminals saved", and a file is not one.
    """
    if not isinstance(node, dict):
        return 0
    if node.get("type") == "pane":
        return 0 if node.get("kind") == "editor" else 1
    return sum(_count_panes(child) for child in node.get("children", ()))


def panel_path(path: str, deck_path: str) -> str:
    """Where an open file is, in the width the session panel actually has.

    Relative to the deck first, which is both shorter and the more useful answer:
    ``src\asomedeck\files.py`` says where you are, while the absolute path mostly
    repeats the deck root printed in the header above it. Elided from the left, since
    the end of a path is the part that identifies the file.
    """
    if not path:
        return ""
    if deck_path:
        try:
            relative = os.path.relpath(path, deck_path)
        except ValueError:
            # A different drive. Windows raises here rather than falling back to the
            # absolute path, and a file open outside the deck is legitimate.
            relative = path
        if not relative.startswith(".."):
            path = relative
    if len(path) <= PANEL_PATH_CHARS:
        return path
    return "…" + path[-(PANEL_PATH_CHARS - 1):]


def pane_title(reported: str, pane_id: int, cwd: str) -> str:
    """What to call a pane, given what the program running in it says it is.

    Programs announce themselves through OSC 0/2 and we now listen: Claude Code sends
    "✳ Claude Code", and MSYS bash sends "MINGW64:/d/PY/AsomeDeck".

    The second is the reason this is not simply "show the title". Bash is reporting the
    directory, which the pane footer already displays and the deck header displays
    again -- printing it a third time in the one field that could say what is *running*
    would be worse than the placeholder it replaces. So a title that is only the shell
    announcing *this* pane's directory is dropped, and the pane keeps its number.

    A shell that has been cd'd elsewhere keeps its title, and that is the distinction the
    fallback exists for: the footer shows the directory the pane launched in and never
    updates, so the title is the only place the move shows up.

    Nothing here invents a name. It either repeats what the program said or falls back to
    the pane's own label.
    """
    fallback = f"pane {pane_id}"
    title = (reported or "").strip()
    if not title:
        return fallback

    # "MINGW64:/d/PY/AsomeDeck", "MSYS:/c/x" -- the prefix is the MSYSTEM value.
    body = title.split(":", 1)[1].strip() if _looks_like_msys_prefix(title) else title
    if _is_path_like(body) and _is_here(body, cwd):
        return fallback
    return title


def _is_here(body: str, cwd: str) -> bool:
    r"""Whether a path-like title is naming the directory the pane already reports.

    Only *this* directory is dropped. A shell that has been cd'd somewhere else is saying
    something the rest of the pane does not: the footer shows the cwd the pane launched
    in and never updates, so "~/decks/other" in the title is the only place that
    movement appears at all.

    Both spellings of the same directory count as here, because MSYS bash writes
    ``D:\PY\AsomeDeck`` as ``/d/PY/AsomeDeck`` and it is the same place. The drive letter
    is compared rather than ignored -- an earlier form of this matched on the tail alone,
    which made C:\PY\AsomeDeck read as D:\PY\AsomeDeck.

    An empty cwd cannot be compared against, and a title that might be the cwd is worth
    less than the pane's number, so unknown drops.
    """
    here = (cwd or "").replace("\\", "/").rstrip("/").lower()
    if not here:
        return True
    spellings = {here}
    if len(here) > 1 and here[1] == ":":
        spellings.add("/" + here[0] + here[2:])   # d:/py/x -> /d/py/x, as MSYS writes it
    return body.replace("\\", "/").rstrip("/").lower() in spellings


def _looks_like_msys_prefix(title: str) -> bool:
    r"""MINGW64:, MSYS:, UCRT64: -- the MSYSTEM value bash puts before the path.

    Two characters minimum, because a one-letter head is a drive: "C:\PY\AsomeDeck" is a
    path in its entirety, and treating the C as a prefix left the rest unrecognisable as
    one.
    """
    head, _, rest = title.partition(":")
    return bool(rest) and len(head) > 1 and head.isupper() and head.isalnum()


def _is_path_like(text: str) -> bool:
    return (text.startswith(("/", "~", "\\"))
            or (len(text) > 1 and text[1] == ":"))


def _and_list(names: list[str]) -> str:
    """"Api", "Api and Web", "Api, Web and Ledger". Prose, for a sentence in a dialog.

    Qt-free and module-level so the awkward cases -- one name, two names -- are settled
    once by a test rather than inside a dialog nobody can reach without clicking.
    """
    if len(names) <= 1:
        return names[0] if names else ""
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _spare_name(path: str, taken: list[str]) -> str:
    """A name for a second deck on a folder that already has one: "api 2", "api 3".

    Only a suggestion -- the dialog offering it is editable, and the number means nothing
    beyond "not the one above". It starts from the folder rather than from the existing
    row's name because that name may have been rewritten into something this has no
    business appending a digit to.
    """
    base = os.path.basename(path.rstrip("\\/")) or path
    lowered = {name.strip().lower() for name in taken}
    number = 2
    while f"{base} {number}".lower() in lowered:
        number += 1
    return f"{base} {number}"


def _unused_name(base: str, taken: list[str]) -> str:
    """``base`` if nothing is called that, else the first "base N" that is free.

    Not ``_spare_name`` with a different argument: that one is answering "a *second*
    name for this folder" and numbers unconditionally, which is right there and wrong
    here -- the first deck with no folder should be offered "Scratch", not "Scratch 2".
    """
    if base.strip().lower() not in {name.strip().lower() for name in taken}:
        return base
    return _spare_name(base, taken)


def _deck_cwd(deck: Deck) -> str:
    """Where a pane in this deck starts when it has no directory of its own.

    A deck with no folder can only offer the home directory, and the empty string is not
    an answer: ``unixpty.spawn`` chdirs only ``if cwd``, so an empty one silently inherits
    the *app's* working directory -- ``/`` for a bundle launched from the Dock. That is
    the same wrong answer ``_seed_deck_dir`` exists to keep out of the registry, arriving
    by the other door.
    """
    return deck.path or os.path.expanduser("~")


def _folder_key(path: str) -> str:
    """One spelling per folder, for telling whether two decks' paths are the same place.

    Case-folded on every platform, which is what ``db.decks_at`` already does with
    ``COLLATE NOCASE`` -- and two answers to "which decks share a folder" that disagree
    would let one pane's name be refused by one and allowed by the other.
    """
    return os.path.normcase(os.path.normpath(os.path.abspath(path))).lower()


def fit_buttons(box: QMessageBox) -> None:
    """Stop a dialog squeezing its buttons below their own text.

    The stylesheet gives dialog buttons ``min-width: 96px`` so a bare OK is not a stub.
    Qt takes that as the *minimum size hint* rather than a floor under the natural one, so
    a button whose label is wider is free to be compressed back to it -- and the label is
    then elided from both ends. "Quit and close terminals" arrived on screen as
    "it and close termir".

    Nothing about it is font-specific; Barlow and Roboto both wanted 162px and both got
    130px. Raising each button's minimum to the width it actually asked for makes the
    dialog grow instead of the text shrink.
    """
    for button in box.buttons():
        button.setMinimumWidth(max(button.minimumWidth(), button.sizeHint().width()))


def _seed_deck_dir() -> str:
    """A plausible folder to register on a first run, when the registry is empty.

    ``os.getcwd()`` on its own is only sensible when somebody ran the app from a shell.
    A bundle launched from Finder or the Dock inherits ``/`` as its working directory, and
    a first run then registered the entire filesystem as a deck -- which is not a
    cosmetic wrong answer: the file tree walks it and GitStatus polls it every three
    seconds.

    So the working directory is used when it looks like a deck and the home directory is
    used when it does not. Home is a defensible answer either way; ``/`` never is.
    """
    cwd = os.getcwd()
    home = os.path.expanduser("~")
    root = os.path.abspath(os.sep)
    if not cwd or os.path.abspath(cwd) == root or not os.path.isdir(cwd):
        return home
    return cwd


class MainWindow(QMainWindow):
    #: Frameless on Windows, native chrome everywhere else -- a default rather than a
    #: literal ``True`` because the entire frameless path is Win32.
    #:
    #: It buys more than it looks like. ``self._helper`` stays None when this is False, and
    #: that one condition already gates ``restore_native_behaviours`` in ``showEvent`` and
    #: the whole ``nativeEvent`` override below, both of which were written to be skippable
    #: because the tests skip them. So macOS gets a real title bar -- traffic lights,
    #: resize, zoom, Mission Control, Stage Manager -- by not doing anything, which is the
    #: right amount of platform-specific window code to write when the platform already has
    #: a window manager that works.
    DEFAULT_FRAMELESS = platform_.IS_WINDOWS

    def __init__(self, db: Database, theme: th.Theme | None = None,
                 frameless: bool | None = None, restore_sessions: bool = True,
                 config: Config | None = None) -> None:
        super().__init__()
        self.db = db
        #: A path-less Config by default, so a window built without one -- every test, and
        #: --smoke -- reads the registry's defaults and writes nothing to the real file.
        self.config = config or Config()
        self.theme = theme or th.Theme(self.config.theme)
        self.setWindowTitle("AsomeDeck")
        self.resize(1480, 900)
        self.setMinimumSize(1180, 640)

        self._trees: dict[int, PaneTree] = {}
        #: One allocator for every tree in the window, so that no two panes anywhere in it
        #: share a number. Per-tree would be enough for anything on screen -- a number is
        #: only ever read next to the pane holding it -- and is not enough for the claims
        #: board, which lives in a folder rather than in a tree and is written to by every
        #: pane whose deck points there. See ``PaneIds``.
        self._pane_ids = PaneIds()
        self._git: dict[int, GitState] = {}
        #: The branch dropdown, kept alive between popup and dismissal. See _on_branches.
        self._branches_menu: QMenu | None = None
        self.deck: Deck | None = None
        self._screen_signal_wired = False
        self._styles_restored = False
        #: Set once closeEvent has committed to quitting. A shell exiting normally should
        #: take its pane with it; the same signal firing because *we* just closed it on
        #: the way out should not, or the window dismantles itself as it dies and anything
        #: reading the panes afterwards -- the smoke report, most obviously -- finds none.
        self._quitting = False
        #: Set to "close" to skip the quit prompt. Smoke mode needs it: a modal dialog
        #: in an unattended run hangs it. "detach" went with the docking backend -- a pty
        #: cannot be handed back to the desktop, so there is nothing left to choose.
        self.quit_action: str | None = None
        #: False suppresses relaunching into a restored layout. Smoke mode needs it: a
        #: saved seven-pane layout would drown the three sessions it means to test.
        self._restore_sessions = restore_sessions

        # None means "decide by platform"; the tests and --smoke still pass False outright.
        if frameless is None:
            frameless = self.DEFAULT_FRAMELESS
        self._helper: fl.FramelessHelper | None = None
        if frameless:
            self._helper = fl.enable_frameless(self)

        self.git = GitStatus(self)
        #: Looked for here and again whenever a menu that offers cloning opens, not every time
        #: the commands refresh: that happens constantly, and this walks PATH on disk.
        self._git_program = git_clone.find_git()
        self._clone_dialog: CloneDialog | None = None
        self.branches = GitBranches(self)
        self._build()
        self._build_actions()
        self._wire()

        self._uptime_timer = QTimer(self)
        self._uptime_timer.setInterval(UPTIME_TICK_MS)
        self._uptime_timer.timeout.connect(self._tick)
        self._uptime_timer.start()

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(LAYOUT_SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self.save_layouts)

        self._roster_timer = QTimer(self)
        self._roster_timer.setSingleShot(True)
        self._roster_timer.setInterval(ROSTER_DEBOUNCE_MS)
        self._roster_timer.timeout.connect(self.write_rosters)
        #: Folders given a ``panes.md`` this run that may still list somebody, keyed as
        #: ``_folder_key`` spells them, with the path and deck name to write it under.
        #: Kept so the last pane over a folder closing rewrites that folder's roster as
        #: empty -- once nothing over it is running, nothing else would ever visit it.
        self._roster_folders: dict[str, tuple[str, str]] = {}

        self.sidebar.set_mode(self.db.get_meta("sidebar_mode", "decks") or "decks")
        self.sidebar.set_collapsed(self.db.get_meta("sidebar_collapsed", "0") == "1")
        self.panel.set_collapsed(self.db.get_meta("panel_collapsed", "0") == "1")
        self._load_decks()

    # --- construction ----------------------------------------------------------------

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.title_bar = TitleBar(self.theme)
        root_layout.addWidget(self.title_bar)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.model = DeckListModel(DeckStore([]), self)
        self.sidebar = Sidebar(self.model, self.theme)
        self.sidebar.set_launcher(shells.default().label, shells.default().command)
        body_layout.addWidget(self.sidebar)

        centre = QWidget()
        centre_layout = QVBoxLayout(centre)
        centre_layout.setContentsMargins(0, 0, 0, 0)
        centre_layout.setSpacing(0)

        self.header = DeckHeader(self.theme)
        centre_layout.addWidget(self.header)
        self.tabs = SessionTabStrip(self.theme)
        centre_layout.addWidget(self.tabs)

        grid_and_panel = QWidget()
        grid_layout = QHBoxLayout(grid_and_panel)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(0)

        self.stack = QStackedWidget()
        # Measured, not decorative: flush to the frame every resize border under a
        # terminal is unreachable, because the hit-test never reaches this process.
        if self._helper is not None:
            fl.reserve_gutter(self.stack)
        grid_layout.addWidget(self.stack, 1)

        self.panel = SessionPanel(self.theme)
        grid_layout.addWidget(self.panel)
        centre_layout.addWidget(grid_and_panel, 1)

        body_layout.addWidget(centre, 1)
        root_layout.addWidget(body, 1)

        self.status = StatusBar(self.theme)
        self.status.set_shells(" · ".join(s.label for s in shells.available()))
        root_layout.addWidget(self.status)

        self.setCentralWidget(root)
        # Parented to the central widget, so the resize that lays out the chrome is the
        # one that lays this out too, and so it covers the chrome rather than sitting in
        # a hole punched in it.
        self.command_palette = Palette(self.theme, root)

    def _command_slots(self) -> dict[str, object]:
        """What each command in the registry actually does.

        commands.py says what a command is called and what it costs to press; this says
        what happens. Keeping the two apart is what lets the registry be checked headless,
        and what makes "every command has a slot" a test rather than a hope --
        ``_build_actions`` indexes this by id, so a command added to the registry without
        a slot fails at construction instead of arriving as a menu entry that does nothing.
        """
        return {
            "project.add": self._add_deck,
            "deck.clone": self._clone_deck,
            "deck.new": self._new_deck,
            "app.quit": self.close,
            "project.next": lambda: self._cycle_deck(1),
            "project.prev": lambda: self._cycle_deck(-1),
            "project.shell": self._change_launcher,
            "project.rename": self._rename_current_deck,
            "project.move.up": lambda: self._move_deck(-1),
            "project.move.down": lambda: self._move_deck(1),
            "project.remove": self._remove_current_deck,
            "session.new": lambda: self._new_session(None),
            # Wrapped, not passed bare: QAction.triggered carries a ``checked`` bool, and
            # PySide would hand it to the one optional parameter this takes.
            "session.new.in": lambda: self._new_session_in(),
            "pane.split.right": lambda: self._new_session(Qt.Orientation.Horizontal),
            "pane.split.down": lambda: self._new_session(Qt.Orientation.Vertical),
            "pane.zoom": self._toggle_pane_zoom,
            "pane.close": self._close_active_pane,
            "view.palette": self._open_palette,
            "view.sidebar": lambda: self._toggle_panel(self.sidebar),
            "view.panel": lambda: self._toggle_panel(self.panel),
            "view.theme": self._toggle_theme,
            "help.about": self._about,
        }

    def _build_actions(self) -> None:
        """One QAction per command, kept by id for the menus and the palette to reuse."""
        slots = self._command_slots()
        self.command_actions: dict[str, QAction] = {}
        for command in COMMANDS:
            action = QAction(command.label, self)
            chord = self.config.shortcut(command)
            if chord:
                action.setShortcut(QKeySequence(chord))
                # Spec 7: these belong to Qt whichever pane has focus. Widget scope would
                # stop firing the moment a terminal takes focus, which is immediately and
                # forever.
                action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            action.triggered.connect(slots[command.id])
            self.addAction(action)
            self.command_actions[command.id] = action
        self._refresh_commands()

    def _refresh_commands(self) -> None:
        """Grey out what does not apply, from the registry's own predicates.

        Enabling matters beyond the menus: a disabled QAction does not fire its shortcut
        either, so Ctrl+Shift+W with no deck open stops reaching a handler that would
        only have returned early.
        """
        for command in COMMANDS:
            self.command_actions[command.id].setEnabled(command.enabled(self))

    def _wire(self) -> None:
        self.title_bar.minimizeRequested.connect(self.showMinimized)
        self.title_bar.maximizeRequested.connect(self._toggle_maximised)
        self.title_bar.closeRequested.connect(self.close)
        self.title_bar.themeToggled.connect(self._toggle_theme)
        self.title_bar.set_actions(self.command_actions)
        self.title_bar.paletteRequested.connect(self._open_palette)
        self.title_bar.set_search_hint(self.config.shortcut(by_id("view.palette")))
        self.command_palette.closed.connect(self._focus_active_pane)
        for menu in self.title_bar.menus.values():
            # Recheck on open, not only when the chrome happens to refresh. "Move up" on
            # the first deck is the case that makes this necessary: what it depends on
            # changes without anything else about the window changing.
            menu.aboutToShow.connect(self._refresh_commands)
        self.title_bar.dragRequested.connect(lambda: fl.start_system_move(self))

        self.header.newSessionRequested.connect(lambda: self._new_session(None))
        self.header.openInEditorRequested.connect(self._open_in_editor)
        self.header.settingsRequested.connect(self._deck_settings)
        self.header.branchesRequested.connect(self._show_branches)
        self.branches.ready.connect(self._on_branches)
        self.tabs.newSessionRequested.connect(lambda: self._new_session(None))
        self.tabs.tabActivated.connect(self._activate_pane)
        self.tabs.tabClosed.connect(self._close_pane)

        self.sidebar.deckSelected.connect(lambda row: self._open_deck(row.deck_id))
        self.sidebar.addDeckRequested.connect(self._add_deck)
        self.sidebar.cloneDeckRequested.connect(self._clone_deck)
        self.sidebar.newDeckRequested.connect(self._new_deck)
        self.sidebar.addMenuOpening.connect(self._recheck_git)
        self.title_bar.menus["File"].aboutToShow.connect(self._recheck_git)
        self.title_bar.menus["File"].setToolTipsVisible(True)
        self._recheck_git()
        self.sidebar.sessionSelected.connect(self._on_session_row)
        self.sidebar.launcherChangeRequested.connect(self._change_launcher)
        self.sidebar.fileActivated.connect(self._open_file)
        # Straight at the tree, not re-exposed on Sidebar. ``set_root`` and ``refresh``
        # are already reached this way; forwarding one signal of the three would make the
        # panel look like a boundary it has never been.
        self.sidebar.files.directoryMenuRequested.connect(self._folder_menu)
        self.sidebar.modeChanged.connect(
            lambda mode: self.db.set_meta("sidebar_mode", mode))
        self.sidebar.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sidebar.view.customContextMenuRequested.connect(self._deck_menu)
        # Scoped to the list and not to the window, which is why it can be a bare key at
        # all -- see KEY_RENAME. It is a QShortcut rather than a Command for the same
        # reason editor.py's Ctrl+S is not one.
        self.rename_shortcut = QShortcut(QKeySequence(KEY_RENAME), self.sidebar.view)
        self.rename_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.rename_shortcut.activated.connect(self._rename_selected_deck)

        self.git.updated.connect(self._on_git)
        self.sidebar.collapsedChanged.connect(
            lambda on: self.db.set_meta("sidebar_collapsed", "1" if on else "0"))
        self.panel.collapsedChanged.connect(
            lambda on: self.db.set_meta("panel_collapsed", "1" if on else "0"))

    # --- decks --------------------------------------------------------------------

    def _load_decks(self) -> None:
        decks = self.db.decks()
        if not decks:
            # An empty registry is a dead app. Seeding with the working directory gives a
            # first run something real to open instead of an empty shell.
            self.db.add_deck(_seed_deck_dir(), shells.default().shell_id)
            decks = self.db.decks()
        self._reload_rows()
        self._refresh_rail()
        self._open_deck(self.db.decks()[0].id)

    def _reload_rows(self) -> None:
        """Rebuild the sidebar rows, keeping the git state already gathered.

        A model reset drops everything the rows had learned, so the cached GitState is
        re-applied rather than left for the next poll -- otherwise every deck switch
        would blank the branch chips for a few seconds.
        """
        self.model.replace(rows_from_db(self.db.decks()))
        for deck_id, state in self._git.items():
            if state.known:
                self.model.set_git(deck_id, state.branch, state.changed,
                                   state.clean)
        # Through the same path as every other refresh. Doing its own tally here meant
        # passing ``tree.count()`` where _refresh_chrome passes the number of *live*
        # sessions, so the figure beside a deck's name changed meaning depending on
        # which of the two had run last.
        self._refresh_status()
        self.sidebar.refresh_count()

    def _open_deck(self, deck_id: int) -> None:
        if deck_id <= 0:
            return
        if self.deck is not None and self.deck.id == deck_id:
            self._refresh_chrome()
            return
        deck = next((p for p in self.db.decks() if p.id == deck_id), None)
        if deck is None:
            return
        self.deck = deck
        self.db.touch(deck.id)
        self._reload_rows()
        self._refresh_rail()
        # No launch, no teardown -- just which page is current (spec 4).
        self.stack.setCurrentWidget(self._tree_for(deck))
        # Poll the active deck only; others refresh when they become active. A deck with
        # no folder has neither a repository to poll nor a tree to fill, and `watch` would
        # not say so: handed an empty path it starts its three-second timer and lets
        # `refresh` return early forever. The tree is re-rooted at nothing rather than
        # left showing the deck you came from.
        if deck.path:
            self.git.watch(deck.id, deck.path)
        else:
            self.git.stop()
        self.sidebar.files.set_root(deck.path)
        self.sidebar.set_files_available(bool(deck.path))

        row = self.model.row_for_deck(deck.id)
        if row >= 0:
            self.sidebar.view.setCurrentIndex(self.model.index(row, 0))
        log.info("opened deck %s (%s)", deck.name, deck.path)
        self._refresh_launcher()
        self._refresh_chrome()

    def _tree_for(self, deck: Deck) -> PaneTree:
        """The deck's pane tree, created and layout-restored on first open."""
        tree = self._trees.get(deck.id)
        if tree is not None:
            return tree
        tree = PaneTree(self.theme, self._pane_ids)
        tree.paneActivated.connect(self._on_pane_activated)
        tree.paneRemoved.connect(lambda _p: self._refresh_chrome())
        # Every way a pane ends goes through here -- closed from its header, from a tab,
        # or by its shell exiting -- so this is the one place the roster hears about it.
        tree.paneRemoved.connect(lambda _p: self._schedule_roster())
        tree.paneCloseRequested.connect(self._close_pane)
        tree.layoutChanged.connect(self._schedule_save)
        tree.zoomChanged.connect(lambda _p, t=tree: self._on_zoom_changed(t))
        tree.hiddenChanged.connect(lambda t=tree: self._on_hidden_changed(t))
        tree.paneMenuRequested.connect(self._pane_menu)
        self._trees[deck.id] = tree
        self.stack.addWidget(tree)

        saved = self.db.load_layout(deck.id)
        if saved:
            panes = tree.restore(saved, self.theme.hex("term"))
            log.info("restored %d pane(s) for %s", len(panes), deck.name)
            self._settle_names(panes)
            if panes and self._should_relaunch():
                self._relaunch_into(panes, deck)
        return tree

    def _should_relaunch(self) -> bool:
        """Whether to refill a restored layout with fresh terminals.

        The shell state is gone either way -- scrollback, environment, whatever was
        running died with the process that hosted it. What can come back is a terminal at
        the right cwd in the right slot, which is what spec 9's layout table is for. A
        grid of empty rectangles is the worst of both: the arrangement without the point
        of it.

        This used to ask first above six panes, on the grounds that fifteen terminals
        reappearing is an ambush. The ambush was the *cost* -- fifteen render processes --
        and with that gone the question was asking the user to approve something that now
        takes half a second. ``restore_sessions`` in ``app_meta`` is still the way to say
        no, once, instead of being asked every time.
        """
        return (self._restore_sessions
                and self.db.get_meta("restore_sessions", "1") == "1")

    def _relaunch_into(self, panes: list[Pane], deck: Deck) -> None:
        """Start a terminal in each restored pane.

        This was a queue: one launch at a time, each waiting for the previous to report
        itself running, with a fifteen-second backstop for one that did neither. Two
        reasons built it and both are gone. MSYS2's emulated fork failed under concurrency,
        which left with mintty; then every pane carried a web view, and a dozen render
        processes starting together made the window unresponsive for as long as it took.

        Measured once the renderer was native: twelve panes issued in 126 ms, all twelve
        shells live 0.48 s later. There is nothing left to serialise.
        """
        for pane in panes:
            if pane.restore_kind == "editor" and self._restore_editor(pane):
                continue
            if not pane.has_session:
                self._launch_into(pane, deck)

    def _launch_into(self, pane: Pane, deck: Deck) -> bool:
        """Start a shell in a pane. True if it began starting.

        The renderer is imported here rather than at module scope so a missing backend
        surfaces as a dialog rather than a window that never opens. What it needs is imported
        at the top of its own modules for the same reason: deferred to spawn time, the
        failure would land inside a Qt slot, where the crash hook exits the app instead.
        """
        if pane.has_session or pane.kind == "editor":
            # An editor pane reports no session, which is true and is also exactly what
            # an empty pane reports. Without this it would be handed a shell.
            return False
        try:
            from .term_pane import TerminalPane
        except ImportError as exc:
            QMessageBox.warning(
                self, "Terminal backend unavailable",
                'Install it with:  pip install -e ".[dev]"\n\n' + str(exc),
            )
            return False

        cwd = self._launch_cwd(pane, deck)
        wanted = pane.launcher_id or deck.launcher_id or ""
        spec = shells.resolve(wanted)
        if wanted and spec.shell_id != wanted:
            # Named by a saved layout, gone from this machine. Say so once, here, rather
            # than let the pane report a shell it is not running.
            log.warning("shell %r is not installed; starting %s instead", wanted, spec.shell_id)
        pane.launcher_id = spec.shell_id
        # Identity last, so a shell definition can never shadow it. Nothing in shells.py
        # sets an ASOMEDECK_ name today; this is about what a hand-added launcher could do
        # to a pane's own number without anybody noticing it had.
        # No board for a deck with no folder. There is no shared working tree for two
        # agents to collide in, and `board.ensure("")` would resolve `.asomedeck` against
        # the *process* working directory and seed it wherever the app was launched from.
        # `env_for` drops BOARD_VAR when it is handed None, which is the case it was
        # already written for.
        env = {**spec.env, **board.env_for(
            pane_id=pane.pane_id,
            deck_name=deck.name,
            board=board.ensure(deck.path, deck.name) if deck.path else None,
        )}
        view = TerminalPane(list(spec.argv), cwd, self.theme, env, parent=pane)
        pane.attach_body(view)
        view.exited.connect(lambda _code, p=pane: self._on_session_exit(p))
        self._wire_reports(pane, view)
        view.started.connect(lambda p=pane, v=view, sp=spec: self._on_session_started(p, v, sp))
        pane.set_session_info(title=pane.session_title or f"pane {pane.pane_id}",
                              shell=spec.label, cmd=spec.command, pid="starting",
                              cwd=cwd, alive=False)
        self._refresh_chrome()
        return True

    def _launch_cwd(self, pane: Pane, deck: Deck) -> str:
        """Where this pane's shell starts: its own directory, or the deck's.

        A pane may be sent somewhere else -- a subdirectory, a sibling worktree, the
        machine's home -- and ``Pane.cwd`` holds that where it survives a restart. What
        it cannot promise is that the directory is still there: layouts outlive the folders
        they name, and a shell spawned into a path that has been deleted or unmounted does
        not start at all. So the override is checked before it is used, and a stale one
        degrades to the deck rather than to a pane that never fills.

        ``pane.cwd`` is left alone either way. Rewriting it to the fallback would quietly
        convert "wherever the deck is" into a stored path, and turn a directory that is
        merely unmounted this morning into one the pane has forgotten.

        The deck itself may have no folder, and then the fallback is home -- see
        ``_deck_cwd`` for why the empty string cannot be passed on to a pty.
        """
        fallback = _deck_cwd(deck)
        if pane.cwd and os.path.isdir(pane.cwd):
            return pane.cwd
        if pane.cwd:
            log.warning("pane %d wanted %s, which is not there; starting in %s",
                        pane.pane_id, pane.cwd, fallback)
        return fallback

    def _wire_reports(self, pane: Pane, view) -> None:
        """Connect what a terminal body reports about itself into its pane header.

        Asked for rather than assumed, the same way ``pane_tree`` duck-types ``is_alive``.
        That is not defensiveness: term_pane.py says the whole renderer is consumed by
        getattr, which is what let an xterm.js pane be swapped for a painted one by
        changing an import, and a hard attribute access here would quietly take that back.

        ``exited`` and ``started`` stay at the call site because they are not optional --
        a body with no way to say it finished is not a session.
        """
        titled = getattr(view, "titleChanged", None)
        if titled is not None:
            titled.connect(lambda text, p=pane: self._on_title(p, text))
        menued = getattr(view, "contextMenuRequested", None)
        if menued is not None:
            # An editor body has no such signal and gets QPlainTextEdit's own menu; only a
            # terminal needs one built for it.
            menued.connect(partial(self._terminal_menu, view))
        rang = getattr(view, "bellRang", None)
        if rang is not None:
            rang.connect(lambda p=pane: self._on_bell(p))
        resized = getattr(view, "resized", None)
        if resized is not None:
            # The pane header's one permanently empty field, filled at last:
            # set_geometry_label had no caller anywhere in the app, so every header
            # carried a blank where its size should be.
            resized.connect(lambda cols, rows, p=pane: p.set_geometry_label(f"{cols}×{rows}"))

    def _on_session_started(self, pane: Pane, view, spec) -> None:
        """The shell is up. Until this fires the pane genuinely has no session.

        The page has to load before the pty is spawned, so a pane is not live for a few
        hundred milliseconds after it is created, and chrome drawn in between would
        report it as empty.
        """
        pane.started_at = time.monotonic()
        pane.set_session_info(title=pane.session_title or f"pane {pane.pane_id}",
                              shell=spec.label, cmd=spec.command, pid=f"pid {view.pid}",
                              cwd=pane.cwd_text, alive=True)
        pane.focus_body()
        self._refresh_chrome()
        # Here and not at launch: until now the pane has no session, and the roster lists
        # what is running rather than what was asked for.
        self._schedule_roster()

    def _on_title(self, pane: Pane, reported: str) -> None:
        """A program in a pane renamed itself."""
        title = pane_title(reported, pane.pane_id, pane.cwd_text)
        if title == pane.session_title:
            return
        pane.session_title = title
        pane.title_changed_at = time.monotonic()
        pane.title_changes += 1
        pane.title_label.setText(title)
        self._refresh_chrome()

    def _on_session_exit(self, pane: Pane) -> None:
        if self._quitting:
            # We closed it. Leave the arrangement standing for whatever inspects it next.
            return
        tree = self._tree_of(pane)
        if tree is not None:
            tree.close_pane(pane)
        self._refresh_chrome()

    def _pane_label(self, pane: Pane) -> str:
        """The pane's sub-text, wherever a pane is listed rather than drawn.

        The hidden suffix is added here rather than in the tab strip so that the three
        places that list panes -- the tabs, the session panel's cards and the palette's
        session rows -- all say it, and none of them has to learn what hidden means.

        In words, not only in colour. A dimmed tab reads as a dead session, which is the
        opposite of what hiding does: the shell is very much alive, and it is the pane
        that is gone.
        """
        if pane.kind == "editor":
            label = "editor"
        elif not pane.has_session:
            label = "empty"
        else:
            label = shells.by_id(pane.launcher_id).label
        return f"{label} · hidden" if pane.hidden else label

    def _toggle_panel(self, panel) -> None:
        """Collapse or expand a side panel, refreshing the rail on the way in.

        The dots are built from the model, which changes while the panel is expanded and
        nobody is looking at them.
        """
        panel.toggle()
        self._refresh_rail()

    def _refresh_rail(self) -> None:
        if self.sidebar.collapsed:
            self.sidebar.refresh_rail(self.deck.id if self.deck else None)

    def _refresh_accordion(self) -> None:
        """Tell the sidebar what each deck has under it.

        Only a deck that is open has panes -- the others have a saved arrangement and
        nothing running -- so those get a line saying how many terminals come back rather
        than rows implying some already exist.
        """
        for row in self.model.store.rows:
            if row.deck_id in self._trees:
                continue
            saved = self.db.load_layout(row.deck_id)
            count = _count_panes(saved) if saved else 0
            text = ("not open · "
                    + (f"{count} terminal{'s' if count != 1 else ''} saved"
                       if count else "no saved terminals"))
            self.model.set_children(row.deck_id,
                                    [DeckPlaceholder(row.deck_id, text)])
        self._refresh_open_children()

    def _refresh_open_children(self) -> None:
        """The half of the accordion that reads panes rather than the database.

        Split out so ``_tick`` can run it every second. The other half calls
        ``load_layout`` for each deck that is *not* open, which is a query per deck
        per tick for rows whose text -- "3 terminals saved" -- cannot change while they
        stay closed.
        """
        for deck_id, tree in self._trees.items():
            if self.model.deck_row(deck_id) is None:
                continue                      # a tree outliving its row, mid-removal
            self.model.set_children(deck_id, [
                SessionRow(
                    deck_id=deck_id,
                    pane_id=pane.pane_id,
                    title=pane.caption,
                    shell=self._pane_label(pane),
                    state=pane.session_state,
                    uptime=(_format_uptime(time.monotonic() - pane.started_at)
                            if pane.started_at else ""),
                    pid=str(getattr(pane.body, "pid", "") or ""),
                )
                for pane in tree.panes
            ])

    def _on_session_row(self, deck_id: int, pane_id: int) -> None:
        """A terminal was picked in the sidebar. Open its deck if needed, focus it."""
        if self.deck is None or self.deck.id != deck_id:
            self._open_deck(deck_id)
        tree = self._trees.get(deck_id)
        if tree is None:
            return
        for pane in tree.panes:
            if pane.pane_id == pane_id:
                self._activate_pane(pane)
                return

    def _cycle_deck(self, step: int) -> None:
        decks = self.db.decks()
        if len(decks) < 2 or self.deck is None:
            return
        ids = [p.id for p in decks]
        if self.deck.id not in ids:
            return
        self._open_deck(ids[(ids.index(self.deck.id) + step) % len(ids)])

    def _add_deck(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Add deck folder", os.getcwd())
        if path:
            self._adopt_folder(path)

    def can_clone(self) -> bool:
        return self._git_program is not None

    def _recheck_git(self) -> None:
        """Look for Git again, and grey or ungrey the entries that need it.

        So that installing Git, or having a folder appear where it is expected, does not take
        a restart to notice. The File menu's action is set directly as well as through the
        registry because the sidebar's dropdown has an action of its own.
        """
        self._git_program = git_clone.find_git()
        available = self.can_clone()
        why = "" if available else git_clone.NO_GIT_HINT
        self.sidebar.set_clone_available(available, why)
        action = self.command_actions["deck.clone"]
        action.setEnabled(available)
        action.setToolTip(why)

    def _clone_deck(self) -> None:
        """Pick a folder, give an address, and the clone becomes a deck when it lands.

        One at a time. The dialog is not modal, so this can be asked for again while a clone
        is running, and that means "show me that clone" rather than a second one.
        """
        if self._clone_dialog is not None:
            self._clone_dialog.raise_()
            self._clone_dialog.activateWindow()
            return
        self._recheck_git()
        if not self.can_clone():
            return
        dialog = CloneDialog(self._clone_folder(), self._git_program, self)
        dialog.cloned.connect(self._on_cloned)
        dialog.finished.connect(self._on_clone_dialog_closed)
        self._clone_dialog = dialog
        dialog.show()

    def _clone_folder(self) -> str:
        """Where to offer: the last place, or beside the open deck, or home.

        Beside the open deck because repositories on one machine tend to live side by side, so
        the parent of the one you are in is usually the answer to "where do these go".
        """
        remembered = self.db.get_meta(CLONE_FOLDER, "") or ""
        if os.path.isdir(remembered):
            return remembered
        if self.deck is not None and self.deck.path:
            beside = os.path.dirname(self.deck.path)
            if os.path.isdir(beside):
                return beside
        return os.path.expanduser("~")

    def _on_cloned(self, destination: str) -> None:
        self.db.set_meta(CLONE_FOLDER, os.path.dirname(destination))
        self._adopt_folder(destination)

    def _on_clone_dialog_closed(self) -> None:
        if self._clone_dialog is not None:
            self._clone_dialog.deleteLater()
            self._clone_dialog = None

    def _adopt_folder(self, path: str) -> None:
        """Make a deck over this folder and open it. Where picking and cloning both end up."""
        here = self.db.decks_at(path)
        deck = (self._add_alongside(path, here) if here
                   else self.db.add_deck(path, shells.default().shell_id))
        if deck is None:
            return
        self._reload_rows()
        self._refresh_rail()
        self._open_deck(deck.id)

    def _new_deck(self) -> None:
        """A deck with no folder: terminals, and nothing underneath them.

        No picker, because there is nothing to pick, and none of `_add_alongside`'s
        question either -- that exists to disambiguate two rows over one folder, and a
        deck with no folder collides with nothing. What it does share with that path is
        having to ask for a name: the line under a sidebar row is the path, which is empty
        here by construction, so a row that took its name from a folder it does not have
        would arrive blank.

        What such a deck gives up is everything derived from a working tree -- the branch
        chip, the file tree, the palette's file rows and the claims board. What it keeps
        is the part worth having a row for: its own terminals, layout, shell and colour.
        """
        taken = [deck.name for deck in self.db.decks()]
        name, chose = QInputDialog.getText(
            self, "New empty deck", "Name it -- there is no folder to name it after:",
            text=_unused_name(SCRATCH_NAME, taken))
        if not chose or not name.strip():
            return
        deck = self.db.add_deck("", shells.default().shell_id, name.strip())
        self._reload_rows()
        self._refresh_rail()
        self._open_deck(deck.id)

    def _add_alongside(self, path: str, here: list[Deck]) -> Deck | None:
        """The folder is already in the sidebar. Ask what was meant. None to do nothing.

        ``db.add_deck`` used to answer this by silently handing back the existing row,
        which was right while a folder could only have one. It can now have several -- one
        per task, each with its own terminals, layout and shell -- so the add is a real
        request about half the time, and the half it is not has not gone away: picking a
        folder you already have open is still usually a slip.

        Neither answer is safe enough to assume. Guessing "another" leaves a duplicate row
        the user has to notice and delete; guessing "switch" silently refuses something the
        feature exists to allow. So it asks, and the question names the rows already there
        rather than saying "already added" and leaving you to find them.

        The name is asked for in the same breath and not defaulted quietly, because two
        rows over one folder are told apart by name and nothing else -- the path line under
        them is identical by construction. A default is offered; an empty answer cancels
        rather than making a second row called what the first one is.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Already in the sidebar")
        box.setText(f"{os.path.basename(path.rstrip('\\/')) or path} is already open "
                    f"as {_and_list([deck.name for deck in here])}.")
        box.setInformativeText(
            "Several decks can share one folder -- separate terminals and layouts over "
            "the same working tree. Add another, or switch to the one you have?")
        another = box.addButton("Add another", QMessageBox.ButtonRole.AcceptRole)
        switch = box.addButton("Switch to it", QMessageBox.ButtonRole.RejectRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(switch)
        fit_buttons(box)
        box.exec()

        if box.clickedButton() is switch:
            return here[0]
        if box.clickedButton() is not another:
            return None
        name, chose = QInputDialog.getText(
            self, "Name the new one", "Two decks on one folder need different names:",
            text=_spare_name(path, [deck.name for deck in here]))
        if not chose or not name.strip():
            return None
        return self.db.add_deck(path, shells.default().shell_id, name.strip())

    def _deck_shell(self, deck: Deck) -> shells.ShellSpec:
        """The shell new panes in this deck start with.

        ``deck.launcher_id`` has been stored since the schema was written and read by
        nothing -- every pane took a module constant instead. This is what makes the
        column mean something.
        """
        return shells.resolve(deck.launcher_id or "")

    def _refresh_launcher(self) -> None:
        spec = self._deck_shell(self.deck) if self.deck else shells.default()
        self.sidebar.set_launcher(spec.label, spec.command)

    def _shell_menu(self, deck: Deck, parent) -> QMenu:
        """The installed shells, as a menu that applies its own choice.

        Only installed shells are offered. This is the one menu where listing something
        absent would hand back exactly the failure it exists to fix.

        Built rather than shown, so the sidebar footer can ``exec`` it at its link and the
        deck context menu can nest the same thing as a submenu -- the split
        ``_build_deck_menu`` already makes, for the same reason. Each action carries
        its own deck id, which is what lets the context-menu copy act on the row that
        was right-clicked instead of on whatever is open.
        """
        menu = QMenu(parent)
        current = self._deck_shell(deck).shell_id
        for spec in shells.available():
            action = menu.addAction(f"{spec.label}  →  {spec.command}")
            action.setCheckable(True)
            action.setChecked(spec.shell_id == current)
            action.triggered.connect(
                lambda _checked=False, pid=deck.id, chosen=spec:
                    self._set_launcher(pid, chosen))
        return menu

    def _set_launcher(self, deck_id: int, spec: shells.ShellSpec) -> None:
        """Choose the shell this deck's *next* panes start with.

        Panes already running keep their shell: a live pty cannot become a different
        program, and killing someone's session to honour a menu click is not a trade
        worth making. The choice applies to the next pane, and to the layout when it is
        restored.
        """
        stored = self.db.deck_by_id(deck_id)
        if stored is None or stored.launcher_id == spec.shell_id:
            # Picking the shell already in use is not a change, and clicking the ticked
            # row of a checkable menu is how people close it.
            return
        self.db.set_launcher(deck_id, spec.shell_id)
        if self.deck is not None and self.deck.id == deck_id:
            self.deck.launcher_id = spec.shell_id
            self._refresh_launcher()
        log.info("deck %d now launches %s", deck_id, spec.shell_id)

    def _change_launcher(self) -> None:
        """The sidebar footer's "change", and the registry's ``deck.shell``."""
        if self.deck is None:
            return
        anchor = self.sidebar.launcher_change
        menu = self._shell_menu(self.deck, self)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _rename_current_deck(self) -> None:
        """The registry's ``deck.rename``, which is about the open one."""
        if self.deck is not None:
            self._rename_deck(self.deck.id)

    def _rename_deck(self, deck_id: int) -> None:
        """Rename any deck, without touching the folder it points at.

        The name is this app's label for a directory, which is why renaming it is safe and
        why it is worth having: two checkouts of the same repository are both called
        ``app`` on disk and need not be here.

        By id rather than acting on ``self.deck``, which is what kept this out of the
        sidebar's context menu when the accent picker went in -- a menu opened on one row
        would have renamed whichever deck happened to be open. The shape is
        ``_set_deck_accent``'s: touch the database, and update ``self.deck`` and the
        chrome only if the deck being renamed is the one on screen.
        """
        row = self.model.deck_row(deck_id)
        if row is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Rename deck", "Name in AsomeDeck:", text=row.name)
        name = name.strip()
        if not accepted or not name or name == row.name:
            return
        self.db.rename_deck(deck_id, name)
        open_one = self.deck is not None and self.deck.id == deck_id
        if open_one:
            self.deck = self.db.deck_by_id(deck_id) or self.deck
        self._reload_rows()
        self._refresh_rail()
        # After the reload, not before: the header takes its title from the model row, so
        # refreshing the chrome first would repaint it from the name that just changed.
        if open_one:
            self._refresh_chrome()
        # The roster names each pane's deck, so a rename is a change to it.
        self._schedule_roster()
        log.info("deck %d renamed to %s", deck_id, name)

    def _rename_selected_deck(self) -> None:
        """F2 in the deck list. The row the keyboard is on, which is not necessarily
        the deck that is open."""
        row = self.sidebar.current_row()
        if row is not None:
            self._rename_deck(row.deck_id)

    def can_move_deck(self, delta: int) -> bool:
        """Whether the open deck can move that far in the sidebar order.

        Public, and named for the registry: ``commands.py`` asks the window this rather
        than working out the ordering itself, which is not something a list of labels
        should know.
        """
        order = [deck.id for deck in self.db.decks()]
        if self.deck is None or self.deck.id not in order:
            return False
        return 0 <= order.index(self.deck.id) + delta < len(order)

    def _move_deck(self, delta: int) -> None:
        """Move the open deck up or down the sidebar.

        The other half of a column that has been written since the schema existed and read
        by nothing: ``sort_order`` decides the list order on every launch, and until now
        the only way to change it was UPDATE by hand. Drag-and-drop is the M4 form of this;
        two menu entries are what makes the column reachable at all.
        """
        if not self.can_move_deck(delta):
            return
        order = [deck.id for deck in self.db.decks()]
        here = order.index(self.deck.id)
        order[here], order[here + delta] = order[here + delta], order[here]
        self.db.reorder(order)
        self._reload_rows()
        self._refresh_rail()
        self._refresh_chrome()

    # --- the palette -------------------------------------------------------------------

    def _open_palette(self) -> None:
        """Ctrl+Shift+K. Rows are built now, not kept: what is running and what applies
        are both only true at the moment it is asked for."""
        self._refresh_commands()
        self.command_palette.open_with(self._palette_rows())

    def _palette_rows(self) -> list[Row]:
        """Everything the palette can offer, as callables it can run without knowing what
        they are.

        Commands come from the registry with the registry's own ``enabled`` deciding
        whether they appear -- so a row cannot outlive its command, and cannot show a
        chord that config.py has rebound. The other three kinds are state the window
        already holds: the deck list, the panes with shells in them, and the files of
        the deck currently open.
        """
        rows: list[Row] = []
        for command in COMMANDS:
            action = self.command_actions[command.id]
            # The palette does not offer to open the palette.
            if command.id == "view.palette" or not action.isEnabled():
                continue
            rows.append(Row("command", command.label,
                            action.shortcut().toString(
                                QKeySequence.SequenceFormat.NativeText),
                            action.trigger))

        open_id = self.deck.id if self.deck else None
        for deck in self.db.decks():
            if deck.id == open_id:
                continue
            # "no folder" rather than a blank second column, for the same reason the
            # sidebar row says it -- see DeckRow.path_text.
            rows.append(Row("deck", deck.name, deck.path or "no folder",
                            partial(self._open_deck, deck.id),
                            search=f"{deck.name} {deck.path}"))

        for deck_id, tree in self._trees.items():
            name = next((p.name for p in self.db.decks() if p.id == deck_id), "")
            for pane in tree.panes:
                if not pane.has_session and pane.kind != "editor":
                    continue
                label = pane.caption
                # The title as well as the caption: a named pane is shown by its name, and
                # somebody typing "claude" is still looking for the one running it.
                rows.append(Row("session", label, name,
                                partial(self._reveal_pane, deck_id, pane),
                                search=f"{label} {pane.session_title} {name}"))

        # `files.walk("")` is already empty rather than an error, but a deck with no
        # folder has no files to offer and asking is the clearer way to say so.
        if self.deck is not None and self.deck.path:
            root = self.deck.path
            for path in files.walk(root):
                relative = os.path.relpath(path, root).replace("\\", "/")
                rows.append(Row("file", os.path.basename(path), relative,
                                partial(self._open_file, path), search=relative))
        return rows

    def _reveal_pane(self, deck_id: int, pane: Pane) -> None:
        """Bring a pane to the front from wherever it is, switching deck if it is not
        in the one on screen."""
        if self.deck is None or self.deck.id != deck_id:
            self._open_deck(deck_id)
        self._activate_pane(pane)
        self._refresh_chrome()

    def _open_in_editor(self) -> None:
        """Show the file tree's selection in the editor pane.

        With nothing selected the button shows the tree instead of doing nothing. That is
        not a consolation prize: the selection lives in a panel that may be on another
        tab or collapsed entirely, so "there is nothing to open" and "you cannot see what
        you would be opening" are the same situation from the user's side.
        """
        path = self.sidebar.selected_file()
        if path:
            self._open_file(path)
            return
        self.sidebar.set_collapsed(False)
        self.sidebar.set_mode("files")

    def _deck_settings(self) -> None:
        """Everything that applies to the open deck, under the button that asks for it.

        The Deck menu itself, not a second list built to match it. Two lists that mean
        the same thing disagree the moment either is edited, which is the failure the
        registry exists to make impossible -- so the menu is popped up here rather than
        recreated.
        """
        self._refresh_commands()
        menu = self.title_bar.menus["Deck"]
        button = self.header.settings
        menu.popup(button.mapToGlobal(QPoint(0, button.height() + 4)))

    def _remove_current_deck(self) -> None:
        """The menu form of the sidebar's right-click entry, on the open deck."""
        if self.deck is not None:
            self._remove_deck(self.deck.id)

    def _about(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("About AsomeDeck")
        box.setText(f"AsomeDeck {__version__}")
        box.setInformativeText(
            "A desktop shell that runs your terminals in one window, grouped by deck."
        )
        fit_buttons(box)
        box.exec()

    def _terminal_menu(self, view, point) -> None:
        """Right-click inside a terminal, at the point the view reported.

        The pane it belongs to is already the active one, so the split and close rows act
        on the pane that was clicked: ``mousePressEvent`` takes focus unconditionally on
        press, ``PaneTree`` watches ``QApplication.focusChanged``, and Windows does not
        deliver the context-menu event until the release.
        """
        self._build_terminal_menu(view).exec(point)

    def _build_terminal_menu(self, view) -> QMenu:
        """A terminal's right-click menu, built but not shown.

        Split from ``_terminal_menu`` for the reason ``_build_deck_menu`` is split from
        ``_deck_menu``: ``exec`` blocks on a real event loop, so the contents have to be
        reachable without opening it.

        The last three rows are the window's **own** ``QAction`` objects rather than
        copies, which is the trick ``TitleBar.set_actions`` already uses. Two things come
        free with it: each row shows the chord that actually fires it, including one
        config.json has rebound, and ``_refresh_commands`` greying an action greys the row.
        The clipboard rows cannot do the same because they are handled by whichever view
        has focus rather than by the window, so they carry their chords as labels.
        """
        self._refresh_commands()
        menu = QMenu(self)

        copy = menu.addAction("Copy")
        _label_shortcut(copy, KEY_COPY)
        # The one row whose availability is about this moment rather than this window.
        copy.setEnabled(view.has_selection)
        copy.triggered.connect(lambda: view.copy())

        paste = menu.addAction("Paste")
        _label_shortcut(paste, KEY_PASTE)
        paste.triggered.connect(lambda: view.paste())

        select_all = menu.addAction("Select all")
        select_all.triggered.connect(lambda: view.select_all())

        menu.addSeparator()
        clear = menu.addAction("Clear scrollback")
        clear.setEnabled(view.has_scrollback)
        clear.triggered.connect(lambda: view.clear_scrollback())

        menu.addSeparator()
        for command_id in ("pane.split.right", "pane.split.down", "pane.close"):
            menu.addAction(self.command_actions[command_id])
        return menu

    def _pane_menu(self, pane, point) -> None:
        """A pane header's right-click menu. See _terminal_menu on the build/show split."""
        self._build_pane_menu(pane).exec(point)

    def _build_pane_menu(self, pane) -> QMenu:
        """The pane header menu, built but not shown.

        The five rows below the separator are the window's own ``QAction`` objects, the
        trick ``_build_terminal_menu`` explains: each shows the chord that actually fires
        it, config.json rebinds included, and ``_refresh_commands`` greying the action
        greys the row. "New session in folder…" is among them because the folder it asks
        for is a *sibling* of this pane -- the dialog opens at the active pane's directory,
        which is why the row belongs on a pane's menu and not only on the Terminal one.

        Those five act on whichever pane is *active*, so this makes the right-clicked pane
        active first. The press has already done that -- ``Pane.mousePressEvent`` fires on
        it, and Windows does not deliver the context-menu event until the release -- but
        leaning on that would make four rows depend on the order a platform happens to
        deliver two events in. Doing it here costs a call that is usually a no-op and
        makes the menu mean this pane on any platform, including from a test that never
        sent a press at all.

        The three rows above the separator have no ``Command``. Naming and hiding are
        about the pane that was right-clicked, and are reachable from here and nowhere
        else by design, so there is no chord to show and nothing for the palette to list.
        """
        tree = self._tree_of(pane)
        if tree is not None:
            tree.set_active(pane)
        self._refresh_commands()
        menu = QMenu(self)

        name = menu.addAction("Name pane…")
        name.triggered.connect(lambda: self._name_pane(pane))

        hide = menu.addAction("Hide pane")
        hide.setEnabled(tree is not None and tree.can_hide(pane))
        hide.triggered.connect(lambda: tree.hide_pane(pane))

        buried = tree.hidden_count() if tree is not None else 0
        show = menu.addAction(f"Show hidden panes ({buried})" if buried
                              else "Show hidden panes")
        show.setEnabled(bool(buried))
        show.triggered.connect(lambda: tree.show_all_panes())

        menu.addSeparator()
        for command_id in ("pane.split.right", "pane.split.down", "session.new.in",
                           "pane.zoom", "pane.close"):
            menu.addAction(self.command_actions[command_id])
        return menu

    # --- pane names ---------------------------------------------------------------------

    def _name_pane(self, pane: Pane) -> None:
        """Ask for a name for this pane, and keep asking until it is one that can be used.

        Asked again rather than refused, with what was typed still in the field: a name
        that is taken or malformed is nearly always one keystroke from a good one, and
        making somebody retype it to find that out is the dialog's fault, not theirs.
        """
        typed = pane.name
        while True:
            typed, accepted = QInputDialog.getText(
                self, "Name pane",
                "Name, for you and the agents here to call this pane by.\n"
                "Leave it empty for none.",
                text=typed)
            if not accepted:
                return
            problem = self._rename_pane(pane, typed)
            if not problem:
                return
            QMessageBox.information(self, "Name pane", problem)

    def _rename_pane(self, pane: Pane, typed: str) -> str:
        """Give a pane a name, or say why it cannot have that one. "" when it worked.

        Split from the dialog so the rules can be tested without one, the same reason
        every menu here is built apart from being shown.

        Unique among the panes over one *folder*, not one deck: ``panes.md`` lives in the
        folder and lists every pane of every deck over it, and two lines there both called
        ``designer`` would leave anybody reading it to guess. Two different folders can
        each have their own -- that is the point of keeping the roster beside the work.
        """
        name = board.clean_name(typed)
        if name is None:
            return (f"“{typed.strip()}” cannot be a pane name.\n\n"
                    "Start with a letter, then letters and digits, with a single - or _ "
                    f"between words. At most {board.NAME_MAX} characters.")
        if name == pane.name:
            return ""
        holder = next((other for other, _deck in self._panes_beside(pane)
                       if name and other.name == name), None)
        if holder is not None:
            return (f"Pane {holder.pane_id} is already called {name}.\n\n"
                    "Names are unique among the panes working in one folder, so that "
                    "an agent asked about one knows which pane is meant.")
        pane.set_name(name)
        log.info("pane %d named %r", pane.pane_id, name)
        self._schedule_save()
        self._schedule_roster()
        self._refresh_chrome()
        return ""

    def _panes_beside(self, pane: Pane) -> list[tuple[Pane, int]]:
        """Every other pane over the same folder as this one, with the deck holding it.

        The open decks only. A deck that is not open has a saved arrangement and nothing
        running, and its names are checked when it opens -- see ``_settle_names``.
        """
        deck_id = next((deck_id for deck_id, tree in self._trees.items()
                        if pane in tree.panes), None)
        if deck_id is None:
            return []
        beside: list[tuple[Pane, int]] = []
        for sibling in self._siblings_of(deck_id):
            tree = self._trees.get(sibling)
            if tree is not None:
                beside.extend((other, sibling) for other in tree.panes if other is not pane)
        return beside

    def _settle_names(self, restored: list[Pane]) -> None:
        """Drop a restored name that another pane over the same folder already holds.

        ``PaneIds.take``'s rule, for names: what a saved layout asks for is a preference
        and not a demand. Two decks over one folder can each have saved a ``designer``
        while only one was open, and the second to open is the one that gives it up --
        the change sticks the next time its layout is saved.

        Checked against panes already settled, so that a layout repeating a name within
        itself keeps the first and not neither.
        """
        restoring = set(restored)
        settled: set[Pane] = set()
        for pane in restored:
            if pane.name and any(
                    other.name == pane.name
                    for other, _deck in self._panes_beside(pane)
                    if other not in restoring or other in settled):
                log.warning("pane %d: %r is already a name over this folder; dropped",
                            pane.pane_id, pane.name)
                pane.set_name("")
            settled.add(pane)

    # --- the roster ----------------------------------------------------------------------

    def _schedule_roster(self) -> None:
        """Rewrite ``panes.md`` shortly, coalescing whatever else changes first."""
        self._roster_timer.start()

    def write_rosters(self, final: bool = False) -> None:
        """Rewrite ``panes.md`` in every folder that has, or just had, panes running.

        One file per folder, listing the running terminals of every open deck over it.
        Folders with nothing running and no roster this run are left alone, so a deck that
        has only ever been looked at gets no ``.asomedeck`` for it.

        ``final`` is the window closing: everything it lists is about to end, so every
        roster it has written is emptied rather than left naming panes that are gone.
        """
        self._roster_timer.stop()
        decks = {deck.id: deck for deck in self.db.decks()}
        folders: dict[str, tuple[str, str, list[board.RosterEntry]]] = {}
        for deck_id, tree in ({} if final else self._trees).items():
            deck = decks.get(deck_id)
            if deck is None or not deck.path:
                continue
            live = [pane for pane in tree.panes
                    if pane.kind != "editor" and pane.has_session]
            if not live:
                continue
            _path, _name, entries = folders.setdefault(
                _folder_key(deck.path), (deck.path, deck.name, []))
            entries.extend(board.RosterEntry(pane.pane_id, pane.name, deck.name)
                           for pane in live)
        for key, (path, name) in self._roster_folders.items():
            folders.setdefault(key, (path, name, []))

        for key, (path, name, entries) in folders.items():
            board.write_roster(path, entries, name)
            if entries:
                self._roster_folders[key] = (path, name)
            else:
                # Written empty once, which is all it needs: nothing over it is running,
                # so nothing would change it until something starts there again.
                self._roster_folders.pop(key, None)

    def _deck_menu(self, point) -> None:
        index = self.sidebar.view.indexAt(point)
        # deck_at, not row_at: the list holds session rows now, and offering to remove
        # "a deck" for a right-click on a terminal would name the wrong thing and then
        # do it.
        row = self.model.deck_at(index)
        if row is None:
            return
        menu, custom, _rename, remove = self._build_deck_menu(row)
        chosen = menu.exec(self.sidebar.view.viewport().mapToGlobal(point))
        if chosen is remove:
            self._remove_deck(row.deck_id)
        elif chosen is custom:
            self._pick_deck_accent(row)

    def _build_deck_menu(self, row) -> tuple[QMenu, QAction, QAction, QAction]:
        """The right-click menu for one deck, built but not shown.

        Separate from ``_deck_menu`` because ``exec`` blocks on a real event loop, and
        under test that is a hang rather than a menu -- so what the menu *contains* has to
        be reachable without opening it.

        Everything here acts on ``row``, never on ``self.deck``. Rename and the shell
        picker were both window-scoped until now, which is precisely why neither could
        appear in a menu opened on a row.
        """
        menu = QMenu(self)
        swatches = AccentSwatchRow(self.theme, current=row.accent)
        holder = QWidgetAction(menu)
        holder.setDefaultWidget(swatches)
        menu.addAction(holder)
        # A QWidgetAction does not dismiss the menu the way an item does, so the close is
        # ours to make. exec() then returns None and the identity comparisons in the caller
        # all fall through, which is what should happen: the colour was the click.
        swatches.picked.connect(
            lambda accent, pid=row.deck_id: (self._set_deck_accent(pid, accent),
                                                menu.close())
        )
        custom = menu.addAction("Custom colour…")
        menu.addSeparator()
        rename = menu.addAction("Rename…")
        _label_shortcut(rename, KEY_RENAME)
        # Connected rather than compared against exec's return value, like the swatch row
        # above it: a row that carries its own deck id is a row a test can trigger.
        rename.triggered.connect(
            lambda _checked=False, pid=row.deck_id: self._rename_deck(pid))
        deck = self.db.deck_by_id(row.deck_id)
        if deck is not None:
            shell_menu = self._shell_menu(deck, menu)
            shell_menu.setTitle("Change shell")
            menu.addMenu(shell_menu)
        menu.addSeparator()
        remove = menu.addAction(f"Remove {row.name} from AsomeDeck")
        return menu, custom, rename, remove

    def _set_deck_accent(self, deck_id: int, accent: str) -> None:
        """Give a deck its own colour in the sidebar, or "" to take the theme's.

        Sidebar only, deliberately: the rail on its row and its dot on the collapsed rail.
        Re-tinting the whole window per deck would mean rebuilding the stylesheet on
        every switch, and the thing being answered here is "which of these is which", not
        "what is this app's colour".
        """
        self.db.set_accent(deck_id, accent)
        if self.deck is not None and self.deck.id == deck_id:
            self.deck.accent = accent
        self._reload_rows()
        self._refresh_rail()
        log.info("deck %d accent set to %s", deck_id, accent or "default")

    def _pick_deck_accent(self, row) -> None:
        """Anything outside the four presets, through the platform colour dialog.

        Stored exactly as chosen. Legibility is Theme.tint's problem and it is solved at
        paint time, because a colour that reads on #131313 and a colour that reads on the
        light theme's cream cannot be the same hex -- and the theme can change after this
        dialog closes.
        """
        initial = self.theme.tint(row.accent)
        colour = QColorDialog.getColor(initial, self, f"Accent for {row.name}")
        if colour.isValid():
            self._set_deck_accent(row.deck_id, colour.name())

    def _remove_deck(self, deck_id: int) -> None:
        tree = self._trees.get(deck_id)
        if tree is not None and tree.count():
            # Removing the deck deletes its tree, and the shells parented inside it
            # go with it. That is now an ordinary consequence rather than a hazard -- but
            # it is still someone's running work, so ask rather than assume.
            QMessageBox.information(
                self, "Deck in use",
                "Close this deck's terminals before removing it.",
            )
            return
        self.db.remove_deck(deck_id)   # cascades to its layout
        if tree is not None:
            self.stack.removeWidget(tree)
            tree.deleteLater()
            del self._trees[deck_id]
        if self.deck is not None and self.deck.id == deck_id:
            self.deck = None
        self._reload_rows()
        self._refresh_rail()
        remaining = self.db.decks()
        if remaining:
            self._open_deck(remaining[0].id)
        else:
            self._load_decks()

    def _on_git(self, deck_id: int, state: GitState) -> None:
        """One poll's answer, given to every deck it is true of.

        ``GitStatus`` watches the open deck alone, so a row only ever learns its branch
        by being opened. That was the whole story while a folder held one deck. Now that
        several can share a checkout, the answer that came back describes all of them --
        it is one ``git status`` over one working tree -- and giving it to the one row that
        asked would leave its siblings blank beside it, which reads as "not a repository"
        rather than "nobody has opened this one yet".

        Keyed on the path rather than on the poll's own deck, so a row added later gets
        the branch the moment anything over that folder is polled.
        """
        self._git[deck_id] = state
        for sibling in self._siblings_of(deck_id):
            self._git[sibling] = state
            if state.known:
                self.model.set_git(sibling, state.branch, state.changed, state.clean)
        if self.deck is not None and self.deck.id == deck_id:
            self.panel.set_files(list(state.files[:12]), state.known)
            # The same status the panel lists, spread over the tree. Also the app's
            # cheapest "something on disk moved" signal, so the listing resyncs here
            # rather than on a timer of its own.
            self.sidebar.files.set_marks(self.deck.path,
                                         state.files if state.known else ())
            self.sidebar.files.refresh()
            self._refresh_header()

    # --- branches ---------------------------------------------------------------------

    def _show_branches(self) -> None:
        """The branch chip was clicked. Ask git, and answer in ``_on_branches``.

        Asked here rather than kept warm by the poll: the list is wanted about once an
        hour and a ``for-each-ref`` on every three-second tick would be a second process
        against a working tree somebody's agent is using.
        """
        if self.deck is not None and self.deck.path:
            self.branches.ask(self.deck.path)

    def _on_branches(self, path: str, branches) -> None:
        """git answered. Drop the menu under the chip, if we still want it.

        ``popup`` and not ``exec``: this runs inside a QProcess's ``finished``, and a
        modal menu there would spin a nested event loop inside a signal emission from an
        object the same handler is about to reuse. The menu is kept on the window for the
        same reason -- popped and unreferenced, Python would collect it mid-display.
        """
        if self.deck is None or path != self.deck.path or self.header.branch.isHidden():
            return
        if self._branches_menu is not None:
            self._branches_menu.deleteLater()
        self._branches_menu = self._branch_menu(list(branches))
        chip = self.header.branch
        self._branches_menu.popup(chip.mapToGlobal(QPoint(0, chip.height() + 4)))

    def _branch_menu(self, branches: list[Branch]) -> QMenu:
        """The dropdown, from a plain list -- so a test can build one without a git."""
        menu = QMenu(self)
        menu.setFont(self.theme.font("menu"))
        # Off by default, and the relative date lives in one: a branch row wide enough to
        # carry "3 weeks ago" as well as the name and the track is a menu half the window.
        menu.setToolTipsVisible(True)
        # No "Branches" heading. `addSection` puts its text on a separator, and this app's
        # sheet pins `QMenu::separator` to a 1px line -- so the heading would be an action
        # that draws nothing. It has nothing to say either: the menu opens directly under
        # a chip whose label is a branch name.
        if not branches:
            nothing = menu.addAction("No branches, or git did not answer")
            nothing.setEnabled(False)
            return menu

        # Current first, then git's own --sort=-committerdate. Only the one lift: the
        # chip already names the branch you are on, and hunting for it in a date-ordered
        # list to confirm that is a search the menu can do for you.
        ordered = sorted(branches, key=lambda branch: not branch.current)
        for branch in ordered[:BRANCH_MENU_LIMIT]:
            action = menu.addAction(f"{branch.name}   {branch.track}".rstrip())
            action.setToolTip(f"{branch.when} — click to copy the name"
                              if branch.when else "Click to copy the name")
            if branch.current:
                # Only this one is checkable. A check beside every row would be a set of
                # toggles for a menu that changes nothing; beside one it is "you are here".
                action.setCheckable(True)
                action.setChecked(True)
            # Bound as a default, not captured: every row in this loop would otherwise
            # close over the last branch. `triggered` also carries a checked flag on one
            # of its two overloads, so the slot has to be willing to be handed one.
            action.triggered.connect(
                lambda _checked=False, name=branch.name: self._copy_branch(name))

        hidden = len(ordered) - BRANCH_MENU_LIMIT
        if hidden > 0:
            more = menu.addAction(f"… and {hidden} more")
            more.setEnabled(False)
        return menu

    def _copy_branch(self, name: str) -> None:
        """What a row does, and deliberately not a checkout.

        A window full of agents mid-edit is the worst place to move a working tree from,
        and the terminal that can do it properly -- and tell you why it refused -- is one
        click away. But a dropdown of rows that do nothing at all is the
        button-that-looks-like-it-works this chrome refuses everywhere else, and the thing
        actually wanted next to a terminal is the name in a state you can paste.
        """
        QApplication.clipboard().setText(name)
        log.debug("copied branch name %r", name)

    def _siblings_of(self, deck_id: int) -> list[int]:
        """Every deck over the same folder as this one, including itself.

        Just itself, for every deck that does not share its folder with another -- which
        is all of them until somebody adds a second, and is why this is a list rather than
        a branch at each call site.
        """
        deck = self.db.deck_by_id(deck_id)
        if deck is None or not deck.path:
            # A deck with no folder shares one with nobody. `decks_at("")` would answer
            # with every other pathless deck, and the git state of a folder none of them
            # has would be written across the lot.
            return [deck_id]
        return [row.id for row in self.db.decks_at(deck.path)] or [deck_id]

    @property
    def tree(self) -> PaneTree | None:
        widget = self.stack.currentWidget()
        return widget if isinstance(widget, PaneTree) else None

    def _new_session(self, orientation: Qt.Orientation | None, cwd: str = "") -> None:
        """A terminal, optionally somewhere other than the deck's own folder.

        The empty pane is reused only when no directory was asked for. Handing a waiting
        pane a cwd would be the same gesture doing two different things depending on what
        the grid happened to look like -- "open a terminal in src" is a request for a
        terminal in src, not a request to relabel whichever rectangle is free.
        """
        tree = self.tree
        if tree is None or self.deck is None:
            return
        active = tree.active
        if orientation is None and not cwd and active is not None and not active.has_session:
            pane = active
        else:
            pane = tree.add_pane(self.theme.hex("term"), orientation,
                                 self._deck_shell(self.deck).shell_id, cwd)
        self._launch_into(pane, self.deck)

    def _new_session_in(self, start: str = "") -> None:
        """Ask for a directory, then open a terminal in it.

        Seeded at the active pane's directory rather than the deck's, because the
        reason to reach for this is usually that you are already somewhere and want a
        second shell beside it.
        """
        if self.deck is None:
            return
        active = self.tree.active if self.tree is not None else None
        here = start or (active.cwd_text if active is not None else "") or _deck_cwd(self.deck)
        path = QFileDialog.getExistingDirectory(self, "New session in folder", here)
        if path:
            self._new_session(None, os.path.normpath(path))

    # --- files ------------------------------------------------------------------------

    def _folder_menu(self, path: str, point) -> None:
        """Right-click on a folder in Files mode.

        One row, and it is the point of the whole per-pane cwd feature: the file tree is
        already showing you the directory you mean, so picking it out of a modal folder
        dialog -- which is what ``session.new.in`` makes you do -- is a worse way to say
        the same thing. That command stays for the folders this tree cannot reach, which
        is every folder outside the open deck.
        """
        menu = QMenu(self)
        here = menu.addAction(f"New session in {os.path.basename(path) or path}")
        here.setEnabled(self.tree is not None and self.deck is not None)
        here.triggered.connect(lambda: self._new_session(None, path))
        menu.exec(point)

    def _open_file(self, path: str) -> None:
        """Show a file from the tree, in this deck's one editor pane.

        One pane, reused. Browsing a tree opens files as fast as you can click, and a
        pane per click would bury the terminals the window exists for inside a minute --
        which is why "one reusable viewer" is the design rather than a tab bar full of
        files nobody closed.
        """
        tree = self.tree
        if tree is None or self.deck is None:
            return
        pane = self._editor_pane(tree) or self._new_editor_pane(tree)
        if pane is None:
            return
        opener = getattr(pane.body, "open_path", None)
        if not callable(opener) or not opener(path):
            # Refused: binary, too large, gone, or a dirty buffer the user kept. The
            # pane keeps whatever it was showing; editor.py has already said why.
            return
        self._activate_pane(pane)
        self._refresh_chrome()
        # Which file the editor pane holds is part of the arrangement -- restore() reopens
        # it by path -- and opening one moves no splitter and adds no pane, so it is the
        # one layout change PaneTree cannot see.
        self._schedule_save()

    @staticmethod
    def _editor_pane(tree: PaneTree) -> Pane | None:
        return next((pane for pane in tree.panes if pane.kind == "editor"), None)

    def _new_editor_pane(self, tree: PaneTree) -> Pane | None:
        """Find a slot for the editor and put one in it.

        An empty pane is taken over rather than split beside: a restored layout that was
        never filled, or a terminal that just exited, is a rectangle showing nothing, and
        splitting it would leave two.
        """
        active = tree.active
        if active is not None and not active.has_session and active.kind != "editor":
            pane = active
        else:
            pane = tree.add_pane(self.theme.hex("term"), Qt.Orientation.Horizontal)
        self._attach_editor(pane)
        return pane

    def _attach_editor(self, pane: Pane) -> EditorPane:
        """Put an editor body in a pane and connect it to the chrome."""
        view = EditorPane(self.theme, parent=pane)
        pane.attach_body(view)
        pane.started_at = time.monotonic()
        view.titleChanged.connect(lambda text, p=pane: self._on_editor_title(p, text))
        view.statusChanged.connect(lambda text, p=pane: self._on_editor_status(p, text))
        pane.set_session_info(title="no file", shell="editor", cmd="", pid="",
                              cwd="", alive=False)
        # set_session_info takes alive=False to mean a dead session and greys the dot.
        # This pane has no session and is not dead, so the dot is set afterwards rather
        # than by lying about aliveness to a method that means something else by it.
        pane.set_session_state(LIVE)
        return view

    def _restore_editor(self, pane: Pane) -> bool:
        """Reopen the file a saved layout had in this pane. False if it is gone.

        False matters: the caller then launches a shell into the pane instead, so a file
        deleted between sessions costs a terminal in its place rather than an empty
        rectangle with no explanation.
        """
        path, pane.restore_kind, pane.restore_path = pane.restore_path, "", ""
        if not path or not os.path.isfile(path):
            if path:
                log.info("saved editor pane dropped: %s is gone", path)
            return False
        view = self._attach_editor(pane)
        if not view.open_path(path):
            return False
        self._refresh_chrome()
        return True

    def _on_editor_title(self, pane: Pane, title: str) -> None:
        """The editor renamed itself: a file was opened, or edited, or saved.

        Straight through, unlike a terminal's title -- pane_title() exists to make sense
        of what a shell reports about itself, and a filename needs none of it.
        """
        pane.session_title = title
        pane.title_label.setText(title)
        self._refresh_chrome()

    def _editor_location(self, pane: Pane) -> str:
        return panel_path(pane.file_path, self.deck.path if self.deck else "")

    @staticmethod
    def _on_editor_status(pane: Pane, status: str) -> None:
        """Language, encoding, line ending and line count, in the pane's cwd slot.

        Reusing the footer field rather than adding a strip of its own: an editor pane
        has no cwd, and the four facts are the same shape as the one it replaces.
        """
        pane.cwd_label.setText(status)

    def _on_zoom_changed(self, tree: PaneTree) -> None:
        """Not _schedule_save: zoom changes nothing ``to_tree`` serialises.

        The focus call is the point of this existing at all. Clicking the zoom button or
        double-clicking a pane header leaves the keyboard on the header -- Qt gives click
        focus to the nearest ancestor that accepts it, which is the Pane, not the terminal
        inside it -- so without this you enlarge a shell and then cannot type into it.

        Guarded on the current page because a background deck's tree can unzoom on its
        own, when a pane in it closes. Pulling focus out of the deck on screen because
        something moved in one that is not would be worse than the problem being fixed.
        """
        self._refresh_chrome()
        if tree is self.tree:
            tree.focus_active()

    def _on_hidden_changed(self, tree: PaneTree) -> None:
        """_on_zoom_changed's twin, and for both of its reasons.

        Not _schedule_save: hiding changes nothing ``to_tree`` serialises, which is the
        whole argument for ``hiddenChanged`` being a signal of its own.

        The focus call matters more here than it does for zoom. Hiding the pane you were
        typing in moves the keyboard wherever Qt likes -- onto a header, most often --
        so without this you tuck one pane away and find yourself typing into nothing.
        Guarded on the current page for the same reason: a background deck's tree can
        lose a hidden pane when it closes, and that is no reason to pull focus out of the
        deck on screen.
        """
        self._refresh_chrome()
        if tree is self.tree:
            tree.focus_active()

    def _toggle_pane_zoom(self) -> None:
        """Give the focused pane the whole tree, or put the grid back.

        Only the command needs a slot here. The pane's own button and the double-click on
        its header go straight to the tree, because hiding siblings is entirely the tree's
        business -- unlike closing a pane, which ends a session the window owns.
        """
        tree = self.tree
        if tree is not None:
            tree.toggle_zoom(tree.active)

    def _close_active_pane(self) -> None:
        tree = self.tree
        if tree is not None and tree.active is not None:
            self._close_pane(tree.active)

    def _close_pane(self, pane: Pane) -> None:
        """End the shell and drop the pane.

        No WM_CLOSE, no waiting, no orphan: we own the pty, so killing it takes the shell
        with it. The docking path needed a whole protocol here because the shell belonged
        to a window belonging to another process.
        """
        closer = getattr(pane.body, "close_session", None)
        if callable(closer):
            closer(force=True)
        tree = self._tree_of(pane)
        if tree is not None:
            tree.close_pane(pane)
        self._refresh_chrome()

    def _tree_of(self, pane: Pane) -> PaneTree | None:
        """Which deck's tree holds this pane. Panes outlive the current page."""
        return next((t for t in self._trees.values() if pane in t.panes), None)

    def _activate_pane(self, pane: Pane) -> None:
        tree = self._tree_of(pane)
        if tree is not None:
            tree.set_active(pane)
            pane.focus_body()

    def _focus_active_pane(self) -> None:
        """Put the keyboard back where it was before an overlay took it."""
        tree = self.tree
        if tree is not None and tree.active is not None:
            tree.active.focus_body()

    def _on_pane_activated(self, _pane: Pane) -> None:
        self._refresh_chrome()

    def live_panes(self) -> list[Pane]:
        return [p for t in self._trees.values() for p in t.panes if p.has_session]

    def _toggle_maximised(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def _toggle_theme(self) -> None:
        mode = "light" if self.theme.mode == "dark" else "dark"
        self.theme = th.Theme(mode)
        self.config.theme = mode
        # Written now rather than at exit, because the exit that matters is the one that
        # does not run closeEvent. A preference that survives only a clean shutdown is the
        # same bargain the layouts used to offer, and it was not a good one.
        self.config.save()
        th.apply(QApplication.instance(), self.theme)
        self.title_bar.set_theme_label(mode)
        self.sidebar.delegate.apply_theme(self.theme)
        self.sidebar.view.viewport().update()
        # The rail dots carry per-deck colours in widget-level stylesheets, which the
        # application sheet cannot reach. A stored colour means a different value in each
        # mode, so the rail is rebuilt rather than repolished.
        self.sidebar.apply_theme(self.theme)
        self.sidebar.files.apply_theme(self.theme)
        self.command_palette.apply_theme(self.theme)
        for tree in self._trees.values():
            for pane in tree.panes:
                # A pty pane recolours immediately, running session and all -- the thing
                # docking cannot do, because there the colours are fixed at launch.
                retheme = getattr(pane.body, "apply_theme", None)
                if callable(retheme):
                    retheme(self.theme)
        log.info("theme switched to %s", mode)

    # --- chrome refresh ----------------------------------------------------------------

    def _tick(self) -> None:
        now = time.monotonic()
        tree = self.tree
        for pane in (tree.panes if tree else []):
            if pane.started_at is not None:
                pane.set_uptime(_format_uptime(now - pane.started_at))
            if pane.kind == "editor":
                checker = getattr(pane.body, "check_disk", None)
                if callable(checker):
                    checker()
        self._tick_states(now)
        active = tree.active if tree else None
        if active is None or active.started_at is None:
            return
        if active.kind == "editor":
            # Same four rows, filled with what an open file actually has. Launcher and
            # Command would otherwise report the shell of a pane that is not running one.
            self.panel.set_session(
                active.session_title or "no file", "Editor",
                _format_uptime(now - active.started_at), "",
                self._editor_location(active),
            )
            return
        spec = shells.by_id(active.launcher_id)
        self.panel.set_session(
            active.session_title or f"pane {active.pane_id}", spec.label,
            _format_uptime(now - active.started_at),
            str(getattr(active.body, "pid", "")), spec.command,
        )

    def _observed(self, pane: Pane) -> bool:
        """Whether you can actually see this pane right now.

        Four things have to hold, and dropping any one of them makes the sticky states
        lie. It has to be the active pane in its tree rather than merely present; its tree
        has to be the one in the stack, since every other deck has an "active" pane of
        its own that nobody is looking at; it must not be hidden; and the window has to be
        in the foreground -- a turn that finished while the app sat behind a browser is the
        case most worth keeping.
        """
        tree = self.tree
        return (tree is not None and pane is tree.active
                and not pane.hidden and self.isActiveWindow())

    def _tick_states(self, now: float) -> None:
        """Re-read every pane's state, in every deck -- not just the visible one.

        This walked ``self.tree`` alone until now, which is whichever deck is in the
        stack. Panes elsewhere were never re-read, so their state froze at whatever it
        happened to be when you switched away, and the sidebar went on reporting it
        confidently for as long as you stayed gone. A status light for the decks you
        are *not* looking at is the whole point of putting one in the sidebar, so the loop
        has to cover the ones you are not looking at.

        Affordable for all of them: per pane this is a subtraction and a string compare,
        and everything downstream repaints only what actually moved.
        """
        for tree in self._trees.values():
            for pane in tree.panes:
                if pane.kind == "editor":
                    # activity.py reads a *session*: a program renaming itself over a pty.
                    # An editor has neither, and asking would report it dead every tick.
                    continue
                observed = self._observed(pane)
                if observed:
                    pane.clear_alerts()
                raw = activity.state_for(
                    alive=pane.has_session,
                    title=pane.session_title,
                    since_title_change=(None if pane.title_changed_at is None
                                        else now - pane.title_changed_at),
                )
                # The edge into WAITING, not the level. A program sits at its prompt for as
                # long as you leave it there, so arming the flag on the level would re-arm
                # it the tick after it was cleared and the dot would never go back to blue.
                if raw == WAITING and pane.activity_state != WAITING:
                    if (pane.title_changes >= activity.TURN_TITLE_CHANGES
                            and not observed):
                        pane.finished = True
                    # Zeroed on the edge either way: what matters next time is how much
                    # renaming happens between here and the *next* rest.
                    pane.title_changes = 0
                pane.activity_state = raw
                pane.set_session_state(activity.displayed(
                    raw, attention=pane.attention, finished=pane.finished))
        self._refresh_status()
        self._refresh_open_children()

    def _on_bell(self, pane: Pane) -> None:
        """A program rang. Worth remembering only if you are not already looking at it.

        Sitting in the pane when it rings means you have already had the notification --
        keeping the flag would leave an amber dot on the deck you are staring at, to be
        cleared by the next tick, which is a blink and not a signal.
        """
        if self._observed(pane):
            return
        pane.note_bell()
        pane.set_session_state(activity.displayed(
            pane.activity_state, attention=True, finished=pane.finished))
        self._refresh_status()
        self._refresh_open_children()

    def _refresh_status(self) -> None:
        """Push each open deck's pane states onto its sidebar row.

        Every deck with a tree, not only ``self.deck``: a dot on a collapsed row
        earns its place by saying what is happening somewhere you are not.
        """
        for deck_id, tree in self._trees.items():
            panes = [p for p in tree.panes if p.kind != "editor"]
            self.model.set_session_count(
                deck_id,
                sum(1 for p in panes if p.has_session),
                # From the raw reading, not the displayed one: a pane that rang the bell
                # mid-run displays as attention, and it is still doing work.
                sum(1 for p in panes if activity.busy(p.activity_state)),
                activity.rollup(p.session_state for p in panes),
            )

    def _current_row(self):
        """The sidebar row for the open deck, by id.

        By id and not by position: row_for_deck counts through the flattened list, so
        once a deck above this one was expanded that number indexed past the end of
        store.rows -- an IndexError out of _refresh_header, which the git poll calls every
        three seconds, which the excepthook turns into the app exiting.
        """
        if self.deck is None:
            return None
        return self.model.deck_row(self.deck.id)

    def _refresh_header(self) -> None:
        tree = self.tree
        state = self._git.get(self.deck.id) if self.deck else None
        self.header.set_deck(
            self._current_row(), tree.count() if tree else 0,
            branch=state.branch if state and state.known else "",
            diff=state.summary if state and state.known else "",
        )

    def _refresh_chrome(self) -> None:
        self._refresh_commands()
        tree = self.tree
        panes = tree.panes if tree else []
        active = tree.active if tree else None
        self._refresh_accordion()

        self._refresh_header()
        # Every open deck, not just this one -- see _refresh_status. The block here
        # only ever updated ``self.deck``, so a background deck's count and dot were
        # written once, when you left it, and never again.
        self._refresh_status()

        self.tabs.sync(
            [(p, p.caption, self._pane_label(p), p.has_session, p.hidden) for p in panes],
            active,
        )
        # Live sessions, across every deck rather than the visible one: terminals
        # elsewhere are still running and hiding them would make the quit prompt a
        # surprise. Empty panes are not counted -- a restored layout with nothing started
        # in it has no sessions, and saying otherwise was the same overclaiming that
        # spec 14.3 cut the context meter for.
        live = sum(1 for t in self._trees.values() for p in t.panes if p.has_session)
        # deck_count, not rowCount: rowCount is what the sidebar draws, terminals of
        # expanded decks included.
        self.status.set_counts(live, self.model.deck_count())
        name = self.deck.name if self.deck else "-"
        # The number stays even when there is a name. It is what the board's claims are
        # written under, and once a program has titled the pane this is the one place left
        # on screen that still shows it.
        focus = name
        if active is not None:
            focus = f"{name} · pane {active.pane_id}"
            if active.name:
                focus += f" · {active.name}"
        self.status.set_focus_label(focus)
        self.panel.set_cards(
            [(p.caption, self._pane_label(p), p.has_session) for p in panes]
        )
        if active is None or not active.has_session:
            self.panel.clear_session()

    # --- DPI diagnostics ----------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if self._helper is not None and not self._styles_restored:
            # Qt's FramelessWindowHint clears WS_THICKFRAME, and without it Windows will
            # not run its sizing loop no matter what the hit-test answers.
            fl.restore_native_behaviours(self)
            self._styles_restored = True
        handle = self.windowHandle()
        if handle is not None and not self._screen_signal_wired:
            handle.screenChanged.connect(lambda _s: self._log_dpi("screen changed"))
            self._screen_signal_wired = True
            self._log_dpi("shown")

    def nativeEvent(self, event_type, message):  # noqa: N802 - Qt override
        """Answer WM_NCHITTEST and WM_NCCALCSIZE for the frameless window.

        ``frameless.py`` has synthesised these results since the frameless window landed,
        and nothing ever called it: no override existed here, so ``hit_test`` was dead
        code and dragging an edge did nothing. Resizing worked at all only because
        ``restore_native_behaviours`` puts ``WS_THICKFRAME`` back, which gives Windows a
        sizing loop but no border inside the client rect to start it from.

        The 6px gutter ``reserve_gutter`` holds open around the pane area exists precisely
        so this handler is the window under the cursor at the edges. That was measured
        when a terminal was a foreign window; it still holds for a QWidget, which would
        otherwise answer HTCLIENT for every pixel it covers.
        """
        if self._helper is not None:
            answer = self._helper.native_event(int(message))
            if answer is not None:
                return answer
        return super().nativeEvent(event_type, message)

    def _log_dpi(self, reason: str) -> None:
        """Record the window's DPI state.

        Much shorter than it was. The old version reported every child's DPI awareness
        and its offset inside its container, because a foreign window hosted at a
        different DPI was the one thing that could quietly break docking. A web view is
        an ordinary Qt widget and scales with the window like everything else.
        """
        host = self.windowHandle()
        screen = host.screen().name() if host is not None and host.screen() else "?"
        log.info("DPI [%s] screen=%s dpr=%.2f", reason, screen,
                 host.devicePixelRatio() if host is not None else 0.0)

    def _raise_test_exception(self) -> None:
        raise RuntimeError("deliberate crash-path test - shells must die with the process")

    # --- shutdown -----------------------------------------------------------------------

    def _schedule_save(self) -> None:
        """Write the arrangement shortly, coalescing whatever else changes first."""
        self._save_timer.start()

    def save_layouts(self) -> None:
        """Persist every deck's arrangement. Sessions are not saved; see db.py."""
        self._save_timer.stop()
        for deck_id, tree in self._trees.items():
            if tree.count():
                self.db.save_layout(deck_id, tree.to_tree())
            else:
                self.db.clear_layout(deck_id)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Save the arrangement, end the shells, quit.

        Far shorter than the docking version, which had to choose between closing
        terminals and detaching them onto the desktop, wait for stragglers, and treat a
        refusal as a special case. Owning the pty removes the choice: the shells go when
        we do, whatever we ask. What remains worth asking is whether the user meant it.
        """
        # Unsaved work first: a terminal closing loses scrollback, an editor closing
        # loses something the user typed and cannot get back.
        dirty = self._dirty_editors()
        if dirty and self.quit_action != "close":
            if not self._confirm_unsaved(dirty):
                event.ignore()
                return
        live = self.live_panes()
        if live and self.quit_action != "close":
            if not self._confirm_quit(len(live)):
                event.ignore()
                return
        # Saved before anything closes, while the arrangement still exists.
        self.save_layouts()
        self._quitting = True
        # A clone in progress goes with the app. It is only ever a copy of something elsewhere,
        # and left running it would be a hidden process finishing into a folder nobody asked
        # to keep.
        if self._clone_dialog is not None:
            self._clone_dialog.abandon()
        # Every pane, not just the live ones. `has_session` is False until the shell has
        # actually spawned, so a pane still starting up is not in `live` -- and quitting
        # inside that window used to walk straight past it, leaving the shell it was about
        # to own with nobody to close it. Asking a pane that has no session is free.
        for pane in (p for tree in self._trees.values() for p in tree.panes):
            closer = getattr(pane.body, "close_session", None)
            if callable(closer):
                closer(force=True)
        # Now, not on the timer: the timer would fire after there is no window to fire it.
        self.write_rosters(final=True)
        self.git.stop()
        self.db.end_run()
        super().closeEvent(event)

    def _dirty_editors(self) -> list[Pane]:
        """Editor panes with unsaved changes, across every deck.

        Every deck, not the visible one: switching away from a deck leaves its
        panes standing, so an edit made ten minutes ago in another tree is still there
        and still unsaved.
        """
        return [pane for tree in self._trees.values() for pane in tree.panes
                if pane.kind == "editor" and getattr(pane.body, "dirty", False)]

    def _confirm_unsaved(self, panes: list[Pane]) -> bool:
        """Ask before quitting on unsaved files. True to proceed."""
        names = ", ".join(sorted(os.path.basename(pane.file_path) or "a new file"
                                 for pane in panes))
        noun = "file has" if len(panes) == 1 else "files have"
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved changes")
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setText(f"{len(panes)} {noun} unsaved changes.")
        box.setInformativeText(names)
        save = box.addButton("Save all and quit", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard and quit", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(save)
        fit_buttons(box)
        box.exec()
        if box.clickedButton() is save:
            # Every pane, then judge: `all(...)` over a generator stops at the first
            # failure and would leave the rest unsaved after the user asked to save all.
            saved = [pane.body.save() for pane in panes]
            return all(saved)
        return box.clickedButton() is discard

    def _confirm_quit(self, count: int) -> bool:
        """Ask before ending running shells. True to proceed.

        "Detach and quit" is gone with the backend that made it possible. A pty cannot
        be handed back to the desktop, so offering that choice would be offering a lie.
        """
        noun = "terminal" if count == 1 else "terminals"
        box = QMessageBox(self)
        box.setWindowTitle("Quit AsomeDeck")
        # No stock icon: Windows blue question mark is the one element the theme cannot
        # reach, and it reads as a foreign control in an amber palette.
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setText(f"{count} {noun} still running.")
        box.setInformativeText(
            "Quitting ends the shells and anything they are running.\n\n"
            "The pane arrangement is saved, and reopening starts fresh shells in it."
        )
        go = box.addButton(f"Quit and close {noun}", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(go)
        fit_buttons(box)
        box.exec()
        return box.clickedButton() is go

    def warn_previous_run_crashed(self) -> None:
        """Spec 8, mitigation 2. A crash takes every embedded terminal with it."""
        QMessageBox.warning(
            self, "Previous session ended unexpectedly",
            "AsomeDeck did not shut down cleanly last time, so any terminals it was "
            "hosting closed with it.\n\nPane arrangements were preserved.",
        )


def _format_uptime(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {secs:02d}s"
