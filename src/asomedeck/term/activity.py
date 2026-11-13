"""Is the program in this pane busy, or waiting for you?

Spec 14.3 cut this once already. Under docking the app could see that a process was alive
and nothing else, so ``StatusDot`` shipped with LIVE and DEAD and left RUNNING and WAITING
defined but unreachable -- a launcher had to "opt into title_patterns", a mechanism nobody
could build, because reading a foreign window's caption was the only source and it was not
one worth trusting.

Owning the byte stream changes what is observable. The program sets its own title through
OSC 0/2 and we already parse it, so this is the program talking rather than us guessing.

Observed, by running it:

    startup   "✳ Claude Code"
    working   "● What you can do"      (a spinner frame; the glyph changes)
    done      "✳ What you can do"

The rule below is ordered so that each state is claimed for a different reason, and the
weakest claim is the default:

1. Nothing running -- DEAD. Not a guess.
2. The title changed a moment ago -- RUNNING. A program animating its own title is doing
   something, whatever the glyph happens to be. This is the only claim made from
   observation rather than from the program's word, and it is the one that survives a
   spinner set changing between releases.
3. The title has settled and starts with a mark meaning ready -- WAITING. The program
   said so.
4. Anything else -- LIVE. Alive, and no claim about what it is doing.

Rule 2 preceding rule 3 is what makes the ``✳`` reading safe. That glyph appears in
Claude Code's spinner sequence as well as at rest, so a frame of it mid-spin would read as
"waiting" -- except that a frame does not last, and rule 3 only applies to a title that has
been still for ``SETTLE_SECONDS``.
"""

from __future__ import annotations

from ..ui.widgets.status_dot import ATTENTION, DEAD, LIVE, RUNNING, WAITING

#: Leading marks a program uses to say it is ready for input. Observed, not guessed:
#: Claude Code shows this at startup and again when a turn finishes.
IDLE_MARKS: tuple[str, ...] = ("✳",)   # ✳ EIGHT SPOKED ASTERISK

#: How many times a program must rename itself before the rest it settles into counts as
#: a turn *ending* rather than a program starting up ready.
#:
#: The settle rule alone cannot separate the two: Claude Code names itself "✳ Claude Code"
#: once at launch and rests there, which is the same mark, held just as still, as the one a
#: finished turn ends on. What differs is the journey. A spinner renames the pane on every
#: frame; a launch renames it once. Two is the smallest number that tells them apart, and
#: without it restoring a deck with four saved sessions announced four finished turns.
TURN_TITLE_CHANGES = 2

#: How long a title must hold still before it is read as a resting state rather than one
#: frame of an animation. Comfortably longer than a spinner tick and short enough that
#: finishing a turn lights the dot promptly.
SETTLE_SECONDS = 1.5


def state_for(*, alive: bool, title: str, since_title_change: float | None) -> str:
    """One pane's session state.

    ``since_title_change`` is seconds since the program last renamed itself, or None if it
    never has. None is not "a long time ago": a shell that never sets a title is not idle,
    it is simply not saying, which is what LIVE means.
    """
    if not alive:
        return DEAD
    if since_title_change is not None and since_title_change < SETTLE_SECONDS:
        return RUNNING
    if title and title.lstrip().startswith(IDLE_MARKS):
        return WAITING
    return LIVE


def busy(state: str) -> bool:
    """Whether a state counts as work in progress, for the per-deck tally."""
    return state == RUNNING


def displayed(raw: str, *, attention: bool, finished: bool) -> str:
    """What the dot shows: the live reading, overruled by anything left unseen.

    ``state_for`` above answers "what is this pane doing *now*", which is the wrong
    question for a sidebar. A turn that finished while you were in another deck is over
    by the time you look -- the pane is sitting at LIVE, indistinguishable from a shell
    nobody has touched, and the one thing worth telling you has already scrolled past.

    So the two states worth acting on are sticky. They are set by an event, they outrank
    whatever the pane is doing now, and they are cleared by *looking* -- see
    ``MainWindow._observed``. Attention outranks finished because a program that rang the
    bell is asking, where a finished turn is only reporting.

    Both survive DEAD deliberately. A run that rang and then exited is exactly the case you
    would want the sidebar to have kept.

    Note what the last line does to a raw WAITING. Green is earned by a turn *ending*
    unseen, not by a program resting -- and a program rests for as long as you leave it
    there, so passing the raw reading through would keep the dot green forever after you
    had looked, which is the one thing the flags exist to avoid. Once seen, "at a ready
    prompt" is just idle, and idle is blue.
    """
    if attention:
        return ATTENTION
    if finished:
        return WAITING
    return LIVE if raw == WAITING else raw


def rollup(states) -> str:
    """One state for a deck row, from every pane under it.

    The loudest wins, because a collapsed row answers one question: does this need me?
    RUNNING collapses into LIVE here and nowhere else -- across a list of decks the
    difference between "working" and "idle" is noise, since neither is asking for
    anything, and spending a third colour on it would dilute the two that are. Expand the
    deck and the session rows still draw the distinction.
    """
    seen = set(states)
    for state in (ATTENTION, WAITING):
        if state in seen:
            return state
    return LIVE if seen & {RUNNING, LIVE} else DEAD
