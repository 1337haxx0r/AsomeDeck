"""Every action the window offers, named once.

Menus, a Ctrl+K palette and rebindable keys look like three features and are one missing
object. Without a registry each of them restates the same list: ``_build_actions`` holds
a tuple of ``(label, shortcut, slot)``, ``TitleBar`` holds five menu names, and a config
file would need a third naming scheme to say *which* shortcut the user just rebound. Three
copies of one list disagree the moment anything is added to it.

So the list lives here, and everything downstream reads it. The window supplies a callable
per ``Command.id``; ``TitleBar`` groups on ``Command.menu``; the palette filters on
``Command.label``; and config.py overrides ``Command.shortcut`` **by id** -- which is only
possible because commands have ids at all. That is the whole reason this module exists
before the features that use it.

Deliberately no Qt import. A registry is data, and keeping it data means
``tests/test_commands.py`` runs on a machine with no window station, alongside the rest of
the headless suite.

What is *not* here is behaviour. A ``Command`` says what something is called, where it
appears and what it costs to press; ``MainWindow.command_handlers`` says what it does. The
split is what lets this file be checked without constructing a window.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: The menu bar, in order. Owned here rather than by chrome.py because a menu is now
#: exactly "the commands whose ``menu`` is this string" -- a name in one list and no
#: commands in the other is the empty dropdown this module exists to prevent.
MENUS: tuple[str, ...] = ("File", "Deck", "Terminal", "View", "Help")


def _always(_window: Any) -> bool:
    return True


@dataclass(frozen=True, slots=True)
class Command:
    """One thing the window can be asked to do.

    ``enabled`` is given the window and answers whether the command applies right now --
    "Split right" with no deck open has nothing to split. Menus grey out on it and the
    palette hides on it, so the reason a command is unavailable is written once here
    instead of being re-derived at each call site.

    ``shortcut`` is a default, not a fact. config.py will override it by id, which is why
    nothing downstream may hardcode a key sequence.
    """

    id: str
    label: str
    menu: str
    shortcut: str = ""
    enabled: Callable[[Any], bool] = field(default=_always)


def _has_deck(window: Any) -> bool:
    return getattr(window, "deck", None) is not None


def _has_tree(window: Any) -> bool:
    return getattr(window, "tree", None) is not None


def _can_clone(window: Any) -> bool:
    """Whether Git is here to do it. The window knows: this module does not look at the disk."""
    check = getattr(window, "can_clone", None)
    return bool(check()) if callable(check) else False


def _can_move(delta: int) -> Callable[[Any], bool]:
    """Whether the open deck has anywhere to go in that direction.

    Asked of the window rather than worked out here: this module knows what commands are
    called, not what order decks sit in. Greying matters more than usual for these two
    -- at the top of the list "Move up" is not unavailable in some subtle sense, it is a
    button that would visibly do nothing.
    """
    def check(window: Any) -> bool:
        mover = getattr(window, "can_move_deck", None)
        return bool(mover(delta)) if callable(mover) else False

    return check


#: The registry. Order within a menu is the order the menu shows.
#:
#: On shortcuts: every default here is either a ``Ctrl+Shift`` chord or a ``Ctrl+Alt``
#: one, and that is not a style choice. A bare ``Ctrl+<letter>`` is a control byte a shell
#: is entitled to receive -- binding one window-wide makes it unreachable inside every
#: pane, which is what ``Ctrl+B`` did to readline's backward-char, vim's page-up and a
#: tmux prefix for as long as it was the sidebar key. Commands with no safe chord get no
#: chord; a menu entry is not worth breaking a terminal for.
#: The ``project.*`` ids below are deliberately not ``deck.*``. An id is the one part of a
#: Command that is *stored*: config.py overrides shortcuts by it, so a user who rebound
#: ``project.next`` has that id sitting in their config.json. Renaming would not error --
#: it would silently drop the binding and hand back the default, which is the worst shape
#: a break can take. The labels beside them are what anybody actually reads, and those say
#: deck. ``deck.new`` below is spelled the new way for the opposite reason: it has never
#: shipped, so no config.json anywhere holds it and there is no binding to break.
COMMANDS: tuple[Command, ...] = (
    # --- File ---
    Command("project.add", "Add deck…", "File"),
    #: A deck over a folder that does not exist yet, because this is what makes it.
    Command("deck.clone", "Clone repository…", "File", enabled=_can_clone),
    #: No folder picker behind this one. A deck with terminals and nothing under them --
    #: no file tree, no git chip, no claims board -- for the work that is not a checkout.
    Command("deck.new", "New empty deck…", "File"),
    Command("app.quit", "Quit", "File"),

    # --- Deck ---
    Command("project.next", "Next deck", "Deck", "Ctrl+Alt+Right", _has_deck),
    Command("project.prev", "Previous deck", "Deck", "Ctrl+Alt+Left", _has_deck),
    Command("project.shell", "Change shell…", "Deck", enabled=_has_deck),
    Command("project.rename", "Rename deck…", "Deck", enabled=_has_deck),
    Command("project.move.up", "Move up", "Deck", enabled=_can_move(-1)),
    Command("project.move.down", "Move down", "Deck", enabled=_can_move(1)),
    Command("project.remove", "Remove from AsomeDeck", "Deck", enabled=_has_deck),

    # --- Terminal ---
    Command("session.new", "New session", "Terminal", "Ctrl+Shift+T", _has_tree),
    #: No default chord. A terminal in the deck's own folder is the gesture worth a
    #: key; one that stops to ask which folder is a menu item, and a chord that opens a
    #: modal dialog is worse than no chord at all when it is pressed by accident.
    Command("session.new.in", "New session in folder…", "Terminal", enabled=_has_tree),
    Command("pane.split.right", "Split right", "Terminal", "Ctrl+Shift+D", _has_tree),
    Command("pane.split.down", "Split down", "Terminal", "Ctrl+Shift+E", _has_tree),
    #: Ctrl+Alt+Z rather than the Ctrl+Shift+Z this obviously wants to be. That chord is
    #: Windows' redo, and QPlainTextEdit claims it -- along with Ctrl+Z and Ctrl+Y -- by
    #: accepting the ShortcutOverride, so a window action bound to it is simply dead
    #: whenever an editor pane has focus. Zoom would work in a terminal and silently do
    #: nothing in a file, which is this file's own complaint about Ctrl+B aimed at the
    #: editor instead of a shell. The cost of Ctrl+Alt is that Windows delivers AltGr as
    #: Ctrl+Alt, so on a layout where AltGr+Z composes a character this takes it -- the
    #: same trade Ctrl+Alt+B and Ctrl+Alt+Left/Right already made, and rebindable by id.
    Command("pane.zoom", "Toggle pane zoom", "Terminal", "Ctrl+Alt+Z", _has_tree),
    Command("pane.close", "Close pane", "Terminal", "Ctrl+Shift+W", _has_tree),

    # --- View ---
    #: Ctrl+Shift+K and not the Ctrl+K the title bar chip has always advertised. Ctrl+K is
    #: readline's kill-line and a control byte a shell is entitled to receive, so binding
    #: it window-wide would break it inside every pane -- the exact harm this file was
    #: written to stop repeating. The chip reads its label from here, so it now says what
    #: actually works rather than what was drawn once.
    Command("view.palette", "Command palette…", "View", "Ctrl+Shift+K"),
    Command("view.sidebar", "Toggle decks", "View", "Ctrl+Shift+B"),
    Command("view.panel", "Toggle session panel", "View", "Ctrl+Alt+B"),
    Command("view.theme", "Toggle theme", "View"),

    # --- Help ---
    Command("help.about", "About AsomeDeck", "Help"),
)

_BY_ID: dict[str, Command] = {command.id: command for command in COMMANDS}


def by_id(command_id: str) -> Command:
    """The command with this id.

    Raises rather than returning None: an unknown id is a typo in a menu, a palette row or
    a config key, and every one of those is better as a traceback at startup than as a
    button that silently does nothing.
    """
    try:
        return _BY_ID[command_id]
    except KeyError:
        raise KeyError(f"no such command: {command_id!r}") from None


def in_menu(menu: str) -> tuple[Command, ...]:
    """This menu's commands, in registry order."""
    return tuple(command for command in COMMANDS if command.menu == menu)


def chord_text(chord: str) -> str:
    """A chord in the spelling this platform's users actually press.

    ``"Ctrl+Shift+D"`` is how a chord is *written* -- in this registry, in config.json, and
    in QKeySequence -- but it is not what a Mac user presses, because Qt maps Ctrl to Cmd
    there. Left alone the UI advertises ``ctrl+shift+d`` beside a control that only answers
    to Cmd+Shift+D, which is the same class of lie as the ``Ctrl+K`` search chip that no
    handler ever answered: a label naming a key that does nothing.

    ``NativeText`` is Qt's own answer, and it renders the glyphs macOS uses in its menus --
    so the status bar hint and the command palette agree with the menu bar, and with every
    other Mac application. On Windows it returns the string unchanged.

    QtGui is imported here rather than at module scope on purpose: this module is a plain
    registry of dataclasses that the headless half of the test suite imports directly, and
    it is worth keeping it that way for one function.
    """
    from PySide6.QtGui import QKeySequence

    if not chord:
        return ""
    return QKeySequence(chord).toString(QKeySequence.SequenceFormat.NativeText)
