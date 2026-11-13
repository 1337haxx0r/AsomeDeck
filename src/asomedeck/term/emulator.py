"""The terminal itself: VT bytes in, a screen somebody else can paint.

This is the half of the job xterm.js used to do, minus the drawing. ``conpty.py`` owns the
shell and hands us text; this turns that text into a grid of cells with colours and
attributes, and says when it changed. ``ui/term_view.py`` paints the result.

pyte supplies the parser and the screen model, which is the part with the long tail --
CSI, OSC, charsets, margins, tab stops, insert/delete semantics. Two things it does not
supply, and both matter enough to be the reason this module exists rather than being three
lines somewhere else:

**Scrollback.** ``pyte.HistoryScreen`` exists, and its model is *pagination*:
``prev_page()`` and ``next_page()`` move half a screen at a time through a pair of deques.
That does not map onto a wheel or a scrollbar, both of which want a continuous offset. But
the line about to be lost passes through ``index()`` on its way out, so catching it there
costs one reference per scrolled line and gives an ordinary list to index into.

**The alternate screen.** pyte parses ``?1049h``, stores the mode, and does nothing with
it. Nothing at all: ``?1049l`` does not put the primary screen back. So ``vim`` would
paint over the shell's output and still be there after you quit it, with the scrollback
underneath unreachable. Every full-screen program does this -- vim, less, top, git log --
so it is not an edge case, it is Tuesday.

**The sequences it mis-routes.** Not gaps -- a gap would be harmless. pyte's CSI parser
throws away the private marker and the intermediate byte, both of which decide *which*
sequence it is looking at, so a handful of ordinary sequences arrive at the wrong handler
and get executed as something else. ``DeckStream`` filters those out before pyte sees
them; the class docstring has the one that started it.

Throughput is the risk this design accepts. pyte is pure Python, so a shell dumping a
megabyte cannot be fed to it synchronously from the reader thread's signal. Output is
buffered and drained on a timer instead, capped per tick, which turns a flood into a slow
scroll rather than a frozen window.
"""

from __future__ import annotations

import copy
import logging
import re
from collections import deque

import pyte
from PySide6.QtCore import QObject, QTimer, Signal
from pyte.screens import Char, Margins, StaticDefaultDict

log = logging.getLogger(__name__)

#: pyte stores DEC *private* modes shifted five bits left, so ``?1049h`` lands in
#: ``screen.mode`` as ``1049 << 5``. Anything asking "is this mode set" has to agree on
#: that encoding, so it is spelled once here rather than as magic numbers at each site.
PRIVATE_SHIFT = 5


def private(mode: int) -> int:
    """A DEC private mode number, as pyte stores it in ``Screen.mode``."""
    return mode << PRIVATE_SHIFT


#: DECCKM. Arrows send SS3 (``ESC O A``) instead of CSI (``ESC [ A``). Set by readline and
#: by vim, and getting it wrong is why arrow keys print ``^[[A`` in some programs.
MODE_APP_CURSOR = private(1)

#: Bracketed paste. Pasted text is wrapped so the shell can tell it from typing, which is
#: what stops a pasted newline from executing a half-finished command.
MODE_BRACKETED_PASTE = private(2004)

#: Mouse reporting. 1000 is press/release, 1002 adds drag, 1003 adds motion; 1006 changes
#: the *encoding* to SGR, which is the only one that works past column 95.
MODE_MOUSE_BUTTON = private(1000)
MODE_MOUSE_DRAG = private(1002)
MODE_MOUSE_ANY = private(1003)
MODE_MOUSE_SGR = private(1006)
MOUSE_TRACKING_MODES = (MODE_MOUSE_BUTTON, MODE_MOUSE_DRAG, MODE_MOUSE_ANY)

#: Unshifted, because these are matched against the arguments to set_mode/reset_mode
#: rather than against the stored set. 47 is the original, 1047 clears on the way out and
#: 1049 also saves the cursor; all three mean "switch buffers".
ALT_SCREEN_MODES = frozenset({47, 1047, 1049})

DEFAULT_SCROLLBACK = 5000

#: Drain the buffer at roughly frame rate. Feeding pyte per chunk instead would put a pure
#: Python parser on the far end of every 16 KB read the pty produces.
FLUSH_MS = 16

#: Ceiling per drain, sized as a *time* budget rather than a data one.
#:
#: Measured with tools/probe_termview.py: pyte parses plain output at roughly 1.3 MB/s on
#: this machine. So the cap is not "how much data is a lot", it is "how much data fits in
#: one frame" -- at that rate 16 KB is about 12 ms, which leaves the flush inside a 16 ms
#: tick. Picking a round 256 KB instead would have been a 200 ms freeze per tick, i.e. the
#: exact thing the timer exists to prevent, arrived at by a different route.
#:
#: The consequence is a real one and worth stating: a shell dumping tens of megabytes
#: renders at about a megabyte a second. It scrolls rather than freezing, which is the
#: trade a pure-Python parser buys.
MAX_CHARS_PER_FLUSH = 16 * 1024


class DeckScreen(pyte.Screen):
    """A pyte screen that keeps what scrolls off the top and owns an alternate buffer."""

    def __init__(self, columns: int, lines: int,
                 scrollback: int = DEFAULT_SCROLLBACK) -> None:
        #: Lines that have scrolled off, oldest first. These are the same
        #: ``StaticDefaultDict`` rows the live buffer holds, so painting one is identical
        #: to painting the other -- the renderer never learns which it has.
        self.history: deque[StaticDefaultDict[int, Char]] = deque(maxlen=scrollback)
        self._alt = False
        self._saved_buffer: dict | None = None
        self._saved_cursor = None
        #: Set by whoever owns this screen. Plain attributes rather than subclass hooks
        #: because ``pyte.Stream`` binds the screen's methods into a dispatch table when
        #: it attaches -- replacing ``screen.bell`` after that point silently does
        #: nothing, which is a genuinely difficult afternoon.
        self.on_bell = None
        self.on_title = None
        self.on_response = None
        super().__init__(columns, lines)

    @property
    def in_alt_screen(self) -> bool:
        return self._alt

    # --- notifications ---------------------------------------------------------------

    def bell(self, *args) -> None:
        super().bell(*args)
        if self.on_bell is not None:
            self.on_bell()

    def set_title(self, param) -> None:
        super().set_title(param)
        if self.on_title is not None:
            self.on_title(self.title)

    def write_process_input(self, data: str) -> None:
        """Answer the shell when it asks the terminal a question.

        pyte parses these and calls this method to reply; the method it ships is a
        documented no-op, so a terminal that does not override it simply never answers.

        Never answering is not a quiet degradation. ConPTY opens by sending ``CSI c`` --
        "what kind of terminal are you?" -- and then *waits*. Measured against this pane
        before it was wired: the shell went silent for 3.6 seconds, timed out, and only
        then drew its first prompt. Every new pane cost that.

        The same channel carries ``CSI 6 n``, the cursor-position report, which is how
        prompts like starship and oh-my-posh find out where they are, and how programs
        that need to know the cursor column ask.
        """
        if self.on_response is not None:
            self.on_response(data)

    # --- scrollback ------------------------------------------------------------------

    def index(self) -> None:
        """Overloaded to keep the line that is about to fall off the top.

        Only outside a scrolling region and outside the alternate screen. A program that
        set margins is scrolling part of its own display -- vim redrawing a window, less
        moving a page -- and pushing that into scrollback fills it with other people's
        furniture instead of the output you were looking for.
        """
        bottom = (self.margins or Margins(0, self.lines - 1)).bottom
        if self.cursor.y == bottom and self.margins is None and not self._alt:
            self.history.append(self.buffer[0])
        super().index()

    # --- alternate screen ------------------------------------------------------------

    def set_mode(self, *modes: int, **kwargs) -> None:
        if kwargs.get("private") and not self._alt and ALT_SCREEN_MODES.intersection(modes):
            self._enter_alt(save_cursor=1049 in modes)
        super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes: int, **kwargs) -> None:
        if kwargs.get("private") and self._alt and ALT_SCREEN_MODES.intersection(modes):
            self._leave_alt(restore_cursor=1049 in modes)
        super().reset_mode(*modes, **kwargs)

    def _enter_alt(self, save_cursor: bool) -> None:
        self._saved_buffer = dict(self.buffer)
        self._saved_cursor = copy.copy(self.cursor) if save_cursor else None
        self.buffer.clear()
        self.margins = None
        self._alt = True
        self.dirty.update(range(self.lines))

    def _leave_alt(self, restore_cursor: bool) -> None:
        self.buffer.clear()
        if self._saved_buffer is not None:
            self.buffer.update(self._saved_buffer)
        if restore_cursor and self._saved_cursor is not None:
            self.cursor = self._saved_cursor
        self._saved_buffer = None
        self._saved_cursor = None
        self.margins = None
        self._alt = False
        self.dirty.update(range(self.lines))

    def resize(self, lines: int | None = None, columns: int | None = None) -> None:
        """Overloaded so the primary screen does not come back at the old size.

        pyte resizes ``self.buffer``, and while we are in the alternate screen the primary
        one is not in ``self.buffer``. Clipping it the same way pyte clips -- rows off the
        top, columns off the right -- keeps the two consistent enough that leaving vim
        after a resize does not restore a screen wider than the window.
        """
        was_lines, was_columns = self.lines, self.columns
        super().resize(lines, columns)
        if self._saved_buffer is None or (self.lines, self.columns) == (was_lines,
                                                                        was_columns):
            return
        drop = max(0, was_lines - self.lines)
        clipped: dict = {}
        for y, row in self._saved_buffer.items():
            if y < drop:
                continue
            kept = StaticDefaultDict(self.default_char)
            kept.update({x: ch for x, ch in row.items() if x < self.columns})
            clipped[y - drop] = kept
        self._saved_buffer = clipped


#: The two ways a CSI sequence can begin: the seven-bit ``ESC [`` and the eight-bit C1
#: byte, because pyte accepts both and a filter that misses one is a filter with a hole.
CSI_INTRODUCER = re.compile("\x1b\\[|\x9b")

#: Parameter bytes, 0x30-0x3F. The last four are *private markers* and only ever lead:
#: ``?`` is the one pyte understands, and ``< = >`` are the three it drops.
CSI_PARAM = "0123456789:;<=>?"

#: Intermediate bytes, 0x20-0x2F. They appear in DECSCUSR (``CSI 5 SP q``, the cursor
#: shape) and in the DEC request/report pairs (``CSI ? 2026 $ p``). pyte routes none of
#: them, and drops the space specifically.
CSI_INTERMEDIATE = " !\"#$%&'()*+,-./"

#: Anything this long without a final byte is not a sequence that is going to arrive. Past
#: it the held half is handed to pyte and the filter gives up on that one, so a stream of
#: junk cannot grow a buffer here without limit.
CSI_LIMIT = 256

#: What ``CSI > c`` is answered with. Secondary DA is the one query pyte answers *wrongly*
#: rather than not at all -- with the marker dropped it arrives at
#: ``report_device_attributes`` as the primary form and gets a primary reply -- so
#: dropping the sequence silently would trade a wrong answer for no answer, and this
#: module's position on questions the shell blocks on is two screens up. ``0`` is the
#: VT100 family, which is what pyte's primary answer already claims to be.
SECONDARY_DA = "\x1b[>0;0;0c"


def fold_subparams(params: str) -> str:
    """A parameter list with its colon subparameters folded into a form pyte can route.

    Two of these occur in practice: ``4:3``, a curly underline, with ``4:0`` for none; and
    ``38:2::255:0:0``, the ITU spelling of a 24-bit colour. pyte implements neither
    spelling, and because a colon is not a digit its parser *ends the sequence there* and
    draws the remainder -- a literal ``3m`` in the output. So the choice is between the
    nearest thing pyte can express and visible garbage, and the nearest thing wins.
    """
    folded = []
    for param in params.split(";"):
        if ":" not in param:
            folded.append(param)
            continue
        head, *rest = param.split(":")
        if head in ("38", "48"):
            if rest[:1] == ["2"] and len(rest) == 5:
                # The colour-space id, which the colon form carries as a field -- usually
                # empty, sometimes 0 -- and the semicolon form does not have at all. Left
                # in, it shifts r, g and b one place and changes the colour.
                rest = ["2", *rest[2:]]
            folded.append(";".join([head] + [part for part in rest if part]))
        elif head == "4":
            # 4:0 is "no underline". 4:1 through 4:5 are styles -- double, curly, dotted,
            # dashed -- and pyte has one underline to draw them all with.
            folded.append("24" if rest[:1] == ["0"] else "4")
        else:
            # Everything else keeps its code and loses its arguments, which is the only
            # safe shape for a code pyte does not know: it skips unknown codes one at a
            # time, so an underline colour left with its arguments would arrive as a
            # 255 pyte ignores and a 0 it reads as "reset every attribute".
            folded.append(head)
    return ";".join(folded)


class DeckStream(pyte.Stream):
    """pyte's parser, with the CSI forms it mis-routes taken out before it sees them.

    pyte reads CSI parameters and dispatches on the final byte, and in between it discards
    two kinds of byte that decide which sequence it is looking at::

        elif char in SP_OR_GT:
            pass  # Secondary DA is not supported atm.

    ``>`` there is a private parameter marker and the space is an intermediate byte.
    Discarding them does not ignore the sequence, it *rewrites* it into a different one
    that pyte does implement -- and the difference is visible. Claude Code opens by sending
    ``CSI > 4 m``: "stop reporting modified keys the xterm way". With the marker gone that
    is ``CSI 4 m``, underline on, and every character printed after it is underlined in a
    terminal where nothing ever asked for an underline -- until something happens to reset
    attributes, which inside a full-screen program can be never.

    The rule is about routing rather than about coverage, which is what keeps it small: a
    sequence pyte does not implement has to be *ignored*, not executed as a different one.
    What pyte does with the sequences it routes correctly stays pyte's business.
    """

    def __init__(self, screen=None, strict: bool = True) -> None:
        super().__init__(screen, strict)
        #: A sequence the end of a chunk cut in half. Held rather than passed on, because
        #: the half that arrives next carries no introducer for the scan to find it by --
        #: pyte's own state machine would pick it up, which is the problem, not the fix.
        self._held = ""

    def feed(self, data: str) -> None:
        super().feed(self._filtered(data))

    def _filtered(self, data: str) -> str:
        if self._held:
            data, self._held = self._held + data, ""
        out: list[str] = []
        pos = 0
        while (match := CSI_INTRODUCER.search(data, pos)) is not None:
            out.append(data[pos:match.start()])
            text, end = self._sequence(data, match.start(), match.end())
            if end is None:
                held = data[match.start():]
                if len(held) <= CSI_LIMIT:
                    self._held = held
                    return "".join(out)
                out.append(held)  # Not a sequence. pyte can have it and whatever it means.
                return "".join(out)
            out.append(text)
            pos = end
        tail = data[pos:]
        if tail.endswith("\x1b"):
            # The ``[`` is in the next chunk, and the two have to be seen together.
            self._held, tail = "\x1b", tail[:-1]
        return "".join(out) + tail

    def _sequence(self, data: str, start: int, cursor: int) -> tuple[str, int | None]:
        """One CSI sequence: what to pass on in its place, and where it ended.

        An end of ``None`` means the chunk ran out before the final byte did.
        """
        length = len(data)
        params_at = cursor
        while cursor < length and data[cursor] in CSI_PARAM:
            cursor += 1
        params = data[params_at:cursor]
        intermediate_at = cursor
        while cursor < length and data[cursor] in CSI_INTERMEDIATE:
            cursor += 1
        intermediate = data[intermediate_at:cursor]
        if cursor >= length:
            return "", None

        final, end = data[cursor], cursor + 1
        if not ("@" <= final <= "~"):
            # Not a final byte at all. pyte acts on the control characters it allows here
            # and stays in the sequence, so hand the lot over unchanged rather than form a
            # second opinion about malformed input.
            return data[start:end], end
        if intermediate or params[:1] in ("<", "=", ">"):
            if final == "c" and params in (">", ">0"):
                self._report_secondary_device_attributes()
            return "", end
        if ":" in params:
            return data[start:params_at] + fold_subparams(params) + final, end
        return data[start:end], end

    def _report_secondary_device_attributes(self) -> None:
        if self.listener is not None:
            self.listener.write_process_input(SECONDARY_DA)


class Emulator(QObject):
    """One terminal: text in, a paintable screen out.

    Deliberately knows nothing about Qt widgets or about the pty. It is handed text and
    says when the screen changed; who produced the text and who draws the result are both
    somebody else's problem, which is what makes it testable without a window.
    """

    #: The screen changed. Coalesced -- one per drain, not one per escape sequence.
    updated = Signal()
    bell = Signal()
    titleChanged = Signal(str)
    #: A reply the shell is waiting for. Terminals are not write-only: the shell asks
    #: questions and blocks on the answers. See DeckScreen.write_process_input.
    respond = Signal(str)

    def __init__(self, cols: int, rows: int, scrollback: int = DEFAULT_SCROLLBACK,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.screen = DeckScreen(cols, rows, scrollback)
        self.stream = DeckStream(self.screen)
        self._pending: list[str] = []
        self._pending_len = 0

        # Single-shot and restarted rather than periodic: an idle terminal should not wake
        # the CPU sixty times a second for a buffer it knows is empty.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(FLUSH_MS)
        self._timer.timeout.connect(self._flush)

        self._title = ""
        self.screen.on_bell = self.bell.emit
        self.screen.on_title = self._on_title
        self.screen.on_response = self.respond.emit

    def _on_title(self, title: str) -> None:
        if title != self._title:
            self._title = title
            self.titleChanged.emit(title)

    # --- input -----------------------------------------------------------------------

    def feed(self, text: str) -> None:
        """Queue shell output. Parsed on the next drain, never inline.

        Inline would put a pure Python parser directly under the pty reader's signal, so a
        burst would be parsed at whatever rate it arrived -- which is exactly the case
        that needs rate limiting rather than the case that does not need it.
        """
        if not text:
            return
        self._pending.append(text)
        self._pending_len += len(text)
        if not self._timer.isActive():
            self._timer.start()

    def _flush(self) -> None:
        if not self._pending:
            return
        text = "".join(self._pending)
        if len(text) > MAX_CHARS_PER_FLUSH:
            # Keep the tail for the next tick. Splitting mid-escape-sequence is safe:
            # pyte.Stream is a state machine that carries its position across feeds.
            text, rest = text[:MAX_CHARS_PER_FLUSH], text[MAX_CHARS_PER_FLUSH:]
            self._pending = [rest]
            self._pending_len = len(rest)
            self._timer.start()
        else:
            self._pending = []
            self._pending_len = 0

        try:
            self.stream.feed(text)
        except Exception:
            # A malformed sequence must not take the app down. Losing a frame of output is
            # survivable; an exception out of a Qt slot is not -- the crash hook exits.
            log.exception("terminal stream error; %d chars dropped", len(text))
        self.updated.emit()

    def drain(self) -> None:
        """Parse everything queued, right now. For tests and probes, not the UI."""
        while self._pending:
            self._flush()

    # --- geometry --------------------------------------------------------------------

    def resize(self, cols: int, rows: int) -> None:
        if (cols, rows) == (self.screen.columns, self.screen.lines):
            return
        self.screen.resize(rows, cols)
        self.updated.emit()

    # --- queries the renderer and the key encoder ask -------------------------------

    @property
    def app_cursor_keys(self) -> bool:
        return MODE_APP_CURSOR in self.screen.mode

    @property
    def bracketed_paste(self) -> bool:
        return MODE_BRACKETED_PASTE in self.screen.mode

    @property
    def mouse_tracking(self) -> bool:
        return any(m in self.screen.mode for m in MOUSE_TRACKING_MODES)

    @property
    def mouse_sgr(self) -> bool:
        return MODE_MOUSE_SGR in self.screen.mode

    @property
    def reports_motion(self) -> bool:
        return MODE_MOUSE_DRAG in self.screen.mode or MODE_MOUSE_ANY in self.screen.mode

    @property
    def history(self) -> deque:
        return self.screen.history

    def clear_history(self) -> None:
        """Throw away the scrollback, keeping what is on screen.

        Here rather than in the view so the renderer never touches pyte's containers
        directly -- everything else it needs from the screen is a property on this class,
        and a caller that reaches past them is a caller that breaks when the parser does.
        """
        self.screen.history.clear()

    def line(self, index: int):
        """Row ``index`` counting from the oldest scrollback line.

        History and live rows are the same kind of object, so the renderer walks one
        continuous range and never asks which side of the boundary it is on.
        """
        history = self.screen.history
        if index < len(history):
            return history[index]
        return self.screen.buffer[index - len(history)]

    @property
    def total_lines(self) -> int:
        return len(self.screen.history) + self.screen.lines

    def text_for(self, top: int, bottom: int) -> str:
        """Plain text for an inclusive range of absolute rows. Used by selection."""
        out = []
        for index in range(max(0, top), min(self.total_lines, bottom + 1)):
            row = self.line(index)
            out.append("".join(row[x].data for x in range(self.screen.columns)).rstrip())
        return "\n".join(out)
