"""Gate: does the file tree actually reach the editor pane, end to end?

Unit tests cover the pieces -- ``files.py`` round-trips bytes, the model is lazy, the
grammar scans correctly. What they cannot cover is this deck's recurring failure: code
that is correct and never invoked. ``nativeEvent``, ``write_process_input``,
``WA_StyledBackground``, ``db.set_launcher`` and ``titleChanged`` were each right, tested,
and dead, because nothing called them or nothing painted them.

So every check below goes through the real ``MainWindow`` and asserts *arrival* -- a
signal that fired, a pixmap with more than one colour in it, a file on disk -- rather
than a function that returned the right value when called by hand.

  1. The window builds with both sidebar modes, and Files paints.
  2. A real click in the tree emits, reaches the window, and opens a pane.
  3. The editor paints, in more than one colour, so the highlighter ran.
  4. The pane chrome carries the file: header title, tab strip, footer status.
  5. No shell is launched into the editor pane, and it is not counted as a session.
  6. A second click reuses the pane instead of splitting the grid again.
  7. Editing marks it dirty; saving writes exactly the bytes and clears it.
  8. The file changing on disk reloads a clean buffer by itself.
  9. Git marks from the real repository reach the tree.
 10. Ctrl+S belongs to the editor, not the window -- in a terminal it is XOFF.
 11. The arrangement persists the file and restores it.

Run:  .venv\\Scripts\\python.exe tools\\probe_files.py
Exit: 0 if every check passes.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QModelIndex, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from asomedeck.db import Database  # noqa: E402
from asomedeck.ui import theme as th  # noqa: E402
from asomedeck.ui.chrome import SessionTab  # noqa: E402
from asomedeck.ui.file_tree import IS_DIR_ROLE, MARK_ROLE, PATH_ROLE  # noqa: E402
from asomedeck.ui.main_window import MainWindow  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The invalid index: "the root".
ROOT = QModelIndex()

# The dirty marker and the folder mark are not ASCII, and a Windows console is cp1252 by
# default -- printing them raises UnicodeEncodeError and kills the probe at the check it
# was reporting on.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def pump(app: QApplication, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def wait_for(app: QApplication, predicate, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def colours(widget) -> int:
    """How many distinct colours a widget paints. One means it painted nothing."""
    pixmap = widget.grab()
    if pixmap.isNull():
        return 0
    image = pixmap.toImage()
    seen = set()
    for y in range(0, image.height(), 3):
        for x in range(0, image.width(), 3):
            seen.add(image.pixel(x, y))
            if len(seen) > 40:
                return len(seen)
    return len(seen)


def row_named(model, name: str, parent=ROOT):
    for row in range(model.rowCount(parent)):
        index = model.index(row, 0, parent)
        if index.data() == name:
            return index
    return QModelIndex()


def main() -> int:  # noqa: C901 - a probe is a list of checks
    failures: list[str] = []

    app = QApplication(sys.argv)
    th.load_fonts()
    theme = th.Theme("dark")
    th.apply(app, theme)

    db = Database(":memory:")
    db.add_deck(REPO, "gitbash")
    window = MainWindow(db, theme, frameless=False, restore_sessions=False)
    window.quit_action = "close"
    window.resize(1480, 900)
    window.show()
    pump(app, 0.4)

    # --- 1 -----------------------------------------------------------------------
    print("[1] the sidebar has two modes and Files paints")
    window.sidebar.set_mode("files")
    tree_view = window.sidebar.files
    model = tree_view.tree_model
    if model.canFetchMore(QModelIndex()):
        model.fetchMore(QModelIndex())
    pump(app, 0.3)
    names = [model.index(row, 0, QModelIndex()).data() for row in range(model.rowCount())]
    painted = colours(window.sidebar)
    print(f"    mode={window.sidebar.mode} rows={len(names)} colours={painted}")
    print(f"    top level: {', '.join(names[:8])}")
    if window.sidebar.mode != "files":
        failures.append("the sidebar did not switch to Files")
    if "src" not in names or "pyproject.toml" not in names:
        failures.append(f"the repository did not list correctly: {names}")
    if ".git" in names or "__pycache__" in names:
        failures.append("generated directories are showing")
    if painted < 3:
        failures.append(f"the sidebar painted {painted} colour(s)")
    else:
        print("    PASS")

    # --- 2 -----------------------------------------------------------------------
    print("[2] a click in the tree reaches the window")
    src = row_named(model, "src")
    tree_view.expand(src)
    pump(app, 0.2)
    asomedeck = row_named(model, "asomedeck", src)
    tree_view.expand(asomedeck)
    pump(app, 0.2)
    target = row_named(model, "files.py", asomedeck)
    if not target.isValid():
        print("    FAIL could not find src/asomedeck/files.py in the tree")
        return 1
    # Through the view's own handler, so the signal chain is what is being tested and
    # not a direct call to the window.
    tree_view._on_clicked(target)
    pump(app, 0.3)

    pane_tree = window.tree
    editors = [pane for pane in pane_tree.panes if pane.kind == "editor"]
    print(f"    panes={len(pane_tree.panes)} editors={len(editors)}")
    if len(editors) != 1:
        failures.append(f"expected one editor pane, found {len(editors)}")
        print("    FAIL")
        return 1
    pane = editors[0]
    body = pane.body
    print(f"    opened {os.path.basename(body.file_path)}")
    if os.path.normcase(body.file_path) != os.path.normcase(str(target.data(PATH_ROLE))):
        failures.append("the pane opened a different file than the one clicked")
    else:
        print("    PASS")

    # --- 3 -----------------------------------------------------------------------
    print("[3] the editor paints, in more than one colour")
    pump(app, 0.2)
    painted = colours(body.edit)
    print(f"    {painted} distinct colours in the viewport")
    if painted < 4:
        failures.append(f"the editor painted {painted} colour(s) - is the highlighter on?")
    else:
        print("    PASS text and syntax colouring both reached the screen")

    # --- 4 -----------------------------------------------------------------------
    print("[4] the pane chrome carries the file")
    header = pane.title_label.text()
    footer = pane.cwd_label.text()
    tab_text = " ".join(tab.label.text() for tab in window.tabs.findChildren(SessionTab))
    print(f"    header={header!r} footer={footer!r}")
    if "files.py" not in header:
        failures.append(f"the pane header does not name the file: {header!r}")
    if "python" not in footer or "utf-8" not in footer:
        failures.append(f"the pane footer does not describe the file: {footer!r}")
    if "files.py" not in tab_text:
        failures.append(f"the tab strip does not name the file: {tab_text!r}")
    if not any("pane header" in f or "pane footer" in f or "tab strip" in f
               for f in failures):
        print("    PASS")

    # --- 5 -----------------------------------------------------------------------
    print("[5] no shell was launched into it")
    launched = window._launch_into(pane, window.deck)
    live = len(window.live_panes())
    print(f"    _launch_into returned {launched}; live sessions across all decks: {live}")
    if launched:
        failures.append("a shell was launched into the editor pane")
    elif pane.body is not body:
        failures.append("the editor body was replaced")
    elif live:
        failures.append(f"{live} session(s) counted, but nothing is running")
    else:
        print("    PASS an editor is not a session, so the quit prompt stays honest")

    # --- 6 -----------------------------------------------------------------------
    print("[6] a second file reuses the pane")
    before = len(pane_tree.panes)
    other = row_named(model, "db.py", asomedeck)
    tree_view._on_clicked(other)
    pump(app, 0.3)
    after = len(pane_tree.panes)
    print(f"    panes {before} -> {after}, now showing {os.path.basename(body.file_path)}")
    if after != before:
        failures.append(f"the grid grew from {before} to {after} panes on a second click")
    elif not body.file_path.endswith("db.py"):
        failures.append("the pane did not switch files")
    else:
        print("    PASS")

    # --- 7 -----------------------------------------------------------------------
    print("[7] editing and saving")
    scratch = os.path.join(REPO, ".probe_scratch.py")
    with open(scratch, "wb") as handle:
        handle.write(b"x = 1\r\ny = 2\r\n")
    try:
        body.open_path(scratch)
        pump(app, 0.2)
        body.edit.setPlainText("x = 1\ny = 99\n")
        pump(app, 0.2)
        dirty_title = pane.title_label.text()
        print(f"    dirty title={dirty_title!r}")
        if not body.dirty or "●" not in dirty_title:
            failures.append("editing did not show as unsaved in the pane header")
        if not body.save():
            failures.append("save failed")
        pump(app, 0.2)
        with open(scratch, "rb") as handle:
            written = handle.read()
        print(f"    wrote {written!r}")
        if written != b"x = 1\r\ny = 99\r\n":
            failures.append(f"save did not preserve CRLF: {written!r}")
        elif body.dirty or "●" in pane.title_label.text():
            failures.append("the pane still reads as unsaved after saving")
        else:
            print("    PASS the edit landed and the file kept its line endings")

        # --- 8 -------------------------------------------------------------------
        print("[8] the file changing on disk")
        with open(scratch, "wb") as handle:
            handle.write(b"x = 1\r\ny = 1000\r\n")
        reloaded = wait_for(app, lambda: "1000" in body.edit.toPlainText(), 4)
        print(f"    reloaded by itself: {reloaded}")
        if not reloaded:
            failures.append("a clean buffer did not follow the file on disk")
        else:
            print("    PASS an agent rewriting the file updates the pane beside it")
    finally:
        os.unlink(scratch)

    # --- 9 -----------------------------------------------------------------------
    print("[9] git marks reach the tree")
    marked = wait_for(
        app,
        lambda: any(model.index(row, 0, asomedeck).data(MARK_ROLE)
                    for row in range(model.rowCount(asomedeck))),
        12,
    )
    shown = {model.index(row, 0, asomedeck).data(): model.index(row, 0, asomedeck).data(MARK_ROLE)
             for row in range(model.rowCount(asomedeck))}
    shown = {name: mark for name, mark in shown.items() if mark}
    print(f"    marked in src/asomedeck: {shown or 'nothing'}")
    if not marked:
        failures.append("no git marks arrived (is the working tree clean?)")
    else:
        folder_mark = model.index(src.row(), 0, QModelIndex()).data(MARK_ROLE)
        print(f"    the src/ folder shows {folder_mark!r}")
        if not folder_mark:
            failures.append("a folder containing a change is not marked")
        else:
            print("    PASS")

    # --- 10 ----------------------------------------------------------------------
    print("[10] Ctrl+S is the editor's, not the window's")
    window_keys = [a.shortcut().toString() for a in window.actions()]
    editor_keys = [a.shortcut().toString() for a in body.actions()]
    print(f"    window: {[k for k in window_keys if k]}")
    print(f"    editor: {[k for k in editor_keys if k]}")
    if "Ctrl+S" in window_keys:
        failures.append("Ctrl+S is a window shortcut - it would freeze a terminal (XOFF)")
    elif "Ctrl+S" not in editor_keys:
        failures.append("the editor has no Ctrl+S")
    else:
        print("    PASS scoped to the widget, so a terminal never sees it")

    # --- 11 ----------------------------------------------------------------------
    print("[11] the arrangement remembers the file")
    body.open_path(os.path.join(REPO, "README.md"))
    pump(app, 0.2)
    saved = pane_tree.to_tree()
    window.save_layouts()
    stored = db.load_layout(window.deck.id)
    nodes = []

    def walk(node):
        if node.get("type") == "pane":
            nodes.append(node)
        for child in node.get("children", ()):
            walk(child)

    walk(stored or {})
    editor_nodes = [n for n in nodes if n.get("kind") == "editor"]
    print(f"    stored {len(nodes)} pane(s), {len(editor_nodes)} editor(s)")
    if not editor_nodes:
        failures.append("the saved layout does not record the editor pane")
    elif not str(editor_nodes[0].get("path", "")).endswith("README.md"):
        failures.append(f"the saved path is wrong: {editor_nodes[0]}")
    else:
        from asomedeck.ui.pane_tree import PaneTree

        fresh = PaneTree(theme)
        restored = fresh.restore(saved, theme.hex("term"))
        back = [p for p in restored if p.restore_kind == "editor"]
        print(f"    restored {len(back)} editor pane(s) -> "
              f"{os.path.basename(back[0].restore_path) if back else '-'}")
        if not back:
            failures.append("restoring did not bring the editor pane back")
        else:
            print("    PASS")

    window.close()
    pump(app, 0.3)

    print("\n" + "=" * 70)
    if failures:
        print(f"RESULT: FAIL - {len(failures)} problem(s)")
        for problem in failures:
            print(f"  - {problem}")
        return 1
    print("RESULT: PASS - the tree, the pane and the file are wired end to end.")
    return 0


if __name__ == "__main__":
    _ = Qt, IS_DIR_ROLE
    raise SystemExit(main())
