"""Gate: does taking a pane off the grid cost it anything?

Two features ask that question and share the machinery that answers it. Zoom hides a
pane's siblings; hiding takes one pane away and leaves the rest. Both come down to
``QWidget.hide()`` on a splitter child, so both live here.

``tests/test_pane_tree.py`` covers the tree algebra with stand-in bodies, which is the right
place for it and cannot answer the question either feature turns on. Qt lays a hidden widget
out at zero extent and delivers the resize on the way back -- so the failure mode is a
*live* pseudo-console being resized to the 2x1 floor ``grid_size`` clamps to, and pyte
discarding everything that no longer fits. A stand-in body records that it happened; only a
real shell shows what it costs.

So this runs the real window with real shells on real ConPTYs and asks:

  1. Three panes start and their shells are live.
  2. The zoomed pane actually gets the area -- more columns and more rows than it had.
  3. The others are hidden while it does.
  4. The keyboard is inside the zoomed pane, not on its header. Qt gives click focus to the
     nearest ancestor that accepts it, which is the Pane, so this is not automatic.
  5. The button shows the zoomed state. The rule is a descendant selector and stays
     unpainted unless the button itself is repolished.
  6. **The hidden panes are never resized.** The one this file exists for.
  7. Unzooming returns every pane to the exact grid it had.
  8. Every shell survived, and the saved arrangement still carries real proportions.

Then the same questions for hiding, plus the one that only exists where the two meet:

  9. A hidden pane gives its space to the others, and its own shell keeps running.
 10. Its pseudo-console is never resized while it is off the grid.
 11. **Zooming and unzooming something else does not bring it back.** Unzoom shows every
     child of every splitter it collapsed, so without a single visibility predicate this
     is where a deliberately hidden pane reappears.
 12. Showing it again returns every pane to the exact grid it had, shells intact.

Run:  .venv\\Scripts\\python.exe tools\\probe_zoom.py
Exit: 0 if every check passes.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from asomedeck import config as cfg  # noqa: E402
from asomedeck.db import Database  # noqa: E402
from asomedeck.ui import theme as th  # noqa: E402
from asomedeck.ui.main_window import MainWindow  # noqa: E402

#: Long enough for three shells to spawn and draw a prompt on a cold cache.
SETTLE_MS = 2500
#: Long enough for a splitter relayout and the resize events it defers.
RELAYOUT_MS = 1200

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' -- ' + detail) if detail else ''}")
    if not ok:
        failures.append(label)


def grids(window: MainWindow) -> dict[int, tuple[int, int]]:
    """Each pane's grid in cells, straight from the emulator the pty is talking to."""
    found = {}
    for pane in window.tree.panes:
        view = getattr(pane.body, "view", None)
        if view is not None:
            found[pane.pane_id] = (view.emulator.screen.columns, view.emulator.screen.lines)
    return found


def main() -> int:
    app = QApplication([])
    theme = th.Theme("dark")
    th.load_fonts()
    th.apply(app, theme)

    # A throwaway database and a path-less Config, for the reason conftest.py gives: an
    # unattended run must not rewrite the state or preferences of whoever is running it.
    db = Database(pathlib.Path(tempfile.mkdtemp(prefix="asomedeck-zoom-")) / "state.db")
    db.add_deck(str(pathlib.Path.cwd()), "gitbash")
    window = MainWindow(db, theme, frameless=False, restore_sessions=False,
                        config=cfg.Config())
    window.quit_action = "close"
    window.resize(1400, 860)
    window.show()

    state: dict = {}

    def open_three() -> None:
        window._new_session(None)
        window._new_session(Qt.Orientation.Horizontal)
        window._new_session(Qt.Orientation.Vertical)
        QTimer.singleShot(SETTLE_MS, zoom_one)

    def zoom_one() -> None:
        print("  tree:", window.tree.describe())
        state["before"] = grids(window)
        check("three shells are live",
              len(state["before"]) == 3 and all(p.has_session for p in window.tree.panes),
              str(state["before"]))
        state["target"] = window.tree.panes[1]
        window.tree.toggle_zoom(state["target"])
        QTimer.singleShot(RELAYOUT_MS, inspect_zoomed)

    def inspect_zoomed() -> None:
        target = state["target"]
        zoomed = grids(window)
        was, now = state["before"][target.pane_id], zoomed[target.pane_id]
        check("the zoomed pane got the area", now[0] > was[0] and now[1] > was[1],
              f"{was} -> {now}")
        check("the others are hidden",
              all(p.isHidden() for p in window.tree.panes if p is not target))
        # window.focusWidget(), not hasFocus(): hasFocus() is False whenever the window is
        # not the OS-active one, which says nothing about where focus was put and makes
        # this fail on an unattended run that never came to the foreground.
        landed = window.focusWidget()
        check("the keyboard is inside the zoomed pane", landed is target.body.view,
              f"focus is on {type(landed).__name__}")
        button = target.zoom_button
        check("the button shows the zoomed state",
              target.zoomed
              and button.palette().color(button.foregroundRole()).name()
              == theme.hex("accent"))
        state["while_zoomed"] = {k: v for k, v in zoomed.items() if k != target.pane_id}
        window.tree.toggle_zoom(target)
        QTimer.singleShot(RELAYOUT_MS, inspect_restored)

    def inspect_restored() -> None:
        after = grids(window)
        untouched = {k: v for k, v in state["before"].items() if k in state["while_zoomed"]}
        check("the hidden panes were never resized", state["while_zoomed"] == untouched,
              f"they read {state['while_zoomed']}, were {untouched}")
        check("every pane is back to the grid it had", after == state["before"],
              f"{state['before']} -> {after}")
        check("nothing is left hidden", not any(p.isHidden() for p in window.tree.panes))
        check("all three shells survived", all(p.has_session for p in window.tree.panes))
        saved = window.tree.to_tree()
        check("the arrangement kept real proportions",
              saved["type"] == "split" and all(saved["sizes"]), str(saved.get("sizes")))
        state["grid"] = after
        QTimer.singleShot(0, hide_one)

    # --- hiding ----------------------------------------------------------------------

    def hide_one() -> None:
        print("  hiding pane", window.tree.panes[0].pane_id)
        state["buried"] = window.tree.panes[0]
        window.tree.hide_pane(state["buried"])
        QTimer.singleShot(RELAYOUT_MS, inspect_hidden)

    def inspect_hidden() -> None:
        buried = state["buried"]
        now = grids(window)
        others = [p for p in window.tree.panes if p is not buried]
        check("the hidden pane is off the grid", buried.isHidden())
        # Its siblings share what it gave up, and which of them grows depends on where it
        # sat in the tree -- so the claim is that somebody got bigger, not that all did.
        was = {p.pane_id: state["grid"][p.pane_id] for p in others}
        got = {p.pane_id: now[p.pane_id] for p in others}
        check("its space went to the others",
              any(got[pid] > was[pid] for pid in was), f"{was} -> {got}")
        check("its shell is still running", buried.has_session)
        check("its pseudo-console was not resized",
              now[buried.pane_id] == state["grid"][buried.pane_id],
              f"{state['grid'][buried.pane_id]} -> {now[buried.pane_id]}")
        saved = window.tree.to_tree()
        check("the arrangement still carries real proportions while hidden",
              saved["type"] == "split" and all(saved["sizes"]), str(saved.get("sizes")))
        window.tree.toggle_zoom(others[0])
        QTimer.singleShot(RELAYOUT_MS, unzoom_over_hidden)

    def unzoom_over_hidden() -> None:
        window.tree.toggle_zoom(window.tree.zoomed)
        QTimer.singleShot(RELAYOUT_MS, inspect_composed)

    def inspect_composed() -> None:
        buried = state["buried"]
        now = grids(window)
        check("unzooming did not bring the hidden pane back",
              buried.isHidden() and window.tree.is_hidden(buried))
        check("it still has not been resized",
              now[buried.pane_id] == state["grid"][buried.pane_id],
              f"{state['grid'][buried.pane_id]} -> {now[buried.pane_id]}")
        window.tree.show_all_panes()
        QTimer.singleShot(RELAYOUT_MS, inspect_shown)

    def inspect_shown() -> None:
        after = grids(window)
        check("nothing is left hidden after showing them",
              not any(p.isHidden() for p in window.tree.panes))
        check("every pane is back to the grid it had", after == state["grid"],
              f"{state['grid']} -> {after}")
        check("all three shells survived the round trip",
              all(p.has_session for p in window.tree.panes))
        window.close()
        app.quit()

    QTimer.singleShot(600, open_three)
    app.exec()

    print()
    print("PANE PROBE:", "all checks passed" if not failures
          else f"FAILED: {', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
