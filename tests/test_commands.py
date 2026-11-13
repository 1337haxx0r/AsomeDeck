"""The registry has to hold together on its own.

Menus, the palette and rebindable keys all index this list, and each of them fails in a
different and confusing way when it does not hold: a duplicate id makes a config override
land on whichever command sorted last, a duplicate shortcut makes one of the two chords
silently dead, and a menu name nothing matches is an empty dropdown.

None of this needs a window, which is the point of keeping commands.py free of Qt.
"""

from __future__ import annotations

import pytest

from asomedeck.commands import COMMANDS, MENUS, Command, by_id, chord_text, in_menu


def test_ids_are_unique():
    ids = [command.id for command in COMMANDS]
    assert len(ids) == len(set(ids))


def test_default_shortcuts_are_unique():
    """Two commands on one chord means one of them can never be pressed."""
    chords = [command.shortcut for command in COMMANDS if command.shortcut]
    assert len(chords) == len(set(chords))


def test_every_command_lands_in_a_real_menu():
    for command in COMMANDS:
        assert command.menu in MENUS, command.id


def test_every_menu_has_something_in_it():
    """A menu name with no commands is a dropdown that opens onto nothing."""
    for menu in MENUS:
        assert in_menu(menu), menu


def test_no_command_steals_a_bare_control_letter():
    """Ctrl+<letter> is a control byte the shell in a pane is entitled to receive.

    Registered window-wide it becomes unreachable everywhere, which is what Ctrl+B did to
    readline's backward-char and vim's page-up for as long as it was the sidebar key.
    Modified chords -- Ctrl+Shift, Ctrl+Alt -- are not control bytes and are safe.
    """
    for command in COMMANDS:
        parts = command.shortcut.split("+")
        assert not (parts[0] == "Ctrl" and len(parts) == 2 and len(parts[1]) == 1), command.id


def test_by_id_raises_on_a_typo():
    """A wrong id is a traceback, not a button that quietly does nothing."""
    assert by_id("view.theme").label == "Toggle theme"
    try:
        by_id("view.thmee")
    except KeyError as exc:
        assert "view.thmee" in str(exc)
    else:
        raise AssertionError("expected KeyError")


def test_in_menu_keeps_registry_order():
    terminal = in_menu("Terminal")
    assert [c.id for c in terminal] == [c.id for c in COMMANDS if c.menu == "Terminal"]


def test_enabled_defaults_to_available():
    assert Command("x.y", "X", "View").enabled(object()) is True


def test_chord_text_speaks_the_platforms_own_spelling():
    """A chord is written "Ctrl+Shift+D" and pressed differently depending on the platform.

    Qt maps Ctrl to Cmd on macOS, which is what makes every default in this registry a
    native chord there for free -- but it also means the *label* has to be translated or
    the UI advertises a key that does nothing. That is the same failure as the old
    ``Ctrl+K`` search chip, which named a chord no handler answered.
    """
    pytest.importorskip("PySide6.QtGui")
    from asomedeck.platform_ import IS_MACOS

    rendered = chord_text("Ctrl+Shift+D")
    assert rendered
    if IS_MACOS:
        # The Cmd glyph, and no literal "Ctrl" left anywhere in it.
        assert "⌘" in rendered
        assert "Ctrl" not in rendered
    else:
        assert rendered == "Ctrl+Shift+D"


def test_chord_text_passes_the_empty_chord_through():
    """Commands with no safe chord get none, and the chip must not render "None"."""
    assert chord_text("") == ""
