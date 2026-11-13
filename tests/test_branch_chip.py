"""The branch in the deck header is a control, not a run of text.

It was one third of a joined string -- "pane-roster   |   clean   |   3 panes" -- in a
label with nothing behind it, and it is the only part of that line anybody has a follow-up
question about: the count of changed paths and the count of panes are complete answers,
and the branch name is the start of "what else is there".

What the dropdown deliberately does *not* do is check anything out. A window full of
agents mid-edit is the worst place to move a working tree from, and the terminal that can
do it properly is one click away -- so the rows copy the name instead, which is what you
were going to paste into that terminal anyway.
"""

from __future__ import annotations

import dataclasses

import pytest

pytest.importorskip("PySide6.QtWidgets")


from asomedeck.git_status import Branch  # noqa: E402
from asomedeck.ui.main_window import BRANCH_MENU_LIMIT  # noqa: E402


def _rows(menu):
    """The menu's real entries, should anything ever add a separator between them."""
    return [action for action in menu.actions() if not action.isSeparator()]


def test_the_menu_is_branches_and_nothing_else(window):
    """No heading. `addSection` writes its text onto a separator and this app's sheet
    pins those to a 1px line, so a heading here is an action that draws nothing -- and a
    menu opening under a chip labelled with a branch name has nothing to introduce."""
    menu = window._branch_menu([Branch("master", current=True)])
    assert [action.isSeparator() for action in menu.actions()] == [False]


def _set(window, branch: str, diff: str = "clean", sessions: int = 3):
    window.header.set_deck(window.model.deck_row(window.deck.id), sessions,
                           branch=branch, diff=diff)


# --- the chip ---------------------------------------------------------------------------

def test_a_branch_puts_the_chip_up_and_leaves_the_rest_in_the_meta_line(window):
    _set(window, "pane-roster")

    assert window.header.branch.text() == "pane-roster  ▾"
    assert not window.header.branch.isHidden()
    assert window.header.meta.text() == "clean   |   3 panes"


def test_no_branch_hides_the_chip_and_the_separator_beside_it(window):
    """A folder that is not a repository, or a git that has not answered yet.

    The separator goes with it: an empty bordered box next to a lone "|" is chrome for
    something that is not there, and the old code omitted the run for the same reason.
    """
    _set(window, "")

    assert window.header.branch.isHidden()
    assert window.header._branch_sep.isHidden()
    assert window.header.meta.text() == "clean   |   3 panes"


def test_the_chip_comes_back_down_when_a_deck_stops_having_one(window):
    _set(window, "main")
    _set(window, "")
    assert window.header.branch.isHidden()

    _set(window, "main")
    assert not window.header.branch.isHidden()


def test_no_deck_at_all_takes_the_chip_with_it(window):
    window.header.set_deck(None, 0)
    assert window.header.branch.isHidden()


def test_one_pane_is_not_pluralised_now_the_branch_has_left_the_join(window):
    _set(window, "main", sessions=1)
    assert window.header.meta.text() == "clean   |   1 pane"


# --- the click --------------------------------------------------------------------------

def test_clicking_the_chip_asks_git_for_this_deck(window, monkeypatch):
    asked = []
    monkeypatch.setattr(window.branches, "ask", asked.append)

    window.header.branch.clicked.emit()

    assert asked == [window.deck.path]


def test_a_deck_with_no_folder_asks_nothing(window, monkeypatch):
    """There is no repository under it, and `ask("")` on a pathless deck would run git
    in whatever directory the process happens to be sitting in."""
    monkeypatch.setattr(window.branches, "ask",
                        lambda _p: pytest.fail("git was run for a deck with no folder"))
    window.deck = dataclasses.replace(window.deck, path="")

    window.header.branch.clicked.emit()


def test_an_answer_for_a_folder_we_have_since_left_is_dropped(window):
    """The deck can be switched between the click and the reply. A branch list for the
    folder you just walked away from is worse than none."""
    _set(window, "main")

    window._on_branches("D:/somewhere/else", (Branch("main", current=True),))

    assert window._branches_menu is None


# --- the menu ---------------------------------------------------------------------------

def test_the_branch_you_are_on_is_first_and_the_only_one_checked(window):
    """git sorts by commit date, which is right for everything except the branch the chip
    already names -- looking for that in a date-ordered list is a search."""
    menu = window._branch_menu([
        Branch("master", when="2 days ago"),
        Branch("fix/tab-strip", when="3 weeks ago"),
        Branch("pane-roster", current=True, when="3 hours ago"),
    ])

    rows = _rows(menu)
    assert [row.text() for row in rows] == ["pane-roster", "master", "fix/tab-strip"]
    assert [row.isChecked() for row in rows] == [True, False, False]


def test_the_track_rides_beside_the_name_and_the_date_goes_in_the_tooltip(window):
    """A row wide enough for the name, the track *and* "3 weeks ago" is a menu half the
    window. The date is what you check second, so it is one hover away."""
    menu = window._branch_menu([Branch("master", track="ahead 2", when="2 days ago")])

    row = _rows(menu)[0]
    assert row.text() == "master   ahead 2"
    assert "2 days ago" in row.toolTip()


def test_a_branch_with_no_upstream_gets_no_trailing_whitespace(window):
    assert _rows(window._branch_menu([Branch("solo")]))[0].text() == "solo"


def test_a_long_list_is_capped_and_says_how_many_it_dropped(window):
    """A repository that has been alive for years has hundreds of branches, and a menu
    that tall cannot be used. What the cap drops is the coldest end of git's own sort."""
    many = [Branch(f"branch-{n}") for n in range(BRANCH_MENU_LIMIT + 7)]

    rows = _rows(window._branch_menu(many))

    assert len(rows) == BRANCH_MENU_LIMIT + 1
    assert rows[-1].text() == "… and 7 more"
    assert not rows[-1].isEnabled()


def test_an_exact_fit_grows_no_overflow_row(window):
    rows = _rows(window._branch_menu([Branch(f"b{n}") for n in range(BRANCH_MENU_LIMIT)]))
    assert len(rows) == BRANCH_MENU_LIMIT
    assert "more" not in rows[-1].text()


def test_nothing_to_show_is_a_row_saying_so_rather_than_an_empty_menu(window):
    """git missing, a folder that stopped being a repository, or a checkout with no
    commit yet. A menu that opens blank looks like the click failed."""
    rows = _rows(window._branch_menu([]))

    assert len(rows) == 1
    assert not rows[0].isEnabled()


# --- what a row does --------------------------------------------------------------------

class _Clipboard:
    """Stands in for the real one.

    The system clipboard is shared with every other process on the machine, and on
    Windows a program holding it open makes ``setText`` fail silently -- so asserting
    against it makes this file fail for reasons that have nothing to do with this file.
    What is worth pinning is that the right name is handed over.
    """

    def __init__(self) -> None:
        self.text_ = ""

    def setText(self, text):  # noqa: N802 - matches QClipboard
        self.text_ = text


def test_a_row_copies_the_name(window, monkeypatch):
    copied = []
    monkeypatch.setattr(window, "_copy_branch", copied.append)

    _rows(window._branch_menu([Branch("feat/reconcile")]))[0].trigger()

    assert copied == ["feat/reconcile"]


def test_each_row_copies_its_own_name(window, monkeypatch):
    """The late-binding trap. Rows built in a loop that close over the loop variable all
    copy whichever branch happened to be last, and every one of them looks right."""
    copied = []
    monkeypatch.setattr(window, "_copy_branch", copied.append)

    for row in _rows(window._branch_menu(
            [Branch("first"), Branch("second"), Branch("third")])):
        row.trigger()

    assert copied == ["first", "second", "third"]


def test_copying_is_what_reaches_the_clipboard(window, monkeypatch):
    clipboard = _Clipboard()
    monkeypatch.setattr("asomedeck.ui.main_window.QApplication",
                        type("_App", (), {"clipboard": staticmethod(lambda: clipboard)}))

    window._copy_branch("feat/reconcile")

    assert clipboard.text_ == "feat/reconcile"


def test_a_row_does_not_move_the_working_tree(window, monkeypatch):
    """The whole shape of this feature. Nothing here may reach `git checkout`, and the
    only process the window is allowed to start for it is the one that read the list."""
    def refuse(*args, **kwargs):
        raise AssertionError("the branch menu started a process")

    monkeypatch.setattr("PySide6.QtCore.QProcess.start", refuse)

    _rows(window._branch_menu([Branch("master")]))[0].trigger()
