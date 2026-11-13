# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for AsomeDeck.

Run from the repo root:

    .venv\\Scripts\\python.exe -m PyInstaller packaging\\asomedeck.spec --noconfirm

Produces ``dist/AsomeDeck-<version>.exe`` and nothing beside it.

**onefile.** One executable and nothing else to copy around. The cost is real and
worth knowing: a onefile build unpacks its whole payload into a temporary directory on
every launch, so startup is slower than a directory build every single time -- see the
measured figure in docs/design-notes.md. Dropping QtWebEngine keeps that survivable; it would be
markedly worse with a browser inside.

The script frozen here is ``entry.py``, not ``src/asomedeck/__main__.py``. The latter is a
module written with relative imports, and PyInstaller runs whatever it is given as
top-level ``__main__``, which has no parent package -- so freezing it directly fails at
import before the window ever appears.

**windowed.** No console window: the terminals live inside the app, and a stray conhost
behind it is exactly the desktop clutter the app exists to remove. ``app.py`` only
installs a stderr log handler when there is a stderr, because in this mode there is not.

Two collections are explicit rather than left to PyInstaller's analysis:

* ``pyte`` and ``wcwidth`` carry data modules that are imported by name.
* ``assets`` has to travel: the icon, the fonts directory the theme globs at startup, and the
  console host in ``assets/conpty``. ``asomedeck/resources.py`` is what makes all three
  resolvable here as well as from source. ``conpty.dll`` finds ``OpenConsole.exe`` beside
  itself, so those two have to arrive in one folder.
"""

import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

# PyInstaller executes a spec with its own directory *not* on sys.path, so the helper
# beside this file is not importable without saying so. SPECPATH is injected for exactly
# this.
sys.path.insert(0, SPECPATH)  # noqa: F821 - injected by PyInstaller
import version_info  # noqa: E402

# Regenerated on every build from asomedeck.__version__, so the exe's Properties pane
# cannot report a version the app does not.
VERSION_FILE = str(version_info.write())

#: Read back off the module that just wrote the resource, rather than imported a second
#: time here: version_info reaches ``asomedeck.__version__`` to build the file above, so
#: taking the name from there is what makes the version in the filename and the version in
#: Properties provably the same read. tests/test_version.py keeps that single source honest.
VERSION = version_info.__version__

datas = [("../assets", "assets")]
binaries = []
hiddenimports = ["asomedeck", "asomedeck.resources"]

for package in ("pyte", "wcwidth"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

hiddenimports += collect_submodules("asomedeck")

#: Qt modules this app never touches. QtWebEngine went with xterm.js and must not come
#: back in through a transitive hook -- tests/test_import_boundary.py pins the source side
#: of that, and this is the packaging side of the same rule.
excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSerialPort",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtTest",
    "tkinter", "unittest", "pydoc", "doctest", "pytest", "_pytest",
]

a = Analysis(
    ["entry.py"],
    pathex=["../src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    # The exe carries its version in its own name -- ``AsomeDeck-0.7.2.exe`` -- the same
    # way the macOS bundle does, and for the same reason: two builds can then sit in
    # ``dist/`` at once. Overwriting the previous one means the only way to answer "is
    # this the build with the fix" is to launch it and read the status bar, and a
    # ``--noconfirm`` build that replaced an exe you were still running is worse than
    # that. Everything *inside* stays plain ``AsomeDeck``: the window title, the taskbar
    # name, and every string in the version resource but OriginalFilename, which is
    # supposed to record the name the binary was built as.
    name=f"AsomeDeck-{VERSION}",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="../assets/icon/asomedeck.ico",
    version=VERSION_FILE,
)
