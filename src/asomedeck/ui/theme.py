"""AsomeDeck theme - port of the AsomeDeck Workspace design system (spec 14.4).

Single source of truth for colour. Tokens are verbatim from the DS THEMES map; no hex
literal belongs anywhere else in the app.

Usage::

    theme = Theme("dark", accent=ACCENTS[0])
    load_fonts()                      # after QApplication exists, before any QSS
    app.setPalette(theme.palette())   # defaults, so no bare QWidget rule is needed
    app.setStyleSheet(theme.qss())
    painter.fillRect(rect, theme.c("panel"))   # delegates read tokens directly

Four things in the DS do not survive a naive port, and are handled here rather than
rediscovered later. Each was measured against PySide6 6.11.2, not assumed:

* ``text-transform`` is not a Qt stylesheet property. It parses and is silently dropped,
  so uppercase must happen in Python -- see ``ui/widgets/labels.py``. (``letter-spacing``
  *is* supported and is used below.)
* Qt rounds a fractional QSS ``font-size`` half-up (``11.5px`` becomes 12), while Python's
  ``round`` rounds half-to-even (``round(14.5)`` is 14). Left alone the same token renders
  at different sizes depending on which path styled the widget, so ``font`` rounds half-up
  to match QSS.
* ``QFont.PercentageSpacing`` is a percentage of character advance, not of the em. For
  Roboto Mono at 11px the DS ``.18em`` came out at 1.215px per character instead of 1.98 --
  about 61% of the intended tracking. ``AbsoluteSpacing`` lands on it exactly.
* A focus ring must not be a border that appears and disappears. That changes the content
  rect and resizes the embedded terminal on every focus change; on a console host it is a
  character-cell reflow per click. Rings here are always-present borders that change
  colour.
"""

from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass
from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

from .. import platform_, resources

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Tokens - verbatim from the design system
# --------------------------------------------------------------------------

TOKENS: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#131313", "panel": "#191919", "term": "#0e0e0e", "status": "#171717",
        "border": "#292929", "border2": "#242424", "chip": "#303030",
        "borderStrong": "#454545", "tab": "#1f1f1f", "hover": "#242424", "sel": "#242424",
        "text": "#e9e9e7", "dim2": "#c2c2bf", "dim": "#8f8f8c",
        "dimmer": "#858582", "label": "#6d6d6a",
        "add": "#7fae6a", "del": "#c07064", "tool": "#7ba3b5",
        "warn": "#d0a555",
        "onAccent": "#0d1114", "clean": "#777774",
        "titlebar": "#282828",
    },
    "light": {
        "bg": "#f4f1e9", "panel": "#eae5d9", "term": "#fbf9f3", "status": "#efeae0",
        "border": "#d8d0be", "border2": "#e2dbcb", "chip": "#d3cab6",
        "borderStrong": "#b9ae97", "tab": "#e3ddcd", "hover": "#e6e0d1", "sel": "#e0d9c6",
        "text": "#211d15", "dim2": "#4a4335", "dim": "#6b6353",
        "dimmer": "#7c7361", "label": "#7c7361",
        "add": "#4c7a2c", "del": "#a63f28", "tool": "#3a6a80",
        "warn": "#8a6410",
        "onAccent": "#fbf9f3", "clean": "#7c7361",
        "titlebar": "#f8f5ee",
    },
}

#: `titlebar` is the one token not in the DS map. The design gives the title bar the
#: same `panel` as the sidebar, which reads as one continuous slab; lifting it separates
#: the window chrome from the content beneath it.
#:
#: Lifted again since -- dark went #202020 -> #282828, light #f2eee4 -> #f8f5ee. One step
#: was not enough to read as chrome against `panel` (#191919) next to it, and in the light
#: theme the bar was actually *darker* than the window behind it, which inverted the
#: relationship the dark theme establishes.

#: Accent options exposed in the DS prop editor. Index 0 is the default.
#:
#: The steel blue leads rather than the amber it used to. The other three stay, because
#: this list is now reachable: it is the palette the per-deck swatch row offers.
ACCENTS = ["#7fa6b8", "#e5a83f", "#8ab06a", "#c08fd0"]

#: Each accent's light-theme twin, and the reason `accent` cannot be one value.
#:
#: The light `bg` is a cream at 0.88 relative luminance, so every value above lands near
#: 2:1 against it -- ample for a filled button, invisible as the 3px sidebar rail or a 1px
#: focus ring. Each twin is the same hue with the value dropped until it clears 3:1, which
#: is exactly where the amber pair already sat before this map existed. Two of them are
#: tokens the DS already carries at both ends: `tool` and `add`.
ACCENT_LIGHT = {
    "#7fa6b8": "#3a6a80",   # the light `tool` token; dark's `tool` is #7ba3b5
    "#e5a83f": "#a9761a",   # what ACCENT_LIGHT_DEFAULT was, when amber led
    "#8ab06a": "#4c7a2c",   # the light `add` token
    "#c08fd0": "#7a4a8c",
}
ACCENT_LIGHT_DEFAULT = ACCENT_LIGHT[ACCENTS[0]]

#: `onAccent` is the one token whose two modes are not the same idea, and that is not an
#: oversight. It is never drawn on `bg` -- it is drawn *on the accent*, and the two accents
#: sit on opposite sides of the middle. Dark fills with #7fa6b8 and takes a cool near-black
#: at 7.3:1; light fills with the darker #3a6a80, where a near-black measures 3.0:1 and the
#: 13px semibold #AccentButton label goes muddy, so light takes the cream instead at 5.6:1.
#: Both were amber-tuned warm blacks before the accent stopped being amber.

#: Close-button hover, the one chrome colour the DS states outside the token map.
CLOSE_HOVER = "#8a3226"

# --------------------------------------------------------------------------
# File-type hues - ours, not the design system's
# --------------------------------------------------------------------------

#: Outside ``TOKENS`` on purpose. That map is the DS transcribed, and a sidebar tree
#: coloured by file type is not in it; mixing the two would make "which of these did a
#: designer choose" unanswerable. Each value is (dark, light) -- the same pairing
#: ``ACCENT_LIGHT`` exists for, and for the same reason: a hue that reads on #131313 is
#: near-invisible on a cream at 0.88 luminance.
#:
#: Five hues and not thirteen. The question a 272px tree answers is *is this code, a
#: script, config, a page or a doc* -- at a glance, down a column, next to a terminal --
#: and thirteen hues answer it worse than five by making the column itself the noise.
#:
#: Three of the five are pairs this file already carries, and are reused rather than
#: re-picked precisely because their light halves were chosen against the cream by hand.
_CODE = ("#7ba3b5", "#3a6a80")      # the `tool` token, both modes
_SCRIPT = ("#8ab06a", "#4c7a2c")    # ACCENTS[2] and its ACCENT_LIGHT twin, the light `add`
_MARKUP = ("#c08fd0", "#7a4a8c")    # ACCENTS[3] and its ACCENT_LIGHT twin
#: The two that are new. `_DATA` takes the light `warn` token as its light half and a
#: fresh dark half: dark `warn` is what a pane's dot turns when it is asking for you
#: (`dot_color("attention")`), and a `.json` sitting in a list is not asking for anything.
#: `_DOCS` is new at both ends -- nothing in the DS is a rose, which is the point, since
#: prose is the one thing in a tree that is not code.
_DATA = ("#d8a44a", "#8a6410")
_DOCS = ("#c98f8a", "#9c4f45")

#: Keyed by what ``files.language_for`` returns, not by extension: that table already
#: exists, is already tested, and already knows that ``.env.local`` is a shell file and
#: ``Dockerfile.dev`` is not a Python one. Keyed per bucket rather than per family so
#: splitting one language off its neighbours later is editing a value, not a structure.
FILE_HUES: dict[str, tuple[str, str]] = {
    "python": _CODE, "js": _CODE, "clike": _CODE,
    "shell": _SCRIPT, "powershell": _SCRIPT, "batch": _SCRIPT,
    "json": _DATA, "yaml": _DATA, "toml": _DATA, "sql": _DATA,
    "markup": _MARKUP, "css": _MARKUP,
    "markdown": _DOCS,
}

#: How far a file with no git mark drops from its hue. The tree's existing rule is that a
#: changed file earns full contrast and everything else stays quiet, so the marks are what
#: the eye lands on; type colouring has to survive that rule rather than replace it. Hue
#: says what the file is, value says whether git has touched it, and both still read.
#:
#: Two factors because the two themes mute in opposite directions: dark's hues are light
#: on near-black and quieten by darkening, light's are dark on cream and quieten by
#: lightening. One factor would make the light theme's unchanged files the loud ones.
#:
#: The numbers are measured, not chosen. Quiet here means "as quiet as the grey these
#: filenames used to be" -- `dim` sits at 5.7:1 in dark and 5.3:1 in light -- and not one
#: step further: at 0.72/1.34, which is where this started, the light theme's muted green
#: measured 2.7:1 against the cream, under the 3:1 floor `_readable_on` enforces for every
#: other colour in this file. An unchanged file is most of the tree, so it is the tier
#: that has to stay readable; the loud one is the exception.
MUTE = {"dark": 0.84, "light": 1.16}

#: What that floor is, so a retune cannot quietly cross it. `_readable_on` uses the same
#: 3:1 for a user's own accent colour.
MUTE_FLOOR = 3.0

# --------------------------------------------------------------------------
# Type scale - Barlow (UI) / Roboto Mono (data, paths, labels)
# --------------------------------------------------------------------------

FONT_UI = "Roboto"
FONT_MONO = "Roboto Mono"

#: Roboto rather than the Barlow the design system named. Barlow is a condensed grotesque:
#: measured at 13px its x-height is fine (0.505 em) but it sets the same string in 203px
#: where Roboto needs 264, and that 30% squeeze is what read as cramped rather than dense.
#: Roboto is wider *and* taller in the x-height that matters at chrome sizes (0.528), and
#: it is the sibling of the Roboto Mono already specified for data -- so the pairing is the
#: one the design was reaching for anyway.
#:
#: Fallbacks used when a face is absent. They lose the design, which is a cosmetic loss and
#: not worth refusing to start over -- but they have to name a face the platform actually
#: has, or the fallback is not a fallback. ``assets/fonts/`` ships no .ttf, so this is not
#: the exceptional path: it is what every install renders in today.
#:
#: Segoe UI and Consolas are Windows faces and neither exists on a Mac, so before the port
#: ``load_fonts`` substituted Roboto Mono for a family that was equally missing and Qt chose
#: its own default instead -- a *proportional* one, for roles the design system marks as
#: mono. ui/term_view.py is insulated from that by its own StyleHint.Monospace guard, so
#: the terminal grid was never at risk; the chrome, which asks for these through
#: ``Theme.font`` with no such guard, was.
#:
#: Menlo rather than SF Mono, though SF Mono is the more modern pairing: it ships with
#: macOS but is not published to the font database under that name, so a third-party app
#: asking for it by family gets nothing. Menlo is present and enumerable, which is the only
#: property a fallback needs.
if platform_.IS_MACOS:
    FONT_UI_FALLBACK = "Helvetica Neue"
    FONT_MONO_FALLBACK = "Menlo"
else:
    FONT_UI_FALLBACK = "Segoe UI"
    FONT_MONO_FALLBACK = "Consolas"

#: (family, px, weight, letter-spacing in em) keyed by DS role.
TYPE: dict[str, tuple[str, float, QFont.Weight, float]] = {
    "brand":        (FONT_MONO, 11,   QFont.Weight.DemiBold, 0.18),
    "eyebrow":      (FONT_MONO, 10,   QFont.Weight.Normal,   0.18),
    "menu":         (FONT_UI,   13,   QFont.Weight.Normal,   0.0),
    "deckName":  (FONT_UI,   14.5, QFont.Weight.DemiBold, 0.0),
    #: Sidebar sub-text: the deck path, and the branch/diff/time metadata under it.
    #: Up from the DS 10.5/10, which assumed Roboto Mono. Unbundled -- see
    #: assets/fonts/README.md -- these render in the Consolas fallback, whose x-height is
    #: 0.489em against Roboto Mono's 0.528, so the DS figure arrived about 8% shorter
    #: still. These two roles carry the path and the git state, which are the things you
    #: scan the sidebar *for*, so they are sized to survive the substitution.
    "deckPath":  (FONT_MONO, 12,   QFont.Weight.Normal,   0.0),
    "deckMeta":  (FONT_MONO, 11.5, QFont.Weight.Normal,   0.0),
    #: A terminal's name on a child row. Prose, not a path or a figure, so it takes the
    #: UI face: at a given size a proportional Roboto both reads larger than Roboto Mono
    #: and sets narrower, which in a 272px sidebar is the difference between "Merge and
    #: push to master" fitting and eliding. The uptime beside it stays mono so the
    #: figures still line up down the column.
    "sessionName":  (FONT_UI,   12.5, QFont.Weight.Normal,   0.0),
    "headerTitle":  (FONT_UI,   19,   QFont.Weight.DemiBold, -0.005),
    "headerMeta":   (FONT_MONO, 11,   QFont.Weight.Normal,   0.0),
    "tab":          (FONT_MONO, 12,   QFont.Weight.Normal,   0.0),
    "tabMeta":      (FONT_UI,   11,   QFont.Weight.Normal,   0.0),
    "button":       (FONT_UI,   13,   QFont.Weight.Normal,   0.0),
    "buttonAccent": (FONT_UI,   13,   QFont.Weight.DemiBold, 0.0),
    #: The terminal grid. Its own role rather than paneTitle+1, which is what it used
    #: to borrow -- and it borrowed the number as *points* while every other role here
    #: is pixels, so the terminal rendered about a third larger than the same figure
    #: means anywhere else in the app.
    "terminal":     (FONT_MONO, 15,   QFont.Weight.Normal,   0.0),
    #: The editor pane's text, and the line numbers beside it. Smaller than the
    #: terminal role on purpose: a terminal is what you work in, an open file is what
    #: you glance at, and a pane showing both at 15px shows noticeably less of the file.
    "code":         (FONT_MONO, 13,   QFont.Weight.Normal,   0.0),
    "gutter":       (FONT_MONO, 11,   QFont.Weight.Normal,   0.0),
    "paneTitle":    (FONT_MONO, 11.5, QFont.Weight.Normal,   0.0),
    "paneCmd":      (FONT_MONO, 10.5, QFont.Weight.Normal,   0.0),
    "paneFooter":   (FONT_MONO, 10.5, QFont.Weight.Normal,   0.0),
    "panelKey":     (FONT_UI,   12.5, QFont.Weight.Normal,   0.0),
    "panelVal":     (FONT_MONO, 12,   QFont.Weight.Normal,   0.0),
    "statusBar":    (FONT_MONO, 11,   QFont.Weight.Normal,   0.0),
}

#: Roles the DS renders in caps. QSS cannot do this, so the widgets must.
UPPERCASE_ROLES = frozenset({"brand", "eyebrow"})

RADIUS = {"sm": 3, "md": 5, "lg": 6, "chip": 4, "rail": 2}

#: Chrome geometry in logical px (DS values).
GEOM = {
    "titlebar_h": 38,
    "sidebar_w": 272,
    "sidebar_header_h": 46,
    "deck_header_h": 62,
    "tabstrip_h": 42,
    "pane_header_h": 30,
    "pane_footer_h": 30,
    "session_panel_w": 264,
    #: Collapsed width for either side panel. Wide enough for a 22px chevron and a
    #: 10px dot with margins either side; narrow enough that the pane grid gains a
    #: useful amount -- 272 + 264 down to 88 is about 450px back.
    "rail_w": 44,
    "statusbar_h": 30,
    "row_pad": 11,
    "row_pad_compact": 8,
    #: Spec 14.7: a frameless window's resize borders sit inside the client rect, and a
    #: docked terminal owns every point it covers. Measured by tools/probe_frameless.py --
    #: flush to the frame all 8 edges are unreachable, at 6px none are.
    "resize_gutter": 6,
    #: Both scrollbars, and the gutter term_view.py reserves for the terminal's. It is
    #: here rather than only in the sheet because the terminal's bar is an overlay: the
    #: view has to subtract exactly what the stylesheet draws, and two copies of that
    #: number disagree by making text run under the handle.
    "scrollbar_w": 10,
}


def _px(value: float) -> int:
    """Round half-up, because that is what Qt's stylesheet parser does.

    Python's round is half-to-even, so round(14.5) is 14 while QSS turns 14.5px into 15.
    Half the type scale sits on a .5, so the mismatch would make the same token render at
    two different sizes depending on which path styled the widget.
    """
    return int(value + 0.5)


@dataclass
class Theme:
    mode: Literal["dark", "light"] = "dark"
    accent: str | None = None

    def __post_init__(self) -> None:
        if self.accent is None:
            self.accent = ACCENTS[0] if self.mode == "dark" else ACCENT_LIGHT_DEFAULT

    # -- token access ------------------------------------------------------

    @property
    def _t(self) -> dict[str, str]:
        return TOKENS[self.mode]

    def hex(self, name: str) -> str:
        """Raw hex for a token. 'accent' resolves to the active accent."""
        if name == "accent":
            return str(self.accent)
        return self._t[name]

    def c(self, name: str) -> QColor:
        """QColor for a token - for QPainter and delegate code."""
        return QColor(self.hex(name))

    def tint(self, hex_color: str) -> QColor:
        """A caller's own colour, expressed in this theme's terms.

        Per-deck accents are stored as one hex, but a hex is only ever right for one
        background, and this app has two. Dark takes the value as given. Light swaps a
        preset for the twin ``ACCENT_LIGHT`` names, and darkens anything else -- a colour
        out of ``QColorDialog`` -- until it clears 3:1 against `bg`, because the alternative
        is a deck marking itself with a rail nobody can see. Empty means "no override"
        and resolves to the theme's own accent, so callers need not branch.
        """
        if not hex_color:
            return self.c("accent")
        if self.mode == "dark":
            return QColor(hex_color)
        return QColor(ACCENT_LIGHT.get(hex_color) or self._readable_on(hex_color, self.hex("bg")))

    def file_color(self, bucket: str, muted: bool = False) -> QColor:
        """A filename's colour in the sidebar tree, by what kind of file it is.

        ``bucket`` is what ``files.language_for`` returned. ``muted`` is the tree's
        existing "git has not touched this" tier, kept rather than replaced -- see
        :data:`MUTE`.
        """
        pair = FILE_HUES.get(bucket)
        if pair is None:
            # An image, an archive, a .lock, anything the language table does not claim.
            # These are exactly the two tiers the tree drew before it had any hues, so an
            # unrecognised file looks precisely as it did.
            return self.c("dim" if muted else "text")
        base = pair[0] if self.mode == "dark" else pair[1]
        return QColor(self._shift(base, MUTE[self.mode]) if muted else base)

    def font(self, role: str) -> QFont:
        family, size, weight, spacing = TYPE[role]
        font = QFont(family)
        font.setPixelSize(_px(size))
        font.setWeight(weight)
        if spacing:
            # Absolute, not percentage: PercentageSpacing scales the character advance,
            # which for a monospace face is roughly 0.6em, so the DS .18em would land at
            # about 61% of its intended tracking.
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing * size)
        return font

    @staticmethod
    def text_for(role: str, text: str) -> str:
        """Apply the DS casing for a role. QSS text-transform is silently ignored."""
        return text.upper() if role in UPPERCASE_ROLES else text

    # -- state colours (DS dotFor / animFor) --------------------------------

    def dot_color(self, state: str) -> QColor:
        """Colour for a session state.

        Hue answers one question -- does this want me? -- so running and live share the
        accent: both mean "nothing for you here", and the difference between them is
        carried by motion rather than by a third colour. Green is reserved for a turn that
        has *finished*, amber for a program that rang the bell, and those two are the only
        ones worth a glance across the sidebar.

        `running` was green until now, which spent the loudest colour in the palette on
        the state that resolves itself.
        """
        return {
            "attention": self.c("warn"),
            "waiting": self.c("add"),
            "running": self.c("accent"),
            "active": self.c("accent"),
            "live": self.c("accent"),
        }.get(state, self.c("borderStrong"))

    def dot_pulses(self, state: str) -> bool:
        """Motion means live, not merely noteworthy.

        `waiting` no longer pulses: a finished turn is at rest, and a resting state that
        keeps moving reads as work still happening.
        """
        return state in ("running", "attention")

    def pulse_period_ms(self, state: str) -> int:
        return 1400 if state == "attention" else 2200

    # -- palette -----------------------------------------------------------

    def palette(self) -> QPalette:
        """Application defaults.

        Deliberately used instead of a bare ``QWidget { background: ... }`` stylesheet
        rule. That selector reaches every widget including internal sub-widgets of
        composite controls, and it would also repaint the container a foreign terminal
        window is parented into. A palette sets the same defaults without giving the
        style engine a reason to fill those rects.
        """
        pal = QPalette()
        bg, text = self.c("bg"), self.c("text")
        pal.setColor(QPalette.ColorRole.Window, bg)
        pal.setColor(QPalette.ColorRole.Base, self.c("panel"))
        pal.setColor(QPalette.ColorRole.AlternateBase, self.c("tab"))
        pal.setColor(QPalette.ColorRole.WindowText, text)
        pal.setColor(QPalette.ColorRole.Text, text)
        pal.setColor(QPalette.ColorRole.ButtonText, text)
        pal.setColor(QPalette.ColorRole.Button, self.c("panel"))
        pal.setColor(QPalette.ColorRole.ToolTipBase, self.c("panel"))
        pal.setColor(QPalette.ColorRole.ToolTipText, text)
        pal.setColor(QPalette.ColorRole.Highlight, self.c("accent"))
        pal.setColor(QPalette.ColorRole.HighlightedText, self.c("onAccent"))
        pal.setColor(QPalette.ColorRole.PlaceholderText, self.c("label"))
        return pal

    # -- stylesheet --------------------------------------------------------

    def qss(self) -> str:
        t = self._t
        a = self.accent
        r = RADIUS
        return f"""
/* ---- base ----
   Scoped to the surfaces we own. There is deliberately no bare QWidget rule; see
   Theme.palette(). */
QMainWindow, QDialog {{ background: {t['bg']}; color: {t['text']}; }}
QToolTip {{
    background: {t['panel']}; color: {t['text']};
    border: 1px solid {t['chip']}; padding: 4px 7px;
}}

/* ---- title bar ---- */
#TitleBar {{
    background: {t['titlebar']};
    border-bottom: 1px solid {t['border']};
    min-height: {GEOM['titlebar_h']}px; max-height: {GEOM['titlebar_h']}px;
}}
#BrandMark {{ background: {a}; border-radius: {r['rail']}px; }}
/* Uppercase is applied in Python: QSS text-transform is parsed and dropped. */
#BrandText {{
    font-family: "{FONT_MONO}"; font-size: 11px; font-weight: 600;
    letter-spacing: 2px; background: transparent;
}}
#MenuButton {{ background: transparent; color: {t['dim']}; padding: 3px 2px; border: none; }}
#MenuButton:hover {{ color: {t['text']}; }}
/* A button with a QMenu draws an arrow beside its label by default and reserves the
   width for it. The title bar's five are word-only by design (spec 14.7), so the
   indicator is removed rather than merely hidden -- image alone leaves the gap. */
#MenuButton::menu-indicator {{ image: none; width: 0; }}
/* Both, deliberately. #WinClose used to match only its own :hover rule, so the close
   button had no base styling at all -- it looked right purely because the widget sets
   setFlat and a fixed width in code. */
#WinButton, #WinClose {{
    background: transparent; color: {t['dim']}; border: none;
    font-family: "{FONT_MONO}"; font-size: 12px;
    min-width: 46px; max-width: 46px;
}}
/* Two spellings of the same state. The pseudo-class is the idiomatic one; the property
   is the one that can be set from a test and demonstrated to paint. See WindowButton. */
/* `chip`, not `hover`. The generic hover token is a *lift* from the list backgrounds it
   was picked against (#242424 over #131313); against the lifted title bar it lands darker
   than the bar and reads as recessed. chip contrasts the right way in both themes:
   lighter than the bar in dark, darker than it in light. */
#WinButton:hover, #WinButton[hovered="true"] {{
    background: {t['chip']}; color: {t['text']};
}}
#WinClose:hover, #WinClose[hovered="true"] {{ background: {CLOSE_HOVER}; color: #ffffff; }}

/* ---- chips ---- */
#Chip {{
    background: transparent; border: 1px solid {t['chip']};
    border-radius: {r['chip']}px; color: {t['label']};
    font-family: "{FONT_MONO}"; font-size: 11px; padding: 4px 9px;
}}
#Chip:hover {{ border-color: {a}; }}

/* ---- sidebar ---- */
#Sidebar {{
    background: {t['panel']}; border-right: 1px solid {t['border']};
    min-width: {GEOM['sidebar_w']}px; max-width: {GEOM['sidebar_w']}px;
}}
#SidebarHeader {{
    border-bottom: 1px solid {t['border2']};
    min-height: {GEOM['sidebar_header_h']}px; max-height: {GEOM['sidebar_header_h']}px;
}}
#SidebarFooter {{ border-top: 1px solid {t['border2']}; }}
#Eyebrow {{
    font-family: "{FONT_MONO}"; font-size: 10px; letter-spacing: 1.8px;
    color: {t['label']}; background: transparent;
}}
#IconButton {{
    background: transparent; border: 1px solid {t['chip']};
    border-radius: {r['chip']}px; color: {t['dim2']};
    min-width: 25px; max-width: 25px; min-height: 25px; max-height: 25px;
}}
#IconButton:hover {{ border-color: {a}; color: {a}; }}
/* The same removal as #MenuButton, for the same reason one size down: the sidebar's "+"
   carries a QMenu, and the default indicator both draws an arrow beside the glyph and
   reserves width for it -- inside a box fixed at 25px that pushes the "+" off centre. */
#IconButton::menu-indicator {{ image: none; width: 0; }}
#DeckList {{ background: transparent; border: none; padding: 9px 9px 0 9px; }}
#DeckList::item {{ border: none; }}
/* ---- sidebar mode switch ---- */
/* Two words, not tabs with borders: the sidebar header is 46px and a real tab strip
   inside it would leave no room for the count and the two buttons beside it. */
#SidebarTab {{
    background: transparent; border: none; color: {t['label']};
    font-family: "{FONT_MONO}"; font-size: 10px; letter-spacing: 1.8px;
    padding: 3px 0;
}}
#SidebarTab:hover {{ color: {t['dim2']}; }}
#SidebarTab[on="true"] {{ color: {t['text']}; border-bottom: 1px solid {a}; }}

/* ---- file tree ---- */
#FileTree {{ background: transparent; border: none; padding: 6px 4px 0 6px; }}
#FileTree::item {{ border: none; }}
/* The expand arrows are painted by FileDelegate, so the branch column is emptied
   rather than restyled -- left alone, Qt's stylesheet style drops its own arrow in and
   the row ends up with two. */
#FileTree::branch {{ background: transparent; border-image: none; image: none; }}
#FileTreeEmpty {{ color: {t['label']}; background: transparent; font-size: 12px; }}

#AgentPicker {{
    background: {t['tab']}; border: 1px solid {t['chip']};
    border-radius: {r['md']}px; padding: 9px 11px;
}}

/* ---- deck header ---- */
#DeckHeader {{
    background: {t['bg']}; border-bottom: 1px solid {t['border']};
    min-height: {GEOM['deck_header_h']}px; max-height: {GEOM['deck_header_h']}px;
}}
#DeckTitle {{ font-size: 19px; font-weight: 600; background: transparent; }}
#DeckPath, #DeckMeta {{
    font-family: "{FONT_MONO}"; font-size: 11px;
    color: {t['label']}; background: transparent;
}}
/* The branch, as a chip rather than a run of the meta line. Borrowed from the sidebar
   row's branch chip (delegates.py) so the same fact looks the same in both places, and
   it takes #IconButton's accent hover because it is a control and the text beside it
   is not. */
#BranchChip {{
    font-family: "{FONT_MONO}"; font-size: 11px;
    color: {t['dim']}; background: transparent;
    border: 1px solid {t['chip']}; border-radius: {r['chip']}px; padding: 1px 6px;
}}
#BranchChip:hover {{ border-color: {a}; color: {a}; }}
#GhostButton {{
    background: transparent; border: 1px solid {t['chip']};
    border-radius: {r['md']}px; color: {t['dim2']}; padding: 8px 13px;
}}
#GhostButton:hover {{ border-color: {t['borderStrong']}; color: {t['text']}; }}
#AccentButton {{
    background: {a}; border: none; border-radius: {r['md']}px;
    color: {t['onAccent']}; font-weight: 600; padding: 8px 14px;
}}
#AccentButton:hover {{ background: {self._shift(a, 1.12)}; }}
#AccentButton:pressed {{ background: {self._shift(a, 0.94)}; }}

/* ---- session tab strip ---- */
#TabStrip {{
    background: {t['status']}; border-bottom: 1px solid {t['border']};
    min-height: {GEOM['tabstrip_h']}px; max-height: {GEOM['tabstrip_h']}px;
}}
QTabBar {{ background: transparent; qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: {t['tab']}; border: 1px solid {t['border2']}; border-bottom: none;
    border-top-left-radius: {r['md']}px; border-top-right-radius: {r['md']}px;
    color: {t['dim']}; font-family: "{FONT_MONO}"; font-size: 12px;
    padding: 0 11px 0 13px; margin: 5px 2px 0 2px; min-height: 32px;
}}
QTabBar::tab:selected {{
    background: {t['term']}; border-color: {t['borderStrong']}; color: {t['text']};
}}
QTabBar::tab:hover:!selected {{ border-color: {t['chip']}; }}
#LayoutToggle {{
    background: transparent; border: 1px solid {t['chip']};
    border-radius: {r['sm']}px; color: {t['dim']};
    font-family: "{FONT_MONO}"; font-size: 11px; padding: 4px 9px;
}}
#LayoutToggle:hover {{ border-color: {a}; }}
#LayoutToggle:checked {{ background: {t['sel']}; border-color: {a}; color: {t['text']}; }}
#SessionTab {{
    background: {t['tab']}; border: 1px solid {t['border2']}; border-bottom: none;
    border-top-left-radius: {r['md']}px; border-top-right-radius: {r['md']}px;
}}
#SessionTab:hover {{ border-color: {t['chip']}; }}
#SessionTab[selected="true"] {{
    background: {t['term']}; border-color: {t['borderStrong']};
}}
/* A hidden pane's tab: dashed, because the pane it names is not on the grid. Dashed
   rather than merely dim -- dim is what a dead session looks like, and a hidden pane's
   shell is still running. The tab also says "hidden" in its sub-text, since a border
   style is a thin thing to hang a state on by itself. */
#SessionTab[hidden="true"] {{
    background: transparent; border-style: dashed; border-color: {t['chip']};
}}
#SessionTab[hidden="true"] QLabel {{ color: {t['dim']}; }}

/* ---- terminal panes ----
   Every ring is a permanent border that only changes colour. A border that appears on
   focus would change the content rect and reflow the terminal on every click. */
#PaneGrid {{ background: {t['border']}; }}
Pane {{ border: 1px solid transparent; }}
Pane[active="true"] {{ border: 1px solid {a}; }}
#PaneHeader {{
    background: {t['term']}; border-bottom: 1px solid {t['border2']};
    min-height: {GEOM['pane_header_h']}px; max-height: {GEOM['pane_header_h']}px;
}}
#PaneHeader[active="true"] {{ background: {t['status']}; }}
#PaneTitle {{
    font-family: "{FONT_MONO}"; font-size: 12px;
    color: {t['text']}; background: transparent;
}}
#PaneName {{
    font-family: "{FONT_MONO}"; font-size: 12px; font-weight: 600;
    color: {t['text']}; background: transparent;
}}
#PaneMeta {{ font-size: 12px; color: {t['label']}; background: transparent; }}
#PaneFooter {{
    background: {t['term']}; border-top: 1px solid {t['border2']};
    color: {t['label']}; font-family: "{FONT_MONO}"; font-size: 11px;
    min-height: {GEOM['pane_footer_h']}px; max-height: {GEOM['pane_footer_h']}px;
}}
#PaneFooter[active="true"] {{ background: {t['status']}; }}
#ShellChip {{
    background: transparent; border: 1px solid {t['chip']};
    border-radius: {r['sm']}px; color: {t['dim']};
    font-family: "{FONT_MONO}"; font-size: 10px; padding: 1px 6px 2px;
}}
#PaneCmd, #PaneGeometry {{
    font-family: "{FONT_MONO}"; font-size: 11px;
    color: {t['label']}; background: transparent;
}}

/* ---- session panel ---- */
#SessionPanel {{
    background: {t['panel']}; border-left: 1px solid {t['border']};
    min-width: {GEOM['session_panel_w']}px; max-width: {GEOM['session_panel_w']}px;
}}
#PanelSection {{ border-bottom: 1px solid {t['border2']}; }}
#PanelKey {{ font-size: 13px; color: {t['label']}; background: transparent; }}
#PanelVal {{
    font-family: "{FONT_MONO}"; font-size: 12px;
    color: {t['dim2']}; background: transparent;
}}
#SessionCard {{
    background: {t['tab']}; border: 1px solid {t['border']};
    border-radius: {r['md']}px; padding: 10px 11px;
}}
#SessionCard:hover {{ border-color: {t['borderStrong']}; }}
#SessionCard[active="true"] {{ border-color: {a}; }}

/* ---- editor pane ---- */
#CodeEdit {{
    background: {t['term']}; color: {t['dim2']}; border: none;
    selection-background-color: {a}; selection-color: {t['onAccent']};
}}
/* The file changed underneath the buffer. Amber rather than red: nothing is broken and
   nothing has been lost, there are simply two versions and the user picks. */
#EditorBanner {{
    background: {t['tab']}; border-bottom: 1px solid {a};
}}

/* ---- status bar ---- */
#StatusBar {{
    background: {t['status']}; border-top: 1px solid {t['border']};
    color: {t['label']}; font-family: "{FONT_MONO}"; font-size: 11px;
    min-height: {GEOM['statusbar_h']}px; max-height: {GEOM['statusbar_h']}px;
}}
#StatusSep {{ color: {t['chip']}; background: transparent; }}

/* The pane's own close button. Dim until the pointer is on it: one of these sits in
   every pane, and a row of bright crosses reads as an error state. */
#PaneClose {{
    background: transparent; border: none; color: {t['label']};
    font-family: "{FONT_MONO}"; font-size: 11px;
    min-width: 18px; max-width: 18px; min-height: 18px; max-height: 18px;
    border-radius: {r['sm']}px;
}}
#PaneClose:hover {{ background: {CLOSE_HOVER}; color: #ffffff; }}

/* The zoom button, beside it and deliberately not styled like it. Same 18px cell, but the
   hover is neutral: PaneClose earns the red because it ends a session, and wearing that
   for a reversible view change would make the safer of the two buttons look like the
   dangerous one. Lit while zoomed, because the pane being large is not by itself an
   answer to "why can I only see one of them". */
#PaneZoom {{
    background: transparent; border: none; color: {t['label']};
    font-family: "{FONT_MONO}"; font-size: 11px;
    min-width: 18px; max-width: 18px; min-height: 18px; max-height: 18px;
    border-radius: {r['sm']}px;
}}
#PaneZoom:hover {{ background: {t['sel']}; color: {t['text']}; }}
Pane[zoomed="true"] #PaneZoom {{ color: {a}; }}

/* ---- collapsed side panels ---- */
/* The width lives here rather than in setMaximumWidth, because min-width above would
   argue with it and QSS wins. SidePanel flips the property and repolishes. */
#Sidebar[collapsed="true"], #SessionPanel[collapsed="true"] {{
    min-width: {GEOM['rail_w']}px; max-width: {GEOM['rail_w']}px;
}}
#RailButton {{
    background: transparent; border: none; color: {t['dim']};
    font-family: "{FONT_MONO}"; font-size: 13px;
    border-radius: {r['sm']}px;
}}
#RailButton:hover {{ background: {t['hover']}; color: {t['text']}; }}
#RailDot {{
    background: {t['borderStrong']}; border: none;
    border-radius: 5px; min-width: 10px; max-width: 10px;
    min-height: 10px; max-height: 10px;
}}
#RailDot[live="true"] {{ background: {t['add']}; }}
#RailDot[active="true"] {{ background: {a}; }}
#RailDot:hover {{ background: {t['text']}; }}

/* ---- scrollbars ---- */
QScrollBar:vertical {{ background: transparent; width: {GEOM['scrollbar_w']}px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {t['chip']}; border-radius: 5px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {t['borderStrong']}; }}
/* The editor does not wrap, so it is the one surface with a horizontal bar. Without
   this rule it renders in the platform style -- a grey Windows scrollbar across the
   bottom of a dark pane. */
QScrollBar:horizontal {{ background: transparent; height: {GEOM['scrollbar_w']}px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {t['chip']}; border-radius: 5px; min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: {t['borderStrong']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

/* ---- splitter (pane gaps read as the DS 1px borders) ---- */
QSplitter::handle {{ background: {t['border']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QSplitter::handle:hover {{ background: {a}; }}

/* ---- dialogs ----
   Once any stylesheet is set, Qt's stylesheet style paints buttons itself and stops
   consulting the palette. Without a rule here the quit prompt renders its buttons in the
   default foreground on our dark surface, which is unreadable -- the palette being
   correct is not enough. */
QMessageBox, QInputDialog {{ background: {t['bg']}; }}
QMessageBox QLabel, QInputDialog QLabel {{
    color: {t['text']}; background: transparent; font-size: 13px;
}}
QDialogButtonBox QPushButton,
QMessageBox QPushButton,
QInputDialog QPushButton {{
    background: {t['tab']};
    border: 1px solid {t['chip']};
    border-radius: {r['md']}px;
    color: {t['text']};
    padding: 7px 16px;
    min-width: 96px;
}}
QDialogButtonBox QPushButton:hover,
QMessageBox QPushButton:hover,
QInputDialog QPushButton:hover {{
    border-color: {t['borderStrong']}; background: {t['hover']};
}}
QDialogButtonBox QPushButton:pressed,
QMessageBox QPushButton:pressed,
QInputDialog QPushButton:pressed {{ background: {t['sel']}; }}
QDialogButtonBox QPushButton:default,
QMessageBox QPushButton:default {{
    background: {a}; border-color: {a}; color: {t['onAccent']}; font-weight: 600;
}}
QDialogButtonBox QPushButton:default:hover,
QMessageBox QPushButton:default:hover {{ background: {self._shift(a, 1.12)}; }}
QDialogButtonBox QPushButton:disabled {{ color: {t['dim']}; }}
QDialogButtonBox QPushButton:default:disabled {{
    background: {t['tab']}; border-color: {t['chip']}; color: {t['dim']};
}}

/* ---- clone dialog ----
   The one dialog here built by hand, so it has to ask for what QMessageBox and QInputDialog
   are given above. #CloneBrowse by name rather than `#CloneDialog QPushButton`: an id outranks
   every `QDialogButtonBox QPushButton:default` rule, and Clone would lose its fill. */
#CloneDialog QLabel {{ color: {t['text']}; background: transparent; font-size: 13px; }}
#CloneDialog #CloneStatus {{ color: {t['dim']}; }}
#CloneDialog #CloneStatus[tone="bad"] {{ color: {t['warn']}; }}
#CloneDialog QLineEdit {{
    background: {t['panel']}; color: {t['text']};
    border: 1px solid {t['chip']}; border-radius: {r['md']}px;
    padding: 6px 8px; font-size: 13px;
    selection-background-color: {a}; selection-color: {t['onAccent']};
}}
#CloneDialog QLineEdit:focus {{ border-color: {a}; }}
#CloneDialog QLineEdit:disabled {{ color: {t['dim']}; }}
#CloneBrowse {{
    background: {t['tab']}; color: {t['text']};
    border: 1px solid {t['chip']}; border-radius: {r['md']}px;
    padding: 6px 14px; font-size: 13px;
}}
#CloneBrowse:hover {{ border-color: {t['borderStrong']}; background: {t['hover']}; }}
#CloneBrowse:pressed {{ background: {t['sel']}; }}
#CloneBrowse:disabled {{ color: {t['dim']}; }}
#CloneDialog QProgressBar {{
    background: {t['panel']}; border: none; border-radius: 3px; max-height: 6px;
}}
#CloneDialog QProgressBar::chunk {{ background: {a}; border-radius: 3px; }}

/* ---- menus ---- */
QMenu {{ background: {t['panel']}; border: 1px solid {t['chip']}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: {r['sm']}px; }}
QMenu::item:selected {{ background: {t['hover']}; }}
QMenu::separator {{ height: 1px; background: {t['border2']}; margin: 4px 8px; }}
/* The swatch row inside a QMenu. Transparent, not `panel`: a QWidgetAction sits on the
   menu's own background, and painting it again lands a slightly different rounded rect
   inside the menu's border. Every mark on it is drawn by AccentSwatchRow. */
#AccentSwatches {{ background: transparent; }}

/* ---- command palette ---- */
/* The scrim paints itself: it needs an alpha the token map does not carry. Everything
   inside it is ordinary chrome and belongs here with the rest. */
#PaletteCard {{
    background: {t['panel']}; border: 1px solid {t['borderStrong']};
    border-radius: {r['lg']}px;
}}
#PaletteInput {{
    background: transparent; border: none;
    border-bottom: 1px solid {t['border']};
    color: {t['text']}; padding: 12px 14px; selection-background-color: {a};
    selection-color: {t['onAccent']};
}}
#PaletteList {{ background: transparent; border: none; padding: 3px; outline: none; }}

/* ---- toolbar / status bar (milestone 3 chrome, pre-14.7) ---- */
QToolBar {{
    background: {t['panel']}; border: none;
    border-bottom: 1px solid {t['border']}; spacing: 4px; padding: 4px 8px;
}}
QToolBar QToolButton {{
    background: transparent; border: 1px solid transparent;
    border-radius: {r['chip']}px; color: {t['dim2']}; padding: 5px 10px;
}}
QToolBar QToolButton:hover {{ border-color: {t['chip']}; color: {t['text']}; }}
QToolBar QToolButton:pressed {{ background: {t['hover']}; }}
QToolBar::separator {{ background: {t['border']}; width: 1px; margin: 4px 6px; }}
QStatusBar {{
    background: {t['status']}; color: {t['label']};
    border-top: 1px solid {t['border']};
}}
QStatusBar QLabel {{
    color: {t['label']}; font-family: "{FONT_MONO}"; font-size: 11px;
}}
"""

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _shift(hex_color: str, factor: float) -> str:
        """Approximates the DS ``filter: brightness()`` hover, which QSS lacks."""
        colour = QColor(hex_color)
        h, s, v, alpha = colour.getHsv()
        return QColor.fromHsv(h, s, max(0, min(255, int(v * factor))), alpha).name()

    @classmethod
    def _readable_on(cls, hex_color: str, background: str, target: float = 3.0) -> str:
        """Darken a colour in place until it separates from `background`.

        Only ever reached for a colour the user picked themselves. The presets have hand-
        chosen twins in ``ACCENT_LIGHT``; this is what stops a custom pale yellow from
        being stored happily and then painting nothing on the light theme's cream. Hue and
        saturation are untouched -- it must still read as the colour that was chosen -- and
        the loop is bounded because at v=0 nothing can darken further.
        """
        colour = QColor(hex_color)
        for _ in range(24):
            if cls._contrast(colour, QColor(background)) >= target:
                break
            h, s, v, alpha = colour.getHsv()
            if v == 0:
                break
            colour = QColor.fromHsv(h, s, max(0, int(v * 0.92)), alpha)
        return colour.name()

    @staticmethod
    def _luminance(colour: QColor) -> float:
        """WCAG relative luminance. sRGB is gamma-encoded, so the channels linearise
        first -- averaging the raw bytes would call #808080 half as bright as white."""
        channels = []
        for raw in (colour.redF(), colour.greenF(), colour.blueF()):
            channels.append(raw / 12.92 if raw <= 0.03928 else ((raw + 0.055) / 1.055) ** 2.4)
        red, green, blue = channels
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    @classmethod
    def _contrast(cls, first: QColor, second: QColor) -> float:
        lighter, darker = sorted((cls._luminance(first), cls._luminance(second)), reverse=True)
        return (lighter + 0.05) / (darker + 0.05)


def default_font_dir() -> pathlib.Path:
    # Delegated, because the answer differs once the app is frozen: there is no src/ in a
    # PyInstaller bundle and this walk lands nowhere. See asomedeck/resources.py.
    return resources.font_dir()


def styled(widget, name: str):
    """Give a widget a QSS identity that actually paints, and return it.

    ``setObjectName`` alone is not enough for a plain ``QWidget``. QLabel, QPushButton and
    friends draw their own styled background, but a bare container does not: without
    ``WA_StyledBackground`` its ``background:`` rule is parsed, matched, and then silently
    ignored, so the widget shows whatever is behind it.

    That is why the title bar stayed the colour of the window no matter what its token
    said -- and it applied to every panel, header, footer and status bar in the chrome.
    The rules were right the whole time; nothing was painting them.
    """
    widget.setObjectName(name)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    return widget


def load_fonts(font_dir: str | pathlib.Path | None = None) -> dict[str, bool]:
    """Register whatever faces are bundled, and substitute for the ones that are not.

    Must run after a QGuiApplication exists -- QFontDatabase has nowhere to register a
    face before that -- and before any QSS is applied.

    Never fatal. A missing file means the design degrades to Segoe UI and Consolas;
    refusing to start over a typeface would trade a cosmetic problem for a total one.
    Returns which families ended up available so the caller can log or surface it.
    """
    directory = pathlib.Path(font_dir) if font_dir is not None else default_font_dir()
    loaded = 0
    if directory.is_dir():
        for path in sorted(directory.glob("*.ttf")) + sorted(directory.glob("*.otf")):
            if QFontDatabase.addApplicationFont(str(path)) == -1:
                log.warning("could not register font %s", path)
            else:
                loaded += 1
    else:
        log.info("no bundled font directory at %s", directory)

    families = set(QFontDatabase.families())
    available = {FONT_UI: FONT_UI in families, FONT_MONO: FONT_MONO in families}
    missing = [name for name, ok in available.items() if not ok]
    for family, fallback in ((FONT_UI, FONT_UI_FALLBACK), (FONT_MONO, FONT_MONO_FALLBACK)):
        if not available[family]:
            # Without an explicit substitution Qt picks its own default, which for a
            # monospace role can silently become a proportional face.
            QFont.insertSubstitution(family, fallback)
            # And a substitution naming a family this machine does not have is the same
            # thing again one step removed -- which is exactly what shipped before the
            # port, on every Mac, silently. Said out loud rather than left to be inferred
            # from text that looks slightly wrong.
            if fallback not in families:
                log.warning(
                    "fallback font %r is not available either; %r will render in whatever "
                    "Qt chooses, which for a monospace role may be proportional",
                    fallback, family,
                )
    if missing:
        log.warning(
            "font(s) not available: %s - falling back to %s / %s. Drop the .ttf files "
            "into %s to match the design.",
            ", ".join(missing), FONT_UI_FALLBACK, FONT_MONO_FALLBACK, directory,
        )
    else:
        log.info("registered %d font file(s) from %s", loaded, directory)
    return available


def apply(app, theme: Theme) -> None:
    """Palette then stylesheet, in that order, so QSS always wins."""
    app.setPalette(theme.palette())
    app.setStyleSheet(theme.qss())
