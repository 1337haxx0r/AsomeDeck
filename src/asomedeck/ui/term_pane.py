"""A terminal pane: one shell on a pseudo-console, rendered by Qt.

Built to the shape its predecessor had -- ``started`` / ``exited``, ``is_alive()``,
``focus_terminal()``, ``close_session()``, ``apply_theme()``, ``pid``. That is the whole
surface ``pane_tree.Pane`` and ``main_window`` consume, and they consume it by ``getattr``,
which is what let the xterm.js pane be swapped out for this one by changing an import.

What changed underneath is everything the web view was costing. Measured, three panes:

* **No render process.** Twelve processes and 572 MB became eight and 210 MB, and the web
  view's share of that scaled per pane. It is why restoring a layout had to be sequential.
* **No page load.** The old pane could not spawn its shell until QtWebEngine had loaded
  ``terminal.html`` and the QWebChannel bridge had connected -- a few hundred milliseconds
  during which output had to be buffered and the pane honestly reported itself as empty.
  The widget here exists as soon as it is constructed, so the pty starts on the next turn
  of the event loop and there is nothing to buffer.

The one thing that has *not* changed: the shell still dies with this process, because the
pty belongs to it. A broker holding the ConPTYs remains the v2 answer.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..term.conpty import ConptySession
from .term_view import TerminalView
from .theme import Theme

log = logging.getLogger(__name__)


class TerminalPane(QWidget):
    """One shell, rendered by Qt, with no window and no browser of its own."""

    #: The shell is running. Still deferred by one event-loop turn rather than emitted
    #: from __init__ -- callers connect to it *after* constructing us, so emitting
    #: inline would fire into an empty connection list and stall the relaunch queue.
    started = Signal()
    exited = Signal(int)
    #: The program set the terminal title (OSC 0/2). Forwarded rather than consumed: the
    #: emulator captured this from the first commit and nothing listened, so a pane running
    #: Claude Code still called itself "Git Bash".
    titleChanged = Signal(str)
    #: The grid changed size, in cells. Forwarded from the view for the same reason
    #: titleChanged is: what main_window holds is a Pane, and reaching through it into
    #: ``view.view`` would make the pane header depend on which renderer is inside.
    resized = Signal(int, int)
    #: Right-click inside the terminal, in global coordinates, when the program running in
    #: it has not claimed the mouse. Forwarded for the reason the two above are.
    contextMenuRequested = Signal(object)

    def __init__(self, argv: list[str], cwd: str, theme: Theme | None = None,
                 env: dict[str, str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme or Theme()
        self._argv, self._cwd, self._env = list(argv), cwd, env
        self._started = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.view = TerminalView(self._theme, parent=self)
        layout.addWidget(self.view)

        self.session = ConptySession(self._argv, self._cwd, self._env, parent=self)
        self.session.output.connect(self.view.feed)
        self.session.exited.connect(self._on_exited)
        self.view.dataReady.connect(self._on_data)
        self.view.resized.connect(self.session.resize)
        self.view.resized.connect(self.resized)
        self.view.titleChanged.connect(self.titleChanged)
        self.view.contextMenuRequested.connect(self.contextMenuRequested)

        # Deferred to the next turn so Qt has laid the widget out first: starting now
        # would spawn the shell at the default 80x24 and immediately resize it, and a
        # shell that has already drawn its prompt does not redraw it.
        QTimer.singleShot(0, self._start)

    # --- lifecycle -------------------------------------------------------------------

    def _start(self) -> None:
        if self._started:
            return
        self._started = True
        cols, rows = self.view.grid_size()
        # Applied before start(): with no pty yet this only records the size, which is
        # then what the pseudo-console is created with.
        self.session.resize(cols, rows)
        self.view.emulator.resize(cols, rows)
        try:
            self.session.start()
        except Exception:
            log.exception("failed to start %s in %s", self._argv, self._cwd)
            self.view.feed("\r\n\x1b[31mFailed to start this shell. See the log.\x1b[0m\r\n")
            self.exited.emit(1)
            return
        log.info("terminal pane ready, pid=%s grid=%dx%d", self.session.pid, cols, rows)
        # The starting size, which no resizeEvent will announce: the view has already been
        # laid out by now, so nothing further changes and the header would otherwise stay
        # blank until the first splitter drag.
        self.resized.emit(cols, rows)
        self.started.emit()

    def _on_data(self, data: bytes) -> None:
        # keys.py deals in bytes because that is what a terminal protocol is; a session
        # takes str and encodes it back. Our own encodings are always valid UTF-8, so this
        # round-trips exactly; `replace` is a guard against a stray surrogate, not a
        # working part of the path.
        #
        # str rather than bytes because that is the shape the Windows binding fixed, and
        # the Unix backend matches it rather than the reverse -- changing the contract would
        # mean touching every caller to save one encode in one file.
        self.session.write(data.decode("utf-8", errors="replace"))

    def _on_exited(self, code: int) -> None:
        self.exited.emit(code)

    # --- the surface main_window and pane_tree duck-type ----------------------------

    @property
    def pid(self) -> int:
        return self.session.pid

    def is_alive(self) -> bool:
        return self.session.is_alive()

    def apply_theme(self, theme: Theme) -> None:
        """Recolour a *running* terminal, including its scrollback."""
        self._theme = theme
        self.view.apply_theme(theme)

    def focus_terminal(self) -> None:
        self.view.setFocus()

    def close_session(self, force: bool = False) -> None:
        self.session.close(force=force)

    # --- what the right-click menu acts on ------------------------------------------
    #
    # Forwarded rather than left to the caller to reach through ``pane.view``. What
    # main_window holds is a Pane, and a menu that indexed into the renderer inside it
    # would be a menu that only works while the renderer is this one -- which is exactly
    # the coupling the getattr surface above exists to avoid.

    @property
    def has_selection(self) -> bool:
        return self.view.has_selection

    @property
    def has_scrollback(self) -> bool:
        return self.view.has_scrollback

    def copy(self) -> None:
        self.view.copy()

    def paste(self) -> None:
        self.view.paste()

    def select_all(self) -> None:
        self.view.select_all()

    def clear_scrollback(self) -> None:
        self.view.clear_scrollback()
