# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for AsomeDeck on macOS.

Run from the repo root:

    .venv/bin/python -m PyInstaller packaging/asomedeck-mac.spec --noconfirm

or, to get signing and notarization handled too, ``bash packaging/build_mac.sh``.

Produces ``dist/AsomeDeck-<version>.app`` -- see the BUNDLE at the bottom for why the
version is in the name.

**A separate file from asomedeck.spec, not a branch inside it.** The two disagree on nearly
every line that matters -- ``version_info`` builds a Windows PE resource and must not even
be imported here, the icon is a different file in a different format, the Windows console
host must not come along, and only this one ends in a BUNDLE -- so a single spec would be
six ``if`` blocks deep and harder to read than two files that each say one thing. What they
*do* share is ``entry.py`` and the excludes list, and both are shared deliberately: see
below.

**onedir, not onefile.** This is the one place the Mac build inverts a decision the Windows
build made on purpose, and for two reasons that both only apply here:

* A ``.app`` is already a directory. The single-file property that makes AsomeDeck.exe
  pleasant to hand somebody buys nothing inside a bundle the user drags as one icon anyway
  -- while still costing the unpack-to-temp on every launch that docs/design-notes.md measures at
  2.0-2.4s against 1.0s from source.
* Codesigning a onefile build does not work the way notarization needs. The signature
  covers the outer stub, and the payload it unpacks at runtime is opaque to the notary
  service. Every signed PyInstaller .app is onedir; making that choice now means enrolling
  in the Developer Program later changes credentials and not the build.
"""

import pathlib
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

# PyInstaller executes a spec with its own directory *not* on sys.path. SPECPATH is
# injected for exactly this.
sys.path.insert(0, SPECPATH)  # noqa: F821 - injected by PyInstaller

ROOT = pathlib.Path(SPECPATH).parent  # noqa: F821
sys.path.insert(0, str(ROOT / "src"))

from asomedeck import __version__  # noqa: E402

# The single source of truth, same as the Windows build reaches through version_info.py:
# a bundle whose Info.plist disagrees with the status bar is a bug report that wastes
# everybody's time. tests/test_version.py is what keeps it single.
VERSION = __version__

# Named folder by folder rather than the whole of assets/, because assets/conpty is a Windows
# executable and a DLL, and a bundle has to sign everything it carries.
datas = [("../assets/icon", "assets/icon"), ("../assets/fonts", "assets/fonts")]
binaries = []
hiddenimports = ["asomedeck", "asomedeck.resources"]

for package in ("pyte", "wcwidth"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

hiddenimports += collect_submodules("asomedeck")

#: Copied verbatim from asomedeck.spec, and it must stay that way. This is not tidiness:
#: it is the packaging half of the rule tests/test_import_boundary.py pins in source, and a
#: QtWebEngine that creeps back in through a transitive hook would add 358 MB to a bundle
#: whose whole reason for using PySide6-Essentials was not carrying it.
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
    [],
    exclude_binaries=True,          # onedir: the binaries go to COLLECT below
    name="AsomeDeck",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    # argv_emulation turns files dropped on the Dock icon into argv. AsomeDeck takes no
    # file arguments, and the hook it installs interferes with the event loop, so: off.
    argv_emulation=False,
    # None means "this machine's architecture". The PySide6 wheel is universal2, but not
    # every wheel in the tree need be, and a build that silently drops to one arch is
    # worse than one that was only ever asked for one.
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AsomeDeck",
)

#: The bundle carries its version in its own name -- `AsomeDeck-0.7.0.app` -- while
#: everything inside it stays `AsomeDeck`: the executable, CFBundleName, and the name the
#: Dock and the menu bar show. A `.app` directory's name is not part of what codesign
#: seals, so this is a label on the outside and nothing more.
#:
#: The point is that two builds can sit in `dist/` at once. Overwriting the previous one
#: means the only way to answer "is this the build with the fix" is to launch it and read
#: the status bar, and a `--noconfirm` build that half-replaced a bundle you were still
#: running is worse than that.
app = BUNDLE(
    coll,
    name=f"AsomeDeck-{VERSION}.app",
    icon="../assets/icon/asomedeck.icns",
    bundle_identifier="com.asomedeck.AsomeDeck",
    version=VERSION,
    info_plist={
        "CFBundleName": "AsomeDeck",
        "CFBundleDisplayName": "AsomeDeck",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        # Without this the app renders at 1x and is upscaled -- blurry text on every Mac
        # made in the last decade, which for a terminal is the whole product.
        "NSHighResolutionCapable": True,
        # The floor comes from the PySide6-Essentials wheel, which is
        # macosx_13_0_universal2. Claiming lower would install and then fail to launch.
        "LSMinimumSystemVersion": "13.0",
        "LSApplicationCategoryType": "public.app-category.developer-tools",
        # AsomeDeck runs real shells, and a shell inherits the *app's* TCC identity -- so
        # the first `ls ~/Documents` in a pane triggers macOS's consent prompt under this
        # app's name. These strings are what that prompt says. Without them the prompt is
        # blank, or on some releases the access is denied outright with no dialog at all,
        # which reads as the shell being broken.
        "NSDesktopFolderUsageDescription":
            "AsomeDeck runs your shells, which may read files on your Desktop.",
        "NSDocumentsFolderUsageDescription":
            "AsomeDeck runs your shells, which may read files in your Documents folder.",
        "NSDownloadsFolderUsageDescription":
            "AsomeDeck runs your shells, which may read files in your Downloads folder.",
    },
)
