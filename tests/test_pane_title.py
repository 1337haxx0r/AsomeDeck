"""What to call a pane, given what the program inside it says it is.

Programs announce themselves through OSC 0/2, the emulator has captured that from the
first commit, and until now nothing listened -- so a pane running Claude Code still called
itself "Git Bash".

The rule is deliberately narrow. It either repeats what the program said or falls back to
the pane's number; it never invents a name, which is the same constraint chrome.py states
for every other field. The one thing it drops is a shell announcing the directory the pane
is already reporting, because the pane footer shows the cwd and the deck header shows
it again -- printing it a third time in the one field that could say what is *running* is
worse than the placeholder it replaces.

A shell that has moved is the case that survives, and for a while it did not: both arms of
the comparison returned the fallback, so every path-like title became "pane N" and the two
lines computing the comparison were dead.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from asomedeck.ui.main_window import pane_title  # noqa: E402

CWD = r"D:\PY\AsomeDeck"


def test_a_program_that_names_itself_is_believed():
    """Observed from a real run: Claude Code sends this on startup."""
    assert pane_title("✳ Claude Code", 1, CWD) == "✳ Claude Code"


@pytest.mark.parametrize("reported", [
    "MINGW64:/d/PY/AsomeDeck",     # what MSYS bash sends, observed
    "/d/PY/AsomeDeck",
    r"D:\PY\AsomeDeck",
    "D:/PY/AsomeDeck/",
])
def test_a_shell_announcing_this_panes_directory_is_not_a_name(reported):
    r"""Both spellings of the same place count -- MSYS writes D:\PY\x as /d/PY/x."""
    assert pane_title(reported, 3, CWD) == "pane 3"


@pytest.mark.parametrize("reported", [
    "MSYS:/c/users/someone",
    "~/decks/thing",
    r"C:\PY\AsomeDeck",            # same tail, different drive: a different directory
])
def test_a_shell_that_moved_keeps_saying_where_it_is(reported):
    """The footer shows the cwd the pane *launched* in and never updates, so a title
    naming somewhere else is the only place a cd shows up at all."""
    assert pane_title(reported, 3, CWD) == reported


def test_an_unknown_cwd_still_drops_a_bare_path():
    """Nothing to compare against, and a path that might be the cwd is worth less than the
    pane's own number."""
    assert pane_title("/d/PY/AsomeDeck", 3, "") == "pane 3"


def test_no_title_yet_keeps_the_pane_number():
    assert pane_title("", 0, CWD) == "pane 0"
    assert pane_title("   ", 0, CWD) == "pane 0"


def test_a_colon_in_a_real_title_is_not_mistaken_for_a_shell_prefix():
    """`npm: build` and `vim: notes.md` are programs talking, not shells."""
    assert pane_title("npm: build", 2, CWD) == "npm: build"
    assert pane_title("vim: notes.md", 2, CWD) == "vim: notes.md"


def test_a_program_name_that_happens_to_be_uppercase_survives():
    assert pane_title("BUILD running", 2, CWD) == "BUILD running"


def test_the_title_is_never_invented():
    """Whatever comes back is either what was reported or the fallback -- never a guess
    assembled from the shell, the cwd or anything else the app happens to know."""
    for reported in ("", "✳ Claude Code", "MINGW64:/x", "npm run dev"):
        assert pane_title(reported, 7, CWD) in (reported.strip(), "pane 7")
