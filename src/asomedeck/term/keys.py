"""Keystrokes and mouse events, as a shell expects to receive them.

xterm.js did this. Its comment in ``terminal.html`` was that nothing is interpreted in the
renderer because "the pty is the authority on what a key means" -- which is true of the
*shell*, and was never true of the layer above it. Something has to decide that Left is
``ESC [ D`` most of the time and ``ESC O D`` when the program asked for application cursor
keys, and that Ctrl+C is byte 3. That decision is what this module is.

It is deliberately pure: functions from a Qt event to bytes, with the terminal's current
modes passed in rather than read from a screen. That makes every rule here testable
without a window, a pty or a shell, which matters because the rules are numerous, mostly
invisible, and each one is a program that misbehaves if it is wrong.

References are ``xterm``'s behaviour, not the DEC manuals: what matters is matching what
programs actually expect, and what they expect is xterm.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from .. import platform_

#: Keys whose escape sequence changes with DECCKM (application cursor keys). CSI normally,
#: SS3 when set. readline sets it, vim sets it, and getting it backwards is why arrow keys
#: sometimes print ``^[[A`` instead of moving.
_CURSOR_KEYS = {
    Qt.Key.Key_Up: "A",
    Qt.Key.Key_Down: "B",
    Qt.Key.Key_Right: "C",
    Qt.Key.Key_Left: "D",
    Qt.Key.Key_Home: "H",
    Qt.Key.Key_End: "F",
}

#: Fixed ``CSI n ~`` keys.
_TILDE_KEYS = {
    Qt.Key.Key_Insert: 2,
    Qt.Key.Key_Delete: 3,
    Qt.Key.Key_PageUp: 5,
    Qt.Key.Key_PageDown: 6,
    Qt.Key.Key_F5: 15,
    Qt.Key.Key_F6: 17,
    Qt.Key.Key_F7: 18,
    Qt.Key.Key_F8: 19,
    Qt.Key.Key_F9: 20,
    Qt.Key.Key_F10: 21,
    Qt.Key.Key_F11: 23,
    Qt.Key.Key_F12: 24,
}

#: F1-F4 are SS3, not CSI, and always have been. They are the odd ones out.
_SS3_KEYS = {
    Qt.Key.Key_F1: "P",
    Qt.Key.Key_F2: "Q",
    Qt.Key.Key_F3: "R",
    Qt.Key.Key_F4: "S",
}

#: Keys that are simply one control byte.
_SIMPLE = {
    Qt.Key.Key_Return: b"\r",
    Qt.Key.Key_Enter: b"\r",
    Qt.Key.Key_Tab: b"\t",
    Qt.Key.Key_Backtab: b"\x1b[Z",
    Qt.Key.Key_Escape: b"\x1b",
    # DEL, not BS. Unix terminals have sent 0x7f for the backspace key since the VT220,
    # and readline is configured for it; sending 0x08 makes backspace stop erasing.
    Qt.Key.Key_Backspace: b"\x7f",
}

#: Ctrl with these produces the control byte that is not a letter.
_CTRL_PUNCT = {
    Qt.Key.Key_Space: 0x00,
    Qt.Key.Key_BracketLeft: 0x1B,
    Qt.Key.Key_Backslash: 0x1C,
    Qt.Key.Key_BracketRight: 0x1D,
    Qt.Key.Key_AsciiCircum: 0x1E,
    Qt.Key.Key_Underscore: 0x1F,
    Qt.Key.Key_Question: 0x7F,
}

PASTE_START = b"\x1b[200~"
PASTE_END = b"\x1b[201~"


#: The modifier that means "control byte" to a shell, which is not the same enum member on
#: both platforms.
#:
#: Qt on macOS swaps Control and Meta by default: physical **Cmd** arrives as
#: ``ControlModifier`` and physical **Ctrl** as ``MetaModifier``. That swap is worth keeping
#: -- it is what makes every ``"Ctrl+Shift+T"`` in commands.py bind to Cmd+Shift+T and
#: render as ``⌘⇧T`` in the menus, for free, which is what a Mac user expects. Turning it
#: off with AA_MacDontSwapCtrlAndMeta would mean respelling every chord as ``Meta+…`` and
#: shipping an app whose shortcuts are wrong on its own platform.
#:
#: So the swap stays and this file compensates instead, in the one place it matters: the
#: terminal wants the *physical* control key, because Ctrl+C is SIGINT everywhere and has
#: been since before Qt existed. Reading Meta here is what makes physical Ctrl+C interrupt
#: while Cmd+C is left alone to be copy.
CTRL_MOD = (Qt.KeyboardModifier.MetaModifier if platform_.IS_MACOS
            else Qt.KeyboardModifier.ControlModifier)

#: The modifier a *window* shortcut uses: Cmd on macOS, Ctrl elsewhere -- and the same enum
#: member for both, which is the swap working for us rather than against us. ui/term_view.py
#: reads it for copy and paste, so that the clipboard chord is spelt once.
COMMAND_MOD = Qt.KeyboardModifier.ControlModifier


def _modifier_param(modifiers: Qt.KeyboardModifier) -> int:
    """xterm's modifier encoding: 1 + shift(1) + alt(2) + ctrl(4).

    Returns 1 for "no modifiers", which is the value that means the parameter should be
    omitted entirely rather than sent as ``1``.
    """
    value = 1
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        value += 1
    if modifiers & Qt.KeyboardModifier.AltModifier:
        value += 2
    if modifiers & CTRL_MOD:
        value += 4
    return value


def encode_key(key: Qt.Key, modifiers: Qt.KeyboardModifier, text: str, *,
               app_cursor: bool = False) -> bytes | None:
    """The bytes a shell should receive for one key press, or None if we do not handle it.

    None means "not ours" -- the caller should let Qt do whatever it would have done. That
    is how the window's own shortcuts keep working while a terminal has focus.
    """
    # On macOS a Cmd chord is never the terminal's. Windows has no equivalent case: there
    # Ctrl *is* the control key, so this is a no-op guard rather than a branch. Without it
    # an unbound Cmd+T would fall through to the printable-character path at the bottom of
    # this function and type a bare "t" into the shell.
    if platform_.IS_MACOS and modifiers & Qt.KeyboardModifier.ControlModifier:
        return None

    ctrl = bool(modifiers & CTRL_MOD)
    alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)
    shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
    param = _modifier_param(modifiers)

    # Cursor and edit keys, with modifiers where they carry them. `CSI 1 ; 5 C` is
    # Ctrl+Right, which is how word-wise movement reaches readline and vim.
    if key in _CURSOR_KEYS:
        final = _CURSOR_KEYS[key]
        if param != 1:
            return f"\x1b[1;{param}{final}".encode()
        return f"\x1bO{final}".encode() if app_cursor else f"\x1b[{final}".encode()

    if key in _TILDE_KEYS:
        number = _TILDE_KEYS[key]
        if param != 1:
            return f"\x1b[{number};{param}~".encode()
        return f"\x1b[{number}~".encode()

    if key in _SS3_KEYS:
        final = _SS3_KEYS[key]
        if param != 1:
            return f"\x1b[1;{param}{final}".encode()
        return f"\x1bO{final}".encode()

    # Shift+Tab has two spellings. Qt usually folds it into Key_Backtab, but not always --
    # it depends on the platform plugin and on how the event was synthesised -- and the
    # other spelling arrives as Key_Tab still carrying ShiftModifier. Sent as a plain tab
    # it is indistinguishable from Tab, so a program cycling on Shift+Tab (Claude Code's
    # mode switch, a TUI walking focus backwards) sees the forward key instead.
    if key == Qt.Key.Key_Tab and shift:
        return _SIMPLE[Qt.Key.Key_Backtab]

    if key in _SIMPLE:
        data = _SIMPLE[key]
        # Alt+Return, Alt+Backspace (delete previous word) and friends: ESC then the key.
        return b"\x1b" + data if alt else data

    # Ctrl+letter -> 1..26. Derived from the key code rather than event.text(), because on
    # some layouts Qt reports the control character and on others it reports nothing.
    if ctrl and Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
        byte = bytes([key - Qt.Key.Key_A + 1])
        return b"\x1b" + byte if alt else byte

    if ctrl and key in _CTRL_PUNCT:
        byte = bytes([_CTRL_PUNCT[key]])
        return b"\x1b" + byte if alt else byte

    if not text:
        return None

    # Anything else printable. Alt prefixes ESC, which is how Alt+f / Alt+b reach readline
    # as forward-word and backward-word.
    data = text.encode("utf-8")
    if alt:
        return b"\x1b" + data
    if ctrl and not shift:
        # A Ctrl combination we have no mapping for. Swallow it rather than sending the
        # bare character, or Ctrl+1 types "1".
        return None
    return data


def encode_paste(text: str, *, bracketed: bool) -> bytes:
    """Pasted text, wrapped if the program asked for bracketed paste.

    Normalised to CR first. A pasted "\\n" is Enter to a shell, so pasting three lines into
    bash runs two of them whatever we do -- but sending LF where the terminal contract says
    CR makes it worse, not better.
    """
    data = text.replace("\r\n", "\r").replace("\n", "\r").encode("utf-8")
    if not bracketed:
        return data
    # A paste containing the end marker could otherwise close the bracket early and get
    # the rest interpreted as typing.
    data = data.replace(PASTE_END, b"")
    return PASTE_START + data + PASTE_END


def encode_mouse(button: int, col: int, row: int, *, pressed: bool, motion: bool = False,
                 modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
                 sgr: bool = True) -> bytes | None:
    """A mouse event in SGR (1006) form: ``CSI < b ; col ; row M`` for press, ``m`` for release.

    ``col`` and ``row`` are 1-based cell coordinates. Only SGR is emitted: the original
    X10 encoding packs coordinates into single bytes and simply cannot address a column
    past 223, which any full-screen window here is.
    """
    if not sgr:
        return None
    code = button
    if motion:
        code += 32
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        code += 4
    if modifiers & Qt.KeyboardModifier.AltModifier:
        code += 8
    # CTRL_MOD, like everywhere else here: a Ctrl+click inside a pane is the physical Ctrl
    # key, and reporting Cmd+click as one would make Cmd-clicking a link in a TUI behave as
    # though Control were held.
    if modifiers & CTRL_MOD:
        code += 16
    final = "M" if pressed else "m"
    return f"\x1b[<{code};{col};{row}{final}".encode()


#: Wheel events are reported as buttons 64 (up) and 65 (down), always as a press.
MOUSE_WHEEL_UP = 64
MOUSE_WHEEL_DOWN = 65

#: Qt button -> the number xterm uses.
MOUSE_BUTTONS = {
    Qt.MouseButton.LeftButton: 0,
    Qt.MouseButton.MiddleButton: 1,
    Qt.MouseButton.RightButton: 2,
}
