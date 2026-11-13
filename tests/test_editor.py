"""The editor pane (ui/editor.py) and how the pane tree carries one.

Two groups. The first is the pane's behaviour with a file: dirty state, saving, and
noticing the file change underneath it. The second is the part that only shows up in
integration -- an editor is a *body* in the same tree that holds terminals, and almost
every bug this feature can have is the window mistaking one for the other: launching a
shell into it, counting it as a running session, labelling it with a shell name.

Dialogs are stubbed throughout. The refusals are the paths worth testing and every one
of them ends in a modal, which in an unattended run is a hang rather than a failure.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtWidgets")

from asomedeck.ui.editor import EditorPane  # noqa: E402
from asomedeck.ui.pane_tree import PaneTree  # noqa: E402
from asomedeck.ui.theme import Theme  # noqa: E402


@pytest.fixture
def messages(monkeypatch):
    """Every dialog the pane can raise, captured instead of shown."""
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(EditorPane, "_show_message",
                        lambda self, text, detail: seen.append((text, detail)))
    return seen


@pytest.fixture
def pane(app, messages):
    _ = app, messages
    return EditorPane(Theme("dark"))


@pytest.fixture
def sample(tmp_path):
    target = tmp_path / "sample.py"
    target.write_bytes(b"def go():\n    return 1\n")
    return target


# --- the pane body surface -------------------------------------------------------


def test_an_editor_is_not_a_session(pane):
    """``is_alive`` is False on purpose, and everything downstream depends on it.

    True here would put editor panes in ``live_panes()``, and the quit prompt would
    offer to close terminals that are text files.
    """
    assert pane.pane_kind == "editor"
    assert pane.is_alive() is False
    assert pane.pid == ""


def test_it_answers_the_calls_a_pane_makes_on_a_body(pane):
    """Duck-typed, so a missing method is an AttributeError at runtime, not import."""
    for name in ("is_alive", "focus_terminal", "close_session", "apply_theme"):
        assert callable(getattr(pane, name))
    pane.close_session(force=True)          # must not raise: there is nothing to end


def test_a_theme_switch_reaches_the_editor(pane, sample):
    pane.open_path(str(sample))
    pane.apply_theme(Theme("light"))        # must not raise


# --- opening ---------------------------------------------------------------------


def test_opening_a_file_shows_it(pane, sample):
    assert pane.open_path(str(sample))
    assert pane.edit.toPlainText() == "def go():\n    return 1\n"
    assert pane.file_path == str(sample)
    assert pane.title == "sample.py"
    assert not pane.dirty


def test_opening_picks_the_grammar_from_the_name(pane, sample):
    pane.open_path(str(sample))
    assert pane.highlighter.language == "python"


def test_the_status_line_reports_what_was_loaded(pane, sample):
    seen: list[str] = []
    pane.statusChanged.connect(seen.append)
    pane.open_path(str(sample))
    assert seen and seen[-1] == "python · utf-8 · LF · 3 lines"


def test_a_crlf_file_says_so(pane, tmp_path):
    target = tmp_path / "windows.py"
    target.write_bytes(b"x = 1\r\n")
    seen: list[str] = []
    pane.statusChanged.connect(seen.append)
    pane.open_path(str(target))
    assert "CRLF" in seen[-1]


def test_a_binary_file_is_refused_with_a_reason(pane, tmp_path, messages):
    target = tmp_path / "logo.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    assert pane.open_path(str(target)) is False
    assert messages and "not a text file" in messages[-1][0]
    assert pane.file_path == ""             # the pane keeps showing nothing, not garbage


def test_a_huge_file_is_refused_and_points_at_a_pager(pane, tmp_path, messages):
    from asomedeck import files

    target = tmp_path / "huge.log"
    target.write_bytes(b"x" * (files.MAX_BYTES + 1))
    assert pane.open_path(str(target)) is False
    assert "less" in messages[-1][1]


def test_a_refusal_leaves_the_previous_file_open(pane, sample, tmp_path, messages):
    """A misclick in the tree must not blank the pane you were reading."""
    pane.open_path(str(sample))
    binary = tmp_path / "logo.png"
    binary.write_bytes(b"\x00\x00\x00\x00")
    assert pane.open_path(str(binary)) is False
    assert pane.file_path == str(sample)
    assert pane.edit.toPlainText().startswith("def go()")


def test_a_missing_file_is_refused(pane, tmp_path, messages):
    assert pane.open_path(str(tmp_path / "never_existed.py")) is False
    assert messages


# --- dirty state -----------------------------------------------------------------


def test_typing_marks_the_file_dirty_and_the_title_shows_it(pane, sample):
    pane.open_path(str(sample))
    states: list[bool] = []
    pane.dirtyChanged.connect(states.append)

    pane.edit.setPlainText("def go():\n    return 2\n")
    assert pane.dirty
    assert states == [True]
    assert pane.title == "● sample.py"


def test_loading_a_file_does_not_count_as_typing(pane, sample):
    """setPlainText fires textChanged, so without a guard every open is born dirty."""
    pane.open_path(str(sample))
    assert not pane.dirty
    assert pane.title == "sample.py"


def test_saving_writes_the_edit_and_clears_the_flag(pane, sample):
    pane.open_path(str(sample))
    pane.edit.setPlainText("def go():\n    return 2\n")
    assert pane.save()
    assert sample.read_bytes() == b"def go():\n    return 2\n"
    assert not pane.dirty
    assert pane.title == "sample.py"


def test_saving_keeps_the_files_line_endings(pane, tmp_path):
    target = tmp_path / "windows.py"
    target.write_bytes(b"alpha\r\nbeta\r\n")
    pane.open_path(str(target))
    pane.edit.setPlainText("alpha\ngamma\n")
    assert pane.save()
    assert target.read_bytes() == b"alpha\r\ngamma\r\n"


def test_saving_a_clean_buffer_does_nothing_and_succeeds(pane, sample):
    pane.open_path(str(sample))
    before = sample.stat().st_mtime_ns
    assert pane.save()
    assert sample.stat().st_mtime_ns == before


def test_saving_with_no_file_open_is_a_no_op(pane):
    assert pane.save() is False


# --- the file changing underneath ------------------------------------------------


def test_a_clean_buffer_reloads_itself(pane, sample):
    """The case the pane exists for: an agent rewrites the file in the pane beside it."""
    pane.open_path(str(sample))
    sample.write_bytes(b"def go():\n    return 99\n")
    pane.check_disk()
    assert pane.edit.toPlainText() == "def go():\n    return 99\n"
    assert not pane.dirty
    assert pane.banner.isHidden()


def test_a_dirty_buffer_is_never_overwritten_silently(pane, sample):
    pane.open_path(str(sample))
    pane.edit.setPlainText("mine\n")
    sample.write_bytes(b"theirs\n")

    pane.check_disk()
    assert pane.edit.toPlainText() == "mine\n"          # nothing was destroyed
    assert not pane.banner.isHidden()
    assert "changed on disk" in pane.banner_label.text()


def test_a_deleted_file_is_reported_rather_than_reloaded(pane, sample):
    pane.open_path(str(sample))
    sample.unlink()
    pane.check_disk()
    assert not pane.banner.isHidden()
    assert "deleted or moved" in pane.banner_label.text()


def test_keeping_mine_dismisses_the_banner_and_stops_it_returning(pane, sample):
    pane.open_path(str(sample))
    pane.edit.setPlainText("mine\n")
    sample.write_bytes(b"theirs\n")
    pane.check_disk()

    pane._keep_mine()
    assert pane.banner.isHidden()
    pane.check_disk()
    assert pane.banner.isHidden()           # re-fingerprinted, so it does not fire again
    assert pane.dirty                       # and the buffer is still the user's


def test_our_own_save_does_not_look_like_an_external_change(pane, sample):
    pane.open_path(str(sample))
    pane.edit.setPlainText("edited\n")
    pane.save()
    pane.check_disk()
    assert pane.banner.isHidden()


def test_check_disk_on_an_empty_pane_does_nothing(pane):
    pane.check_disk()                       # must not raise before a file is opened


# --- indentation -----------------------------------------------------------------


def test_reindenting_a_selection_does_not_replace_it(pane, sample):
    """The default Tab behaviour deletes the selection. In a pane opened to read a
    file, that is a destructive surprise one keystroke away from the arrow keys."""
    from PySide6.QtGui import QTextCursor

    pane.open_path(str(sample))
    cursor = pane.edit.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    pane.edit._shift_block(cursor, out=False)
    # The trailing empty line is left alone: a selection that ends at the start of a
    # line does not include that line, which is what every editor does and what stops
    # a full-document indent leaving whitespace on the last line.
    assert pane.edit.toPlainText() == "    def go():\n        return 1\n"


def test_dedenting_stops_at_the_left_margin(pane, sample):
    from PySide6.QtGui import QTextCursor

    pane.open_path(str(sample))
    cursor = pane.edit.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    pane.edit._shift_block(cursor, out=True)
    assert pane.edit.toPlainText() == "def go():\nreturn 1\n"


# --- in the pane tree ------------------------------------------------------------


@pytest.fixture
def tree(app):
    _ = app
    return PaneTree(Theme("dark"))


def test_a_pane_holding_an_editor_reports_its_kind(tree, sample, messages):
    pane = tree.add_pane("#101010")
    pane.attach_body(EditorPane(Theme("dark"), parent=pane))
    pane.body.open_path(str(sample))

    assert pane.kind == "editor"
    assert pane.file_path == str(sample)
    # The distinction the whole integration rests on: no session, but not empty either.
    assert pane.has_session is False


def test_a_pane_holding_a_terminal_still_reports_terminal(tree):
    """The default, and it comes from a body that has never heard of ``pane_kind``."""
    pane = tree.add_pane("#101010")
    assert pane.kind == "terminal"
    assert pane.file_path == ""


def test_a_saved_layout_remembers_the_file(tree, sample, messages):
    pane = tree.add_pane("#101010")
    pane.attach_body(EditorPane(Theme("dark"), parent=pane))
    pane.body.open_path(str(sample))
    tree.add_pane("#101010")                # a terminal beside it

    saved = tree.to_tree()
    editor_nodes = _panes_in(saved)
    assert {"kind": "editor", "path": str(sample)}.items() <= editor_nodes[0].items()
    assert "kind" not in editor_nodes[1]    # the terminal is described by launcher_id


def test_restoring_hands_the_path_back(tree, sample, messages):
    pane = tree.add_pane("#101010")
    pane.attach_body(EditorPane(Theme("dark"), parent=pane))
    pane.body.open_path(str(sample))
    saved = tree.to_tree()

    fresh = PaneTree(Theme("dark"))
    restored = fresh.restore(saved)
    assert restored[0].restore_kind == "editor"
    assert restored[0].restore_path == str(sample)


def test_a_layout_written_before_editors_existed_still_loads(tree):
    """Old rows in the layout table have no ``kind``; they must read as terminals."""
    restored = tree.restore({"type": "pane", "launcher_id": "gitbash"})
    assert restored[0].restore_kind == ""
    assert restored[0].kind == "terminal"


def _panes_in(node) -> list[dict]:
    if node.get("type") == "pane":
        return [node]
    return [pane for child in node.get("children", ()) for pane in _panes_in(child)]


def test_the_saved_terminal_count_ignores_editors(sample):
    """The sidebar line says "N terminals saved". A file is not one."""
    from asomedeck.ui.main_window import _count_panes

    layout = {"type": "split", "orientation": "h", "children": [
        {"type": "pane", "launcher_id": "gitbash"},
        {"type": "pane", "launcher_id": "", "kind": "editor", "path": str(sample)},
    ]}
    assert _count_panes(layout) == 1


def test_a_file_path_survives_a_round_trip_through_json(tree, sample, messages):
    """tree_json is stored as text, and Windows paths are full of backslashes."""
    import json

    pane = tree.add_pane("#101010")
    pane.attach_body(EditorPane(Theme("dark"), parent=pane))
    pane.body.open_path(str(sample))

    revived = json.loads(json.dumps(tree.to_tree()))
    fresh = PaneTree(Theme("dark"))
    assert os.path.normcase(fresh.restore(revived)[0].restore_path) == \
        os.path.normcase(str(sample))


# --- what the session panel shows ------------------------------------------------


def test_a_path_is_shown_relative_to_its_deck():
    """Shorter, and more useful: the header above already prints the deck root."""
    from asomedeck.ui.main_window import panel_path

    assert panel_path(os.path.join("D:", os.sep, "work", "app", "src", "main.py"),
                      os.path.join("D:", os.sep, "work", "app")) == \
        os.path.join("src", "main.py")


def test_a_long_path_is_elided_from_the_left():
    """The end of a path identifies the file; the start is what can be dropped."""
    from asomedeck.ui.main_window import PANEL_PATH_CHARS, panel_path

    deep = os.path.join("a_very_long_directory_name", "and_another_one", "target.py")
    shown = panel_path(os.path.join("D:", os.sep, "proj", deep),
                       os.path.join("D:", os.sep, "proj"))
    assert len(shown) == PANEL_PATH_CHARS
    assert shown.startswith("…") and shown.endswith("target.py")


def test_a_file_outside_the_deck_keeps_its_absolute_path():
    from asomedeck.ui.main_window import panel_path

    outside = os.path.join("D:", os.sep, "elsewhere", "notes.md")
    assert panel_path(outside, os.path.join("D:", os.sep, "proj")) == outside


def test_no_file_shows_nothing():
    from asomedeck.ui.main_window import panel_path

    assert panel_path("", "D:/proj") == ""
