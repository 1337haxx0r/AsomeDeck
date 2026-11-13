r"""The environment a process we start inherits, minus the parts that are ours.

A frozen app is a Python process carrying its own Qt, and both PyInstaller and PySide6
arrange that by writing into ``os.environ``. Every one of those writes is right for us and
wrong for anything we spawn. A pane inherits the lot, so the terminal you opened in order
to run *your* Qt program hands it *our* plugin directory, and it dies before it draws
anything::

    qt.qpa.plugin: Could not load the Qt platform plugin "cocoa" in "" even though it was
    found. This application failed to start because no Qt platform plugin could be
    initialized.

Not hypothetical and not a macOS problem. The bundle that shipped as 0.7.1 exported
``QT_PLUGIN_PATH=/Applications/AsomeDeck-0.7.1.app/Contents/Frameworks/PySide6/Qt/plugins``
into every pane, which was enough to stop this repository's own test suite from running in
one of this app's own terminals. The Windows form is quieter and worse: PyInstaller
prepends the bundle directory to ``PATH`` there (``pyi_rth_pyside6.py``), and PySide6
prepends its package directory when running from source (``PySide6/__init__.py``), so a
pane resolves executables in an order no console on the machine agrees with.

**Why this cannot be a snapshot.** The obvious fix -- remember ``os.environ`` before Qt is
imported, spawn from that -- does not work, because PyInstaller's runtime hooks run before
the first line of our code. ``QT_PLUGIN_PATH`` is already set by the time anything here
could look, and whatever the user had in it is already overwritten and unrecoverable. So
the question has to be asked of the *value*: does this path point inside our own
installation? That is also the only rule that leaves a value the user set alone, which
matters because these are ordinary variables that somebody may be using for their own
reasons.

Deliberately Qt-free, and PySide6 is located through ``sys.modules`` rather than imported.
The variables cleaned up here exist only because something else imported PySide6, so "is
it loaded" is the same question as "is there anything to clean", and this module stays
importable where PySide6 is not installed at all -- which the test suite relies on.

One variable is deliberately left alone. PySide6 puts ``PYSIDE6_OPTION_PYTHON_ENUM`` in
the environment, but it *reads* it first and writes back what it resolved, so a value
found there may be one the user set before launching us. Stripping it would flip a child
back to the default, and unlike the paths above it misdirects nothing: a child that
imports PySide6 would arrive at the same value on its own.
"""

from __future__ import annotations

import os
import pathlib
import sys
from collections.abc import Mapping

#: Search paths that Qt or PyInstaller point at our own copy of things. Filtered entry by
#: entry rather than removed by name: ``PATH`` obviously has to survive the treatment, and
#: any of the others may hold something the user put there.
PATH_VARS = (
    "QT_PLUGIN_PATH",
    "QML2_IMPORT_PATH",
    "QML_IMPORT_PATH",
    "PATH",
    "DYLD_LIBRARY_PATH",
    "LD_LIBRARY_PATH",
)

#: The bootloader's own bookkeeping, removed by name because nothing else ever writes it.
#: The prefix rather than a list of four, because the list is a version detail: this
#: bundle's bootloader carries ``_PYI_APPLICATION_HOME_DIR``, ``_PYI_ARCHIVE_FILE``,
#: ``_PYI_PARENT_PROCESS_LEVEL`` and ``_PYI_SPLASH_IPC``, and the next one will carry
#: whatever it carries. A child that is itself a frozen app reads these as its own and
#: looks for an archive that is ours.
PYI_PREFIX = "_PYI_"

#: What the same bookkeeping was called before the prefix existed. Named explicitly
#: because a build made with an older PyInstaller than pyproject.toml asks for is a
#: mistake that should still produce clean panes.
PYI_LEGACY = frozenset({"_MEIPASS2"})


def _key(path: str) -> str:
    """A directory in the one spelling containment can be tested with.

    Absolute, case-folded where the platform folds case, and always ending in a separator
    so that ``/opt/app`` does not appear to contain ``/opt/applesauce``.

    Lexical on purpose -- no ``realpath``, no ``stat``. This runs over every ``PATH`` entry
    every time a pane launches, and the one thing it must not do is block on an entry that
    names a stale network mount. The containment it needs holds lexically anyway: the
    Resources and Frameworks halves of a ``.app`` are symlinked to each other, but both are
    written down as paths inside the bundle.
    """
    absolute = os.path.normcase(os.path.abspath(path))
    return absolute if absolute.endswith(os.sep) else absolute + os.sep


def _app_bundle(meipass: pathlib.Path) -> pathlib.Path | None:
    """The ``.app`` around ``sys._MEIPASS``, when this is a macOS bundle.

    Needed because a bundle is not one directory. ``_MEIPASS`` is ``Contents/Frameworks``,
    and the QML import path PyInstaller writes there names ``Contents/Resources`` as well:
    codesign requires the qml tree to be split across the two, binaries in one and data in
    the other, symlinked together to keep the illusion of a single directory. Only the
    ``.app`` itself contains both halves.
    """
    for candidate in (meipass, *meipass.parents):
        if candidate.suffix == ".app":
            return candidate
    return None


def our_roots() -> tuple[str, ...]:
    """The directories this application's own copy of Qt lives in.

    Empty when there is nothing to clean -- running from source without PySide6 imported,
    which is most of the test suite.
    """
    found: list[str] = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        found.append(str(meipass))
        bundle = _app_bundle(pathlib.Path(meipass))
        if bundle is not None:
            found.append(str(bundle))
    # Not ``import PySide6``: see the module docstring. Running from source this is the
    # only root there is, and on Windows it is a real one -- PySide6 prepends it to PATH.
    location = getattr(sys.modules.get("PySide6"), "__file__", None)
    if location:
        found.append(os.path.dirname(location))
    roots = set()
    for root in found:
        path = pathlib.Path(root)
        # A root of "/" or "C:\" would swallow every path on the machine. It can only come
        # from something already broken, and the harm from acting on it is unbounded.
        if not root or path.parent == path:
            continue
        roots.add(_key(root))
    return tuple(sorted(roots))


def for_child(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """``os.environ`` with what we added for our own Qt taken back out.

    A copy. ``os.environ`` itself is left exactly as it is, because this process genuinely
    does need those values -- the point is only that nothing we start does.
    """
    environment = dict(os.environ if base is None else base)
    for name in [n for n in environment if n.startswith(PYI_PREFIX) or n in PYI_LEGACY]:
        del environment[name]
    roots = our_roots()
    if not roots:
        return environment
    for name in PATH_VARS:
        value = environment.get(name)
        if not value:
            continue
        kept = [entry for entry in value.split(os.pathsep) if not _is_ours(entry, roots)]
        if kept:
            environment[name] = os.pathsep.join(kept)
        else:
            # Every entry was ours, which is the ``QT_PLUGIN_PATH`` case. Unset, not empty:
            # Qt reads an empty value as a real answer and searches nowhere, where an
            # absent one lets it fall back to the plugin path built into the child's own Qt.
            del environment[name]
    return environment


def _is_ours(entry: str, roots: tuple[str, ...]) -> bool:
    if not entry:
        # An empty PATH entry means the working directory on POSIX. Never ours, and
        # dropping it would change what a shell in a pane resolves.
        return False
    try:
        key = _key(entry)
    except (OSError, ValueError):   # embedded nulls, and whatever else abspath rejects
        return False
    return any(key.startswith(root) for root in roots)
