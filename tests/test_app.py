"""Starting up in a process that has no standard streams.

A windowed PyInstaller build launched from Explorer has none: ``sys.stderr`` is ``None``,
and ``faulthandler.enable()`` defaults to it and raises ``RuntimeError: sys.stderr is
None``. The built exe died on that line before its window appeared.

It passed every check before shipping because each one ran it from a shell, and a shell
hands its own streams to the child. "I ran the exe and it worked" said nothing about the
way anybody actually launches it.

Two things here are shaped by the harness rather than by the code, and both were found the
hard way -- each one made a test pass while proving nothing:

* The absent stream is **passed in**, not patched onto ``sys``. pytest's capture reinstalls
  its own object over such a patch mid-test, so the test ends up exercising the branch it
  was written to avoid.
* The root logger is emptied **at the moment of the call**, not in fixture setup. pytest's
  logging plugin installs its handler after fixtures run, and ``logging.basicConfig`` does
  nothing at all when the root already has handlers -- so every assertion about which
  handlers exist would hold without the code under test having run.
"""

from __future__ import annotations

import faulthandler
import io
import logging

import pytest

from asomedeck import app as app_module


@pytest.fixture
def configure(monkeypatch, tmp_path):
    """Call ``_setup_logging`` with a given stream, and put the process back afterwards.

    Returns the directory the log was pointed at.
    """
    monkeypatch.setattr(app_module, "log_path", lambda: tmp_path / "asomedeck.log")
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level

    def run(stream):
        root.handlers[:] = []
        app_module._setup_logging(stream=stream)
        return tmp_path

    try:
        yield run
    finally:
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        if app_module._fault_file is not None:
            app_module._fault_file.close()
            app_module._fault_file = None
        # Leave the process as we found it: pytest arms its own faulthandler and expects
        # to still own it after we are done.
        faulthandler.disable()
        faulthandler.enable()


def test_setup_logging_survives_having_no_stderr(configure):
    """The crash that shipped. Nothing here may raise."""
    where = configure(None)

    logging.getLogger("asomedeck.test").info("hello from a windowed build")
    assert (where / "asomedeck.log").is_file()
    assert "hello from a windowed build" in (where / "asomedeck.log").read_text(
        encoding="utf-8")


def test_no_stream_handler_is_installed_without_a_stream(configure):
    """A StreamHandler over a None stream raises the first time anything logs."""
    configure(None)

    assert [h for h in logging.getLogger().handlers
            if type(h) is logging.StreamHandler] == []
    assert [h for h in logging.getLogger().handlers
            if isinstance(h, logging.FileHandler)] != []


def test_the_fault_dump_goes_somewhere_readable(configure):
    """A dump aimed at a stderr that does not exist is written to nowhere.

    The handle is held open deliberately: faulthandler keeps the file *descriptor*, so
    letting the object be collected would point a crash dump at a closed one.
    """
    where = configure(None)

    assert faulthandler.is_enabled()
    assert app_module._fault_file is not None
    assert not app_module._fault_file.closed
    assert (where / "asomedeck-fault.log").is_file()


def test_a_real_stream_still_gets_a_console_handler(configure):
    """The ordinary path, so the seam above cannot quietly disable console logging."""
    stream = io.StringIO()
    configure(stream)

    assert [h for h in logging.getLogger().handlers
            if type(h) is logging.StreamHandler] != []
    logging.getLogger("asomedeck.test").warning("to the console")
    assert "to the console" in stream.getvalue()
