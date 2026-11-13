"""Which operating system this is, and where its per-user files belong.

Named with a trailing underscore because ``platform`` is a standard-library module and this
package is imported as ``asomedeck.platform_`` from modules that also want the real one.

Two things live here and nothing else does. The first is the pair of predicates every other
platform branch in the codebase reads, so that "what are we running on" is answered in one
place rather than re-derived. The second is the per-user directory layout, which was
previously answered three separate times -- ``config.py`` for config.json, ``db.py`` for
state.db and ``app.py`` for the log -- each with its own copy of the same
``os.environ.get(...) or os.path.expanduser("~")`` line. Three copies of one rule is how a
port moves two of them and leaves the third pointing at the old place.

Deliberately not QStandardPaths. These are read *before* ``QApplication`` exists --
``__main__.main`` loads the config to know which theme to build the application with -- and
QStandardPaths on a bare QCoreApplication-less process is not something to rely on. It would
also relocate the Windows paths, which is the one thing that must not happen: an existing
install has decks and a saved layout under ``%APPDATA%\\AsomeDeck``, and a release that
quietly starts reading somewhere else looks exactly like data loss to the person it happens
to. tests/test_platform.py pins the Windows answers for that reason.
"""

from __future__ import annotations

import os
import pathlib
import sys

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

#: The one name every directory below is nested under, on both platforms.
APP_DIR_NAME = "AsomeDeck"


def _home() -> pathlib.Path:
    return pathlib.Path(os.path.expanduser("~"))


def _windows_roaming() -> pathlib.Path:
    """``%APPDATA%``, or the home directory when it is not set.

    The fallback is not hypothetical: a service account and a stripped-down container both
    reach here without APPDATA, and the original code in config.py and db.py chose exactly
    this shape. Kept identical so the paths do not move.
    """
    return pathlib.Path(os.environ.get("APPDATA") or os.path.expanduser("~"))


def _windows_local() -> pathlib.Path:
    """``%LOCALAPPDATA%``, or the home directory. The log's root, as app.py had it."""
    return pathlib.Path(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"))


def config_dir() -> pathlib.Path:
    """Where config.json lives."""
    if IS_WINDOWS:
        return _windows_roaming() / APP_DIR_NAME
    return _home() / "Library" / "Application Support" / APP_DIR_NAME


def state_dir() -> pathlib.Path:
    """Where state.db lives.

    The same directory as the config on both platforms, but asked for separately because
    they are different kinds of thing -- one is edited by hand, the other is a database --
    and a future move of either should not have to disturb the other.
    """
    if IS_WINDOWS:
        return _windows_roaming() / APP_DIR_NAME
    return _home() / "Library" / "Application Support" / APP_DIR_NAME


def log_dir() -> pathlib.Path:
    """Where asomedeck.log and asomedeck-fault.log live.

    ``~/Library/Logs`` on macOS rather than Application Support: it is where Console.app
    looks, which is the whole point of a log a user might be asked to send.
    """
    if IS_WINDOWS:
        return _windows_local() / APP_DIR_NAME
    return _home() / "Library" / "Logs" / APP_DIR_NAME
