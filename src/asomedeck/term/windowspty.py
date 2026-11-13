"""A shell on a Windows pseudo-console, with nothing between us and the API.

The Windows half of the terminal, and the same shape as ``unixpty.py`` next door --
``spawn``, ``read``, ``write``, ``isalive``, ``setwinsize``, ``terminate``, ``pid``,
``exitstatus`` -- so that everything above it, the session object and the reader thread in
``ptysession.py``, stays one implementation rather than two. The Win32 calls themselves are
in ``winapi.py``, under the rule that file exists to enforce; this module is what they mean.

**Why not pywinpty.** It was how this worked up to 0.8.2, and it cost two things.

One was visible on every launch. A pseudo-console needs no console in the process that
creates one, but pywinpty allocated one anyway when it found none, and hid it a moment
later. A windowed exe never has a console, so the first shell of every launch put a real,
blank console window on the desktop, titled with our app id, before our own window had
painted. Measured: one such window on 7 of 7 launches of the exe, and none when the process
already had a console -- which is why it could not be seen running from a terminal.

The other only showed under load. Its reader forwarded output through a loopback socket with
a sleep per read: 5000 lines took 3.2 s to reach the scrollback where this takes 0.68 s, and
200,000 lines were still arriving after 90 s where this finishes in 26 s.

**Why the host is bundled.** The API is in kernel32, but kernel32's goes to the
``conhost.exe`` that came with this copy of Windows, and on Windows 11 22H2 that one parses
what a program writes and re-renders it, rather than passing it through. So ``CSI ? 1049 h``
never reaches us and ``vim`` runs without an alternate screen, which puts the mouse wheel on
our scrollback instead of on ``vim`` (``ui/term_view.py``, ``wheelEvent``); and a native
program's request for mouse reporting never arrives at all. ``assets/conpty`` holds the host
pywinpty was shipping all along, so behaviour is what it was: of 556 text cells on Claude
Code's first screen, none differ. The system host remains the fallback, and works, with those
losses, if that folder is missing.

**ConPTY's surprises**, all of which shaped this file:

* The shell exiting does not end the output stream. The host holds the pipe open until the
  console is closed, so a reader blocks forever on a pane whose shell has gone. ``_watch``
  waits on the process and closes the console, which is what ends the stream.
* ``ClosePseudoConsole`` can block until its output is drained. So it runs on the watcher
  rather than the UI thread, and a reader told to stop keeps draining: see ``read``.
* Closing the console is also the tree kill. Ending only the shell leaves whatever it was
  running attached to a console nobody can see; closing takes those too, which was measured
  with a shell's foreground and background children and with a client that ignores every
  console control event.
* The bundled host opens by asking the terminal what it is, and waits up to about 3 s for the
  answer before it draws anything. ``Emulator`` replies, so a pane never sees this; a test or
  a probe that reads without replying sees a shell that is inexplicably slow to say anything.
"""

from __future__ import annotations

import codecs
import logging
import os
import shutil
import subprocess
import threading

from .. import resources, winapi

log = logging.getLogger(__name__)

DEFAULT_COLS = 80
DEFAULT_ROWS = 24

#: The exit code of a shell ended by closing its pane rather than by its own hand. Nothing
#: reads it beyond the log; it is non-zero so that the log does not call that a clean exit.
TERMINATED = 1

#: "bundled" or "system" once the first shell has asked. Module state because the choice is
#: per process: winapi has one route, and every pane uses it.
_host: str | None = None


def host() -> str:
    """Choose the console host, once, and say which it is.

    Deferred to the first spawn rather than done at import, so a process that imports the
    terminal and never opens a pane loads nothing. Both files are checked because the DLL
    alone would load, and then quietly start the system host when it could not find its
    partner -- which is the fallback, but it should be logged as one.
    """
    global _host
    if _host is not None:
        return _host
    folder = resources.conpty_dir()
    dll, exe = folder / "conpty.dll", folder / "OpenConsole.exe"
    _host = "system"
    if dll.is_file() and exe.is_file():
        try:
            winapi.use_console_host(str(dll))
            _host = "bundled"
        except (OSError, AttributeError):
            log.exception("could not load the bundled console host at %s", folder)
    if _host == "system":
        log.warning("using the system console host: full-screen programs will not get an "
                    "alternate screen, and native programs cannot claim the mouse")
    else:
        log.info("console host: %s", exe)
    return _host


def _command_line(argv: list[str], env: dict[str, str]) -> str:
    """Resolve the program against the child's PATH and quote the rest for CreateProcess.

    Against the *child's* PATH, because ``env.for_child`` removes our own entries from it and
    the pane's shell should be the one a console on this machine would find.
    """
    command = shutil.which(argv[0], path=env.get("PATH", os.defpath))
    if command is None:
        raise FileNotFoundError(f"The command was not found or was not executable: {argv[0]}")
    return subprocess.list2cmdline([command, *argv[1:]])


def _environment_block(env: dict[str, str]) -> str:
    """The form CreateProcessW takes: ``KEY=value`` NUL-separated, ending in two NULs.

    Sorted, case-insensitively, because the documentation requires it. CreateProcess does not
    check, so a block out of order works until something inside the child relies on it.
    """
    entries = sorted(env.items(), key=lambda item: item[0].upper())
    return "".join(f"{key}={value}\0" for key, value in entries) + "\0"


class WindowsPty:
    """One child process on a pseudo-console."""

    def __init__(self, pid: int, console: int, process: int, input_: int, output: int) -> None:
        self.pid = pid
        self._console: int | None = console
        self._process: int | None = process
        self._input: int | None = input_
        self._output: int | None = output
        self._exitstatus: int | None = None
        #: Set by ``terminate``. From then on ``read`` swallows output rather than returning
        #: it, so that a reader asked to stop stays parked until the console is really gone.
        self._draining = False
        # Kept across reads, for the reason unixpty gives: a read can end in the middle of a
        # multi-byte character, and a box-drawing glyph that straddles two of them must not
        # turn into two replacement characters.
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        #: Guards the console and process handles, which the watcher closes while the UI
        #: thread may be resizing or terminating. Never held across a call that can block.
        self._lock = threading.Lock()
        #: Guards the input handle separately. A write can block on a full pipe, and closing
        #: the console is what would unblock it, so the two must not share a lock.
        self._write_lock = threading.Lock()
        self._watcher = threading.Thread(target=self._watch, daemon=True,
                                         name=f"conpty-watch-{pid}")
        self._watcher.start()

    # --- spawn -------------------------------------------------------------------------

    @classmethod
    def spawn(cls, argv: list[str], cwd: str | None = None,
              env: dict[str, str] | None = None,
              dimensions: tuple[int, int] = (DEFAULT_ROWS, DEFAULT_COLS)) -> WindowsPty:
        """Start ``argv`` on a new pseudo-console.

        Signature mirrors ``UnixPty.spawn``, ``dimensions`` included, so the caller does not
        know which of the two it is talking to.
        """
        host()
        environment = dict(env if env is not None else os.environ)
        # Both before anything is opened, so a shell that is not installed leaks nothing.
        command_line = _command_line(list(argv), environment)
        block = _environment_block(environment)
        rows, cols = dimensions
        console, input_, output = winapi.open_pseudo_console(cols, rows)
        try:
            process, pid = winapi.spawn_on_console(console, command_line,
                                                   cwd or os.getcwd(), block)
        except Exception:
            winapi.close_handle(input_)
            # Closed from a thread that drains it, for the reason ``close_pseudo_console``
            # gives: this runs on the UI thread, and a host with output pending would hold
            # it. A console with no client rarely has any, but rarely is not never.
            threading.Thread(target=cls._abandon, args=(console, output), daemon=True).start()
            raise
        return cls(pid, console, process, input_, output)

    @staticmethod
    def _abandon(console: int, output: int) -> None:
        drain = threading.Thread(target=WindowsPty._drain, args=(output,), daemon=True)
        drain.start()
        winapi.close_pseudo_console(console)
        drain.join()
        winapi.close_handle(output)

    @staticmethod
    def _drain(output: int) -> None:
        try:
            while winapi.read_handle(output, 4096):
                pass
        except OSError:
            pass

    # --- io ----------------------------------------------------------------------------

    def read(self, size: int) -> str:
        """Block until there is output, and return it decoded.

        Raises ``EOFError`` once the console has gone, which is the normal end of every pane
        rather than an error. Can return ``""`` when a chunk ends mid-character; the reader
        already treats that as "nothing yet".
        """
        while True:
            handle = self._output
            if handle is None:
                raise EOFError("pty is closed")
            data = winapi.read_handle(handle, size)
            if not data:
                self._output = None
                winapi.close_handle(handle)
                raise EOFError("pty is closed")
            if self._draining:
                continue
            return self._decoder.decode(data)

    def write(self, data: str) -> int:
        """Send keystrokes.

        ``errors="replace"`` because the text comes from Qt, and a lone surrogate from an
        input method must not become an exception inside a slot, where the crash hook would
        take the whole app down over one keystroke.
        """
        with self._write_lock:
            if self._input is None:
                raise EOFError("pty is closed")
            return winapi.write_handle(self._input, data.encode("utf-8", "replace"))

    def setwinsize(self, rows: int, cols: int) -> None:
        with self._lock:
            if self._console is None:
                raise EOFError("pty is closed")
            winapi.resize_pseudo_console(self._console, cols, rows)

    # --- lifecycle ---------------------------------------------------------------------

    def _watch(self) -> None:
        """Wait for the shell to go, then end the console so the reader hears about it."""
        process = self._process
        winapi.wait_for_process(process)
        code = winapi.process_exit_code(process)
        with self._lock:
            self._exitstatus = code
            self._process = None
            console, self._console = self._console, None
        winapi.close_handle(process)
        winapi.close_pseudo_console(console)
        with self._write_lock:
            handle, self._input = self._input, None
        winapi.close_handle(handle)

    def isalive(self) -> bool:
        with self._lock:
            if self._process is None:
                return False
            return not winapi.wait_for_process(self._process, 0)

    @property
    def exitstatus(self) -> int | None:
        return self._exitstatus

    def terminate(self, force: bool = False) -> None:
        """Take the shell down, and everything it started with it.

        ``force`` changes nothing here, and that is the honest shape of it. On Unix the
        polite form is SIGHUP; Windows has no equivalent that reaches an arbitrary process,
        so both end the shell outright and closing the console deals with the rest.

        Returns at once. The watcher does the closing, and ``read`` stays parked until it
        has finished, so a caller joining the reader is waiting on the console itself.
        """
        self._draining = True
        with self._lock:
            if self._process is None:
                return
            try:
                winapi.terminate_process(self._process, TERMINATED)
            except OSError:
                # Lost the race with its own exit. The watcher handles that path as well.
                log.debug("terminate pid=%s: already exiting", self.pid)
