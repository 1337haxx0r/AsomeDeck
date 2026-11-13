"""Where per-user files live, on each platform.

The reason this file exists is one specific regression, and it is the worst kind: silent.
``config.json``, ``state.db`` and ``asomedeck.log`` each used to resolve their own directory
with their own copy of ``os.environ.get("APPDATA") or os.path.expanduser("~")``. The macOS
port replaced all three with ``platform_``, and if that rewrite moved the Windows answer by
so much as a directory name, an existing install would come up on the next release with no
decks, no saved layout and no error -- indistinguishable, from the outside, from having
lost the lot.

So the Windows cases are pinned against literals rather than against ``platform_``'s own
helpers. Asserting that the function agrees with itself would pass no matter what it
returned; these spell out the paths that shipped in 0.6.0.
"""

from __future__ import annotations

import pathlib

import pytest

from asomedeck import platform_
from asomedeck.app import log_path
from asomedeck.config import default_path
from asomedeck.db import default_db_path

WINDOWS_ONLY = pytest.mark.skipif(not platform_.IS_WINDOWS, reason="Windows layout")
MACOS_ONLY = pytest.mark.skipif(not platform_.IS_MACOS, reason="macOS layout")


def test_exactly_one_platform_is_true():
    """Both false is a platform nobody has tested, and it should be obvious in the logs."""
    assert not (platform_.IS_WINDOWS and platform_.IS_MACOS)


# --- Windows: these paths must not move --------------------------------------------------

@WINDOWS_ONLY
def test_windows_config_and_state_live_under_appdata(monkeypatch):
    monkeypatch.setenv("APPDATA", r"C:\Users\x\AppData\Roaming")
    assert platform_.config_dir() == pathlib.Path(r"C:\Users\x\AppData\Roaming\AsomeDeck")
    assert platform_.state_dir() == pathlib.Path(r"C:\Users\x\AppData\Roaming\AsomeDeck")


@WINDOWS_ONLY
def test_windows_log_lives_under_localappdata(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\x\AppData\Local")
    assert platform_.log_dir() == pathlib.Path(r"C:\Users\x\AppData\Local\AsomeDeck")


@WINDOWS_ONLY
def test_windows_falls_back_to_home_when_appdata_is_unset(monkeypatch):
    """A service account and a stripped-down container both reach here without APPDATA.
    The original code chose this fallback; keeping it is the whole point."""
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    home = pathlib.Path("~").expanduser()
    assert platform_.config_dir() == home / "AsomeDeck"
    assert platform_.log_dir() == home / "AsomeDeck"


# --- macOS -------------------------------------------------------------------------------

@MACOS_ONLY
def test_macos_uses_the_directories_the_platform_expects():
    home = pathlib.Path("~").expanduser()
    support = home / "Library" / "Application Support" / "AsomeDeck"
    assert platform_.config_dir() == support
    assert platform_.state_dir() == support
    # Logs, not Application Support: it is where Console.app looks, which is the point of
    # a log a user might be asked to send.
    assert platform_.log_dir() == home / "Library" / "Logs" / "AsomeDeck"


@MACOS_ONLY
def test_macos_paths_are_absolute_and_not_windows_shaped():
    for path in (platform_.config_dir(), platform_.state_dir(), platform_.log_dir()):
        assert path.is_absolute()
        assert "\\" not in str(path)


# --- the callers actually use it ---------------------------------------------------------

def test_the_three_files_sit_in_the_directories_platform_names():
    """The rewrite is only worth anything if the call sites went through it."""
    assert default_path() == platform_.config_dir() / "config.json"
    assert default_db_path() == platform_.state_dir() / "state.db"
    assert log_path() == platform_.log_dir() / "asomedeck.log"


# --- the first-run seed ------------------------------------------------------------------

def test_the_seed_deck_is_never_the_filesystem_root(monkeypatch, tmp_path):
    """A bundle launched from Finder or the Dock inherits ``/`` as its working directory.

    Registering that as a deck is not a cosmetic wrong answer: FileTree walks it and
    GitStatus polls it every three seconds, so a first run from the Dock would set the app
    scanning the whole disk. Found by launching the real .app rather than by reading the
    code -- running it from a shell hands it a sensible cwd and hides this completely.
    """
    pytest.importorskip("PySide6.QtWidgets")
    from asomedeck.ui.main_window import _seed_deck_dir

    monkeypatch.chdir(pathlib.Path(pathlib.Path("/").anchor))
    assert _seed_deck_dir() == str(pathlib.Path("~").expanduser())

    monkeypatch.chdir(tmp_path)
    assert _seed_deck_dir() == str(tmp_path)
