r"""The claims board: how two agents in one checkout keep out of each other's way.

Panes are isolated by construction. Each is a shell on its own pseudo-console
(``term/conpty.py``) with no channel to any other, and the only thing that crosses a pane
boundary today runs one way: the window reads each pane's title and derives a state from
it (``term/activity.py``). The pane itself learns nothing, about us or about its siblings.

That isolation stops being a virtue the moment two of those shells are running agents,
because they are *not* isolated where it counts. A pane launches into ``deck.path``
unless it was deliberately sent elsewhere (``Pane.cwd``), and "elsewhere" is almost always
a subdirectory of the same checkout -- so the panes of a deck share one working tree
whichever folder each of them starts in. Two agents editing one file is not a race this
app can see, let alone arbitrate.

The board stays at ``deck.path`` for exactly that reason. It is seeded per deck and
not per pane cwd, which is both the honest place for it -- one working tree, one board --
and the only one that does not scatter a ``.asomedeck`` directory across every folder a
terminal has ever been opened in.

A deck may have no folder, and then there is no board and no ``BOARD_VAR``. Nothing is
lost by that: the problem this solves is two agents in one working tree, and a deck
without one has no tree for them to collide in. The window is what knows -- ``ensure``
takes a path and would resolve an empty one against the process working directory, so
``MainWindow._launch_into`` asks the question and passes ``None``.

What is here is the cheapest thing that helps, and it is deliberately not IPC: a markdown
file in the deck, which every session can already read and write -- a pane that started
at the root reaches it by relative path, and one that started in a subdirectory reaches it
through ``BOARD_VAR``, which is why that variable carries an absolute path and not a name.
An agent claims what it is taking before it starts, and drops the claim when it finishes.
Nothing enforces that -- the board is a convention with a place to live, and that is the
whole of it.

The argument against the alternative is the same one db.py and conpty.py make about
sessions. Panes publishing state to each other needs something that outlives a pane and
owns the sessions, which is the broker process both modules name as the v2 question. A
file needs no broker, survives a crash that takes every pty with it, and is legible to the
person watching -- which a bus would not be.

Two things it does not do, both on purpose:

* **It never overwrites a board.** By the time a second pane launches the file may hold
  claims a live session wrote seconds ago, and a launch is exactly the moment that is most
  likely. Only a missing file is ever written.
* **It never edits your ``AGENTS.md``.** That file is yours and is usually in git, so the
  paragraph that tells an agent the board exists is seeded beside the board for you to
  paste. It is the one step this cannot take on your behalf.

``AGENTS.md`` and not ``CLAUDE.md``, which is what this seeded until 0.9.1. A pane runs
whatever agent you start in it, and ``AGENTS.md`` is the file most of them read; Claude Code
reads ``CLAUDE.md``, which can pull the same file in with one line. A snippet addressed to
one vendor's file made the board one vendor's convention.

**The roster is the other file here, and it is the opposite kind.** The board is written by
agents and never by us; ``panes.md`` is written by us and never by agents. It lists the
panes running over this folder -- number, name, deck -- because the window is the only
thing that knows which of them are alive, and an agent asked to "check with designer"
needs somewhere to find out which pane that is. So it is rewritten whole whenever that
changes, and it says so at the top.

A pane's *name* is given to it in the window, never by the program inside it. Names come
from the person, mean a role ("designer", "tests"), and are unique among the panes over one
folder -- which is what lets two checkouts each have their own ``designer`` without the
two ever meeting. The number stays the identity: it is in the environment, which cannot be
changed after spawn, while a name can be changed at any time and is looked up here.

The directory ignores itself: ``.asomedeck/.gitignore`` holds ``*``, which covers the file
naming it, so git reports nothing here at all. Coordination scratch must not turn every
checkout it touches permanently dirty -- least of all in the sidebar's own git chip, which
counts untracked paths (``git_status.py``).

Qt-free, like ``config.py`` and ``commands.py``. This is filesystem work with no window in
it, and ``tests/test_board.py`` runs headless because of that.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: Everything this module writes lives in one directory, inside the deck.
DIR_NAME = ".asomedeck"
BOARD_NAME = "board.md"
ROSTER_NAME = "panes.md"
SNIPPET_NAME = "agents-md-snippet.md"
#: What builds up to 0.9.1 seeded instead. Retired by ``ensure`` -- see ``_retire``.
OLD_SNIPPET_NAME = "claude-md-snippet.md"

#: What a shell is told about the pane holding it. ``shells.py`` already reads
#: ``ASOMEDECK_GIT_ROOT``, so the prefix is ours and this stays consistent with it.
PANE_VAR = "ASOMEDECK_PANE_ID"
#: Named ``DECK_VAR`` and spelled ``ASOMEDECK_PROJECT``, which is not an oversight. The
#: value is a contract with everything running inside a pane -- a shell profile, a script,
#: an agent that was told to read it -- and none of those get a deprecation notice. A
#: variable that quietly stops being set is a bug in somebody else's code that they have
#: no way of connecting to a rename in ours.
DECK_VAR = "ASOMEDECK_PROJECT"
BOARD_VAR = "ASOMEDECK_BOARD"

#: ``*`` matches this file too, which is the point: the directory disappears from git
#: entirely rather than arriving as one tracked file and a pile of ignored ones.
GITIGNORE = """\
# Coordination scratch for AsomeDeck, not part of this repository. This pattern covers the
# file it is written in, so the whole directory stays out of `git status`.
*
"""

BOARD = """\
# Claims board -- {deck}

The terminals working here share one checkout -- whichever folder each of them started
in, and whichever deck in the sidebar each of them belongs to, they are editing the
tree this file sits at the root of. Nothing stops two of them changing the same file at
the same moment. This file is how they agree not to.

**Before starting work**, read the claims below. If you need something another pane has
claimed, take something else, or say so and wait -- do not edit it underneath them.

**When you start**, add a line. **When you finish**, delete your line.

One claim per line, in this shape, so the list stays scannable:

    - pane 3 (designer) -- src/asomedeck/ui/term_view.py, term/keys.py -- reflow on resize

Paths are relative to this directory, not to wherever your shell happens to have started,
so that two panes in different folders name the same file the same way.

The number is the one in the pane header. Your own is in `$ASOMEDECK_PANE_ID`
(`$env:ASOMEDECK_PANE_ID` in PowerShell), and this file is at `$ASOMEDECK_BOARD`.

The name in brackets is your pane's, if it has one. `panes.md`, beside this file, lists
every pane running here with its number and name -- look yours up there, and leave the
brackets out if your pane has none. The number is what a claim belongs to; the name is
there so whoever reads the line knows who that is.

A claim naming a pane that is not running is stale: a session cannot outlive the app, but
this file does. The sidebar shows which panes are live -- delete the claims of ones that
are not. No two live panes ever share a number, so a number names one session and only
one. The board is worth exactly as much as it is current.

## Claims

_Nothing claimed._
"""

SNIPPET = """\
# Paste this into AGENTS.md

AsomeDeck will not edit your agent instructions for you -- they are your file, and probably
in git. Paste the section below into `AGENTS.md` at the root of this checkout, and the
agents running here will find the board on their own.

Codex and most other coding agents read `AGENTS.md`. Claude Code reads `CLAUDE.md`: give
it one holding the line `@AGENTS.md`, or add that line to the one you have, and it reads
this file too.

---

## Working alongside other sessions

Other terminals over this checkout may be running their own agents in the same working
tree -- including ones in a different deck in the sidebar. Coordinate through `.asomedeck/`:

- `.asomedeck/panes.md` lists the panes running here: number, name and deck. Your pane
  number is in `$ASOMEDECK_PANE_ID`; your name, if you have one, is beside it there. When
  you are asked about a pane by name -- "check with designer" -- that is where the name
  resolves.
- Read `.asomedeck/board.md` before you start work. Its full path is in `$ASOMEDECK_BOARD`.
- Add a claim for what you are taking: `- pane N (name) -- files -- what`.
- Delete your claim when you are done.
- If a file you need is claimed by another pane, work on something else rather than
  editing it underneath them. Say which pane is holding it.
"""

#: What ``OLD_SNIPPET_NAME`` held, byte for byte. Kept only so ``_retire`` can tell a copy
#: nobody touched from one somebody edited.
OLD_SNIPPET = """\
# Paste this into CLAUDE.md

AsomeDeck will not edit `CLAUDE.md` for you -- it is your file, and it is probably in git.
Paste the section below into the one at the root of this checkout, and the sessions
running here will find the board on their own.

---

## Working alongside other sessions

Other terminals over this checkout may be running their own agents in the same working
tree -- including ones in a different deck in the sidebar. Coordinate through `.asomedeck/board.md`:

- Read it before you start work.
- Add a claim for what you are taking: `- pane N -- files -- what`. Your pane number is in
  `$ASOMEDECK_PANE_ID`, and the board's path is in `$ASOMEDECK_BOARD`.
- Delete your claim when you are done.
- If a file you need is claimed by another pane, work on something else rather than
  editing it underneath them. Say which pane is holding it.
"""

ROSTER_HEAD = """\
# Panes -- {folder}

Written by AsomeDeck whenever a terminal over this folder starts, stops or is renamed, so
it is never older than the last of those. Do not edit it: the next change rewrites it.

Your own pane number is in `$ASOMEDECK_PANE_ID` (`$env:ASOMEDECK_PANE_ID` in PowerShell).
Names are given to panes in AsomeDeck -- right-click a pane's header -- and never by the
program running in one, and no two panes listed here share one. When you are asked about a
pane by name, this is where the name resolves to a number.

If AsomeDeck is not running, nothing listed here is either: the terminals end with it.

"""

ROSTER_EMPTY = "_No terminals running._\n"

#: How long a name may be. Long enough for "frontend-tests", short enough that a header and
#: a tab still have room for the title beside it.
NAME_MAX = 24

#: A letter first, so a name can never be read as a pane number. Then words of letters and
#: digits joined by single ``-`` or ``_``, so a name never contains the `` -- `` that
#: separates the fields of a claim, nor anything a shell or a file name would choke on --
#: a name is something an agent will type, and one day a directory under ``.asomedeck``.
_NAME = re.compile(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*")


def clean_name(text: str) -> str | None:
    """A pane name as typed, in the form it is kept -- or None if it cannot be one.

    Lowercased, with runs of whitespace turned into a hyphen, so "Front End" is kept as
    ``front-end``: a name is meant to be typed by whoever refers to it, and nobody should
    have to remember which capitals a pane was given. The empty string is a valid answer
    and means "no name".
    """
    name = "-".join(str(text or "").strip().lower().split())
    if not name:
        return ""
    if len(name) > NAME_MAX or not _NAME.fullmatch(name):
        return None
    return name


@dataclass(frozen=True, slots=True)
class RosterEntry:
    """One running pane, as ``panes.md`` lists it."""

    pane_id: int
    name: str = ""
    deck: str = ""


def render_roster(folder: str, entries: Iterable[RosterEntry]) -> str:
    """The whole of ``panes.md``, ordered by pane number.

    By number rather than by deck or by name: the number is what an agent starts from --
    it is in its environment -- and a list it has to scan for it is a list sorted on it.

    A table, because three short columns are what a table is for and it reads the same to
    a person and to an agent. A ``|`` cannot reach a cell from a name -- ``clean_name``
    refuses one -- but a deck name is free text, so it is escaped.
    """
    rows = sorted(entries, key=lambda entry: entry.pane_id)
    text = ROSTER_HEAD.format(folder=folder)
    if not rows:
        return text + ROSTER_EMPTY
    lines = ["| pane | name | deck |", "|---:|---|---|"]
    for entry in rows:
        deck = entry.deck.replace("|", "\\|")
        lines.append(f"| {entry.pane_id} | {entry.name} | {deck} |")
    return text + "\n".join(lines) + "\n"


def board_dir(deck_path: str | Path) -> Path:
    return Path(deck_path) / DIR_NAME


def board_path(deck_path: str | Path) -> Path:
    return board_dir(deck_path) / BOARD_NAME


def roster_path(deck_path: str | Path) -> Path:
    return board_dir(deck_path) / ROSTER_NAME


def ensure(deck_path: str | Path, deck_name: str = "") -> Path | None:
    """Make sure the board exists, and answer where it is. ``None`` if it could not be.

    Called on every launch rather than once per deck, because "once" has nowhere to be
    recorded that a deleted directory would not outlive. The cost is a handful of stat
    calls against a path ``shells.resolve`` is already walking on the same code path.

    A failure is quiet and returns ``None``, which switches off ``BOARD_VAR`` downstream.
    Somebody opening a deck on a read-only share should get terminals that work and no
    board, not a launch that refuses -- the precedent ``config.save`` sets for a
    ``%APPDATA%`` it cannot write.
    """
    directory = board_dir(deck_path)
    board = directory / BOARD_NAME
    name = deck_name or Path(deck_path).name
    try:
        directory.mkdir(parents=True, exist_ok=True)
        _seed(directory / ".gitignore", GITIGNORE)
        _seed(directory / SNIPPET_NAME, SNIPPET)
        _retire(directory / OLD_SNIPPET_NAME, OLD_SNIPPET)
        _seed(board, BOARD.format(deck=name))
    except OSError as exc:
        log.warning("could not prepare the claims board in %s: %s", directory, exc)
        # A board that was already there is still usable, whatever failed after it.
        return board if board.is_file() else None
    return board


def write_roster(deck_path: str | Path, entries: Iterable[RosterEntry],
                 deck_name: str = "") -> Path | None:
    """Rewrite ``panes.md`` from scratch. Where it went, or None if it could not be.

    Through ``ensure`` first, so a directory somebody deleted mid-session comes back with
    its ``.gitignore`` -- a roster landing in a ``.asomedeck`` git can see would put this
    folder in every ``git status`` until the next launch noticed.

    Written beside itself and moved into place, unlike ``_seed``. Agents read this file
    while we rewrite it, and a reader must get the old list or the new one, never the
    first half of either. A failure is quiet for ``ensure``'s reason, and a stale roster is
    corrected by the next change anyway.
    """
    if ensure(deck_path, deck_name) is None:
        return None
    path = roster_path(deck_path)
    temp = path.with_name(f".{ROSTER_NAME}.tmp")
    try:
        temp.write_text(render_roster(Path(deck_path).name, entries), encoding="utf-8")
        os.replace(temp, path)
    except OSError as exc:
        # Most likely an agent holding the file open at the moment of the swap, which
        # Windows refuses. The next change rewrites it.
        log.warning("could not write the pane roster in %s: %s", path.parent, exc)
        try:
            temp.unlink()
        except OSError:
            pass
        return None
    return path


def _retire(path: Path, text: str) -> None:
    """Remove a file an older build seeded, if it is still exactly what was seeded.

    Only then. An untouched copy is ours, and leaving it would put two snippets side by
    side that disagree about which file to paste into. An edited one is somebody's work,
    and the directory being ours does not make the edit ours.
    """
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            path.unlink()
    except (OSError, UnicodeDecodeError):
        pass


def _seed(path: Path, text: str) -> None:
    """Write a file, but only the first time.

    Not atomic, unlike ``config.save``, and it does not need to be: the only write that
    ever happens here is the one that creates the file, so there is no previous version
    for a half-finished write to destroy. Launches are issued from the UI thread one at a
    time, so there is no race between two panes to lose either.
    """
    if path.exists():
        return
    path.write_text(text, encoding="utf-8")


def env_for(*, pane_id: int, deck_name: str = "",
            board: str | Path | None = None) -> dict[str, str]:
    """What the shell in a pane is told about where it is running.

    Merged over ``ShellSpec.env`` at spawn, so the program inside a pane knows which pane
    it is without having to ask us -- which matters because there is nothing to ask. A pty
    carries bytes and nothing else; the environment it is spawned with is the only other
    channel into it, and it is one-way, which is exactly the shape this needs.

    Empty values are left out rather than set empty. A variable naming a board that is not
    there is worse than its absence, because a script testing whether the name is set
    would find it set and then fail to read the file.

    **``PANE_VAR`` is the identity; ``DECK_VAR`` is only context.** The number is
    allocated to be unique across the window (``ui.pane_tree.PaneIds``) precisely so that
    a claim can be keyed on it alone, and nothing should ever key on the name beside it.
    Two reasons, and each is on its own sufficient. A deck can be renamed at any time
    and this environment cannot: the agent holding it would go on claiming under a name
    that has left the sidebar, which reads exactly like the stale claim the board asks its
    reader to delete. And nothing makes names unique or even well-shaped -- two decks
    may share one, and a name containing the claim line's own ``--`` separator would make
    the line it appears in unparseable.
    """
    env = {PANE_VAR: str(pane_id)}
    if deck_name:
        env[DECK_VAR] = deck_name
    if board is not None:
        env[BOARD_VAR] = os.fspath(board)
    return env
