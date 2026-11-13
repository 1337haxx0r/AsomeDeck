"""The settings file, and every way it can be wrong.

A config is read on the launch path, before there is a window to show an error in, so the
only acceptable behaviour for a bad one is to carry on with defaults and say so in the
log. Each test here is a way a hand-edited file goes wrong -- which is not hypothetical:
hand-editing is the entire reason this is a file rather than more `app_meta` rows.

No Qt. The store is Qt-free so it can be read before the QApplication exists, and this
suite is what keeps it that way.
"""

from __future__ import annotations

import json

from asomedeck import config as cfg
from asomedeck.commands import by_id
from asomedeck.ui import theme as th


def test_the_modes_match_the_themes_that_exist():
    """config.py names the modes rather than importing them, to stay clear of Qt. This is
    the seam that keeps the copy honest: a third theme added to TOKENS and not here would
    be a mode the config layer silently refuses to load."""
    assert set(cfg.MODES) == set(th.TOKENS)
    assert cfg.DEFAULT_MODE in cfg.MODES


def test_a_first_run_has_no_file_and_no_complaint(tmp_path):
    config = cfg.load(tmp_path / "config.json")
    assert config.theme == cfg.DEFAULT_MODE
    assert config.keys == {}


def test_it_round_trips(tmp_path):
    path = tmp_path / "config.json"
    config = cfg.load(path)
    config.theme = "light"
    config.keys["pane.close"] = "Ctrl+Alt+W"
    config.save()

    assert cfg.load(path).theme == "light"
    assert cfg.load(path).keys == {"pane.close": "Ctrl+Alt+W"}


def test_the_file_is_meant_to_be_read(tmp_path):
    """Indented, newline-terminated JSON. The argument for a file over database rows is
    that a person can open it, and minified JSON on one line is not that."""
    path = tmp_path / "config.json"
    cfg.Config(theme="light", path=path).save()
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n") and "\n  " in text
    assert json.loads(text)["theme"] == "light"


def test_an_unbound_command_falls_back_to_the_registry(tmp_path):
    config = cfg.Config(keys={"pane.close": "Ctrl+Alt+W"})
    assert config.shortcut(by_id("pane.close")) == "Ctrl+Alt+W"
    assert config.shortcut(by_id("session.new")) == by_id("session.new").shortcut


def test_an_empty_chord_unbinds(tmp_path):
    """Distinct from "not mentioned": a user who wants Ctrl+Shift+W back for the shell
    needs a way to say so, and deleting the line would only restore the default."""
    assert cfg.Config(keys={"pane.close": ""}).shortcut(by_id("pane.close")) == ""


def test_a_pathless_config_writes_nothing(tmp_path, monkeypatch):
    """What every test and every --smoke run gets. The equivalent of Database(":memory:"):
    an unattended run must not rewrite the preferences of whoever's machine it is on."""
    monkeypatch.setattr(cfg, "default_path", lambda: tmp_path / "config.json")
    cfg.Config(theme="light").save()
    assert not (tmp_path / "config.json").exists()


def test_a_corrupt_file_is_kept_rather_than_overwritten(tmp_path):
    """The next save would otherwise destroy the half-finished edit that broke it, which
    is exactly what the person wants to look at."""
    path = tmp_path / "config.json"
    path.write_text('{"theme": "light",,}', encoding="utf-8")

    config = cfg.load(path)

    assert config.theme == cfg.DEFAULT_MODE
    assert (tmp_path / "config.json.bad").read_text(encoding="utf-8") == '{"theme": "light",,}'
    config.save()   # and the path is still live afterwards
    assert cfg.load(path).theme == cfg.DEFAULT_MODE


def test_a_file_that_is_not_an_object_is_corrupt_too(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert cfg.load(path).theme == cfg.DEFAULT_MODE
    assert (tmp_path / "config.json.bad").is_file()


def test_a_theme_that_does_not_exist_does_not_reach_the_theme(tmp_path):
    """th.Theme indexes TOKENS directly, so an invented mode would be a KeyError on the
    launch path -- an app that will not start because of one mistyped word."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"theme": "solarized"}), encoding="utf-8")
    config = cfg.load(path)
    assert config.theme == cfg.DEFAULT_MODE
    th.Theme(config.theme)   # would raise if the value got through


def test_a_binding_for_a_command_that_does_not_exist_is_dropped(tmp_path):
    """A typo, or a command from a newer build. Neither has anything to bind to, and the
    registry raises on an unknown id by design."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"keys": {"pane.clsoe": "Ctrl+Alt+W", "pane.close": "F8"}}),
                    encoding="utf-8")
    assert cfg.load(path).keys == {"pane.close": "F8"}


def test_a_chord_that_is_not_a_string_is_dropped(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"keys": {"pane.close": 8}}), encoding="utf-8")
    assert cfg.load(path).keys == {}


def test_keys_that_are_not_an_object_are_ignored(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"theme": "light", "keys": ["Ctrl+W"]}), encoding="utf-8")
    config = cfg.load(path)
    assert (config.theme, config.keys) == ("light", {})


def test_an_unwritable_path_does_not_take_the_app_down(tmp_path):
    """A theme toggle is not worth a traceback. A directory where the file should be is
    the cheapest way to make the write fail for real."""
    path = tmp_path / "config.json"
    path.mkdir()
    cfg.Config(theme="light", path=path).save()   # logs, does not raise
