"""Entry point: ``python -m asomedeck``."""

from __future__ import annotations

import logging
import pathlib
import sys

from PySide6.QtCore import Qt, QTimer

from . import config as cfg
from .app import build_application
from .db import Database
from .ui import theme as th
from .ui.main_window import MainWindow


def main() -> int:
    smoke = "--smoke" in sys.argv
    # Read before the QApplication, because the theme it names has to be applied before
    # the first window is painted. Nothing here needs Qt, which is why it can run first.
    #
    # Smoke runs get a throwaway config and a throwaway database, for one reason: an
    # unattended run must not rewrite what is on the machine it happens to be running on.
    # Pointing them at the real database meant every run appended its panes to the user's
    # saved layout, which quietly grew to 25.
    config = cfg.Config() if smoke else cfg.load()
    app = build_application(sys.argv, config.theme)

    if smoke:
        import tempfile
        db = Database(pathlib.Path(tempfile.mkdtemp(prefix="asomedeck-smoke-")) / "state.db")
    else:
        db = Database()
    was_clean = db.begin_run()

    window = MainWindow(db, th.Theme(config.theme),
                        restore_sessions=not smoke, config=config)
    window.show()

    # Spec 8, mitigation 2. Deferred past show() so the warning has a parent window and
    # does not appear before the app it is talking about.
    if not was_clean and not smoke:
        QTimer.singleShot(400, window.warn_previous_run_crashed)

    # --smoke N: open three panes, then quit after N seconds. Exercises launch -> render
    # -> shutdown unattended, for CI and for checking a change did not break the terminal
    # without sitting through it by hand.
    if smoke:
        idx = sys.argv.index("--smoke")
        seconds = float(sys.argv[idx + 1]) if len(sys.argv) > idx + 1 else 12.0
        # Never prompt in an unattended run. A modal quit dialog has hung this path
        # three times now; it is cheaper to make it structurally impossible.
        window.quit_action = "close"
        QTimer.singleShot(600, lambda: window._new_session(None))
        QTimer.singleShot(2800, lambda: window._new_session(Qt.Orientation.Horizontal))
        QTimer.singleShot(5000, lambda: window._new_session(Qt.Orientation.Vertical))
        # --crash: die by unhandled exception rather than quitting, to confirm the shells
        # go down with the process instead of lingering.
        if "--crash" in sys.argv:
            QTimer.singleShot(int(seconds * 1000), window._raise_test_exception)
        else:
            QTimer.singleShot(int(seconds * 1000), window.close)
        result = app.exec()
        _report(window)
        return result

    return app.exec()


def _report(window: MainWindow) -> None:
    """Report what the smoke run achieved.

    Under docking this checked that every terminal survived detachment with its styles
    restored exactly. Nothing has to survive now -- the shells belong to this process --
    so the useful question is the opposite one: did they all start, and did they all go.
    """
    log = logging.getLogger("asomedeck.smoke")
    panes = [p for tree in window._trees.values() for p in tree.panes]
    started = [p for p in panes if p.started_at is not None]
    alive = [p for p in panes if p.has_session]
    for pane in panes:
        log.info("SMOKE pane %d shell=%s started=%s alive=%s",
                 pane.pane_id, pane.launcher_id, pane.started_at is not None,
                 pane.has_session)
    log.info("SMOKE TOTAL %d/%d pane(s) started; %d still alive at exit",
             len(started), len(panes), len(alive))


if __name__ == "__main__":
    raise SystemExit(main())
