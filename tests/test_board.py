"""The claims board, and the identity a pane hands to the shell inside it.

Two panes in one deck share a working tree, and the board is the convention that keeps
two agents out of the same file. What is testable about a convention is the part the app
owns: that the file gets created, that it is *never* rewritten once a session might be
holding claims in it, that git cannot see any of it, and that a shell is told which pane
it is.

No Qt. This is filesystem work, and the module is Qt-free so that stays true.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from asomedeck import board

# --- seeding ---------------------------------------------------------------------------


def test_it_creates_the_board_and_says_where(tmp_path):
    path = board.ensure(tmp_path, "Widget")
    assert path == tmp_path / board.DIR_NAME / board.BOARD_NAME
    assert path.is_file()
    assert "Widget" in path.read_text(encoding="utf-8")


def test_the_deck_name_falls_back_to_the_directory(tmp_path):
    deck = tmp_path / "some-repo"
    deck.mkdir()
    text = board.ensure(deck).read_text(encoding="utf-8")
    assert "some-repo" in text


def test_it_never_rewrites_a_board_that_exists(tmp_path):
    """The load-bearing one. Every launch calls ensure(), and by the second pane the file
    may hold claims a live session wrote seconds ago -- which is exactly the moment a
    reseed would destroy them."""
    board.ensure(tmp_path, "Widget")
    path = board.board_path(tmp_path)
    path.write_text("- pane 2 -- src/thing.py -- mid-turn\n", encoding="utf-8")

    board.ensure(tmp_path, "Widget")
    assert path.read_text(encoding="utf-8") == "- pane 2 -- src/thing.py -- mid-turn\n"


def test_it_is_safe_to_call_on_every_launch(tmp_path):
    first = board.ensure(tmp_path, "Widget")
    for _ in range(5):
        assert board.ensure(tmp_path, "Widget") == first


def test_the_directory_ignores_itself(tmp_path):
    """A dot-directory that git can see turns every checkout it touches permanently dirty,
    and the sidebar's git chip counts untracked paths. `*` covers the .gitignore naming
    it, so the whole directory disappears rather than arriving as one tracked file."""
    board.ensure(tmp_path, "Widget")
    ignore = tmp_path / board.DIR_NAME / ".gitignore"
    assert ignore.is_file()
    assert "*" in ignore.read_text(encoding="utf-8").split()


def test_the_agents_md_snippet_is_seeded_beside_it(tmp_path):
    """The app will not edit AGENTS.md -- it is the user's file and usually in git -- so
    the paragraph that tells an agent the board exists is left where it can be found."""
    board.ensure(tmp_path, "Widget")
    snippet = (tmp_path / board.DIR_NAME / board.SNIPPET_NAME).read_text(encoding="utf-8")
    assert board.BOARD_NAME in snippet
    assert board.ROSTER_NAME in snippet
    assert board.PANE_VAR in snippet


def test_the_snippet_is_for_every_agent_and_not_one(tmp_path):
    """A pane runs whatever agent you start in it. The snippet is addressed to AGENTS.md,
    and tells Claude Code -- which reads CLAUDE.md -- how to pull the same file in, rather
    than being addressed to one vendor's file and leaving the rest to guess."""
    board.ensure(tmp_path, "Widget")
    snippet = (tmp_path / board.DIR_NAME / board.SNIPPET_NAME).read_text(encoding="utf-8")
    assert snippet.startswith("# Paste this into AGENTS.md")
    assert "@AGENTS.md" in snippet


def test_an_untouched_claude_md_snippet_from_an_older_build_is_retired(tmp_path):
    """Left behind, it would sit beside the new one telling you to paste into a different
    file. It is ours as long as it is exactly what we wrote."""
    old = tmp_path / board.DIR_NAME / board.OLD_SNIPPET_NAME
    old.parent.mkdir()
    old.write_text(board.OLD_SNIPPET, encoding="utf-8")

    board.ensure(tmp_path, "Widget")

    assert not old.exists()
    assert (tmp_path / board.DIR_NAME / board.SNIPPET_NAME).is_file()


def test_an_edited_claude_md_snippet_is_left_alone(tmp_path):
    """The directory is ours; an edit somebody made inside it is not."""
    old = tmp_path / board.DIR_NAME / board.OLD_SNIPPET_NAME
    old.parent.mkdir()
    old.write_text(board.OLD_SNIPPET + "\nmy own note\n", encoding="utf-8")

    board.ensure(tmp_path, "Widget")

    assert old.read_text(encoding="utf-8").endswith("my own note\n")


def test_the_board_explains_itself(tmp_path):
    """Nothing enforces the protocol, so the file has to carry it. An agent handed only
    $ASOMEDECK_BOARD and no CLAUDE.md should still learn what to do from reading it."""
    text = board.ensure(tmp_path, "Widget").read_text(encoding="utf-8")
    assert board.PANE_VAR in text
    assert "## Claims" in text


def test_an_unwritable_deck_gets_no_board_and_no_traceback(tmp_path, monkeypatch):
    """A read-only share should cost you the board, not the terminal. config.save sets the
    precedent: a store that cannot be written is a warning, not a failed launch."""
    def refuse(*_args, **_kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "mkdir", refuse)
    assert board.ensure(tmp_path, "Widget") is None


def test_a_board_that_already_exists_survives_a_later_failure(tmp_path, monkeypatch):
    """Seeding writes three files, and the board is only the last of them. One that is
    already there stays usable however the rest of the pass goes."""
    board.ensure(tmp_path, "Widget")
    (tmp_path / board.DIR_NAME / board.SNIPPET_NAME).unlink()

    original = Path.write_text

    def refuse(self, *args, **kwargs):
        if self.name == board.SNIPPET_NAME:
            raise OSError("read-only file system")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", refuse)
    assert board.ensure(tmp_path, "Widget") == board.board_path(tmp_path)


# --- what the shell is told --------------------------------------------------------------


def test_the_pane_learns_its_own_number():
    env = board.env_for(pane_id=3, deck_name="Widget", board=r"C:\repo\.asomedeck\board.md")
    assert env[board.PANE_VAR] == "3"
    assert env[board.DECK_VAR] == "Widget"
    assert env[board.BOARD_VAR] == r"C:\repo\.asomedeck\board.md"


def test_pane_zero_is_still_a_pane():
    # The first pane of every tree. A falsy id must not read as an absent one.
    assert board.env_for(pane_id=0)[board.PANE_VAR] == "0"


def test_a_board_that_could_not_be_written_is_not_named():
    """Omitted rather than empty: a script testing whether the name is set would find it
    set, and then fail to read a file that is not there."""
    env = board.env_for(pane_id=1, deck_name="Widget", board=None)
    assert board.BOARD_VAR not in env


def test_an_unnamed_deck_is_not_named_either():
    assert board.DECK_VAR not in board.env_for(pane_id=1)


def test_a_path_object_survives_the_trip(tmp_path):
    env = board.env_for(pane_id=1, board=board.board_path(tmp_path))
    # Spawning takes an environment of strings. A Path here reaches the spawn unchanged.
    assert isinstance(env[board.BOARD_VAR], str)
    assert env[board.BOARD_VAR].endswith(board.BOARD_NAME)


# --- pane names --------------------------------------------------------------------------


@pytest.mark.parametrize("typed, kept", [
    ("designer", "designer"),
    ("Designer", "designer"),            # nobody should have to remember the capitals
    ("  tests  ", "tests"),
    ("front end", "front-end"),          # spaces become the separator a name may hold
    ("front   end", "front-end"),
    ("api_v2", "api_v2"),
    ("", ""),                            # empty is a valid answer: no name
    ("   ", ""),
])
def test_a_name_is_kept_in_one_form(typed, kept):
    assert board.clean_name(typed) == kept


@pytest.mark.parametrize("typed", [
    "3",              # would read as a pane number
    "2nd",
    "-lead",
    "a--b",           # the claim line's own separator
    "a -- b",
    "ok|no",          # a table cell's
    "ünï",
    "x" * (board.NAME_MAX + 1),
])
def test_what_cannot_be_a_name_is_refused(typed):
    assert board.clean_name(typed) is None


def test_the_longest_name_allowed_is_allowed():
    assert board.clean_name("x" * board.NAME_MAX) == "x" * board.NAME_MAX


# --- the roster --------------------------------------------------------------------------


def test_the_roster_lists_panes_by_number(tmp_path):
    """By number, because that is what an agent starts from: it is in its environment."""
    text = board.render_roster("repo", [
        board.RosterEntry(5, "", "Repo 2"),
        board.RosterEntry(3, "designer", "Repo"),
    ])
    rows = [line for line in text.splitlines() if line.startswith("| ") and "pane" not in line]
    assert rows == ["| 3 | designer | Repo |", "| 5 |  | Repo 2 |"]


def test_a_roster_with_nobody_in_it_says_so():
    text = board.render_roster("repo", [])
    assert board.ROSTER_EMPTY.strip() in text
    assert "| pane |" not in text


def test_a_deck_name_cannot_break_the_table():
    """Pane names cannot hold a pipe -- clean_name refuses one -- but deck names are
    free text, and one would otherwise open a fourth column."""
    text = board.render_roster("repo", [board.RosterEntry(1, "", "a|b")])
    assert "| 1 |  | a\\|b |" in text


def test_the_roster_explains_itself():
    """An agent pointed at it with no instructions should still learn what it is and
    that it must not write to it."""
    text = board.render_roster("repo", [])
    assert board.PANE_VAR in text
    assert "Do not edit" in text


def test_writing_a_roster_replaces_the_last_one(tmp_path):
    board.write_roster(tmp_path, [board.RosterEntry(1, "designer", "Repo")], "Repo")
    path = board.write_roster(tmp_path, [board.RosterEntry(2, "tests", "Repo")], "Repo")

    assert path == board.roster_path(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "tests" in text and "designer" not in text
    # Moved into place, so nothing is left beside it.
    assert sorted(p.name for p in path.parent.iterdir()) == sorted(
        [".gitignore", board.BOARD_NAME, board.SNIPPET_NAME, board.ROSTER_NAME])


def test_a_roster_keeps_the_directory_out_of_git(tmp_path):
    """Written through ensure, so a .asomedeck deleted mid-session comes back ignored."""
    board.write_roster(tmp_path, [], "Repo")
    assert (tmp_path / board.DIR_NAME / ".gitignore").is_file()


def test_a_roster_that_cannot_be_written_is_a_warning_not_a_crash(tmp_path, monkeypatch):
    """Windows refuses to replace a file another process has open, and an agent reading
    the roster is exactly that. The next change rewrites it."""
    board.ensure(tmp_path, "Repo")

    def refuse(*_args, **_kwargs):
        raise PermissionError("in use")

    monkeypatch.setattr(board.os, "replace", refuse)
    assert board.write_roster(tmp_path, [], "Repo") is None
    assert not any(p.name.endswith(".tmp") for p in (tmp_path / board.DIR_NAME).iterdir())
