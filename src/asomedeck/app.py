"""QApplication construction, logging, and crash reporting.

Much smaller than it was. Most of this file used to be the detach machinery spec §8
describes: five exit paths all racing to restore foreign windows before the process died,
because a child window is destroyed with its parent and those children were other
people's shells.

None of that applies to a pseudo-console. A pty is owned by this process and dies with
it, whatever route the exit takes -- there is no window to restore and nothing to race.
So what is left is logging, a faulthandler for post-mortem evidence, and shutting the
shells down tidily on the way out.

The limitation spec §8 named has not moved: a crash still takes every session with it.
It is now *addressable* rather than impossible -- a broker process holding the ConPTYs
would survive us -- but that is a v2 question and this file does not pretend otherwise.
"""

from __future__ import annotations

import faulthandler
import logging
import sys
from pathlib import Path

from PySide6.QtCore import Qt, qVersion
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication

from . import __version__, platform_, resources, winapi
from .ui import theme

log = logging.getLogger(__name__)


def log_path() -> Path:
    return platform_.log_dir() / "asomedeck.log"


#: Distinguishes "caller said nothing" from "caller said None", which is the whole point
#: of the parameter below.
_UNSET = object()


def _setup_logging(stream: object = _UNSET) -> None:
    """Configure logging, tolerating a process that has no streams.

    ``stream`` is a seam, not a feature: production passes nothing and gets ``sys.stderr``.
    It exists because the case worth testing is the one where there is no stderr, and
    patching ``sys.stderr`` to prove it does not work -- pytest's capture reinstalls its
    own object over the patch, so the test quietly exercises the branch it was written to
    avoid. A parameter cannot be overwritten by the harness.
    """
    err = sys.stderr if stream is _UNSET else stream
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(path, encoding="utf-8")]
    # A windowed PyInstaller build has no streams at all -- sys.stderr is None, and a
    # StreamHandler built on it raises the first time anything logs. The file is the real
    # record anyway; the console handler is a convenience for running from a terminal.
    if err is not None:
        handlers.append(logging.StreamHandler(err))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )
    _enable_faulthandler(path, err)


#: Held open for the life of the process. faulthandler keeps the *file descriptor*, not the
#: object, so letting this be collected would point a crash dump at a closed handle.
_fault_file = None


def _enable_faulthandler(log_file: Path, stream: object) -> None:
    """Arm the post-mortem dump for a native fault.

    A native fault runs no Python, so this cannot clean anything up; it is evidence and
    nothing more, which is all it ever was.

    It has to be told where to write. ``faulthandler.enable()`` defaults to ``sys.stderr``
    and raises ``RuntimeError: sys.stderr is None`` when there is not one -- which is the
    normal state of a windowed build launched from Explorer, so the app died on this line
    before its window ever appeared. It survived every test run only because launching it
    from a shell hands it the shell's streams.

    No stderr is also exactly when a dump is least useful on stderr, so it goes beside the
    log instead, where it can actually be read afterwards.
    """
    global _fault_file
    if stream is not None:
        faulthandler.enable()
        return
    try:
        _fault_file = open(log_file.with_name("asomedeck-fault.log"), "a",
                           encoding="utf-8")
        faulthandler.enable(file=_fault_file)
    except Exception:
        # Evidence is optional; starting is not. Said out loud rather than passed over --
        # a silent except here is how a broken crash dump stays broken.
        log.warning("could not arm the fault handler", exc_info=True)


def install_crash_hook() -> None:
    """Report an unhandled exception and stop, rather than carrying on half-dead.

    Qt catches exceptions raised inside slots, routes them to sys.excepthook, and then
    keeps pumping the event loop. Without this the app would continue in whatever broken
    state produced the traceback. An unhandled exception is a crash: report it and go.
    """
    original = sys.excepthook

    def _excepthook(exc_type, exc, tb):
        log.critical("unhandled exception", exc_info=(exc_type, exc, tb))
        original(exc_type, exc, tb)
        app = QApplication.instance()
        if app is not None:
            app.exit(1)

    sys.excepthook = _excepthook


#: Windows groups taskbar buttons by this, and a bare python.exe otherwise inherits the
#: interpreter's identity -- which shows the Python icon on the taskbar no matter what
#: setWindowIcon says, and groups every Qt app the user runs into one button.
APP_ID = "AsomeDeck.AsomeDeck.Shell.1"


def _claim_taskbar_identity() -> None:
    """Tell Windows this process is its own application, via the Win32 boundary module."""
    if not winapi.set_app_user_model_id(APP_ID):
        log.debug("could not set the app user model id")


def install_icon(app: QApplication) -> bool:
    """Give the app its icon, if one is bundled. Returns whether it was found."""
    path = resources.icon_path()
    if not path.is_file():
        log.warning("no application icon at %s", path)
        return False
    app.setWindowIcon(QIcon(str(path)))
    return True


def build_application(argv: list[str], mode: str = "dark") -> QApplication:
    _setup_logging()

    # PassThrough so Qt does not round 150% down to 100%. Kept from the docking era, where
    # a mismatch desynced a foreign child's geometry; it is still the honest setting.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    _claim_taskbar_identity()
    app = QApplication(argv)
    app.setApplicationName("AsomeDeck")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("AsomeDeck")
    install_icon(app)

    # Fonts before any stylesheet: QFontDatabase needs a live application, and QSS naming
    # a family that is not yet registered resolves to a fallback and stays there.
    theme.load_fonts()
    # The mode the config remembered, applied before the first window exists. Applying
    # "dark" here and switching afterwards would paint one frame of the wrong theme on
    # every light-theme launch.
    theme.apply(app, theme.Theme(mode))

    install_crash_hook()

    # The version leads, because this line is what a bug report quotes.
    log.info("AsomeDeck %s starting; python=%s PySide6=%s frozen=%s",
             __version__, sys.version.split()[0], qVersion(), resources.is_frozen())
    return app
