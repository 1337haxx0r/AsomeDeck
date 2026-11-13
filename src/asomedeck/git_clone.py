"""Clone a repository into a folder, so that the folder can become a deck.

What runs here is a stranger's address, typed or pasted, against a program that can take a
long time and ask for things. So the decisions worth stating are the ones about that:

* ``--`` in front of the address. Without it, an address starting with a dash is an option,
  and ``--upload-pack=<anything>`` is an option that runs a command.
* ``GIT_TERMINAL_PROMPT=0``. There is no terminal behind this process, so a private repository
  would otherwise wait for a username nobody can type, for as long as the dialog stays open. It
  fails at once instead, and ``failure_message`` says what that means. A credential helper with
  its own window is unaffected; it never needed the terminal.
* QProcess, for the reason ``git_status`` gives, and no timeout unlike every other git call in
  the app. Those answer a question and should be quick; a clone of a large repository on a slow
  line is allowed to take an hour, and Cancel is how it ends early.
* Nothing is deleted unless this clone made it. ``CloneJob.start`` refuses a destination that
  has anything in it, which is what makes clearing up after a cancel safe to do without asking.
* A password in the address stays out of the log: ``redact``.

No widgets here: ``ui/clone_dialog.py`` is what the user sees.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import threading
import time

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from . import shells
from .env import for_child

log = logging.getLogger(__name__)

#: What is kept of git's output, for a failure message. Its last lines are the useful ones, and
#: a long clone prints a progress update for every percent of every phase.
OUTPUT_KEPT = 16384

#: How long to keep trying to clear a cancelled clone. A killed git leaves its helpers running
#: for a moment, and on Windows a file somebody still has open cannot be deleted.
CLEANUP_ATTEMPTS = 20
CLEANUP_DELAY_S = 0.25
#: How long quitting waits for that, which is the one place somebody is waiting on it.
CLEANUP_WAIT_S = 3.0
CLEANUP_POLL_MS = 20
KILL_WAIT_MS = 2000

#: Git's own wording, with the part that matters put first. It says terminal prompts are off,
#: which is true and says nothing about what to do next.
LOGIN_HINT = ("This repository needs a login, and there is nowhere to type one here. "
              "Clone it from a terminal pane, or set up a Git credential helper.")

NO_GIT_HINT = "Git is not installed, or is not on your PATH."

_FORBIDDEN_IN_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                   *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}
#: "Receiving objects:  42% (420/1000)". The phase comes first so a "remote:" prefix, which ends
#: in a colon as well, cannot be read as one: it has no percentage after it.
_PROGRESS = re.compile(r"([A-Za-z][A-Za-z ]*):\s+(\d{1,3})%")
_USERINFO = re.compile(r"(?<=//)[^/@\s]+@")


def find_git() -> str | None:
    """Where git is, or None.

    The fallback is why this is not just ``which``. A pane can run Git Bash from where it is
    installed without git being on PATH (``shells.GIT_BASH``), and a window that offers Git
    Bash while insisting Git is not installed would be contradicting itself.
    """
    found = shutil.which("git", path=for_child().get("PATH"))
    if found:
        return found
    installed = os.path.join(shells.GIT_ROOT, "cmd", "git.exe")
    return installed if os.path.isfile(installed) else None


def redact(url: str) -> str:
    """The address with any ``user:password@`` taken out, for writing to the log."""
    return _USERINFO.sub("", url)


def repo_folder_name(url: str) -> str:
    """The folder git would make for this address, or "" if there is nothing to go on.

    Handles the forms an address actually arrives in: ``https://host/owner/repo.git``,
    ``git@host:owner/repo.git``, ``ssh://host/repo``, a local path, and a trailing slash on
    any of them.
    """
    text = re.split(r"[?#]", url.strip(), maxsplit=1)[0].rstrip("/\\")
    if text.lower().endswith((r"\.git", "/.git")):
        text = text[:-5].rstrip("/\\")
    name = re.split(r"[/\\:]", text)[-1]
    if name.lower().endswith(".git"):
        name = name[:-4]
    return "" if name_problem(name) else name


def url_problem(url: str) -> str | None:
    """Why this address cannot be cloned, as far as can be told without trying."""
    text = url.strip()
    if not text:
        return "Enter the address of a repository."
    if text.startswith("-"):
        return "A repository address cannot start with a dash."
    if any(ord(char) < 32 for char in text):
        return "That address has a line break in it."
    return None


def name_problem(name: str) -> str | None:
    """Why this cannot be a folder name, on any platform this runs on.

    Windows' rules for all of them. A deck's folder can be reached from both, and the case that
    matters is not portability anyway: ``..`` and anything with a separator in it would put the
    clone somewhere other than the folder that was picked.
    """
    if not name.strip():
        return "Name the folder to create."
    if name in (".", "..") or _FORBIDDEN_IN_NAME.search(name):
        return f"{name} cannot be a folder name."
    if name != name.rstrip(" ."):
        return "A folder name cannot end in a space or a dot."
    if name.split(".")[0].upper() in _RESERVED_NAMES:
        return f"{name} is a name Windows reserves."
    return None


def destination_problem(folder: str, name: str) -> str | None:
    """Why a clone cannot go to ``folder/name``. An empty folder is fine; git allows that too."""
    if not folder.strip():
        return "Choose a folder to clone into."
    if not os.path.isabs(folder) or not os.path.isdir(folder):
        return f"{folder} is not a folder."
    problem = name_problem(name)
    if problem:
        return problem
    destination = os.path.join(folder, name)
    if os.path.exists(destination) and not _is_empty_folder(destination):
        return f"{destination} already exists and is not empty."
    return None


def same_place_problem(url: str, destination: str) -> str | None:
    """Why a local address cannot also be where the clone goes.

    Git does not catch this one when the source does not exist. It creates the destination
    first, which makes the source exist, then clones that empty folder into itself and exits
    with success -- leaving a deck over nothing, from what was probably a typo.
    """
    text = url.strip()
    # An address with a scheme or a host part is not a path here. "C:\\x" has a colon too, but
    # one that is a drive, which is what the second test is for.
    if "://" in text or (":" in text and not os.path.splitdrive(text)[0]):
        return None
    same = os.path.normcase(os.path.abspath(text)) == os.path.normcase(os.path.abspath(destination))
    return "The repository and the folder to clone into are the same place." if same else None


def _is_empty_folder(path: str) -> bool:
    try:
        with os.scandir(path) as entries:
            return next(entries, None) is None
    except OSError:
        return False


def parse_progress(text: str) -> tuple[str, int] | None:
    """The latest ``(phase, percent)`` in a piece of git's output, or None."""
    found = _PROGRESS.findall(text)
    if not found:
        return None
    phase, percent = found[-1]
    return phase.strip(), min(int(percent), 100)


def failure_message(output: str) -> str:
    """What to tell somebody about a clone that failed, from what git said."""
    if "terminal prompts disabled" in output or "could not read Username" in output:
        return LOGIN_HINT
    lines = [line.strip() for line in re.split(r"[\r\n]+", output) if line.strip()]
    lines = [line for line in lines if not _PROGRESS.search(line)]
    errors = [line for line in lines if line.startswith(("fatal:", "error:", "ERROR:"))]
    chosen = errors or lines[-3:]
    return "\n".join(chosen) or "git stopped without saying why."


def _remove_partial(path: str, keep_folder: bool) -> bool:
    """Clear up what a stopped clone left. True if nothing is left.

    Git marks its pack files read-only, and Windows will not delete a read-only file, so the
    first refusal clears that and tries again rather than leaving a folder that looks
    half-deleted. Junctions and symlinks in the checkout are unlinked rather than followed,
    which is what ``rmtree`` does.
    """
    def writable_then_retry(function, target, _error) -> None:
        os.chmod(target, stat.S_IWRITE)
        function(target)

    for attempt in range(CLEANUP_ATTEMPTS):
        try:
            if keep_folder:
                with os.scandir(path) as entries:
                    children = list(entries)
                for child in children:
                    if child.is_dir(follow_symlinks=False):
                        shutil.rmtree(child.path, onexc=writable_then_retry)
                    else:
                        os.chmod(child.path, stat.S_IWRITE)
                        os.unlink(child.path)
            else:
                shutil.rmtree(path, onexc=writable_then_retry)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt + 1 < CLEANUP_ATTEMPTS:
                time.sleep(CLEANUP_DELAY_S)
    return False


class CloneJob(QObject):
    """One ``git clone``, reporting as it goes. Good for one run."""

    #: (phase, percent). Percent starts over with each phase, as git's own display does.
    progress = Signal(str, int)
    succeeded = Signal(str)  # the destination
    failed = Signal(str)  # something to show a person
    #: Fired after clearing up, so a folder freed by a cancel really is free by now.
    cancelled = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._destination = ""
        self._keep_folder = False
        self._output = ""
        self._cancelling = False
        self._running = False
        self._cleaner: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._running

    @staticmethod
    def _command(git: str, url: str, destination: str) -> tuple[str, list[str]]:
        """Separate from ``start`` so a test can stand a slow program in for git."""
        return git, ["clone", "--progress", "--", url, destination]

    @staticmethod
    def _environment() -> QProcessEnvironment:
        environment = QProcessEnvironment()
        # for_child rather than our own: a frozen app's PATH leads with its bundle, and git's
        # https helper would be looking for DLLs through that.
        for name, value in for_child().items():
            environment.insert(name, value)
        environment.insert("GIT_TERMINAL_PROMPT", "0")
        # parse_progress and failure_message read git's words, and git translates them.
        environment.insert("LC_ALL", "C")
        return environment

    def start(self, url: str, destination: str, git: str) -> None:
        """Start cloning. Answers through the signals, including for a refusal."""
        self._destination = destination
        folder, name = os.path.split(destination)
        # Asked again here and not left to the dialog, because this is what deleting the folder
        # later relies on. The dialog's answer may be from some time ago.
        problem = destination_problem(folder, name) or same_place_problem(url, destination)
        if problem:
            self.failed.emit(problem)
            return
        self._keep_folder = os.path.isdir(destination)
        program, arguments = self._command(git, url.strip(), destination)
        process = QProcess(self)
        process.setProgram(program)
        process.setArguments(arguments)
        process.setWorkingDirectory(folder)
        process.setProcessEnvironment(self._environment())
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read)
        process.errorOccurred.connect(self._error)
        process.finished.connect(self._finished)
        self._process = process
        self._running = True
        log.info("cloning %s into %s", redact(url.strip()), destination)
        process.start()
        # Nothing will ever be typed. Anything that does read standard input should see the end
        # of it rather than wait.
        process.closeWriteChannel()

    def cancel(self, wait: bool = False) -> None:
        """Stop the clone and clear up. ``wait`` is for quitting: nothing runs after that."""
        if not self._running or self._process is None:
            return
        self._cancelling = True
        self._process.kill()
        if wait:
            self._process.waitForFinished(KILL_WAIT_MS)
            if self._cleaner is not None:
                self._cleaner.join(CLEANUP_WAIT_S)

    # --- what the process says ---------------------------------------------------------

    def _read(self) -> None:
        data = bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace")
        self._output = (self._output + data)[-OUTPUT_KEPT:]
        latest = parse_progress(data)
        if latest is not None:
            self.progress.emit(*latest)

    def _error(self, error: QProcess.ProcessError) -> None:
        # The one error that is not followed by ``finished``.
        if error == QProcess.ProcessError.FailedToStart and self._running:
            self._running = False
            log.warning("git could not be started: %s", self._process.errorString())
            self.failed.emit(f"Git could not be started: {self._process.errorString()}")

    def _finished(self, code: int, status: QProcess.ExitStatus) -> None:
        if not self._running:
            return
        self._running = False
        if self._cancelling:
            log.info("clone into %s cancelled", self._destination)
            self._clear_then(self.cancelled.emit)
        elif status == QProcess.ExitStatus.NormalExit and code == 0:
            log.info("cloned into %s", self._destination)
            self.succeeded.emit(self._destination)
        else:
            message = failure_message(self._output)
            log.warning("clone into %s failed, code=%s", self._destination, code)
            self._clear_then(lambda: self.failed.emit(message))

    def _clear_then(self, report) -> None:
        """Clear up on a thread, because a checkout can be many thousands of files.

        The report is made from this thread and not that one: the timer belongs to this object,
        so if the window has gone and taken this object with it, nothing fires at all.

        ``work`` holds strings and not ``self``. A QObject has to be destroyed on the thread it
        belongs to, and Qt aborts the process if it is not, so no thread but this one should
        ever be holding the last reference to one.
        """
        destination, keep_folder = self._destination, self._keep_folder

        def work() -> None:
            if not _remove_partial(destination, keep_folder):
                log.warning("could not remove the partial clone at %s", destination)

        worker = threading.Thread(target=work, daemon=True, name="clone-cleanup")
        self._cleaner = worker
        worker.start()

        timer = QTimer(self)
        timer.setInterval(CLEANUP_POLL_MS)

        def check() -> None:
            if not worker.is_alive():
                timer.stop()
                timer.deleteLater()
                report()

        timer.timeout.connect(check)
        timer.start()
