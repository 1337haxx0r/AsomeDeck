"""Shared fixtures.

Most of the suite is headless on purpose -- the win32 style math and the import boundary
need no window station, so they stay runnable anywhere. Anything needing a QApplication
skips rather than fails, so a headless run still reports the rest honestly instead of
going red for a reason that has nothing to do with the code.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def app():
    widgets = pytest.importorskip("PySide6.QtWidgets")
    existing = widgets.QApplication.instance()
    if existing is not None:
        yield existing
        return
    try:
        instance = widgets.QApplication([])
    except Exception as exc:  # pragma: no cover - headless machine
        pytest.skip(f"no QApplication available: {exc}")
    yield instance


@pytest.fixture
def make_window(app, tmp_path):
    """Build real MainWindows over an in-memory database with one deck registered.

    A factory rather than a window, because the interesting differences between windows
    are constructor arguments -- a config that points at a file, a theme that is not the
    default -- and a fixture that had already built one could not vary them.

    Three things are held constant for every window it makes, and each is a way an
    unattended run could reach past itself: ``:memory:`` so no state.db is touched, a
    path-less Config unless one is passed so no config.json is written, and
    ``quit_action = "close"`` so closing never raises the modal quit prompt, which in a
    test run is a hang and not a dialog.
    """
    pytest.importorskip("PySide6.QtWidgets")
    from asomedeck.db import Database
    from asomedeck.ui import theme as th
    from asomedeck.ui.main_window import MainWindow

    built = []

    def build(config=None, mode: str | None = None, **kwargs):
        theme = th.Theme(mode or (config.theme if config is not None else "dark"))
        th.apply(app, theme)
        db = Database(":memory:")
        db.add_deck(str(tmp_path), "gitbash")
        window = MainWindow(db, theme, frameless=False, restore_sessions=False,
                            config=config, **kwargs)
        window.quit_action = "close"
        built.append(window)
        return window

    yield build
    for window in built:
        window.quit_action = "close"
        window.close()
    # The QApplication is session-scoped, so a window left in the light theme would hand
    # the next test a stylesheet it never asked for.
    th.apply(app, th.Theme("dark"))


@pytest.fixture
def window(make_window):
    """One window, default everything. What most window tests want."""
    return make_window()
