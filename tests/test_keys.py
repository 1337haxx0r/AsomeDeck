"""What a key press means, in bytes.

These are the rules xterm.js used to apply for us, and they are the kind that fail
silently: nothing crashes when Ctrl+Right sends the wrong sequence, a program just stops
moving by words and nobody can say when it started. Each case here is a program that
misbehaves if the rule is wrong, so the docstrings name it.

Headless on purpose. ``Qt.Key`` and ``Qt.KeyboardModifier`` are QtCore enums and the
encoder is pure functions over them, so none of this needs a window.
"""

from __future__ import annotations

import pytest

QtCore = pytest.importorskip("PySide6.QtCore")
Qt = QtCore.Qt

from asomedeck.term import keys  # noqa: E402

NONE = Qt.KeyboardModifier.NoModifier
#: The modifier that means "control byte" *here*, which is not the same enum member on both
#: platforms -- Qt on macOS reports the physical Ctrl key as Meta, having given Control to
#: Cmd. Taken from the encoder rather than restated, so these cases keep asking their real
#: question ("does the control key produce the control byte") instead of quietly becoming a
#: test of Cmd on one platform and Ctrl on the other.
CTRL = keys.CTRL_MOD
#: What the window's shortcuts use: Cmd on macOS, Ctrl elsewhere. Only interesting on macOS,
#: where it is the modifier the terminal must *not* encode.
COMMAND = keys.COMMAND_MOD
ALT = Qt.KeyboardModifier.AltModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


def encode(key, mods=NONE, text="", **kwargs):
    return keys.encode_key(key, mods, text, **kwargs)


# --- control characters ----------------------------------------------------------------

@pytest.mark.parametrize("key,expected", [
    (Qt.Key.Key_A, b"\x01"),
    (Qt.Key.Key_C, b"\x03"),   # SIGINT. The single most important byte here.
    (Qt.Key.Key_D, b"\x04"),   # EOF.
    (Qt.Key.Key_R, b"\x12"),   # reverse-i-search.
    (Qt.Key.Key_Z, b"\x1a"),
])
def test_ctrl_letters_are_their_control_bytes(key, expected):
    assert encode(key, CTRL) == expected


def test_ctrl_space_is_nul():
    """Sends NUL, which is set-mark in readline and in emacs."""
    assert encode(Qt.Key.Key_Space, CTRL, " ") == b"\x00"


def test_backspace_sends_del_not_backspace():
    """0x7f, not 0x08.

    Unix terminals have sent DEL for the backspace key since the VT220 and readline is
    configured for it. Sending 0x08 makes backspace stop erasing, which looks like the
    key is broken rather than like a protocol mistake.
    """
    assert encode(Qt.Key.Key_Backspace) == b"\x7f"


def test_unmapped_ctrl_combination_is_swallowed():
    """Ctrl+1 must not type "1"."""
    assert encode(Qt.Key.Key_1, CTRL, "1") is None


# --- cursor keys -----------------------------------------------------------------------

@pytest.mark.parametrize("key,csi,ss3", [
    (Qt.Key.Key_Up, b"\x1b[A", b"\x1bOA"),
    (Qt.Key.Key_Down, b"\x1b[B", b"\x1bOB"),
    (Qt.Key.Key_Right, b"\x1b[C", b"\x1bOC"),
    (Qt.Key.Key_Left, b"\x1b[D", b"\x1bOD"),
])
def test_arrows_follow_application_cursor_mode(key, csi, ss3):
    """DECCKM. Both directions matter: readline sets it, and plain output does not.

    Getting this backwards is why arrow keys sometimes print ``^[[A`` instead of
    recalling the previous command.
    """
    assert encode(key, app_cursor=False) == csi
    assert encode(key, app_cursor=True) == ss3


def test_modified_arrows_ignore_application_cursor_mode():
    """Ctrl+Right is ``CSI 1 ; 5 C`` whether or not DECCKM is set -- that is how
    word-wise movement reaches readline and vim."""
    assert encode(Qt.Key.Key_Right, CTRL) == b"\x1b[1;5C"
    assert encode(Qt.Key.Key_Right, CTRL, app_cursor=True) == b"\x1b[1;5C"
    assert encode(Qt.Key.Key_Left, SHIFT) == b"\x1b[1;2D"
    assert encode(Qt.Key.Key_Up, ALT) == b"\x1b[1;3A"


def test_home_and_end():
    assert encode(Qt.Key.Key_Home) == b"\x1b[H"
    assert encode(Qt.Key.Key_End, app_cursor=True) == b"\x1bOF"


# --- editing and function keys ---------------------------------------------------------

@pytest.mark.parametrize("key,expected", [
    (Qt.Key.Key_Delete, b"\x1b[3~"),
    (Qt.Key.Key_PageUp, b"\x1b[5~"),
    (Qt.Key.Key_PageDown, b"\x1b[6~"),
    (Qt.Key.Key_Insert, b"\x1b[2~"),
])
def test_tilde_keys(key, expected):
    assert encode(key) == expected


def test_f1_to_f4_are_ss3_and_the_rest_are_csi():
    """The one irregularity in the function-key table, and it is load-bearing."""
    assert encode(Qt.Key.Key_F1) == b"\x1bOP"
    assert encode(Qt.Key.Key_F4) == b"\x1bOS"
    assert encode(Qt.Key.Key_F5) == b"\x1b[15~"
    assert encode(Qt.Key.Key_F12) == b"\x1b[24~"


def test_shift_tab_is_backtab_in_both_spellings():
    """Qt usually folds Shift+Tab into Key_Backtab -- but not always, and the other
    spelling keeps Key_Tab and carries ShiftModifier. Encoded as a plain tab it is
    indistinguishable from Tab, so a program that cycles on Shift+Tab -- Claude Code's
    mode switch, a TUI walking focus backwards -- sees the forward key instead.
    """
    assert encode(Qt.Key.Key_Backtab) == b"\x1b[Z"
    assert encode(Qt.Key.Key_Backtab, SHIFT) == b"\x1b[Z"
    assert encode(Qt.Key.Key_Tab, SHIFT) == b"\x1b[Z"
    # And plain Tab must stay plain, or shell completion breaks.
    assert encode(Qt.Key.Key_Tab) == b"\t"


# --- alt as a prefix -------------------------------------------------------------------

def test_alt_prefixes_escape():
    """Alt+f and Alt+b are forward-word and backward-word in readline, and they arrive
    as ESC followed by the letter."""
    assert encode(Qt.Key.Key_F, ALT, "f") == b"\x1bf"
    assert encode(Qt.Key.Key_B, ALT, "b") == b"\x1bb"


def test_alt_backspace_deletes_a_word():
    assert encode(Qt.Key.Key_Backspace, ALT) == b"\x1b\x7f"


def test_plain_text_passes_through_as_utf8():
    assert encode(Qt.Key.Key_A, NONE, "a") == b"a"
    assert encode(Qt.Key.Key_Odiaeresis, NONE, "ö") == "ö".encode()


# --- paste -----------------------------------------------------------------------------

def test_paste_is_wrapped_only_when_the_program_asked():
    assert keys.encode_paste("ls", bracketed=False) == b"ls"
    assert keys.encode_paste("ls", bracketed=True) == b"\x1b[200~ls\x1b[201~"


def test_paste_normalises_newlines_to_carriage_returns():
    """A shell's line terminator is CR. Sending LF leaves the command unsubmitted."""
    assert keys.encode_paste("a\r\nb\nc", bracketed=False) == b"a\rb\rc"


def test_paste_cannot_close_its_own_bracket():
    """Pasted text containing the end marker would otherwise end bracketed mode early and
    get the remainder interpreted as typing."""
    hostile = "safe\x1b[201~rm -rf /"
    out = keys.encode_paste(hostile, bracketed=True)
    assert out.count(b"\x1b[201~") == 1
    assert out.endswith(b"\x1b[201~")


# --- mouse -----------------------------------------------------------------------------

def test_mouse_uses_sgr_encoding():
    """SGR (1006) only. The original X10 encoding packs a coordinate into one byte and
    cannot address a column past 223, which any full-screen pane here is."""
    assert keys.encode_mouse(0, 12, 3, pressed=True) == b"\x1b[<0;12;3M"
    assert keys.encode_mouse(0, 12, 3, pressed=False) == b"\x1b[<0;12;3m"


def test_mouse_encodes_modifiers_and_far_columns():
    assert keys.encode_mouse(0, 240, 60, pressed=True, modifiers=CTRL) == b"\x1b[<16;240;60M"


def test_mouse_without_sgr_is_refused_rather_than_guessed():
    assert keys.encode_mouse(0, 1, 1, pressed=True, sgr=False) is None


# --- the platform's own modifier layout ------------------------------------------------

def test_the_control_key_is_the_physical_one():
    """Ctrl+C must be SIGINT on every platform, whatever Qt calls the key.

    The regression this guards is specific and bad: with Qt's macOS Ctrl/Cmd swap left
    uncompensated, ``Cmd+C`` encodes as ``\x03`` -- so the standard copy chord interrupts
    the foreground job instead of copying, and the actual Ctrl key does nothing at all.
    """
    assert encode(Qt.Key.Key_C, CTRL) == b"\x03"


@pytest.mark.skipif(keys.CTRL_MOD == keys.COMMAND_MOD,
                    reason="only macOS separates the command key from the control key")
def test_the_command_key_is_never_the_terminals():
    """A Cmd chord belongs to the window, so the encoder declines every one of them.

    None means "not ours" and sends the event on to Qt, which is what lets Cmd+C reach the
    clipboard handler and Cmd+Shift+T reach the window action. The case that made this
    explicit rather than incidental is an *unbound* chord: without the guard, Cmd+T fell
    through to the printable-character branch and typed a bare "t" into the shell.
    """
    assert encode(Qt.Key.Key_C, COMMAND, "c") is None
    assert encode(Qt.Key.Key_T, COMMAND, "t") is None
    assert encode(Qt.Key.Key_A, COMMAND, "a") is None


def test_a_bare_letter_is_still_itself():
    """The guard above must not swallow ordinary typing."""
    assert encode(Qt.Key.Key_A, NONE, "a") == b"a"
