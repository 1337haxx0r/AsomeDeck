"""Gate: is a native Qt terminal a viable replacement for xterm.js in a web view?

The xterm.js pane works -- ``probe_xterm.py`` passes all seven of its checks. What it
costs is 336 MB across five Chromium processes to show three terminals, and a page load
before any shell can start. This asks whether painting the grid ourselves gives that back
without giving up something worse.

Checks 1-7 are ``probe_xterm.py``'s, so the two are directly comparable; check 2 becomes
"the widget paints" where it was "the page and bridge load". Checks 8-10 are new, and they
are the ones that would kill this approach:

  1. Qt initialises and a monospace cell has a sane size.
  2. The widget paints a non-empty pixmap offscreen.
  3. A shell starts on a ConPTY and its banner reaches the screen buffer.
  4. Typed input round-trips: keystroke -> pty -> shell -> VT output -> grid.
  5. Resizing tells the shell its new grid in *cells*.
  6. The theme changes on a *running* terminal.
  7. Closing takes the shell with it, leaving no orphan holding a pty.
  8. Throughput: pyte is pure Python, and this is the number that decides it.
  9. A full-screen program renders and gives the primary screen back on exit. pyte does
     not implement the alternate screen at all, so this is testing our own code.
 10. Selection lifts text back out of the grid.
 13. Right-click yields the app menu, and stands aside for a program that asked for the
     mouse -- with Shift as the override. Driven by a real program setting the mode
     through the pty, because who owns the button is decided by what is running.
 12. The scrollbar takes its range from the scrollback and moves the viewport. Both halves
     of what it needs -- Emulator.total_lines and TerminalView.scroll_offset -- were written
     long before anything called them, which is the failure this file exists to catch.
 11. The shell's own questions get answered, so a pane is usable promptly. ConPTY opens
     with `CSI c` and blocks on the reply; unanswered, every pane cost 3.6 seconds.

Run:  .venv\\Scripts\\python.exe tools\\probe_termview.py
Exit: 0 if every check passes.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QContextMenuEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollBar  # noqa: E402

from asomedeck.term.emulator import Emulator  # noqa: E402
from asomedeck.ui import theme as th  # noqa: E402
from asomedeck.ui.term_pane import TerminalPane  # noqa: E402

GIT_BASH = [r"C:\Program Files\Git\usr\bin\bash.exe", "--login", "-i"]
CWD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: pyte parses roughly 1.3 MB/s here. The budget is deliberately loose -- this is a floor
#: that catches a regression into "unusable", not a benchmark.
THROUGHPUT_FLOOR_MBS = 0.5

#: Time to a usable prompt. `bash --login -i` costs about 450 ms on this machine whoever
#: hosts it, so this is a ceiling that catches the terminal *adding* to that -- not a
#: measurement of bash. It was 3.6 s over budget while the DA1 query went unanswered.
PROMPT_CEILING_S = 2.0


def pump(app: QApplication, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.005)


def wait_for(app: QApplication, predicate, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        pump(app, 0.05)
    return False


def bash_count() -> int:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq bash.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return sum(1 for line in out.splitlines() if "bash.exe" in line)


def screen_text(pane: TerminalPane) -> str:
    emu = pane.view.emulator
    return "\n".join(emu.screen.display[i] for i in range(emu.screen.lines))


def main() -> int:
    failures: list[str] = []

    app = QApplication(sys.argv)
    th.load_fonts()
    theme = th.Theme("dark")
    th.apply(app, theme)

    before = bash_count()
    pane = TerminalPane(GIT_BASH, CWD, theme)
    pane.resize(900, 500)
    pane.show()
    pump(app, 0.3)

    cell_w, cell_h = pane.view.cell_size()
    cols, rows = pane.view.grid_size()
    print(f"[1] cell {cell_w:.2f}x{cell_h}px -> grid {cols}x{rows}")
    if cell_w < 3 or cell_h < 6 or cols < 20 or rows < 5:
        print("    FAIL: implausible cell metrics")
        return 1
    print("    PASS Qt initialised, monospace cell measured")

    print("[2] painting offscreen")
    pixmap = pane.view.grab()
    if pixmap.isNull() or pixmap.width() < 10:
        failures.append("widget produced no pixmap")
        print("    FAIL")
    else:
        print(f"    PASS painted {pixmap.width()}x{pixmap.height()} with no browser")

    print("[3] waiting for the shell banner")
    seen: list[str] = []
    pane.session.output.connect(seen.append)
    # A prompt, not merely the first byte. Waiting on "any output" returns while bash is
    # still starting, and a resize delivered then is one readline is not yet listening
    # for -- which failed check 5 against a terminal that was working correctly.
    started = wait_for(app, lambda: pane.pid != 0 and "$" in screen_text(pane), 25)
    if not started:
        failures.append("no prompt from the shell")
        print("    FAIL")
    else:
        print(f"    PASS {len(''.join(seen))} bytes, pid={pane.pid}, prompt is up")

    print("[4] round-tripping a command through the pty")
    seen.clear()
    pane.session.write("echo TERMVIEW_ROUNDTRIP_OK\r\n")
    got = wait_for(app, lambda: "TERMVIEW_ROUNDTRIP_OK" in screen_text(pane), 15)
    print(f"    {'PASS' if got else 'FAIL'} keystroke -> pty -> shell -> painted grid")
    if not got:
        failures.append("input did not round-trip to the grid")

    print("[5] resize reports cells to the shell, not pixels")
    seen.clear()
    pane.session.resize(132, 40)
    pane.view.emulator.resize(132, 40)
    # Asked more than once, deliberately. bash picks up a new COLUMNS from SIGWINCH via
    # readline, and how soon it does depends on where in its own startup it happens to be
    # -- so a single write here made the probe fail against a terminal that was resizing
    # correctly. Retrying tests the property (the shell learns its grid) rather than the
    # timing (it learns it within 800ms).
    ok_cols = False
    for _ in range(6):
        pump(app, 0.6)
        pane.session.write("echo COLS=$COLUMNS LINES=$LINES\r\n")
        if wait_for(app, lambda: "COLS=132 LINES=40" in screen_text(pane), 3):
            ok_cols = True
            break
    print(f"    {'PASS' if ok_cols else 'FAIL'} shell sees COLUMNS=132 LINES=40 "
          f"(a console host would letterbox instead)")
    if not ok_cols:
        failures.append("resize did not reach the shell as cells")

    print("[6] retheming a running terminal")
    dark_pixels = pane.view.grab().toImage().pixelColor(4, 4)
    pane.apply_theme(th.Theme("light"))
    pump(app, 0.4)
    light_pixels = pane.view.grab().toImage().pixelColor(4, 4)
    changed = dark_pixels != light_pixels
    print(f"    {'PASS' if changed else 'FAIL'} background {dark_pixels.name()} -> "
          f"{light_pixels.name()} on a live session")
    if not changed:
        failures.append("theme did not reach a running terminal")
    pane.apply_theme(theme)
    pump(app, 0.3)

    print("[8] throughput (pure-Python parser -- the number that decides this)")
    bulk = Emulator(120, 40)
    payload = "".join(
        f"{i:06d} the quick brown fox jumps over the lazy dog 0123456789\r\n"
        for i in range(20000)
    )
    t0 = time.perf_counter()
    bulk.stream.feed(payload)
    elapsed = time.perf_counter() - t0
    rate = len(payload) / 1024 / 1024 / elapsed
    ok_rate = rate >= THROUGHPUT_FLOOR_MBS
    print(f"    {'PASS' if ok_rate else 'FAIL'} {rate:.2f} MB/s "
          f"(floor {THROUGHPUT_FLOOR_MBS}); {len(payload)/1024/1024:.2f} MB in {elapsed:.2f}s")
    print(f"         scrollback kept {len(bulk.history)} lines")
    if not ok_rate:
        failures.append(f"throughput {rate:.2f} MB/s below floor")

    print("[9] alternate screen: full-screen program, then back")
    seen.clear()
    pane.session.write("printf 'PRIMARY_MARKER\\n'\r\n")
    wait_for(app, lambda: "PRIMARY_MARKER" in screen_text(pane), 10)
    # Enter the alternate screen, paint it, leave -- exactly what vim and less do.
    pane.view.feed("\x1b[?1049h\x1b[2J\x1b[HALT_SCREEN_MARKER")
    pane.view.emulator.drain()
    pump(app, 0.2)
    in_alt = ("ALT_SCREEN_MARKER" in screen_text(pane)
              and "PRIMARY_MARKER" not in screen_text(pane))
    pane.view.feed("\x1b[?1049l")
    pane.view.emulator.drain()
    pump(app, 0.2)
    restored = ("PRIMARY_MARKER" in screen_text(pane)
                and "ALT_SCREEN_MARKER" not in screen_text(pane))
    print(f"    {'PASS' if in_alt else 'FAIL'} alternate screen replaced the primary")
    print(f"    {'PASS' if restored else 'FAIL'} primary screen came back on exit")
    if not in_alt:
        failures.append("alternate screen did not take over")
    if not restored:
        failures.append("primary screen did not come back (pyte alone does not do this)")

    print("[10] selection lifts text back out of the grid")
    target = None
    emu = pane.view.emulator
    for index in range(emu.total_lines):
        row = emu.line(index)
        text = "".join(row[x].data for x in range(emu.screen.columns))
        if "PRIMARY_MARKER" in text and "printf" not in text:
            target = (index, text.index("PRIMARY_MARKER"))
            break
    if target is None:
        failures.append("could not find a line to select")
        print("    FAIL: nothing to select")
    else:
        row_index, col = target
        pane.view._sel_anchor = (row_index, col)
        pane.view._sel_head = (row_index, col + len("PRIMARY_MARKER"))
        picked = pane.view.selected_text()
        ok_sel = picked == "PRIMARY_MARKER"
        print(f"    {'PASS' if ok_sel else 'FAIL'} selected {picked!r}")
        if not ok_sel:
            failures.append(f"selection returned {picked!r}")
        pane.view.clear_selection()

    print("[13] right-click is ours, unless the program asked for the mouse")
    # End-to-end on purpose: the mode is set by a real program's output travelling back
    # through the pty and the parser, not by poking the emulator. Whether the menu appears
    # is decided by what a running `vim` would actually have done.
    # Connected at the *pane*, not at the view inside it. That is the signal main_window
    # wires, so this covers the forward as well as the decision.
    seen: list = []
    pane.contextMenuRequested.connect(seen.append)

    def right_click(shift: bool = False) -> int:
        seen.clear()
        point = QPoint(40, 20)
        pane.view.contextMenuEvent(QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, point, pane.view.mapToGlobal(point),
            Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier,
        ))
        return len(seen)

    plain = right_click() == 1
    pane.session.write("printf '\\033[?1000h'\r\n")
    tracking = wait_for(app, lambda: pane.view.emulator.mouse_tracking, 10)
    passed_through = right_click() == 0
    shift_override = right_click(shift=True) == 1
    pane.session.write("printf '\\033[?1000l'\r\n")
    released = wait_for(app, lambda: not pane.view.emulator.mouse_tracking, 10)
    returned = right_click() == 1
    for label, good in (("a plain terminal yields the app menu", plain),
                        ("the program's request reached the emulator", tracking),
                        ("a program that wants the mouse keeps the button", passed_through),
                        ("Shift takes the button back", shift_override),
                        ("the program released the mouse", released),
                        ("the menu returns once it does", returned)):
        print(f"    {'PASS' if good else 'FAIL'} {label}")
        if not good:
            failures.append(label)

    print("[12] the scrollbar takes its range from the scrollback")
    bar = pane.view.findChild(QScrollBar)
    if bar is None:
        failures.append("the terminal has no scrollbar")
        print("    FAIL: no QScrollBar under the view")
    else:
        emu = pane.view.emulator
        pane.session.write("seq 1 400\r\n")
        filled = wait_for(app, lambda: bar.maximum() > 0, 15)
        # Wait for the shell to go quiet before dragging. New output pins the view back to
        # the bottom by design, so a drag racing the tail of `seq` would be undone by the
        # very behaviour check 12 asserts two lines above -- and read as a broken bar.
        settled = False
        for _ in range(20):
            was = emu.total_lines
            pump(app, 0.25)
            if emu.total_lines == was:
                settled = True
                break
        rows = emu.screen.lines
        spans = bar.maximum() == emu.total_lines - rows and bar.pageStep() == rows
        at_bottom = bar.value() == bar.maximum() and pane.view.scroll_offset == 0
        # Read straight back: valueChanged is a direct connection, so the viewport has
        # already moved, and pumping first would only invite more output to pin it back.
        bar.setValue(0)
        moved = bar.maximum() > 0 and pane.view.scroll_offset == bar.maximum()
        print(f"    maximum={bar.maximum()} page={bar.pageStep()} rows={rows} "
              f"offset after dragging to the top={pane.view.scroll_offset}")
        for label, good in (("the bar took a range from the scrollback", filled),
                            ("the shell went quiet before the drag", settled),
                            ("it spans exactly the history", spans),
                            ("new output leaves it at the bottom", at_bottom),
                            ("dragging it to the top moved the viewport", moved)):
            print(f"    {'PASS' if good else 'FAIL'} {label}")
            if not good:
                failures.append(label)
        # Back to the live view, so check 11 reads a screen and not scrollback.
        pane.view.scroll_by(-bar.maximum())
        pump(app, 0.2)

    print("[11] the shell's questions get answered")
    replies = []
    fresh = TerminalPane(GIT_BASH, CWD, theme)
    fresh.view.emulator.respond.connect(lambda r: replies.append(r))
    fresh.resize(800, 400)
    fresh.show()
    t0 = time.perf_counter()
    up = wait_for(app, lambda: fresh.pid != 0 and "$" in screen_text(fresh), 20)
    to_prompt = time.perf_counter() - t0
    ok_prompt = up and to_prompt <= PROMPT_CEILING_S
    print(f"    {'PASS' if ok_prompt else 'FAIL'} prompt in {to_prompt:.2f}s "
          f"(ceiling {PROMPT_CEILING_S}s); replied {replies}")
    if not replies:
        failures.append("nothing answered the shell's device-attributes query")
    if not ok_prompt:
        failures.append(f"prompt took {to_prompt:.2f}s, over the {PROMPT_CEILING_S}s ceiling")
    fresh.close_session(force=True)
    pump(app, 0.5)
    fresh.close()

    print("[7] closing takes the shell with it")
    pid = pane.pid
    pane.close_session(force=True)
    gone = wait_for(app, lambda: not pane.is_alive(), 10)
    pump(app, 1.0)
    after = bash_count()
    print(f"    shell pid={pid} gone={gone}; bash.exe {before} -> {after}")
    if not gone:
        failures.append("shell survived close")
    if after > before:
        failures.append(f"leaked {after - before} shell(s)")

    pane.close()
    pump(app, 0.3)

    print("\n" + "=" * 70)
    if failures:
        print(f"RESULT: FAIL - {len(failures)} problem(s)")
        for problem in failures:
            print(f"  - {problem}")
        return 1
    print("RESULT: PASS - a native Qt terminal is viable.")
    print("        No browser, no render process per pane, no page load before the shell")
    print("        can start. The cost is a pure-Python parser: see check 8.")
    return 0


if __name__ == "__main__":
    _ = Qt
    raise SystemExit(main())
