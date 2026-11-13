"""What the palette offers, and in what order.

Ranking is a pure function of two strings, so most of this needs no window: the tests
that matter -- that "pane" finds "Close pane" before "Toggle session panel", that a
scattered match never beats a real substring -- are about ordering, and ordering is where
a palette is won or lost. A list whose top row moves between two equally good matches is
one you cannot learn to type blind, which is most of what a palette is for.

The window half is here too, because the failure it guards is this codebase's recurring
one: the title bar has advertised a search chip since the chrome was built, and nothing
anywhere answered it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from asomedeck.commands import by_id, chord_text  # noqa: E402
from asomedeck.ui.palette import Row, rank, score  # noqa: E402


def _row(kind: str, label: str, detail: str = "", search: str = "") -> Row:
    return Row(kind, label, detail, lambda: None, search)


# --- scoring --------------------------------------------------------------------------

def test_an_empty_query_matches_everything():
    """Opening the palette shows what there is, rather than nothing until you type."""
    assert score("", "anything") == 0


@pytest.mark.parametrize("query,text,expected", [
    ("close", "Close pane", 0),          # the thing starts this way
    ("pane", "Close pane", 1),           # a word in it starts this way
    ("ane", "Close pane", 2),            # somewhere inside a word
    ("spd", "Split down", 3),            # the letters, in order
    ("zzz", "Close pane", None),         # not at all
])
def test_the_ranks_are_what_typing_means(query, text, expected):
    assert score(query, text) == expected


def test_case_does_not_matter():
    assert score("CLOSE", "Close pane") == 0
    assert score("close", "CLOSE PANE") == 0


def test_a_word_start_beats_the_middle_of_a_word():
    """The same letters, starting a word or buried in one."""
    assert score("panel", "Session panel") < score("panel", "Subpanel size")


def test_a_real_match_beats_a_scattered_one():
    assert score("sp", "Split right") < score("sp", "Session panel")


# --- ranking --------------------------------------------------------------------------

def test_the_best_match_leads():
    rows = [_row("command", "Session panel size"), _row("command", "Close pane")]
    assert [r.label for r in rank(rows, "pane")] == ["Close pane", "Session panel size"]


def test_what_does_not_match_is_not_shown():
    rows = [_row("command", "Close pane"), _row("deck", "AsomeDeck")]
    assert [r.label for r in rank(rows, "close")] == ["Close pane"]


def test_kinds_break_a_tie_in_a_fixed_order():
    """Commands first: they are the only rows with no other way to reach them."""
    rows = [_row("file", "deck.py"), _row("deck", "deck"), _row("command", "deck"),
            _row("session", "deck")]
    assert [r.kind for r in rank(rows, "deck")] == ["command", "session", "deck", "file"]


def test_the_shorter_label_wins_a_tie():
    """Scoring alone cannot separate these -- "pane" starts a word in both. The shorter
    row is the one where what you typed is more of what the row is."""
    rows = [_row("command", "Toggle session panel"), _row("command", "Close pane")]
    assert [r.label for r in rank(rows, "pane")] == ["Close pane", "Toggle session panel"]


def test_equal_rows_always_come_back_in_the_same_order():
    """And with nothing typed, by name -- not by length. There is no match to be more or
    less of yet, and a hundred files sorted by how short their names are is noise."""
    rows = [_row("file", "beta.py"), _row("file", "Alpha.py"), _row("file", "gamma.py")]
    assert [r.label for r in rank(rows, "")] == ["Alpha.py", "beta.py", "gamma.py"]


def test_a_file_matches_on_its_path_and_shows_its_name():
    """Typing part of a directory has to find the file in it -- otherwise every `test_`
    file in the deck is the same row four times."""
    row = _row("file", "palette.py", "src/asomedeck/ui", search="src/asomedeck/ui/palette.py")
    assert rank([row], "ui/pal") == [row]
    assert rank([row], "palette") == [row]


def test_the_list_is_cut_rather_than_scrolled():
    rows = [_row("file", f"file{n}.py") for n in range(200)]
    assert len(rank(rows, "", limit=50)) == 50


# --- the window's rows ------------------------------------------------------------------

def test_the_palette_offers_the_registry_and_not_a_copy_of_it(window):
    rows = window._palette_rows()
    labels = {row.label for row in rows if row.kind == "command"}

    assert "Split right" in labels
    # Its own row would be a way to reopen what is already open.
    assert by_id("view.palette").label not in labels
    # The chord shown is the action's, so a config rebind reaches the palette for free.
    chords = {row.label: row.detail for row in rows if row.kind == "command"}
    # chord_text, not the literal: the palette shows the chord in the spelling the user
    # presses, which on macOS is the Cmd glyphs Qt maps "Ctrl+..." onto.
    assert chords["Split right"] == chord_text("Ctrl+Shift+D")


def test_a_command_that_does_not_apply_is_not_offered(window):
    """Greying is not enough in a search box: a row you can select and press Enter on had
    better do something."""
    window.deck = None
    window._refresh_commands()
    labels = {row.label for row in window._palette_rows() if row.kind == "command"}
    assert "Change shell…" not in labels


def test_the_open_deck_is_not_offered_as_somewhere_to_go(window, tmp_path):
    second = tmp_path / "second"
    second.mkdir()
    window.db.add_deck(str(second), "gitbash")

    names = {row.label for row in window._palette_rows() if row.kind == "deck"}

    assert names == {"second"}


def test_the_files_of_the_open_deck_are_offered(window, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.txt").write_text("x", encoding="utf-8")
    (tmp_path / "top.txt").write_text("x", encoding="utf-8")

    files = {row.label: row.search for row in window._palette_rows() if row.kind == "file"}

    assert files["top.txt"] == "top.txt"
    assert files["deep.txt"] == "sub/deep.txt"


# --- the overlay ------------------------------------------------------------------------

def test_the_chip_opens_it(window):
    """It was a QLabel with no handler behind it anywhere in the app."""
    window.title_bar.search_chip.click()
    assert window.command_palette.is_open
    window.command_palette.dismiss()


def test_the_chip_names_the_chord_that_works(window):
    """Ctrl+K is readline's kill-line, so the palette does not take it; the chip has to
    say what it actually took."""
    assert window.title_bar.search_chip.text().startswith(chord_text("Ctrl+Shift+K"))


def test_escape_puts_it_away(window):
    window._open_palette()
    QTest.keyClick(window.command_palette.input, Qt.Key.Key_Escape)
    assert not window.command_palette.is_open


def test_typing_filters_and_enter_runs_the_row(window):
    ran = []
    palette = window.command_palette
    palette.open_with([Row("command", "Close pane", "", lambda: ran.append("close")),
                       Row("command", "Split right", "", lambda: ran.append("split"))])

    QTest.keyClicks(palette.input, "close")
    assert palette.list.count() == 1

    QTest.keyClick(palette.input, Qt.Key.Key_Return)
    assert ran == ["close"]
    assert not palette.is_open


def test_the_arrows_move_the_selection_while_the_text_keeps_the_focus(window):
    """The list never takes focus: typing has to go on filtering while the selection
    moves, which is the whole interaction. Shown for real, because focus is one of the
    few things a hidden window genuinely cannot answer for."""
    window.show()
    QTest.qWaitForWindowExposed(window)
    palette = window.command_palette
    palette.open_with([Row("command", "one", "", lambda: None),
                       Row("command", "two", "", lambda: None)])

    QTest.keyClick(palette.input, Qt.Key.Key_Down)

    assert palette.list.currentRow() == 1
    assert palette.input.hasFocus()
    palette.dismiss()


def test_the_selection_wraps(window):
    palette = window.command_palette
    palette.open_with([Row("command", "one", "", lambda: None),
                       Row("command", "two", "", lambda: None)])

    QTest.keyClick(palette.input, Qt.Key.Key_Up)

    assert palette.list.currentRow() == 1
    palette.dismiss()


def test_enter_on_nothing_does_nothing(window):
    """A query that matches no row leaves an empty list, and Enter on it must not raise."""
    palette = window.command_palette
    palette.open_with([Row("command", "one", "", lambda: None)])
    QTest.keyClicks(palette.input, "zzzz")
    QTest.keyClick(palette.input, Qt.Key.Key_Return)
    assert palette.is_open
    palette.dismiss()


def test_it_reopens_with_a_clean_query(window):
    palette = window.command_palette
    palette.open_with([Row("command", "one", "", lambda: None)])
    QTest.keyClicks(palette.input, "zzzz")
    palette.dismiss()

    window._open_palette()

    assert palette.input.text() == ""
    palette.dismiss()


def test_a_click_outside_puts_it_away_and_a_click_on_the_card_does_not(window):
    """A QFrame does not accept presses, so a click on the card's own border reaches the
    backdrop exactly as a click beside it does."""
    palette = window.command_palette
    palette.open_with([Row("command", "one", "", lambda: None)])

    QTest.mousePress(palette, Qt.MouseButton.LeftButton,
                     pos=palette.card.geometry().center())
    assert palette.is_open

    QTest.mousePress(palette, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    assert not palette.is_open

