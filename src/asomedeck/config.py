r"""User intent, in a file a person can open.

``%APPDATA%\AsomeDeck\config.json``, beside ``state.db``, and the split between the two is
deliberate. **This file is what the user chose** -- theme, and which key runs which
command. **``app_meta`` in the database stays window state** -- sidebar mode, collapsed
panels, clean-shutdown flag. The test for which side a key belongs on is whether losing it
would annoy the user or merely reset a panel.

A file rather than more ``app_meta`` rows, for one reason that is not aesthetic: it can be
read, diffed, backed up and hand-edited before any settings dialog exists. Rebinding a
shortcut works today, in a text editor, because the registry gives commands ids and this
maps ids to chords -- which is the whole argument for commands.py landing first.

Nothing here fails a launch. A missing file is a first run, a corrupt one is set aside and
replaced, and a key naming a command that does not exist is dropped with a warning. That
is the precedent ``db.load_layout`` sets for an unreadable layout: a preference is not
worth refusing to start over.

Qt-free on purpose, like commands.py -- so ``tests/test_config.py`` runs headless, and so
the store can be read before there is a QApplication to theme.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import platform_
from .commands import COMMANDS, Command

log = logging.getLogger(__name__)

#: The modes ``ui/theme.py`` defines tokens for. Restated rather than imported: importing
#: the theme module would pull Qt into a store that is read before any window exists.
#: ``tests/test_config.py`` pins the two lists together so they cannot drift apart.
MODES: tuple[str, ...] = ("dark", "light")

DEFAULT_MODE = "dark"


def default_path() -> Path:
    return platform_.config_dir() / "config.json"


@dataclass
class Config:
    """What the user chose, and where it is written back.

    ``path=None`` is a config that remembers nothing -- ``save`` is a no-op. Tests and
    ``--smoke`` want exactly that, for the same reason they want ``Database(":memory:")``:
    an unattended run must not rewrite the preferences of the person whose machine it is
    running on.
    """

    theme: str = DEFAULT_MODE
    #: command id -> key sequence. An empty string unbinds; an absent id keeps the
    #: registry's default.
    keys: dict[str, str] = field(default_factory=dict)
    path: Path | None = None

    def shortcut(self, command: Command) -> str:
        """This command's chord: the user's, or the registry's default.

        By id, which is the point of ids. A rebind keyed on a label would break the moment
        anything was renamed, and keyed on position the moment anything was inserted.
        """
        return self.keys.get(command.id, command.shortcut)

    def save(self) -> None:
        """Write the file, or decline quietly.

        Written to a sibling and renamed over the original: ``os.replace`` is atomic on
        Windows, so a crash mid-write leaves the previous config intact rather than a
        half-written one that the next launch would report as corrupt.

        A write that fails is logged and swallowed. Somebody with a read-only ``%APPDATA%``
        should get an unremembered theme, not a traceback out of a theme toggle.
        """
        if self.path is None:
            return
        payload = {"theme": self.theme, "keys": self.keys}
        temp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(temp, self.path)
        except OSError as exc:
            log.warning("could not write config at %s: %s", self.path, exc)


def load(path: str | Path | None = None) -> Config:
    """Read the config, falling back to defaults for anything unusable."""
    target = Path(path) if path is not None else default_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Config(path=target)          # first run; save() creates it
    except OSError as exc:
        # Unreadable is not the same as unparseable: the file may be perfectly good and
        # merely locked, so it is left exactly where it is.
        log.warning("could not read config at %s: %s", target, exc)
        return Config(path=target)
    except json.JSONDecodeError as exc:
        return _set_aside(target, f"invalid JSON: {exc}")
    if not isinstance(raw, dict):
        return _set_aside(target, f"expected an object, found {type(raw).__name__}")
    return Config(theme=_mode(raw.get("theme")), keys=_keys(raw.get("keys")), path=target)


def _set_aside(target: Path, why: str) -> Config:
    """Move a corrupt config out of the way and carry on with defaults.

    Renamed rather than overwritten in place, because the next ``save`` would otherwise
    destroy whatever the user was in the middle of editing -- and a hand-edited file that
    stopped parsing is precisely the case where they want to see what they typed.
    """
    spoiled = target.with_name(target.name + ".bad")
    try:
        os.replace(target, spoiled)
        log.warning("config at %s is unusable (%s); kept as %s", target, why, spoiled.name)
    except OSError as exc:
        log.warning("config at %s is unusable (%s) and could not be moved: %s",
                    target, why, exc)
    return Config(path=target)


def _mode(value: object) -> str:
    if value in MODES:
        return str(value)
    if value is not None:
        log.warning("config theme %r is not one of %s; using %s", value, MODES, DEFAULT_MODE)
    return DEFAULT_MODE


def _keys(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        log.warning("config keys should be an object of command id -> chord; ignoring")
        return {}
    known = {command.id for command in COMMANDS}
    bindings: dict[str, str] = {}
    for command_id, chord in value.items():
        if command_id not in known:
            # A typo, or a command from a newer build. Either way there is nothing to
            # bind it to, and a silent drop would leave the user pressing a dead key.
            log.warning("config binds unknown command %r; ignoring", command_id)
        elif not isinstance(chord, str):
            log.warning("config binds %s to %r, which is not a key sequence", command_id, chord)
        else:
            bindings[command_id] = chord
    return bindings
