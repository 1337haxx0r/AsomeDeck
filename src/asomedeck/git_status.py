"""Git state for the sidebar and session panel (spec 14.6).

Runs ``git`` in the deck directory. This is not reading terminal output -- it is
asking git directly, which is why it stays inside spec 1's constraint while the context
meter and turn counts did not.

Two questions, and the difference between them is the schedule rather than the subject.
``GitStatus`` answers *what is the state of this tree* every three seconds because the
answer changes under you. ``GitBranches`` answers *what branches are there* when somebody
clicks the header chip, because that one does not -- and putting it on the tick would
double the git traffic against a working tree full of agents for a list nobody is looking
at. Both obey the three decisions below.

Three decisions worth stating, because each is a bug avoided rather than a preference:

* ``--no-optional-locks``. Plain ``git status`` writes the index cache, so polling one
  every few seconds contends with whatever the user's own shells are doing in the same
  repo -- and the whole point of this app is that agents are running git in there. The
  flag exists for exactly this case.
* QProcess, never ``subprocess.run``. A blocking git call on a cold or large repo freezes
  the UI, and the pane that freezes is the one hosting somebody's terminal.
* A non-repository is not an error. It reports "unknown" and the delegate omits the
  branch chip, rather than rendering a clean tree nobody verified.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

log = logging.getLogger(__name__)

#: Spec 14.6. The active deck only; others refresh on switch and on window focus.
POLL_MS = 3000

#: A repository that has not answered in this long is pathological -- a huge worktree, a
#: network drive, an index lock held by something else. Give up rather than stack calls.
TIMEOUT_MS = 5000


#: What one branch line looks like. Tab-separated because a branch name cannot contain a
#: tab -- git refuses to create one -- so the split is exact where a space would not be.
#:
#: ``%(HEAD)`` is "*" on the checked-out branch and a space on every other, which is the
#: only part of this that is not self-describing.
BRANCH_FORMAT = ("%(HEAD)%09%(refname:short)%09%(upstream:track)%09"
                 "%(committerdate:relative)")


@dataclass(frozen=True, slots=True)
class Branch:
    """One local branch, as the dropdown shows it."""

    name: str
    current: bool = False
    #: "ahead 2", "behind 1", "ahead 1, behind 3", "gone", or empty for no upstream.
    track: str = ""
    #: Relative, from git: "3 days ago". A date would be more precise and less useful --
    #: the question this answers is which branches are still warm.
    when: str = ""


@dataclass
class GitState:
    known: bool = False
    branch: str = ""
    clean: bool = False
    files: tuple[tuple[str, str], ...] = ()   # (mark, path)

    @property
    def changed(self) -> int:
        """How many paths differ from HEAD. Includes untracked files."""
        return len(self.files)

    @property
    def summary(self) -> str:
        """What the chip says.

        A count of *paths*, spelled as one. It used to read ``+3 -1``, which is the
        notation every other tool uses for lines in a diff -- and these were never lines:
        ``+3`` meant three changed paths and ``-1`` meant one deleted file, so a
        three-hundred-line edit and a new empty file each counted as one.

        Real line counts would mean a second ``git diff --numstat`` on every poll, against
        a repository the user's own agents are working in. That is the contention
        ``--no-optional-locks`` exists to avoid, for a number the file list below already
        answers better.
        """
        if not self.known:
            return ""
        if self.clean:
            return "clean"
        return f"{self.changed} changed"


class GitStatus(QObject):
    """Polls one directory at a time and reports what git says."""

    updated = Signal(int, object)   # deck_id, GitState

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._deck_id = 0
        self._path = ""
        self._process: QProcess | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.refresh)

    def watch(self, deck_id: int, path: str) -> None:
        """Point at a deck and poll it until told otherwise."""
        if deck_id == self._deck_id and path == self._path:
            return
        self._deck_id, self._path = deck_id, path
        self._timer.start()
        self.refresh()

    def stop(self) -> None:
        """Watch nothing, and forget what was being watched.

        The forgetting is the part that matters. ``watch`` is a no-op when it is handed
        the deck and path it already holds, which is right for repeated calls and wrong
        after a stop: a window that leaves a repository for a deck with no folder and
        comes back would be told it is already watching, and poll nothing for the rest of
        the run. "Stopped" and "watching this" have to be different states.
        """
        self._deck_id, self._path = 0, ""
        self._timer.stop()
        self._kill()

    def refresh(self) -> None:
        if not self._path:
            return
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            # Still waiting on the previous call. Stacking them would queue git processes
            # against a repository that is already slow.
            return
        self._run()

    # --- process -----------------------------------------------------------------

    def _run(self) -> None:
        process = QProcess(self)
        process.setWorkingDirectory(self._path)
        process.setProgram("git")
        # One call for everything: branch, counts and file list all come out of
        # porcelain=v2 --branch, so there is nothing to correlate across processes.
        process.setArguments([
            "--no-optional-locks", "status", "--porcelain=v2", "--branch",
            "--untracked-files=normal",
        ])
        process.finished.connect(lambda _c, _s, p=process: self._finished(p))
        process.errorOccurred.connect(lambda _e, p=process: self._failed(p))
        self._process = process
        process.start()
        QTimer.singleShot(TIMEOUT_MS, lambda p=process: self._timeout(p))

    def _timeout(self, process: QProcess) -> None:
        if process.state() != QProcess.ProcessState.NotRunning:
            log.warning("git status timed out in %s", self._path)
            process.kill()

    def _failed(self, process: QProcess) -> None:
        # git missing from PATH, or the directory is gone. Neither is worth a dialog.
        if process is self._process:
            self.updated.emit(self._deck_id, GitState())

    def _finished(self, process: QProcess) -> None:
        if process is not self._process:
            return
        if process.exitCode() != 0:
            # Not a repository, most often. Report unknown and let the UI omit the chip.
            self.updated.emit(self._deck_id, GitState())
            return
        text = bytes(process.readAllStandardOutput()).decode("utf-8", "replace")
        self.updated.emit(self._deck_id, parse_porcelain_v2(text))

    def _kill(self) -> None:
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
        self._process = None


class GitBranches(QObject):
    """The local branches of one repository, asked for rather than polled.

    Deliberately not folded into ``GitStatus``. That class exists to answer a question
    the window asks every three seconds, and this is a question the window asks when
    somebody clicks a chip -- adding a second ``git`` to each tick would be exactly the
    contention ``--no-optional-locks`` is up there to avoid, for an answer nobody is
    looking at. What the two do share is the shape: QProcess, a timeout that kills rather
    than stacks, and a failure that reports emptiness instead of raising a dialog.
    """

    #: (path, branches). The path travels with the answer because the deck can be
    #: switched between the click and the reply, and a branch list for the folder you
    #: just left is worse than none.
    ready = Signal(str, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._path = ""

    def ask(self, path: str) -> None:
        if not path:
            self.ready.emit("", ())
            return
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            # A second click while the first is in flight. The reply already on its way
            # answers both, so this returns rather than starting a competing process.
            return
        self._path = path
        process = QProcess(self)
        process.setWorkingDirectory(path)
        process.setProgram("git")
        process.setArguments([
            "--no-optional-locks", "for-each-ref", "--sort=-committerdate",
            "refs/heads", f"--format={BRANCH_FORMAT}",
        ])
        process.finished.connect(lambda _c, _s, p=process: self._finished(p))
        process.errorOccurred.connect(lambda _e, p=process: self._failed(p))
        self._process = process
        process.start()
        QTimer.singleShot(TIMEOUT_MS, lambda p=process: self._timeout(p))

    def _timeout(self, process: QProcess) -> None:
        if process.state() != QProcess.ProcessState.NotRunning:
            log.warning("git for-each-ref timed out in %s", self._path)
            process.kill()

    def _failed(self, process: QProcess) -> None:
        if process is self._process:
            self.ready.emit(self._path, ())

    def _finished(self, process: QProcess) -> None:
        if process is not self._process:
            return
        if process.exitCode() != 0:
            self.ready.emit(self._path, ())
            return
        text = bytes(process.readAllStandardOutput()).decode("utf-8", "replace")
        self.ready.emit(self._path, parse_branches(text))


def parse_branches(text: str) -> tuple[Branch, ...]:
    """Parse ``git for-each-ref`` in :data:`BRANCH_FORMAT`.

    Pure, for the same reason ``parse_porcelain_v2`` is: the format is one we chose and
    the parse is the part that can be wrong without anybody noticing.

    A repository with no commits yet prints nothing and comes back empty, which the menu
    is already able to say.
    """
    branches: list[Branch] = []
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) < 4 or not fields[1]:
            continue
        head, name, track, when = fields[:4]
        branches.append(Branch(
            name=name,
            current=head.strip() == "*",
            # git wraps the track in brackets -- "[ahead 2]" -- which are punctuation for
            # a human reading `git branch -vv`, not part of the value.
            track=track.strip().strip("[]"),
            when=when.strip(),
        ))
    return tuple(branches)


def parse_porcelain_v2(text: str) -> GitState:
    """Parse ``git status --porcelain=v2 --branch``.

    Pure and unit-tested: the format is stable and machine-readable, which is the reason
    for choosing v2 over scraping the human output.
    """
    branch = ""
    files: list[tuple[str, str]] = []

    for line in text.splitlines():
        if line.startswith("# branch.head "):
            branch = line[len("# branch.head "):].strip()
            if branch == "(detached)":
                branch = "detached"
        elif line.startswith("1 "):
            # 1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
            parts = line.split(" ", 8)
            if len(parts) >= 9:
                files.append((_mark(parts[1]), _path_of(parts[8])))
        elif line.startswith("2 "):
            # 2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <X><score> <path><sep><origPath>
            # A rename carries a similarity score the modified form does not, so the path
            # is one field further along -- reading it at the same index yields "R100 src/x".
            parts = line.split(" ", 9)
            if len(parts) >= 10:
                files.append((_mark(parts[1]), _path_of(parts[9])))
        elif line.startswith("u "):
            parts = line.split(" ", 10)
            if len(parts) >= 11:
                files.append(("U", _path_of(parts[10])))
        elif line.startswith("? "):
            files.append(("A", _path_of(line[2:])))

    return GitState(
        known=True,
        branch=branch,
        clean=not files,
        files=tuple(files),
    )


def _mark(xy: str) -> str:
    """Collapse git's two-column status to the single letter the design shows."""
    for char in xy:
        if char in "ADMRC":
            return "A" if char == "A" else char
    return "M"


def _path_of(field: str) -> str:
    # Renames carry "new\t="old"; the new name is the one worth showing.
    return field.split("\t", 1)[0].strip().strip('"')
