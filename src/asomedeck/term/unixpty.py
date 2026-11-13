"""A shell on a Unix pseudo-terminal.

The macOS half of the terminal. It exists to present exactly the surface
``windowspty.WindowsPty`` presents -- ``spawn``, ``read``, ``write``, ``isalive``,
``setwinsize``, ``terminate``, ``pid``, ``exitstatus`` -- so that everything above it, the
session object and the reader thread in ``ptysession.py``, is one implementation rather
than two. The seam is here, at the binding, and not one line higher.

No third-party dependency: a Unix pty is ``os`` and ``fcntl`` and has been since v7.

**Why fork and exec by hand, rather than ``pty.fork()``.** ``pty.fork`` does the session
and controlling-terminal work correctly, but it hands back a master fd with the child
already running, which leaves the initial window size a race: the shell can draw its first
prompt against the default 80x24 before the parent's ``TIOCSWINSZ`` lands, and a prompt
drawn at the wrong width is then reflowed in front of the user on every launch. Opening the
pty ourselves lets the size be set on the slave *before* there is a child to observe it.
"""

from __future__ import annotations

import codecs
import errno
import fcntl
import logging
import os
import pty
import signal
import struct
import termios
import warnings

log = logging.getLogger(__name__)

DEFAULT_COLS = 80
DEFAULT_ROWS = 24

#: The exit status of a child that could not exec at all. 127 is the shell's own convention
#: for "command not found", and this is the same condition seen from the other side.
EXEC_FAILED = 127


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    """Tell a tty its size in cells.

    The trailing two shorts are the pixel dimensions. Nothing consults them -- they are a
    vestige of hardware terminals -- but the struct has four fields and a short write is a
    truncated ioctl, not a partial one.
    """
    packed = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)


class UnixPty:
    """One child process on a pty master fd."""

    def __init__(self, pid: int, fd: int) -> None:
        self.pid = pid
        self.fd = fd
        self._exitstatus: int | None = None
        # Kept across reads, and that is not a nicety. A read returns whatever bytes have
        # arrived, which can end in the middle of a multi-byte character; decoding each
        # chunk independently turns every box-drawing glyph unlucky enough to straddle a
        # 16 KiB boundary into two replacement characters.
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

    # --- spawn -------------------------------------------------------------------------

    @classmethod
    def spawn(cls, argv: list[str], cwd: str | None = None,
              env: dict[str, str] | None = None,
              dimensions: tuple[int, int] = (DEFAULT_ROWS, DEFAULT_COLS)) -> UnixPty:
        """Start ``argv`` on a new pty and return the master side.

        Signature mirrors ``WindowsPty.spawn``, ``dimensions`` included, so the caller does
        not know which of the two it is talking to.
        """
        rows, cols = dimensions
        argv = list(argv)
        environment = dict(env if env is not None else os.environ)
        # A shell that finds these set trusts them over the tty, and they would be this
        # window's size at spawn for the rest of the session -- wrong after the first
        # resize, and wrong in a way that looks like our resize handling is broken.
        environment.pop("LINES", None)
        environment.pop("COLUMNS", None)

        master, slave = pty.openpty()
        # Before the fork, so there is no moment at which a child could read the default.
        _set_winsize(slave, max(1, int(rows)), max(1, int(cols)))

        # Python 3.12 onwards warns that fork() in a multi-threaded process may deadlock,
        # and this process is always multi-threaded: every open pane has a reader thread.
        # The warning is about the child *continuing to run Python* -- inheriting a lock
        # some other thread held at the moment of the fork, with no owner left to release
        # it. That is not this. The child below touches only async-signal-safe syscalls and
        # then execs, which replaces the address space and every lock in it.
        #
        # Suppressed narrowly, around the one call, rather than filtered globally: the
        # warning is correct about fork-without-exec and should still fire if anybody adds
        # one.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning,
                                    message=r".*fork\(\) may lead to deadlocks.*")
            pid = os.fork()
        if pid == 0:
            # --- child. Nothing here may raise into Python's normal exit path: this
            # process is a copy of a running Qt application, and unwinding it would run
            # atexit handlers and flush file objects that belong to the parent. Every
            # failure ends at os._exit.
            try:
                os.close(master)
                # A new session with no controlling terminal, so the next ioctl can make
                # this pty the controlling one. Without this pair there is a pty but no
                # job control: ^C reaches nobody, and curses programs report "not a
                # terminal".
                os.setsid()
                fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
                os.dup2(slave, 0)
                os.dup2(slave, 1)
                os.dup2(slave, 2)
                if slave > 2:
                    os.close(slave)
                # Python sets SIGPIPE to SIG_IGN and the disposition survives exec, so a
                # shell inheriting it mishandles every broken pipe in a pipeline -- `head`
                # closing early becomes an error instead of a normal end.
                for name in ("SIGPIPE", "SIGINT", "SIGQUIT", "SIGTERM", "SIGHUP", "SIGTTOU",
                             "SIGTTIN", "SIGCHLD"):
                    sig = getattr(signal, name, None)
                    if sig is not None:
                        signal.signal(sig, signal.SIG_DFL)
                if cwd:
                    os.chdir(cwd)
                os.execvpe(argv[0], argv, environment)
            except BaseException:
                pass
            os._exit(EXEC_FAILED)

        # --- parent
        os.close(slave)
        return cls(pid, master)

    # --- io ----------------------------------------------------------------------------

    def read(self, size: int) -> str:
        """Block until there is output, and return it decoded.

        Returns ``""`` at end of stream. On macOS a read from a master whose child has gone
        raises ``OSError(EIO)`` rather than returning empty -- that is the *normal* exit
        path for every pane, not an error, so it is translated here rather than logged.
        """
        try:
            data = os.read(self.fd, size)
        except OSError as exc:
            if exc.errno in (errno.EIO, errno.EBADF):
                return ""
            raise
        if not data:
            return ""
        return self._decoder.decode(data)

    def write(self, data: str) -> int:
        """Send keystrokes. Takes ``str`` because that is what the callers have."""
        return os.write(self.fd, data.encode("utf-8"))

    def setwinsize(self, rows: int, cols: int) -> None:
        _set_winsize(self.fd, max(1, int(rows)), max(1, int(cols)))

    # --- lifecycle ---------------------------------------------------------------------

    def _reap(self) -> None:
        """Collect the child's status if it has one, without blocking."""
        if self._exitstatus is not None:
            return
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            # Already reaped, or never ours. Either way there is nothing running.
            self._exitstatus = 0
            return
        if pid == 0:
            return
        if os.WIFEXITED(status):
            self._exitstatus = os.WEXITSTATUS(status)
        elif os.WIFSIGNALED(status):
            # The shell convention, and the one a status line can render.
            self._exitstatus = 128 + os.WTERMSIG(status)
        else:
            self._exitstatus = 0

    def isalive(self) -> bool:
        self._reap()
        return self._exitstatus is None

    @property
    def exitstatus(self) -> int | None:
        self._reap()
        return self._exitstatus

    def terminate(self, force: bool = False) -> None:
        """Take the shell down, and everything it started with it.

        Signals the process *group*, not the pid. The child called ``setsid`` and is a
        group leader, so the group is the shell plus whatever it is running -- and it is
        the "whatever it is running" that matters: killing only the shell leaves a vim or a
        dev server holding the pty open, which is precisely the orphan the ConPTY path does
        not have to think about because closing a pseudo-console takes its whole tree.

        SIGHUP first, which is what a terminal disappearing means and what shells already
        know how to handle. SIGKILL on ``force``.
        """
        if self._exitstatus is not None:
            return
        try:
            os.killpg(os.getpgid(self.pid), signal.SIGKILL if force else signal.SIGHUP)
        except (ProcessLookupError, PermissionError, OSError):
            # Already gone, or never got as far as setsid. Fall back to the pid alone.
            try:
                os.kill(self.pid, signal.SIGKILL if force else signal.SIGHUP)
            except OSError:
                pass
        self.close_fd()

    def close_fd(self) -> None:
        """Drop the master. Unblocks a reader parked in ``os.read``."""
        if self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1
