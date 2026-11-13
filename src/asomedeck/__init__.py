"""AsomeDeck: a desktop shell that runs your terminals in one window.

The version lives here and nowhere else. ``pyproject.toml`` reads it from this attribute
rather than declaring its own, so the two cannot disagree -- and a test asserts they still
match, because the failure mode of a duplicated version is that nobody notices until a bug
report cites one that was never released.

A plain string rather than ``importlib.metadata.version("asomedeck")``: that reads the
installed distribution's metadata, which exists in a source checkout and does not in a
PyInstaller bundle. The version would have been right everywhere except in the build
anybody actually ships.
"""

from __future__ import annotations

__version__ = "0.10.0"
