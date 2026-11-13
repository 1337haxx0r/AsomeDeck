"""The wiring: does a launched pane actually carry its identity into the shell?

`board.py` and `tests/test_board.py` cover what the identity *is*. This covers the half
that has failed in this codebase before -- code that is correct and never invoked. The
environment is the only channel into a pty that is not the byte stream, and it is written
once, at spawn, in one place. If `_launch_into` stops merging it, nothing else notices:
every terminal still works, and the board simply never gets used.

No shell is started. `_launch_into` imports the renderer inside the function, by name, on
every call -- the same indirection that let an xterm.js pane be swapped for a painted one
-- so a fake body substituted on the module answers instead, and records what it was
handed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Signal  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

from asomedeck import board, shells  # noqa: E402
from asomedeck.shells import ShellSpec  # noqa: E402
from asomedeck.ui import term_pane  # noqa: E402


class FakeTerminal(QWidget):
    """A body with the two signals a session must have, and no pty behind it."""

    started = Signal()
    exited = Signal(int)

    def __init__(self, argv, cwd, theme=None, env=None, parent=None):
        super().__init__(parent)
        self.argv, self.cwd, self.env = list(argv), cwd, dict(env or {})

    def is_alive(self) -> bool:
        return False


@pytest.fixture
def launched(window, monkeypatch):
    """Launch one pane into the fixture's deck and hand back the body it built."""
    monkeypatch.setattr(term_pane, "TerminalPane", FakeTerminal)
    pane = window.tree.add_pane()
    assert window._launch_into(pane, window.deck)
    return pane, pane.body


def test_the_shell_is_told_which_pane_it_is(launched):
    pane, body = launched
    assert body.env[board.PANE_VAR] == str(pane.pane_id)


def test_the_shell_is_told_which_deck_it_is_in(launched, window):
    _pane, body = launched
    assert body.env[board.DECK_VAR] == window.deck.name


def test_the_shell_is_told_where_the_board_is(launched, tmp_path):
    _pane, body = launched
    assert body.env[board.BOARD_VAR] == str(board.board_path(tmp_path))


def test_launching_seeds_the_board(launched, tmp_path):
    """Seeded on launch rather than on adding a deck: a deck with no terminal in it
    has nothing to coordinate, and "already done once" has nowhere to be recorded that a
    deleted directory would not outlive."""
    assert board.board_path(tmp_path).is_file()


def _launch_with_shell_env(window, monkeypatch, extra: dict[str, str]):
    """Launch a pane whose shell definition carries `extra`, and answer the pane."""
    spec = shells.default()
    monkeypatch.setattr(term_pane, "TerminalPane", FakeTerminal)
    monkeypatch.setattr(
        shells, "resolve",
        lambda _id: ShellSpec(spec.shell_id, spec.label, spec.argv, {**spec.env, **extra}),
    )
    pane = window.tree.add_pane()
    assert window._launch_into(pane, window.deck)
    return pane


def test_the_shell_definition_still_reaches_the_pty(window, monkeypatch):
    """Identity is merged over `ShellSpec.env`, not instead of it. Git Bash needs
    CHERE_INVOKING to stay in the deck directory, and dropping it would put every pane
    in the user's home."""
    pane = _launch_with_shell_env(window, monkeypatch, {"CHERE_INVOKING": "1"})
    assert pane.body.env["CHERE_INVOKING"] == "1"
    assert pane.body.env[board.PANE_VAR] == str(pane.pane_id)


def test_identity_wins_over_a_shell_that_sets_the_same_name(window, monkeypatch):
    """Nothing in shells.py sets an ASOMEDECK_ name today. This is about what a launcher
    added later could do to a pane's own number without anybody noticing it had."""
    pane = _launch_with_shell_env(window, monkeypatch, {board.PANE_VAR: "99"})
    assert pane.body.env[board.PANE_VAR] == str(pane.pane_id)


def test_a_deck_that_cannot_hold_a_board_still_launches(window, monkeypatch):
    """A read-only share costs you the board, not the terminal -- and the variable is
    absent rather than naming a file that is not there."""
    monkeypatch.setattr(term_pane, "TerminalPane", FakeTerminal)
    monkeypatch.setattr(board, "ensure", lambda *_a, **_k: None)

    pane = window.tree.add_pane()
    assert window._launch_into(pane, window.deck)
    assert board.BOARD_VAR not in pane.body.env
    assert pane.body.env[board.PANE_VAR] == str(pane.pane_id)
