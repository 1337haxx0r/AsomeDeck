r"""What a pane's shell is handed, minus what the frozen app added for itself.

The bug these hold shut was found by running this very suite in one of this app's own
terminals: the 0.7.1 bundle exported its ``QT_PLUGIN_PATH`` into every pane, and pytest
died on ``Could not load the Qt platform plugin`` before collecting a single test. Nothing
in the app was wrong -- the value is correct for the app -- and that is exactly why it
needs a test rather than a fix and a memory of it.

No QApplication and no PySide6. ``env.py`` finds PySide6 through ``sys.modules`` instead
of importing it, which is what lets these run anywhere and lets them fake a frozen bundle
with nothing but ``monkeypatch``.

The paths are built with ``os.path.join`` and ``os.pathsep`` throughout rather than
written as literals, so the Windows shapes are the same tests rather than a second set
that only runs somewhere nobody looks.
"""

from __future__ import annotations

import os
import sys
import types

import pytest

from asomedeck import env


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    """A macOS bundle, in the layout PyInstaller actually produces.

    ``_MEIPASS`` is ``Contents/Frameworks`` and not the ``.app``, which is the detail the
    QML path below turns on: codesign forces the qml tree to be split, so half of what
    PyInstaller writes into the environment sits outside ``_MEIPASS`` entirely.
    """
    bundle = tmp_path / "AsomeDeck-9.9.9.app"
    frameworks = bundle / "Contents" / "Frameworks"
    frameworks.mkdir(parents=True)
    (bundle / "Contents" / "Resources").mkdir(parents=True)
    monkeypatch.setattr(sys, "_MEIPASS", str(frameworks), raising=False)
    monkeypatch.delitem(sys.modules, "PySide6", raising=False)
    return types.SimpleNamespace(app=bundle, meipass=frameworks)


def _env(monkeypatch, **values):
    """A clean ``os.environ`` holding only what a test names."""
    for name in (*env.PATH_VARS, "PYSIDE6_OPTION_PYTHON_ENUM"):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_the_bundles_plugin_path_does_not_reach_the_shell(frozen, monkeypatch):
    """The reported bug, in one assertion."""
    _env(monkeypatch, QT_PLUGIN_PATH=str(frozen.meipass / "PySide6" / "Qt" / "plugins"))

    assert "QT_PLUGIN_PATH" not in env.for_child()


def test_the_variable_is_removed_rather_than_emptied(frozen, monkeypatch):
    """Unset and empty are different answers, and only one of them is right.

    Qt reads an empty ``QT_PLUGIN_PATH`` as a real instruction -- look in nowhere -- so a
    child would fail in the same way for a new reason. Absent lets it fall back to the
    plugin directory built into its own Qt, which is the state it would have been in if
    this app had never run.
    """
    _env(monkeypatch, QT_PLUGIN_PATH=str(frozen.meipass / "PySide6" / "Qt" / "plugins"))

    child = env.for_child()

    assert child.get("QT_PLUGIN_PATH", "sentinel") == "sentinel"


def test_both_halves_of_a_split_qml_path_go(frozen, monkeypatch):
    """The ``.app`` is the root, not ``_MEIPASS``.

    PyInstaller writes two entries here, one under ``Contents/Resources`` and one under
    ``Contents/Frameworks``. A containment test rooted at ``_MEIPASS`` would keep the
    first, and a QML import path holding one half of a symlinked pair is a stranger thing
    to hand a child than the whole of it.
    """
    _env(monkeypatch, QML2_IMPORT_PATH=os.pathsep.join([
        str(frozen.app / "Contents" / "Resources" / "PySide6" / "Qt" / "qml"),
        str(frozen.meipass / "PySide6" / "Qt" / "qml"),
    ]))

    assert "QML2_IMPORT_PATH" not in env.for_child()


def test_the_users_own_plugin_path_survives(frozen, monkeypatch):
    """Ours is identified by where it points, which is the only way to leave theirs alone.

    Somebody who sets ``QT_PLUGIN_PATH`` for their own Qt build and then opens a terminal
    here is entitled to find it still set. The frozen app's value overwrote whatever was
    there before we could read it, so "remove it if it is present" would take theirs too.
    """
    theirs = os.path.join(os.sep + "opt", "qt6", "plugins")
    _env(monkeypatch, QT_PLUGIN_PATH=theirs)

    assert env.for_child()["QT_PLUGIN_PATH"] == theirs


def test_a_path_keeps_every_entry_that_is_not_ours(frozen, monkeypatch):
    """The Windows form of the same leak, and the one that must not be a plain removal.

    PyInstaller prepends the bundle directory to ``PATH`` on Windows so QtNetwork can find
    its OpenSSL DLLs. A pane inheriting that resolves executables in an order no console
    on the machine agrees with -- and a pane inheriting no ``PATH`` at all resolves
    nothing, so this is the variable that proves entries are filtered rather than names.
    """
    mine = str(frozen.meipass)
    theirs = [os.path.join(os.sep + "usr", "bin"), os.path.join(os.sep + "bin")]
    _env(monkeypatch, PATH=os.pathsep.join([mine, *theirs]))

    assert env.for_child()["PATH"].split(os.pathsep) == theirs


def test_a_neighbour_with_a_longer_name_is_not_inside_us(frozen, monkeypatch):
    """``/x/AsomeDeck-9.9.9.app`` does not contain ``/x/AsomeDeck-9.9.9.appendix``.

    A prefix test on the raw strings says it does. That is why ``_key`` puts a separator
    on the end of everything it compares.
    """
    neighbour = str(frozen.app) + "endix"
    _env(monkeypatch, PATH=neighbour)

    assert env.for_child()["PATH"] == neighbour


def test_the_bootloaders_bookkeeping_goes_by_name(frozen, monkeypatch):
    """``_PYI_*`` and the older ``_MEIPASS2``.

    Nothing but the bootloader writes these, and a child that is itself a frozen app reads
    them as its own -- an archive path pointing at our executable, and a nesting level
    saying it was started by a frozen parent when it was started by a shell.
    """
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", str(frozen.app))
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")
    monkeypatch.setenv("_PYI_SOMETHING_INVENTED_LATER", "x")
    monkeypatch.setenv("_MEIPASS2", str(frozen.meipass))

    child = env.for_child()

    assert not [name for name in child if name.startswith("_PYI_")]
    assert "_MEIPASS2" not in child


def test_pyside_option_python_enum_is_left_alone(frozen, monkeypatch):
    """The deliberate exception, and the reason is that we cannot tell ours from theirs.

    PySide6 reads this variable before it writes it back, so a value found here may be one
    the user set before launching us. It also misdirects nothing: a child that imports
    PySide6 arrives at the same value on its own.
    """
    monkeypatch.setenv("PYSIDE6_OPTION_PYTHON_ENUM", "True")

    assert env.for_child()["PYSIDE6_OPTION_PYTHON_ENUM"] == "True"


def test_running_from_source_cleans_only_what_pyside_added(tmp_path, monkeypatch):
    """Not only a frozen problem. PySide6 prepends its own package directory to ``PATH``
    on Windows whether or not anybody has frozen anything, so a developer running from
    source leaks it into panes exactly as the bundle does."""
    package = tmp_path / "site-packages" / "PySide6"
    package.mkdir(parents=True)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setitem(sys.modules, "PySide6",
                        types.SimpleNamespace(__file__=str(package / "__init__.py")))
    elsewhere = os.path.join(os.sep + "usr", "bin")
    _env(monkeypatch, PATH=os.pathsep.join([str(package), elsewhere]))

    assert env.for_child()["PATH"] == elsewhere


def test_nothing_is_touched_when_there_is_nothing_of_ours(tmp_path, monkeypatch):
    """Neither frozen nor holding PySide6: every path is somebody else's.

    This is most of the test suite's own situation, and the case where being wrong would
    be worst -- a ``PATH`` filtered against no roots at all is a ``PATH`` emptied.
    """
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.delitem(sys.modules, "PySide6", raising=False)
    path = os.pathsep.join([os.path.join(os.sep + "usr", "bin"), os.sep + "bin"])
    _env(monkeypatch, PATH=path, QT_PLUGIN_PATH=os.path.join(os.sep + "opt", "plugins"))

    child = env.for_child()

    assert env.our_roots() == ()
    assert child["PATH"] == path
    assert child["QT_PLUGIN_PATH"] == os.path.join(os.sep + "opt", "plugins")


def test_a_root_of_the_whole_filesystem_is_refused(monkeypatch):
    """It would match every path on the machine, and a pane with no ``PATH`` is not a
    terminal. It can only arrive from something already broken, and the damage from
    acting on it is unbounded where the damage from ignoring it is one leaked variable."""
    monkeypatch.setattr(sys, "_MEIPASS", os.path.abspath(os.sep), raising=False)
    monkeypatch.delitem(sys.modules, "PySide6", raising=False)

    assert env.our_roots() == ()


def test_the_apps_own_environment_is_not_disturbed(frozen, monkeypatch):
    """A copy is handed out. This process still needs every one of these values -- it is
    the one they were written for."""
    plugins = str(frozen.meipass / "PySide6" / "Qt" / "plugins")
    _env(monkeypatch, QT_PLUGIN_PATH=plugins)

    env.for_child()

    assert os.environ["QT_PLUGIN_PATH"] == plugins


def test_a_caller_may_hand_in_the_environment_to_clean(frozen):
    """``base`` exists so the rule can be asked about an environment that is not this
    process's, which is what makes every test above independent of the machine."""
    given = {"QT_PLUGIN_PATH": str(frozen.meipass), "HOME": "/somewhere"}

    assert env.for_child(given) == {"HOME": "/somewhere"}
