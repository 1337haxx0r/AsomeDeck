"""A shell on a pseudo-terminal, whichever kind this platform has.

We own the pty and something else draws the result. That is the whole architecture now.

What it buys, against the docking backend it replaced:

* No HWND. No discovery poll, no reparenting, no style capture, no detach contract, and
  no window flashing on the desktop before it is adopted.
* No MSYS pty ceiling. ConPTY is a Windows facility; mintty's ~128-pty pool was a
  Cygwin limit, and exhausting it is what made every terminal fail to start.
* Sessions *could* outlive the app, later. A pty dies with the process holding it, so
  today that is still no -- but a broker process holding ptys is a tractable thing to
  build, whereas surviving with foreign HWNDs in our window tree is impossible.
* A port. Owning a byte stream rather than somebody's window is what made macOS a second
  backend rather than a second application.

What it costs is everything a terminal does: the shell now talks to us in VT sequences
and somebody has to render them. That somebody is term/emulator.py and ui/term_view.py,
which is why this module deals only in bytes and never interprets them.

**This file is now the platform choice and nothing else.** The session and its reader
thread moved to ``ptysession.py`` unchanged, because they never knew what kind of pty they
held; the two backends are ``windowspty.py`` and ``unixpty.py``, both next door. The module
keeps its name, and ``ConptySession`` keeps its, on purpose: both are referred to by name from
``ui/term_pane.py``, ``tests/test_import_boundary.py``, the design notes' file map and three
module docstrings, and renaming a thing in eleven places to describe one of its two
implementations is a worse lie than the one it fixes.

The backend import stays at module scope, not inside ``start()``. The lazy import read
better, but it put the failure somewhere nothing could catch it: ui/main_window.py wraps
the pane import in ``except ImportError`` precisely so a missing backend is a dialog rather
than a crash, and an import deferred to ``start()`` surfaces instead inside a Qt slot, where
the crash hook turns it into app.exit(1). This module *is* the terminal; if it cannot import,
importing the pane should fail too, where somebody is listening.
"""

from __future__ import annotations

from typing import Any

from .. import platform_
from .ptysession import DEFAULT_COLS, DEFAULT_ROWS, PtySession

__all__ = ["ConptySession", "DEFAULT_COLS", "DEFAULT_ROWS"]


if platform_.IS_WINDOWS:
    from .windowspty import WindowsPty

    class ConptySession(PtySession):
        """One shell on a Windows pseudo-console."""

        @staticmethod
        def SPAWN(argv: list[str], cwd: str, env: dict[str, str],  # noqa: N802
                  rows: int, cols: int) -> Any:
            return WindowsPty.spawn(argv, cwd=cwd, env=env, dimensions=(rows, cols))

else:
    # Any Unix, though macOS is the one that is actually tested. Linux would need nothing
    # here beyond somebody running it; shells.py is the file that would have opinions.
    from .unixpty import UnixPty

    class ConptySession(PtySession):
        """One shell on a Unix pty. The name is the module's, not the mechanism's."""

        @staticmethod
        def SPAWN(argv: list[str], cwd: str, env: dict[str, str],  # noqa: N802
                  rows: int, cols: int) -> Any:
            return UnixPty.spawn(argv, cwd=cwd, env=env, dimensions=(rows, cols))
