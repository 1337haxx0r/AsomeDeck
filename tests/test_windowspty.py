"""The Windows pty, against real shells on real pseudo-consoles.

Real processes because nearly everything worth testing here is a behaviour of the host rather
than of this code: that a stream does not end when its shell does, that closing a console
takes the tree with it, that a process with no console stays that way. A fake of any of those
would only pin what this file's author believed.

Each reader replies to the host's opening question, the way ``Emulator`` does in the app. The
host waits about three seconds for that answer before drawing anything, so a test that did not
reply would pass slowly and tell us nothing about how a pane behaves.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from asomedeck import platform_, resources
from asomedeck.term import windowspty

WINDOWS_ONLY = pytest.mark.skipif(not platform_.IS_WINDOWS, reason="Windows pseudo-console")
ASK = "\x1b[c"
ANSWER = "\x1b[?1;0c"
#: What PtySession.close will wait for a reader before giving up. Deleting a QThread that is
#: still running is a Qt fatal, so ending within this is a contract and not a nicety.
JOIN_LIMIT_S = 2.0


class Reader:
    """Drain a pty on a thread, as ptysession._Reader does, and keep what arrives."""

    def __init__(self, pty) -> None:
        self.pty = pty
        self.text = ""
        self.ended_at: float | None = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while True:
            try:
                data = self.pty.read(4096)
            except (EOFError, OSError):
                self.ended_at = time.perf_counter()
                return
            if ASK in data:
                self.pty.write(ANSWER)
            self.text += data

    def wait_for(self, needle: str, timeout: float = 8.0) -> bool:
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            if needle in self.flat:
                return True
            time.sleep(0.01)
        return needle in self.flat

    @property
    def flat(self) -> str:
        # A line can wrap, or the host can reposition rather than print a run of spaces.
        return self.text.replace("\r", "").replace("\n", "")

    def wait_for_end(self, timeout: float = 8.0) -> bool:
        self.thread.join(timeout)
        return not self.thread.is_alive()


def spawn(argv, **kwargs):
    kwargs.setdefault("dimensions", (24, 80))
    pty = windowspty.WindowsPty.spawn(argv, **kwargs)
    return pty, Reader(pty)


# --- the parts that are only arithmetic --------------------------------------------------


def test_an_environment_block_is_sorted_without_regard_to_case_and_doubly_terminated():
    block = windowspty._environment_block({"b": "2", "A": "1", "C": "3"})
    assert block == "A=1\0b=2\0C=3\0\0"


def test_an_empty_environment_is_still_a_valid_block():
    assert windowspty._environment_block({}) == "\0"


@WINDOWS_ONLY
def test_a_program_is_resolved_against_the_path_the_child_will_have(tmp_path):
    program = tmp_path / "only-here.exe"
    program.write_bytes(b"")
    line = windowspty._command_line(["only-here", "two words"], {"PATH": str(tmp_path)})
    assert str(program).lower() in line.lower()
    assert line.endswith('"two words"')


def test_a_shell_that_is_not_installed_fails_before_anything_is_opened():
    with pytest.raises(FileNotFoundError):
        windowspty._command_line(["no-such-program-anywhere"], {"PATH": ""})


def test_a_character_split_across_two_reads_comes_out_whole(monkeypatch):
    # Built without __init__, because this is about the decoder and not about a process.
    pty = object.__new__(windowspty.WindowsPty)
    pty._output = 1
    pty._draining = False
    import codecs
    pty._decoder = codecs.getincrementaldecoder("utf-8")("replace")
    pieces = iter(["─".encode()[:1], "─".encode()[1:]])
    monkeypatch.setattr(windowspty.winapi, "read_handle",
                        lambda _handle, _size: next(pieces), raising=False)
    assert pty.read(1) == ""
    assert pty.read(1) == "─"


# --- the host ----------------------------------------------------------------------------


def test_the_bundled_host_is_where_the_build_expects_it():
    # Not a Windows-only check: a checkout that lost these builds an exe that quietly runs
    # the system host, and that should fail here rather than in somebody's vim.
    folder = resources.conpty_dir()
    assert (folder / "conpty.dll").is_file()
    assert (folder / "OpenConsole.exe").is_file()


@WINDOWS_ONLY
def test_the_bundled_host_is_the_one_in_use():
    pty, reader = spawn(["cmd.exe", "/c", "echo", "up"])
    assert windowspty.host() == "bundled"
    assert reader.wait_for("up")
    pty.terminate()
    assert reader.wait_for_end()


def _in_a_child(body: str, tmp_path: pathlib.Path, *, windowed: bool = False) -> list[str]:
    """Run ``body`` in a fresh interpreter and return the lines it wrote to ``report``.

    Fresh because the things under test are per-process: which host is loaded, and whether
    this process has a console -- and pytest's always does.
    """
    report = tmp_path / "report.txt"
    script = tmp_path / "child.py"
    script.write_text(textwrap.dedent(f"""\
        import ctypes, sys, threading, time
        from asomedeck import resources
        from asomedeck.term import windowspty
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetConsoleWindow.restype = ctypes.c_void_p
        report = open({str(report)!r}, "w", encoding="utf-8")
        def note(line):
            report.write(str(line) + "\\n")
            report.flush()
        def shell_answers(pty):
            seen = ""
            end = time.perf_counter() + 8
            def drain():
                nonlocal seen
                try:
                    while True:
                        data = pty.read(4096)
                        if "\\x1b[c" in data:
                            pty.write("\\x1b[?1;0c")
                        seen += data
                except (EOFError, OSError):
                    pass
            threading.Thread(target=drain, daemon=True).start()
            time.sleep(0.3)
            pty.write("echo child-marker\\r\\n")
            while time.perf_counter() < end:
                if "child-marker" in seen.replace("\\r", "").replace("\\n", ""):
                    return True
                time.sleep(0.02)
            return False
        """) + textwrap.dedent(body), encoding="utf-8")
    executable = pathlib.Path(sys.executable)
    if windowed:
        executable = executable.with_name("pythonw.exe")
        if not executable.is_file():
            pytest.skip("no pythonw.exe beside this interpreter")
    env = dict(os.environ)
    src = str(resources.root() / "src")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (src, env.get("PYTHONPATH")) if p)
    # DETACHED_PROCESS: no console at all, as a double-click gives a windowed exe.
    subprocess.run([str(executable), str(script)], env=env, timeout=60, check=True,
                   creationflags=subprocess.DETACHED_PROCESS if windowed else 0,
                   stdin=subprocess.DEVNULL, capture_output=not windowed)
    return report.read_text(encoding="utf-8").splitlines()


@WINDOWS_ONLY
def test_spawning_a_shell_does_not_give_this_process_a_console(tmp_path):
    """The bug this module exists to end.

    pywinpty allocated a console when it found none, and a console is a window, so the first
    shell of every launch of the windowed exe put one on the desktop. That cannot be seen from
    a process that already has a console, which is what every terminal and this test runner
    have; hence a process without one.
    """
    lines = _in_a_child("""
        note(f"before={kernel32.GetConsoleWindow()}")
        pty = windowspty.WindowsPty.spawn(["cmd.exe"], dimensions=(24, 80))
        note(f"after={kernel32.GetConsoleWindow()}")
        note(f"answers={shell_answers(pty)}")
        pty.terminate()
    """, tmp_path, windowed=True)
    assert lines == ["before=None", "after=None", "answers=True"]


@WINDOWS_ONLY
def test_without_the_bundled_host_a_shell_still_runs_on_the_system_one(tmp_path, monkeypatch):
    lines = _in_a_child("""
        import pathlib
        resources.conpty_dir = lambda: pathlib.Path("no-such-folder")
        pty = windowspty.WindowsPty.spawn(["cmd.exe"], dimensions=(24, 80))
        note(windowspty.host())
        note(f"answers={shell_answers(pty)}")
        pty.terminate()
    """, tmp_path)
    assert lines == ["system", "answers=True"]


# --- a shell's life ----------------------------------------------------------------------


@WINDOWS_ONLY
def test_output_arrives_and_the_stream_ends_when_the_shell_does():
    # The stream ending is the surprising half. ConPTY holds the pipe open past the shell's
    # exit, so without the watcher this read never returns and the pane never hears.
    pty, reader = spawn(["cmd.exe", "/c", "echo", "hello-from-cmd"])
    assert reader.wait_for("hello-from-cmd")
    assert reader.wait_for_end()
    assert not pty.isalive()


@WINDOWS_ONLY
def test_the_exit_code_is_the_shells():
    pty, reader = spawn(["cmd.exe", "/c", "exit 7"])
    assert reader.wait_for_end()
    assert pty.exitstatus == 7


@WINDOWS_ONLY
def test_the_working_directory_and_environment_reach_the_child(tmp_path):
    env = {**os.environ, "ASOMEDECK_PROBE": "value-from-parent"}
    pty, reader = spawn(["cmd.exe", "/c", "cd & echo %ASOMEDECK_PROBE%"],
                        cwd=str(tmp_path), env=env)
    assert reader.wait_for("value-from-parent")
    assert tmp_path.name.lower() in reader.flat.lower()
    assert reader.wait_for_end()


@WINDOWS_ONLY
def test_a_directory_that_is_gone_fails_the_spawn_rather_than_hanging(tmp_path):
    with pytest.raises(OSError):
        windowspty.WindowsPty.spawn(["cmd.exe"], cwd=str(tmp_path / "missing"))


@WINDOWS_ONLY
def test_keys_reach_the_shell():
    pty, reader = spawn(["cmd.exe"])
    assert reader.wait_for(">")
    pty.write("echo typed-into-cmd\r\n")
    assert reader.wait_for("typed-into-cmd")
    pty.terminate()
    assert reader.wait_for_end()


@WINDOWS_ONLY
def test_the_shell_is_told_its_size_at_spawn_and_when_it_changes():
    pty, reader = spawn(["cmd.exe"], dimensions=(30, 100))
    assert reader.wait_for(">")
    pty.write("mode con\r\n")
    assert reader.wait_for("100")
    before = len(reader.text)
    pty.setwinsize(40, 123)
    time.sleep(0.2)
    pty.write("mode con\r\n")
    end = time.perf_counter() + 8
    while time.perf_counter() < end and "123" not in reader.text[before:]:
        time.sleep(0.02)
    assert "123" in reader.text[before:]
    pty.terminate()
    assert reader.wait_for_end()


# --- closing -----------------------------------------------------------------------------


@WINDOWS_ONLY
def test_closing_a_busy_pane_ends_the_reader_inside_the_join_limit():
    pty, reader = spawn(["cmd.exe", "/c", "ping -n 60 127.0.0.1"])
    assert reader.wait_for("127.0.0.1")
    began = time.perf_counter()
    pty.terminate(force=True)
    assert reader.wait_for_end(JOIN_LIMIT_S)
    assert reader.ended_at - began < JOIN_LIMIT_S
    assert not pty.isalive()


@WINDOWS_ONLY
def test_closing_a_pane_that_is_still_starting_ends_the_reader_inside_the_join_limit():
    """Quitting just after launch, before anything has answered the host's opening question.

    A reader here that does not reply is the point: the host is at its most stubborn, sitting
    in the wait that lasts about three seconds, and that is longer than ``PtySession.close``
    waits. Running out that clock would delete a live QThread.
    """
    pty = windowspty.WindowsPty.spawn(["cmd.exe"], dimensions=(24, 80))
    done: list[float] = []

    def drain() -> None:
        try:
            while True:
                pty.read(4096)
        except (EOFError, OSError):
            done.append(time.perf_counter())

    thread = threading.Thread(target=drain, daemon=True)
    thread.start()
    began = time.perf_counter()
    pty.terminate(force=True)
    thread.join(JOIN_LIMIT_S + 3)
    assert done, "the reader never heard that the console had gone"
    assert done[0] - began < JOIN_LIMIT_S


@WINDOWS_ONLY
def test_terminate_returns_without_waiting_for_the_console():
    # It runs on the UI thread, once per pane, on the way out of the app.
    pty, reader = spawn(["cmd.exe"])
    assert reader.wait_for(">")
    began = time.perf_counter()
    pty.terminate(force=True)
    assert time.perf_counter() - began < 0.05
    assert reader.wait_for_end()


@WINDOWS_ONLY
def test_terminating_twice_and_after_exit_are_both_harmless():
    pty, reader = spawn(["cmd.exe", "/c", "exit 0"])
    assert reader.wait_for_end()
    pty.terminate()
    pty.terminate(force=True)


@WINDOWS_ONLY
def test_a_write_or_resize_after_the_shell_has_gone_raises_what_the_session_catches():
    # Closing a pane writes a resize on the way out, so this is the normal path and not an
    # edge: anything but these two would escape into a Qt slot and take the app down.
    from asomedeck.term.ptysession import PtySession

    pty, reader = spawn(["cmd.exe", "/c", "exit 0"])
    assert reader.wait_for_end()
    pty._watcher.join(5)
    with pytest.raises(PtySession.GONE):
        pty.write("too late\r\n")
    with pytest.raises(PtySession.GONE):
        pty.setwinsize(10, 10)
