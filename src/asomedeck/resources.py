"""Where the bundled assets are, in both the ways this app runs.

From a source checkout ``assets/`` sits beside ``src/``, four parents up from a module in
``asomedeck/ui/``. Frozen by PyInstaller there is no ``src/`` at all: the bundle is
unpacked into a temporary directory named by ``sys._MEIPASS``, and the same walk lands
somewhere that does not exist.

This was already a known gap before there was anything to freeze -- the font loader walked
those four parents, and the design notes recorded that a wheel would not ship ``assets/`` at all.
Both callers now ask here instead, so there is one place that knows the answer and one
place to change when it moves again.
"""

from __future__ import annotations

import pathlib
import sys

from . import platform_


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def root() -> pathlib.Path:
    """The directory that contains ``assets``."""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return pathlib.Path(bundle)
    # src/asomedeck/resources.py -> src/asomedeck -> src -> <repo>
    return pathlib.Path(__file__).resolve().parent.parent.parent


def assets() -> pathlib.Path:
    return root() / "assets"


def font_dir() -> pathlib.Path:
    return assets() / "fonts"


def conpty_dir() -> pathlib.Path:
    """The bundled pseudo-console host. Only ``term/windowspty.py`` asks, only on Windows."""
    return assets() / "conpty"


def icon_path() -> pathlib.Path:
    """The icon for ``QIcon`` -- the window, and the taskbar or Dock.

    Not the same file on both platforms, and not for cosmetic reasons. The .ico is a
    multi-size container Windows wants; Qt on macOS reads .icns poorly through QIcon (it is
    the *bundle's* format, declared in Info.plist and drawn by the Dock, not something the
    app loads itself), so the 512px .png is both readable and the right thing to hand
    ``setWindowIcon``. The .icns still ships -- ``packaging/asomedeck-mac.spec`` passes it
    to ``BUNDLE`` -- it is just not loaded through here.
    """
    if platform_.IS_WINDOWS:
        return assets() / "icon" / "asomedeck.ico"
    return assets() / "icon" / "asomedeck.png"


def bundle_icon_path() -> pathlib.Path:
    """The .icns the macOS bundle declares. Read at build time, never at run time."""
    return assets() / "icon" / "asomedeck.icns"
