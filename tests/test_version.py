"""One version, in one place.

A duplicated version does not fail loudly. It drifts, and the way anybody finds out is a
bug report quoting a number that was never released -- so this is pinned rather than
trusted.

``asomedeck.__version__`` is the source. ``pyproject.toml`` declares ``dynamic = ["version"]``
and reads that attribute, so the packaging metadata cannot disagree by construction; these
check that the declaration is still wired that way, and that everything which shows a
version to a human is showing the same one.
"""

from __future__ import annotations

import pathlib
import re

import pytest

import asomedeck


def test_the_version_is_a_release_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+", asomedeck.__version__), asomedeck.__version__


def test_pyproject_does_not_keep_its_own_copy():
    """The moment it does, the two are free to disagree."""
    text = (pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(
        encoding="utf-8")
    assert 'dynamic = ["version"]' in text
    assert 'version = {attr = "asomedeck.__version__"}' in text
    assert not re.search(r'^version\s*=\s*"', text, re.MULTILINE), (
        "pyproject declares a literal version again")


def test_the_installed_distribution_agrees():
    metadata = pytest.importorskip("importlib.metadata")
    try:
        installed = metadata.version("asomedeck")
    except metadata.PackageNotFoundError:  # pragma: no cover - not installed
        pytest.skip("asomedeck is not installed in this environment")
    assert installed == asomedeck.__version__


def test_the_status_bar_shows_the_same_version(app):
    """The one place a user reads it without opening a log."""
    from asomedeck.ui import theme as th
    from asomedeck.ui.chrome import StatusBar

    bar = StatusBar(th.Theme("dark"))
    try:
        assert bar.version.text() == f"v{asomedeck.__version__}"
    finally:
        bar.close()
