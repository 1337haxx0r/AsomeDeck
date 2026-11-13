"""What the window remembers between launches.

Every one of these was a setting the app already had and immediately forgot. The theme
toggle recoloured everything -- chrome, sidebar, running terminals -- and wrote nothing,
so every restart reverted to dark; the shortcuts were module constants under a comment
saying "hardcoded until config.py lands", and config.py did not exist.

The window is given a config or it makes a path-less one, which is the seam these tests
use: a real file under tmp_path proves the write, and the default proves that a window
built without one -- every other test, and --smoke -- touches nothing on the machine.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402

from asomedeck import config as cfg  # noqa: E402
from asomedeck.commands import by_id  # noqa: E402


def test_the_theme_survives_the_toggle(make_window, tmp_path):
    path = tmp_path / "config.json"
    window = make_window(cfg.load(path))

    window._toggle_theme()

    assert window.theme.mode == "light"
    assert json.loads(path.read_text(encoding="utf-8"))["theme"] == "light"
    assert cfg.load(path).theme == "light"


def test_a_remembered_theme_is_what_the_window_opens_in(make_window):
    window = make_window(cfg.Config(theme="light"))
    assert window.theme.mode == "light"
    # The chip used to be built with the literal "dark", so it lied on every light launch.
    assert window.title_bar.theme_button.text() == "light"


def test_a_window_with_no_config_writes_nothing(make_window, tmp_path, monkeypatch):
    """The default a test or a smoke run gets. Nothing may reach the real file."""
    monkeypatch.setattr(cfg, "default_path", lambda: tmp_path / "config.json")
    make_window()._toggle_theme()
    assert not (tmp_path / "config.json").exists()


def test_a_rebound_key_reaches_the_action(make_window):
    """The point of giving commands ids. The file names `pane.close`, not "Close pane" and
    not a position in a list, so a rebind survives a rename and an insertion."""
    window = make_window(cfg.Config(keys={"pane.close": "Ctrl+Alt+W"}))

    assert window.command_actions["pane.close"].shortcut().toString() == "Ctrl+Alt+W"
    assert by_id("pane.close").shortcut == "Ctrl+Shift+W"   # the registry is untouched


def test_an_unbound_command_keeps_no_chord_at_all(make_window):
    """Someone who wants Ctrl+Shift+W back for the shell inside the pane empties it."""
    window = make_window(cfg.Config(keys={"pane.close": ""}))
    assert window.command_actions["pane.close"].shortcut().isEmpty()


# --- the arrangement --------------------------------------------------------------------

def test_a_layout_change_schedules_a_write(window):
    """save_layouts used to run from closeEvent and nowhere else, so a crash lost every
    split and close since the last clean exit -- while the recovery dialog told the user
    "Pane arrangements were preserved"."""
    assert not window._save_timer.isActive()

    window.tree.add_pane()

    assert window._save_timer.isActive()


def test_the_write_lands_and_stops_the_timer(window):
    """Debounced rather than immediate: dragging a splitter emits continuously, and a
    transaction per mouse-move is a database write for every pixel."""
    window.tree.add_pane()
    window.tree.add_pane(orientation=Qt.Orientation.Horizontal)

    window.save_layouts()

    saved = window.db.load_layout(window.deck.id)
    assert saved is not None
    assert not window._save_timer.isActive()
    assert window.db.load_layout(window.deck.id) == window.tree.to_tree()


def test_opening_a_file_is_a_layout_change_too(window, tmp_path):
    """restore() reopens the editor pane by path, so which file it holds is part of the
    arrangement -- and it is the one change the tree itself cannot see, because it moves
    no splitter and adds no pane."""
    target = tmp_path / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    window.tree.add_pane()
    window.save_layouts()
    assert not window._save_timer.isActive()

    window._open_file(str(target))

    assert window._save_timer.isActive()

