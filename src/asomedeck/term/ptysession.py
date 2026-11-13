"""The shell session and its reader thread, with no opinion about which pty it is on.

Everything here was ``conpty.py`` before there was a second platform, and almost all of it
turned out to be platform-free already: a session owns a pty object, a thread blocks on
reading it, and chunks arrive on the UI thread as a queued signal. Only three things ever
knew what kind of pty it was -- how to spawn one, which exceptions mean "it is gone", and
whether to pin a backend through the environment -- so those three are what a subclass
supplies and this module is the rest.

The class is abstract in the sense that ``SPAWN`` must be filled in, and deliberately not
an ABC: the two subclasses live in ``conpty.py``, are chosen by platform at import, and a
missing attribute there would be a startup TypeError either way.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from PySide6.QtCore import QObject, QThread, Signal

from ..env import for_child

log = logging.getLogger(__name__)

#: A pty delivers output in bursts. Reading in a tight loop on a thread and forwarding
#: whole chunks keeps the UI thread out of the hot path entirely.
READ_SIZE = 16384

DEFAULT_COLS = 80
DEFAULT_ROWS = 24


class Pty(Protocol):
    """What a backend hands back, and all this module is allowed to assume about it.

    Written down because there are two implementations of it and neither imports the other:
    ``term/windowspty.WindowsPty`` on Windows and ``term/unixpty.UnixPty`` on macOS. Note
    ``read`` and ``write`` deal in ``str``, not bytes. Both backends decode on their own
    side, because a read can end mid-character and only the side doing the reading can keep
    the remainder for the next one.
    """

    pid: int

    def read(self, size: int) -> str: ...
    def write(self, data: str) -> int: ...
    def isalive(self) -> bool: ...
    def setwinsize(self, rows: int, cols: int) -> None: ...
    def terminate(self, force: bool = False) -> None: ...


class _Reader(QThread):
    """Blocking reads off the pty, forwarded to the UI thread as a queued signal."""

    chunk = Signal(str)
    closed = Signal()

    def __init__(self, pty: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pty = pty
        self._stop = False

    def run(self) -> None:
        while not self._stop:
            try:
                data = self._pty.read(READ_SIZE)
            except EOFError:
                break
            except OSError:
                break
            if not data:
                if not self._pty.isalive():
                    break
                # Both backends block in the read, so this branch is rare: a chunk that ended
                # mid-character is the usual cause. It costs nothing to yield here, and
                # without it a read that ever returns early spins a core per pane -- an
                # expensive way to find out.
                self.msleep(1)
                continue
            self.chunk.emit(data)
        self.closed.emit()

    def stop(self) -> None:
        self._stop = True


class PtySession(QObject):
    """One shell, its pseudo-terminal, and the bytes going each way."""

    output = Signal(str)
    exited = Signal(int)

    #: "The pty is not there any more", in every spelling the backend uses for it.
    #: Subclasses widen this; the two members here are the ones both platforms share.
    #: Notably ``EOFError``, which is *not* an ``OSError`` -- writing to a closed pty raises
    #: it, and catching only OSError let it escape into a Qt slot and take the app down
    #: through the crash hook. That is the normal shutdown path: closing a pane writes a
    #: resize on the way out. A write racing the shell's exit is expected, not exceptional.
    GONE: tuple[type[BaseException], ...] = (OSError, EOFError)

    def __init__(self, argv: list[str], cwd: str, env: dict[str, str] | None = None,
                 cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.argv = list(argv)
        self.cwd = cwd
        # `env.for_child()` and not `os.environ`, because a frozen app's environment is
        # partly addressed to itself -- see `asomedeck/env.py`. This is the single door:
        # both backends spawn through `SPAWN` below and both are handed this dictionary,
        # so the cleanup cannot be true of one platform and forgotten on the other. What
        # the caller passes still wins over it; those variables are ours deliberately.
        self._env = {**for_child(), **(env or {})}
        self._cols, self._rows = cols, rows
        self._pty: Any = None
        self._reader: _Reader | None = None

    # --- the platform seam ---------------------------------------------------------------

    @staticmethod
    def SPAWN(argv: list[str], cwd: str, env: dict[str, str],  # noqa: N802 - a hook, not a method
              rows: int, cols: int) -> Any:
        """Start ``argv`` on a pty. Supplied by the platform subclass in conpty.py."""
        raise NotImplementedError

    # --- lifecycle -----------------------------------------------------------------

    def start(self) -> None:
        self._pty = self.SPAWN(self.argv, self.cwd, self._env, self._rows, self._cols)
        self._reader = _Reader(self._pty, self)
        self._reader.chunk.connect(self.output)
        self._reader.closed.connect(self._on_closed)
        self._reader.start()
        log.info("pty started pid=%s argv=%s cwd=%s", self.pid, self.argv, self.cwd)

    @property
    def pid(self) -> int:
        return int(getattr(self._pty, "pid", 0) or 0)

    def is_alive(self) -> bool:
        return bool(self._pty is not None and self._pty.isalive())

    # --- io ------------------------------------------------------------------------

    def write(self, data: str) -> None:
        if self._pty is None:
            return
        try:
            self._pty.write(data)
        except self.GONE:
            # The shell exited between the keystroke and the write. The reader thread
            # will report it; losing the keystroke is the correct outcome.
            pass

    def resize(self, cols: int, rows: int) -> None:
        """Tell the shell its new size, in cells.

        Cells, not pixels. This is the whole reason a console host letterboxes and a
        pty-backed terminal does not: the shell is told the grid it actually has.
        """
        cols, rows = max(1, int(cols)), max(1, int(rows))
        if (cols, rows) == (self._cols, self._rows):
            return
        self._cols, self._rows = cols, rows
        if self._pty is None:
            return
        try:
            self._pty.setwinsize(rows, cols)
        except self.GONE:
            pass

    # --- shutdown ------------------------------------------------------------------

    #: How long close() waits for the reader thread to notice. Terminating the pty closes
    #: the channel the thread is blocked on, so this is a backstop, not the mechanism.
    JOIN_TIMEOUT_MS = 2000

    def close(self, force: bool = False) -> None:
        """Ask the shell to exit, stop reading, and wait for the reader to actually stop.

        There is no orphan to worry about: killing the pty takes the shell with it, because
        we own the pty rather than a window that happens to have one behind it.

        The wait is not optional. The reader is parented to this QObject, so it is deleted
        when we are -- and deleting a running QThread is a Qt fatal, not a warning. The
        thread is blocked in a read that only returns once the pty is gone, so the order
        here matters: terminate first, then join.
        """
        if self._reader is not None:
            self._reader.stop()
        if self._pty is not None:
            try:
                self._pty.terminate(force=force)
            except Exception:
                pass
        if self._reader is not None and self._reader.isRunning():
            if not self._reader.wait(self.JOIN_TIMEOUT_MS):
                log.warning("reader thread for pid=%s did not stop in %dms",
                            self.pid, self.JOIN_TIMEOUT_MS)

    def _on_closed(self) -> None:
        code = 0
        try:
            code = int(self._pty.exitstatus or 0) if self._pty is not None else 0
        except Exception:
            code = 0
        log.info("pty exited pid=%s code=%s", self.pid, code)
        self.exited.emit(code)
