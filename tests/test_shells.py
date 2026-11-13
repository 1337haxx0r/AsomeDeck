"""Which shell a pane gets, and what happens when it is not installed.

This whole module exists because of one constant. ``shells.DEFAULT = GIT_BASH`` made Git
a hard requirement that nothing declared: `available()` had detected the truth since the
first commit, and every caller picked the constant instead. On a machine without Git the
app started, painted its chrome, and then every pane raised ``FileNotFoundError`` inside
the pty, showed one line of red and closed itself -- with no route out, because the
sidebar's "change" control was a QLabel with nothing connected to it.

The tests below are about the shape of that bug rather than the line that caused it: a
detector nobody consults, and a fallback that assumes an optional dependency is present.
"""

from __future__ import annotations

import pytest

from asomedeck import shells
from asomedeck.platform_ import IS_WINDOWS


@pytest.fixture
def no_git(monkeypatch):
    """A machine where the *preferred* shell was never installed.

    Git Bash on Windows, which is where this bug was found and what the name still says.
    On macOS the same shape is a ``$SHELL`` naming something that is not there -- a
    Homebrew fish recorded in the login shell and then uninstalled -- so the fixture builds
    that instead. Either way ``ALL[0]`` is absent and the rest of the platform's shells are
    real, which is the only property the tests below actually depend on.

    ``GIT_ROOT`` is read at import, so the spec is rebuilt rather than the env patched --
    setting ASOMEDECK_GIT_ROOT here would change nothing about the module already loaded.
    """
    if IS_WINDOWS:
        missing = shells.ShellSpec(
            shell_id="git-bash", label="Git Bash",
            argv=(r"C:\NoSuchGit\usr\bin\bash.exe", "--login", "-i"),
        )
        rest = (shells.PWSH, shells.POWERSHELL, shells.CMD)
        monkeypatch.setattr(shells, "GIT_BASH", missing)
    else:
        missing = shells.ShellSpec(
            shell_id="login-shell", label="fish",
            argv=("/opt/homebrew/bin/fish", "-l"),
        )
        rest = (shells.ZSH, shells.BASH, shells.SH)
    monkeypatch.setattr(shells, "ALL", (missing, *rest))
    # Two tables hold these specs, and a fixture that patches one of them is telling half
    # a lie. ``ALL`` is the preference order that ``default()`` reads; ``_KNOWN`` is the
    # name lookup that ``by_id`` reads, and ``resolve`` goes through ``by_id``. Patching
    # the ``GIT_BASH`` *name* does not reach it either -- ``_KNOWN`` captured the object
    # at import. Leave it alone and the real spec stays reachable by name, so on a machine
    # that has the shell installed the substitution these tests are about never runs, and
    # the failure reads as a bug in ``resolve`` rather than as this.
    others = tuple(s for s in shells._KNOWN if s.shell_id != missing.shell_id)
    monkeypatch.setattr(shells, "_KNOWN", (missing, *others))
    return missing


@pytest.mark.skipif(not IS_WINDOWS, reason="Git Bash is the Windows preference")
def test_default_prefers_git_bash_when_it_is_there():
    """The preference did not change; only its assumption that it always holds."""
    if not shells.GIT_BASH.available():
        pytest.skip("Git is not installed on this machine")
    assert shells.default().shell_id == "git-bash"


def test_default_is_a_shell_that_actually_exists(no_git):
    """The bug itself: the default was picked without asking whether it was installed."""
    assert shells.default().shell_id != no_git.shell_id
    assert shells.default().available()


def test_default_is_always_one_of_the_available_ones(no_git):
    """No route by which the default can be a shell `available()` excluded."""
    assert shells.default().shell_id in {s.shell_id for s in shells.available()}


def test_default_never_raises_even_with_nothing_installed(monkeypatch):
    """``FLOOR`` is the answer -- cmd on Windows, /bin/sh on macOS. A terminal app with no
    shell to name has nothing to offer, and an exception here lands during window
    construction where it reads as a crash."""
    monkeypatch.setattr(shells, "available", lambda: [])
    assert shells.default().shell_id == shells.FLOOR.shell_id


def test_by_id_answers_what_a_pane_is_running_not_what_it_should_be(no_git):
    """`by_id` labels; it must not substitute, or a pane reports a shell it never ran."""
    assert shells.by_id("git-bash").shell_id == "git-bash"


def test_by_id_still_falls_back_for_an_id_that_is_not_a_shell():
    assert shells.by_id("no-such-shell").shell_id in {s.shell_id for s in shells.available()}


def test_resolve_substitutes_a_shell_the_machine_no_longer_has(no_git):
    """A layout saved when Git was installed, restored after it was removed."""
    spec = shells.resolve("git-bash")
    assert spec.shell_id != "git-bash"
    assert spec.available()


def test_resolve_leaves_an_installed_shell_alone():
    """A shell that is present is launched as asked, with no substitution."""
    present = next(iter(shells.available()))
    assert shells.resolve(present.shell_id).shell_id == present.shell_id


def test_resolve_handles_the_empty_id_a_fresh_pane_carries(no_git):
    """`Pane.launcher_id` defaults to "", and that is the common path, not an edge."""
    assert shells.resolve("").available()
