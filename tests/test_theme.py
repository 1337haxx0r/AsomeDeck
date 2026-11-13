"""Design-system port fidelity (spec 14.4).

These exist because every one of them is a bug that renders rather than raises. A dropped
QSS property, a rounding mismatch or the wrong letter-spacing unit produces a window that
opens, works, and quietly does not match the design -- so the only way to notice is to
assert it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QColor, QFont, QFontMetricsF  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402

from asomedeck.ui import theme as th  # noqa: E402

# --- tokens (no QApplication needed) -------------------------------------------------


def test_both_modes_define_the_same_tokens():
    # A token present in one mode only is a KeyError the moment somebody switches theme.
    assert set(th.TOKENS["dark"]) == set(th.TOKENS["light"])


def test_every_token_is_a_hex_colour():
    for mode, tokens in th.TOKENS.items():
        for name, value in tokens.items():
            assert value.startswith("#") and len(value) == 7, f"{mode}.{name} = {value}"


def test_every_file_hue_is_a_pair_of_hex_colours():
    # The same failure as a missing token, one map over: a hue with no light twin paints
    # a dark blue filename on a cream background and nobody notices until they switch.
    for bucket, pair in th.FILE_HUES.items():
        assert len(pair) == 2, bucket
        for value in pair:
            assert value.startswith("#") and len(value) == 7, f"{bucket} = {value}"


def test_every_language_the_editor_knows_has_a_file_hue():
    # Both tables are keyed by what files.language_for returns, on purpose -- so adding a
    # grammar for the editor and saying nothing about the tree is caught here rather than
    # shipping as a file type that silently stays grey. Answer it by adding a hue; if the
    # answer really is "no colour", that is a bucket left out of FILE_HUES and this line.
    from asomedeck.ui.highlight import GRAMMARS

    unhued = set(GRAMMARS) - set(th.FILE_HUES)
    assert unhued == set(), f"no file hue for {sorted(unhued)}"


def test_a_file_hue_answers_in_the_mode_it_was_asked_in():
    dark = th.Theme("dark").file_color("python")
    light = th.Theme("light").file_color("python")
    assert dark.name() == th.FILE_HUES["python"][0]
    assert light.name() == th.FILE_HUES["python"][1]


def test_an_unknown_type_falls_back_to_the_tiers_the_tree_already_drew():
    # An image, an archive, a .lock. Before there were hues these were the only two
    # colours a filename took, and nothing about them has changed.
    theme = th.Theme("dark")
    assert theme.file_color("plain").name() == theme.hex("text")
    assert theme.file_color("plain", muted=True).name() == theme.hex("dim")


def test_a_muted_hue_still_clears_the_floor_every_other_colour_here_clears():
    # The tier that has to stay readable, because it is most of the tree: an unchanged
    # file. At the factors this started on, the light theme's muted green measured 2.7:1
    # against the cream -- fainter than anything else in the window and fainter than the
    # grey it replaced.
    for mode in ("dark", "light"):
        theme = th.Theme(mode)
        for bucket in th.FILE_HUES:
            muted = theme.file_color(bucket, muted=True)
            ratio = th.Theme._contrast(muted, theme.c("bg"))
            assert ratio >= th.MUTE_FLOOR, f"{mode}.{bucket} = {muted.name()} at {ratio:.2f}"


def test_muting_quietens_in_whichever_direction_the_theme_needs():
    # Measured as contrast against the background rather than as a value, because that is
    # the thing being claimed and the two themes move opposite ways to claim it: dark's
    # hues are light on near-black and quieten by darkening, light's are dark on cream and
    # quieten by lightening. One factor for both would make the light theme's *unchanged*
    # files the loud ones -- and this would still pass if it only checked dark.
    for mode in ("dark", "light"):
        theme = th.Theme(mode)
        for bucket in th.FILE_HUES:
            full = th.Theme._contrast(theme.file_color(bucket), theme.c("bg"))
            muted = th.Theme._contrast(theme.file_color(bucket, muted=True), theme.c("bg"))
            assert muted < full, f"{mode}.{bucket}"


def test_accent_resolves_per_mode():
    assert th.Theme("dark").accent == th.ACCENTS[0]
    assert th.Theme("light").accent == th.ACCENT_LIGHT_DEFAULT
    assert th.Theme("dark", accent=th.ACCENTS[2]).hex("accent") == th.ACCENTS[2]


def test_every_accent_has_a_light_twin():
    # ACCENT_LIGHT is what the light theme substitutes; a preset missing from it would
    # fall through to the darkening loop and land somewhere nobody chose.
    assert set(th.ACCENT_LIGHT) == set(th.ACCENTS)
    for value in th.ACCENT_LIGHT.values():
        assert value.startswith("#") and len(value) == 7


def test_tint_swaps_presets_and_falls_back_to_the_theme_accent():
    dark, light = th.Theme("dark"), th.Theme("light")
    for value in th.ACCENTS:
        # Dark stores and paints the same value; light substitutes the designed twin.
        assert dark.tint(value).name() == value
        assert light.tint(value).name() == th.ACCENT_LIGHT[value]
    # "" is "no override", not a colour, and must not need a branch at the call site.
    assert dark.tint("").name() == dark.hex("accent")
    assert light.tint("").name() == light.hex("accent")


def test_tint_darkens_a_custom_colour_until_it_shows_on_the_light_theme():
    light = th.Theme("light")
    pale = "#fff2a8"   # a custom pick, not a preset: 1.05:1 against the cream bg
    assert th.Theme._contrast(QColor(pale), QColor(light.hex("bg"))) < 3.0
    tinted = light.tint(pale)
    assert th.Theme._contrast(tinted, QColor(light.hex("bg"))) >= 3.0
    # Same colour, darker -- hue survives, so the deck is still marked in what was
    # picked rather than in something arbitrary.
    assert tinted.hue() == QColor(pale).hue()
    # Dark has the room to take it as given.
    assert th.Theme("dark").tint(pale).name() == pale


def test_on_accent_reads_against_its_own_accent():
    # The token exists to be drawn on the accent fill, and the two modes fill with colours
    # on opposite sides of the middle -- so this is the assertion, not "both are dark".
    for mode in ("dark", "light"):
        theme = th.Theme(mode)
        contrast = th.Theme._contrast(QColor(theme.hex("onAccent")),
                                      QColor(theme.hex("accent")))
        assert contrast >= 4.5, f"{mode} onAccent on accent is {contrast:.2f}:1"


def test_half_up_rounding_matches_what_qss_does():
    # Python's round is half-to-even: round(14.5) is 14, round(10.5) is 10. Qt's
    # stylesheet parser rounds half-up. Half the type scale sits on a .5.
    assert [th._px(v) for v in (14.5, 12.5, 11.5, 10.5)] == [15, 13, 12, 11]
    assert th._px(11) == 11 and th._px(19) == 19


# --- QSS ------------------------------------------------------------------------------


def test_qss_never_uses_text_transform(app):
    # Qt parses text-transform and drops it silently, so a DS eyebrow styled this way
    # renders lowercase with no warning anywhere. Casing belongs to RoleLabel.
    for mode in ("dark", "light"):
        # The declaration, not the word -- the stylesheet carries a comment explaining
        # exactly why the property is absent.
        assert "text-transform:" not in th.Theme(mode).qss()


def test_qss_has_no_bare_widget_background_rule(app):
    # A bare QWidget selector reaches every widget, including the container a foreign
    # terminal is parented into. Defaults come from the palette instead.
    for mode in ("dark", "light"):
        assert "QWidget {" not in th.Theme(mode).qss()


def test_qss_applies_without_error(app):
    for mode in ("dark", "light"):
        for accent in th.ACCENTS:
            app.setStyleSheet(th.Theme(mode, accent=accent).qss())
    app.setStyleSheet("")


def test_focus_ring_is_always_present_and_only_changes_colour(app):
    qss = th.Theme("dark").qss()
    # A ring that appears on focus changes the content rect, which resizes the embedded
    # terminal on every click -- a character-cell reflow on a console host.
    assert "Pane {" in qss
    assert "border: 1px solid transparent;" in qss
    assert 'Pane[active="true"]' in qss


def test_qss_carries_no_stray_format_placeholders(app):
    rendered = th.Theme("dark").qss()
    assert "{{" not in rendered and "}}" not in rendered


# --- fonts ----------------------------------------------------------------------------


def test_the_scrollbar_width_comes_from_geom_rather_than_a_literal(app, monkeypatch):
    """The sheet draws the bar; term_view.py reserves the gutter it sits in.

    Two copies of that number disagree by one repaint, and the way it shows up is the
    handle sitting over the last column of every terminal.
    """
    monkeypatch.setitem(th.GEOM, "scrollbar_w", 17)

    rendered = th.Theme("dark").qss()

    assert "width: 17px" in rendered
    assert "height: 17px" in rendered


def test_the_terminal_gutter_covers_the_bar_it_reserves_for(app):
    """The invariant, rather than the arithmetic: whatever the bar is, the gutter fits it."""
    from asomedeck.ui.term_view import PAD_RIGHT

    assert PAD_RIGHT >= th.GEOM["scrollbar_w"]


def test_font_uses_absolute_letter_spacing(app):
    # PercentageSpacing is a percentage of character advance, not of the em. For a
    # monospace face the DS .18em lands at roughly 61% of its intended tracking.
    font = th.Theme().font("brand")
    assert font.letterSpacingType() == QFont.SpacingType.AbsoluteSpacing
    # Qt quantizes letter spacing, so 1.98 comes back as 1.96875.
    assert font.letterSpacing() == pytest.approx(0.18 * 11, abs=0.05)


def test_absolute_spacing_hits_the_design_target(app):
    family, size, weight, spacing = th.TYPE["brand"]
    # Same family, size and weight as the role -- otherwise the measurement compares
    # typography, not tracking.
    plain = QFont(family)
    plain.setPixelSize(th._px(size))
    plain.setWeight(weight)
    tracked = th.Theme().font("brand")
    word = "ASOMEDECK"
    added = (
        QFontMetricsF(tracked).horizontalAdvance(word)
        - QFontMetricsF(plain).horizontalAdvance(word)
    ) / len(word)
    assert added == pytest.approx(spacing * size, abs=0.1)

    # And the point of the fix: percentage spacing would fall well short of the target.
    percent = QFont(plain)
    percent.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100 + spacing * 100)
    added_pct = (
        QFontMetricsF(percent).horizontalAdvance(word)
        - QFontMetricsF(plain).horizontalAdvance(word)
    ) / len(word)
    assert added_pct < added * 0.8


def test_roles_without_tracking_get_none(app):
    assert th.Theme().font("tab").letterSpacing() == 0.0


def test_every_role_resolves(app):
    for role in th.TYPE:
        font = th.Theme().font(role)
        assert font.pixelSize() > 0


def test_uppercase_applies_only_to_the_ds_uppercase_roles():
    assert th.Theme.text_for("eyebrow", "Decks") == "DECKS"
    assert th.Theme.text_for("brand", "AsomeDeck") == "ASOMEDECK"
    assert th.Theme.text_for("tab", "agent-1") == "agent-1"


def test_role_label_uppercases_because_qss_cannot(app):
    from asomedeck.ui.widgets.labels import EyebrowLabel

    label = EyebrowLabel("Changed files")
    assert label.text() == "CHANGED FILES"
    assert isinstance(label, QLabel)


def test_load_fonts_is_not_fatal_when_the_directory_is_missing(app, tmp_path):
    # A missing typeface is cosmetic. Refusing to start would trade it for a total failure.
    available = th.load_fonts(tmp_path / "nope")
    assert set(available) == {th.FONT_UI, th.FONT_MONO}


# --- state dots -------------------------------------------------------------------------


def test_live_and_dead_are_distinguishable(app):
    # Spec 14.3: where no title pattern matches, these two are all we can honestly claim.
    theme = th.Theme()
    assert theme.dot_color("live") != theme.dot_color("dead")
    assert theme.dot_pulses("live") is False
    assert theme.dot_pulses("dead") is False


def test_only_live_states_pulse(app):
    """Motion means something is happening, not merely that something is noteworthy.

    ``waiting`` pulsed until now, when it meant "a launcher's title pattern matched". It
    means "the turn finished" now, which is a state at rest -- and a resting dot that
    keeps moving reads as work still going on.
    """
    theme = th.Theme()
    assert theme.dot_pulses("running") is True
    assert theme.dot_pulses("attention") is True
    assert theme.dot_pulses("waiting") is False
    assert theme.pulse_period_ms("attention") == 1400
    assert theme.pulse_period_ms("running") == 2200


def test_hue_separates_wants_you_from_carries_on(app):
    """Running and idle share the accent on purpose: neither is asking for anything, and
    the difference between them is carried by the pulse. The two colours that do stand
    apart are the two worth crossing the room for."""
    theme = th.Theme()
    assert theme.dot_color("running") == theme.dot_color("live")
    for state in ("attention", "waiting"):
        assert theme.dot_color(state) != theme.dot_color("live")
    assert theme.dot_color("attention") != theme.dot_color("waiting")


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_the_attention_colour_is_visible_in_both_themes(mode):
    """A dot is a non-text indicator, so WCAG 1.4.11's 3:1 against the sidebar is the
    floor. `warn` is the one token added for this and the only one never measured."""
    theme = th.Theme(mode=mode)
    assert th.Theme._contrast(QColor(theme.hex("warn")),
                              QColor(theme.hex("panel"))) >= 3.0


def test_status_dot_does_not_animate_an_unknown_state(app):
    from PySide6.QtCore import QVariantAnimation

    from asomedeck.ui.widgets.status_dot import DEAD, LIVE, RUNNING, StatusDot

    dot = StatusDot(DEAD)
    assert dot._animation.state() != QVariantAnimation.State.Running
    dot.set_state(LIVE)
    assert dot._animation.state() != QVariantAnimation.State.Running
    dot.set_state(RUNNING)
    assert dot._animation.state() == QVariantAnimation.State.Running
    dot.set_state(DEAD)
    assert dot._animation.state() != QVariantAnimation.State.Running
