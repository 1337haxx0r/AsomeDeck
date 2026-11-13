"""The platform boundary, after the docking backend was removed.

Spec §10 required that every Win32 import live behind ``embed/`` -- a real constraint when
the app was manipulating other processes' windows across six modules.

That surface is now one file. ``winapi.py`` answers two messages for our own frameless
window and makes the pseudo-console calls; the terminal code above it asks for those by name
and holds nothing but numbers it was handed. This test pins that, so the boundary is not
quietly re-crossed the next time something needs a handle.
"""

from __future__ import annotations

import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "asomedeck"

#: Only this module may touch Win32. Everything else is portable Qt and Python, which is
#: not an accident -- it is what makes a non-Windows port conceivable at all. It is also no
#: longer hypothetical: the macOS port was the cheque this rule wrote, and it cashed.
ALLOWED = {"winapi.py"}

#: And the same rule from the other side, added with the port. A boundary that is only
#: enforced in one direction stops being a boundary the moment the second platform arrives:
#: without this, ``fcntl`` and ``termios`` would be free to spread into term_pane.py or
#: main_window.py exactly as ctypes once threatened to, and the next port would have two
#: messes to clean instead of none.
POSIX_ALLOWED = {"unixpty.py"}

WIN32_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(ctypes|win32gui|win32con|win32api|win32process|win32event)\b",
    re.MULTILINE,
)

POSIX_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(fcntl|termios|pty|tty|grp|pwd)\b",
    re.MULTILINE,
)


def _modules() -> list[pathlib.Path]:
    return sorted(SRC.rglob("*.py"))


def test_win32_is_confined_to_one_module():
    offenders = [
        path.relative_to(SRC).as_posix()
        for path in _modules()
        if WIN32_IMPORT.search(path.read_text(encoding="utf-8"))
        and path.name not in ALLOWED
    ]
    assert offenders == [], f"Win32 imported outside {ALLOWED}: {offenders}"


def test_posix_is_confined_to_one_module():
    """The mirror of the rule above, and for the same reason."""
    offenders = [
        path.relative_to(SRC).as_posix()
        for path in _modules()
        if POSIX_IMPORT.search(path.read_text(encoding="utf-8"))
        and path.name not in POSIX_ALLOWED
    ]
    assert offenders == [], f"POSIX-only modules imported outside {POSIX_ALLOWED}: {offenders}"


def test_the_boundary_modules_exist():
    assert (SRC / "winapi.py").is_file()
    assert (SRC / "term" / "unixpty.py").is_file()
    assert (SRC / "term" / "windowspty.py").is_file()


def test_pywinpty_stays_gone():
    """It put a console window on the desktop on every launch of the exe.

    Three places, because a dependency returns through any one of them: an import, the
    package list, or a build that goes on collecting something nothing imports.
    """
    importing = re.compile(r"^\s*(?:from|import)\s+winpty\b", re.MULTILINE)
    offenders = [
        path.relative_to(SRC).as_posix()
        for path in _modules()
        if importing.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"winpty imported again: {offenders}"
    repo = SRC.parent.parent
    assert "pywinpty" not in (repo / "pyproject.toml").read_text(encoding="utf-8")
    assert "winpty" not in (repo / "packaging" / "asomedeck.spec").read_text(encoding="utf-8")


def test_the_platform_seam_is_the_only_place_that_asks_what_os_this_is():
    """``sys.platform`` is answered once, in platform_.py, and read as a name elsewhere.

    Not stylistic. The codebase had no platform check at all before the port, and the way
    that stays legible is for the branches to be countable -- ``IS_WINDOWS`` and
    ``IS_MACOS`` are greppable, ``sys.platform == "darwin"`` scattered through the UI is
    not, and the second one is how you end up with two files disagreeing about what a Mac
    is.
    """
    probe = re.compile(r"""(sys\.platform|os\.name\s*==|platform\.system\(\))""")
    offenders = [
        path.relative_to(SRC).as_posix()
        for path in _modules()
        if probe.search(path.read_text(encoding="utf-8")) and path.name != "platform_.py"
    ]
    assert offenders == [], f"platform checked outside platform_.py: {offenders}"


def test_the_docking_backend_is_gone():
    # Removing it was the point. A stray re-import would resurrect the HWND discovery,
    # style capture and detach contract this architecture deliberately dropped.
    assert not (SRC / "embed").exists()
    assert not (SRC / "controller.py").exists()


def test_no_module_still_imports_the_docking_backend():
    stale = [
        path.relative_to(SRC).as_posix()
        for path in _modules()
        if re.search(r"\bfrom\s+\.{1,2}(embed|controller)\b", path.read_text(encoding="utf-8"))
    ]
    assert stale == [], f"still importing the removed backend: {stale}"


WEB_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+PySide6\.(QtWebEngine\w*|QtWebChannel)",
    re.MULTILINE,
)


def test_nothing_imports_qtwebengine():
    """The browser went the way the docking backend did, and for a comparable reason.

    xterm.js in a QWebEngineView worked. It cost twelve processes and 572 MB to show three
    terminals where painting the grid ourselves costs eight and 210 MB, and it put a page
    load in front of every shell. Those are also the only two PySide6-Addons modules the
    deck ever imported, so this is what keeps the dependency at PySide6-Essentials.
    """
    offenders = [
        path.relative_to(SRC).as_posix()
        for path in _modules()
        if WEB_IMPORT.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"QtWebEngine is back in: {offenders}"


def test_the_xterm_assets_are_gone():
    assert not (SRC.parent.parent / "assets" / "xterm").exists()


def test_terminal_code_needs_no_win32():
    # The point of a pseudo-console: the terminal is a library call and a byte stream.
    for name in ("term/conpty.py", "term/windowspty.py", "ui/term_view.py", "ui/term_pane.py"):
        text = (SRC / name).read_text(encoding="utf-8")
        assert not WIN32_IMPORT.search(text), f"{name} should not need Win32"


def test_the_pane_imports_a_session_and_never_a_backend():
    """``term_pane.py`` imports ``ConptySession`` and neither implementation behind it.

    The seam is at the binding, and nothing above it gets to know there are two. If this
    fails the platform has leaked one layer up -- and the layer above that is the whole UI.

    Imports only, not prose: ``term/conpty.py`` deliberately kept its name through the port
    (it is referred to by it from the design notes' file map, three module docstrings and this
    file), so the word appears in comments that are describing history rather than choosing
    a backend.
    """
    text = (SRC / "ui" / "term_pane.py").read_text(encoding="utf-8")
    backend_import = re.compile(r"^\s*(?:from|import)\s+.*\b(windowspty|unixpty|ptysession)\b",
                                re.MULTILINE)
    assert not backend_import.search(text), "term_pane.py should import only the session"
