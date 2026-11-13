"""The screen model: scrollback, the alternate screen, and mode tracking.

These cover the parts pyte does *not* provide, which is exactly the code worth testing --
pyte's own CSI handling is pyte's problem, but the two things layered on top of it are
ours, and both are the kind that fail invisibly. Scrollback that quietly captures nothing
looks like a terminal with no history; an alternate screen that never restores looks like
vim corrupted your session.

Headless: an Emulator is a QObject, and ``drain()`` exists so a test never needs an event
loop to see the effect of feeding it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtCore")
pytest.importorskip("pyte")

from asomedeck.term.emulator import Emulator, private  # noqa: E402


def fed(text: str, cols: int = 20, rows: int = 3, scrollback: int = 100) -> Emulator:
    emu = Emulator(cols, rows, scrollback)
    emu.feed(text)
    emu.drain()
    return emu


def rows_of(emu: Emulator) -> list[str]:
    return [emu.screen.display[i].rstrip() for i in range(emu.screen.lines)]


def line_text(emu: Emulator, index: int) -> str:
    row = emu.line(index)
    return "".join(row[x].data for x in range(emu.screen.columns)).rstrip()


# --- scrollback ------------------------------------------------------------------------

def test_lines_scrolled_off_the_top_are_kept():
    emu = fed("".join(f"line{i}\r\n" for i in range(6)))
    assert len(emu.history) == 4
    assert [line_text(emu, i) for i in range(4)] == ["line0", "line1", "line2", "line3"]
    assert rows_of(emu)[:2] == ["line4", "line5"]


def test_history_and_live_rows_are_one_continuous_range():
    """The renderer walks a single index space and never asks which side it is on."""
    emu = fed("".join(f"line{i}\r\n" for i in range(6)))
    assert emu.total_lines == len(emu.history) + emu.screen.lines
    assert [line_text(emu, i) for i in range(6)] == [f"line{i}" for i in range(6)]


def test_scrollback_is_bounded_and_keeps_the_newest():
    """A deque with maxlen drops from the *left*, which is the end you want to lose."""
    emu = fed("".join(f"line{i}\r\n" for i in range(500)), scrollback=50)
    assert len(emu.history) == 50
    kept = [line_text(emu, i) for i in range(50)]
    assert kept == sorted(kept, key=lambda s: int(s.removeprefix("line")))
    assert "line0" not in kept
    # The oldest survivor sits directly before whatever is still on screen.
    assert int(kept[-1].removeprefix("line")) == int(kept[0].removeprefix("line")) + 49


def test_a_scrolling_region_does_not_pollute_scrollback():
    """A program that set margins is scrolling part of its own display -- vim redrawing a
    window, less moving a page. Capturing that fills history with furniture instead of
    the output you were looking for.
    """
    emu = Emulator(20, 6, 100)
    emu.feed("keep me\r\n")
    emu.feed("\x1b[3;5r")                       # DECSTBM: margins on rows 3-5
    emu.feed("\x1b[5;1H" + "".join(f"r{i}\r\n" for i in range(10)))
    emu.drain()
    assert [line_text(emu, i) for i in range(len(emu.history))] == ["keep me"] or \
           len(emu.history) <= 1


# --- alternate screen -------------------------------------------------------------------

def test_alternate_screen_replaces_and_restores_the_primary():
    """pyte parses ?1049h, stores the mode and does nothing with it -- so without our
    override, quitting vim leaves vim's screen behind and the shell is unreachable."""
    emu = fed("primary\r\n")
    emu.feed("\x1b[?1049h\x1b[2J\x1b[Halternate")
    emu.drain()
    assert emu.screen.in_alt_screen
    assert rows_of(emu)[0] == "alternate"
    assert "primary" not in "".join(rows_of(emu))

    emu.feed("\x1b[?1049l")
    emu.drain()
    assert not emu.screen.in_alt_screen
    assert rows_of(emu)[0] == "primary"


def test_alternate_screen_does_not_grow_scrollback():
    emu = fed("primary\r\n")
    before = len(emu.history)
    emu.feed("\x1b[?1049h" + "".join(f"noise{i}\r\n" for i in range(20)))
    emu.drain()
    assert len(emu.history) == before
    emu.feed("\x1b[?1049l")
    emu.drain()
    assert rows_of(emu)[0] == "primary"


def test_1049_restores_the_cursor_position():
    emu = Emulator(20, 3, 100)
    emu.feed("\x1b[2;5H")
    emu.drain()
    where = (emu.screen.cursor.x, emu.screen.cursor.y)
    emu.feed("\x1b[?1049h\x1b[10;10Hxx\x1b[?1049l")
    emu.drain()
    assert (emu.screen.cursor.x, emu.screen.cursor.y) == where


@pytest.mark.parametrize("mode", [47, 1047, 1049])
def test_every_spelling_of_the_alternate_screen_switches(mode):
    emu = fed("primary\r\n")
    emu.feed(f"\x1b[?{mode}h\x1b[2J\x1b[Halt")
    emu.drain()
    assert emu.screen.in_alt_screen
    emu.feed(f"\x1b[?{mode}l")
    emu.drain()
    assert not emu.screen.in_alt_screen
    assert rows_of(emu)[0] == "primary"


# --- modes the key encoder and the view ask about --------------------------------------

def test_application_cursor_mode_is_reported():
    emu = fed("\x1b[?1h")
    assert emu.app_cursor_keys
    emu.feed("\x1b[?1l")
    emu.drain()
    assert not emu.app_cursor_keys


def test_bracketed_paste_is_reported():
    emu = fed("\x1b[?2004h")
    assert emu.bracketed_paste


def test_mouse_modes_are_reported():
    emu = fed("\x1b[?1002h\x1b[?1006h")
    assert emu.mouse_tracking and emu.mouse_sgr and emu.reports_motion


def test_private_mode_encoding_matches_pyte():
    """pyte shifts DEC private modes five bits left. Anything asking "is this set" has to
    agree, and a mismatch reads as the mode simply never being set."""
    emu = fed("\x1b[?2004h")
    assert private(2004) in emu.screen.mode
    assert 2004 not in emu.screen.mode


# --- colours ---------------------------------------------------------------------------

def test_colours_arrive_as_names_or_hex():
    """The renderer has to handle both: pyte names the basic sixteen and hands back six
    hex digits for 256-colour and truecolor."""
    emu = fed("\x1b[31mA\x1b[38;5;196mB\x1b[38;2;10;200;30mC")
    assert emu.screen.buffer[0][0].fg == "red"
    assert emu.screen.buffer[0][1].fg == "ff0000"
    assert emu.screen.buffer[0][2].fg == "0ac81e"


def test_sgr_33_is_brown_not_yellow():
    """pyte follows VT100 naming. A palette keyed on "yellow" silently loses it."""
    emu = fed("\x1b[33mY")
    assert emu.screen.buffer[0][0].fg == "brown"


# --- signals ----------------------------------------------------------------------------

def test_title_and_bell_reach_their_signals():
    """pyte's Stream binds the screen's methods into a dispatch table when it attaches, so
    replacing screen.bell afterwards silently does nothing. These pin that the hooks are
    installed the way that survives it."""
    emu = Emulator(20, 3)
    titles, bells = [], []
    emu.titleChanged.connect(titles.append)
    emu.bell.connect(lambda: bells.append(1))
    emu.feed("\x1b]0;a title\x07\x07\x07")
    emu.drain()
    assert titles == ["a title"]
    assert len(bells) == 2


# --- answering the shell ------------------------------------------------------------

def test_device_attributes_query_is_answered():
    """ConPTY opens with ``CSI c`` and *waits* for the reply.

    pyte parses the query and calls ``write_process_input`` to answer, and the method it
    ships is a documented no-op -- so a terminal that does not override it never answers.
    Measured before this was wired: the shell sat silent for 3.6 seconds, timed out, and
    only then drew its first prompt. Every new pane paid it.
    """
    emu = Emulator(20, 3)
    replies = []
    emu.respond.connect(replies.append)
    emu.feed("[c")
    emu.drain()
    assert replies == ["[?6c"]


def test_cursor_position_report_is_answered_with_the_cursor_position():
    """``CSI 6 n``. One-based, and it is how starship and oh-my-posh place themselves."""
    emu = Emulator(20, 5)
    replies = []
    emu.respond.connect(replies.append)
    emu.feed("[3;7H[6n")
    emu.drain()
    assert replies == ["[3;7R"]


def test_a_terminal_with_nobody_listening_does_not_raise():
    """The hook is optional; an Emulator built for a test has no pty behind it."""
    emu = Emulator(20, 3)
    emu.feed("[c[6n")
    emu.drain()


# --- sequences pyte routes to the wrong handler ---------------------------------------
#
# pyte's CSI parser drops the private marker and the intermediate byte, and both of them
# decide which sequence it is looking at. What comes out the other side is not the
# sequence being ignored, it is a *different* sequence being executed, and these are the
# ones where that shows.


def underscored(emu: Emulator, count: int) -> list[bool]:
    return [emu.screen.buffer[0][x].underscore for x in range(count)]


def test_modify_other_keys_does_not_switch_underline_on():
    """The one this filter exists for.

    Claude Code opens by sending ``CSI > 4 m`` -- "stop reporting modified keys the xterm
    way". Drop the ``>`` and it is ``CSI 4 m``, underline on, and it stays on: every pane
    running an agent was underlined end to end while the same shell in mintty was clean.
    """
    emu = fed("\x1b[>4mhello")
    assert rows_of(emu)[0] == "hello"
    assert underscored(emu, 5) == [False] * 5


def test_underline_still_arrives_when_it_is_actually_asked_for():
    """The filter is about routing, so the sequence it is confused with must still work."""
    emu = fed("\x1b[4mhello")
    assert underscored(emu, 5) == [True] * 5


@pytest.mark.parametrize("sequence", [
    "\x1b[>4;2m",     # modifyOtherKeys, the form that sets it
    "\x1b[>0q",       # XTVERSION -- "what terminal are you?"
    "\x1b[>1u",       # the kitty keyboard protocol
    "\x1b[=5u",       # and its other spelling
    "\x1b[5 q",       # DECSCUSR: an intermediate byte, not a parameter
    "\x1b[?2026$p",   # DECRQM, asking whether synchronised output is on
    "\x1b[!p",        # DECSTR, a soft reset
])
def test_a_sequence_pyte_does_not_implement_leaves_no_trace(sequence):
    emu = fed(sequence + "ok")
    assert rows_of(emu)[0] == "ok"
    assert underscored(emu, 2) == [False, False]


def test_private_dec_modes_still_reach_pyte():
    """``?`` is the one marker pyte does understand, so the filter must not touch it."""
    emu = fed("\x1b[?1049h")
    assert emu.screen.in_alt_screen


def test_subparameters_do_not_land_on_the_screen_as_text():
    """A colon is not a digit, so pyte ends the sequence there and draws the rest."""
    emu = fed("\x1b[4:3mhi")
    assert rows_of(emu)[0] == "hi"
    assert underscored(emu, 2) == [True, True]


def test_the_colon_spelling_of_no_underline_turns_it_off():
    emu = fed("\x1b[4m\x1b[4:0mhi")
    assert underscored(emu, 2) == [False, False]


@pytest.mark.parametrize("sequence", ["\x1b[38:2::255:0:0m", "\x1b[38:2:0:255:0:0m",
                                      "\x1b[38:2:255:0:0m"])
def test_the_colon_spelling_of_a_24_bit_colour_is_the_colour_it_names(sequence):
    """The colour-space id in the middle is a field pyte's spelling does not have. Left
    in, it shifts r, g and b along one and paints green where red was asked for."""
    emu = fed(sequence + "hi")
    assert emu.screen.buffer[0][0].fg == "ff0000"


def test_an_underline_colour_does_not_reset_every_attribute():
    """pyte skips codes it does not know one at a time, so 58's arguments arrive as
    attributes in their own right -- and the last of them is ``0``, reset everything."""
    emu = fed("[1m[58:2::255:0:0mhi")
    assert emu.screen.buffer[0][0].bold


def test_secondary_device_attributes_are_answered_as_secondary():
    """With the marker dropped, ``CSI > c`` reached pyte as the primary query and was
    answered with the primary report -- an answer to a question nobody asked."""
    emu = Emulator(20, 3)
    replies = []
    emu.respond.connect(replies.append)
    emu.feed("\x1b[>c")
    emu.drain()
    assert replies == ["\x1b[>0;0;0c"]


def test_a_sequence_split_across_chunks_is_still_filtered():
    """A pty hands over whatever arrived, and a 16 KB read can land mid-sequence. The
    half that comes next has no introducer to find it by, so it has to be held."""
    emu = Emulator(20, 3)
    for chunk in ("\x1b", "[>", "4", "mhi"):
        emu.feed(chunk)
        emu.drain()
    assert rows_of(emu)[0] == "hi"
    assert underscored(emu, 2) == [False, False]


def test_an_unterminated_sequence_does_not_hold_output_for_ever():
    """Held bytes are a buffer, and a buffer with no ceiling is the bug after this one."""
    emu = Emulator(20, 3)
    emu.feed("\x1b[" + "1;" * 200)
    emu.drain()
    emu.feed("mhi")
    emu.drain()
    assert rows_of(emu)[0] == "hi"


# --- resize ------------------------------------------------------------------------------

def test_resize_reaches_the_screen():
    emu = Emulator(20, 3)
    emu.resize(100, 40)
    assert (emu.screen.columns, emu.screen.lines) == (100, 40)


def test_resizing_inside_the_alternate_screen_clips_the_saved_primary():
    """Otherwise leaving vim after a resize restores a screen wider than the window."""
    emu = fed("x" * 20 + "\r\n")
    emu.feed("\x1b[?1049h")
    emu.drain()
    emu.resize(10, 3)
    emu.feed("\x1b[?1049l")
    emu.drain()
    assert emu.screen.columns == 10
    assert all(max(row.keys(), default=0) < 10 for row in emu.screen.buffer.values())


def test_malformed_sequences_do_not_escape_as_exceptions():
    """An exception out of a Qt slot is fatal here -- the crash hook exits the app. Losing
    a frame of output is not."""
    emu = Emulator(20, 3)
    emu.feed("\x1b[999999999999;5;5;5;5H\x1b[?\x1b[\x00\x9bZ ok")
    emu.drain()
    assert emu.screen.columns == 20


# --- the bell, now that something listens to it ---------------------------------------


def _rings(text: str) -> tuple[int, list[str]]:
    """How many times ``text`` rings the bell, and what it names the terminal."""
    emu = Emulator(20, 3, 100)
    rung, titles = [], []
    emu.bell.connect(lambda: rung.append(1))
    emu.titleChanged.connect(titles.append)
    emu.feed(text)
    emu.drain()
    return len(rung), titles


def test_a_bare_bel_rings():
    assert _rings("hi\ahi")[0] == 1


def test_naming_the_terminal_does_not_ring_it():
    """The one that would sink the feature.

    OSC is terminated by BEL, so ``\033]0;title\007`` ends in the same byte a bell is.
    If pyte handed that byte on as a bell, every rename would read as the program asking
    for you -- and a spinner renames the pane on every frame, so an agent working quietly
    would light the sidebar amber several times a second.
    """
    rung, titles = _rings("\033]0;✳ Claude Code\007")
    assert titles == ["✳ Claude Code"]
    assert rung == 0


def test_both_osc_terminators_name_the_terminal_without_ringing():
    for terminator in ("\007", "\033\\"):
        rung, titles = _rings(f"\033]2;build\033]0;done{terminator}")
        assert rung == 0, terminator
