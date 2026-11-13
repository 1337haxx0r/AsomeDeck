"""The editor's syntax colouring (ui/highlight.py).

The cases worth pinning are not "are keywords blue". They are the two failures every
regex-list highlighter has, and the reason this one scans a single alternation instead:
a comment character inside a string, and a quote inside a comment. Both are asserted
below, in both directions.

Colours are checked by theme *token*, never by hex -- a test that knew the accent was
#e5a83f would be a second source of truth for it, which is the thing theme.py exists to
prevent.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6.QtGui")

from PySide6.QtGui import QTextDocument  # noqa: E402
from PySide6.QtWidgets import QPlainTextEdit  # noqa: E402

from asomedeck.ui.highlight import GRAMMARS, ROLES, CodeHighlighter  # noqa: E402
from asomedeck.ui.theme import Theme  # noqa: E402

#: Editors are kept alive for the duration: a QPlainTextEdit garbage-collected out from
#: under its document takes the formats with it.
_KEEP: list[QPlainTextEdit] = []


def _document(theme: Theme, language: str, text: str) -> QTextDocument:
    """Highlight through a real editor widget, because that is the only way it happens.

    A bare QTextDocument has no document layout, and without one QSyntaxHighlighter
    never processes the contentsChange it subscribed to -- the formats come back empty
    unless ``rehighlight()`` is called by hand. Testing it that way would be testing a
    path the application does not use; going through the widget tests the real one.
    """
    edit = QPlainTextEdit()
    _KEEP.append(edit)
    CodeHighlighter(edit.document(), theme, language)
    edit.setPlainText(text)
    return edit.document()


def _token_at(theme: Theme, document: QTextDocument, line: int, column: int) -> str:
    """Which theme token the character at (line, column) was painted in, or ""."""
    block = document.findBlockByNumber(line)
    for span in block.layout().formats():
        if span.start <= column < span.start + span.length:
            painted = span.format.foreground().color().name().lower()
            for token in {token for token, _i, _b in ROLES.values()}:
                if theme.hex(token).lower() == painted:
                    return token
    return ""


@pytest.fixture
def theme(app):
    _ = app          # QTextDocument needs a QGuiApplication for its font engine
    return Theme("dark")


# --- the two cases a rule list gets wrong ----------------------------------------


def test_a_hash_inside_a_string_is_not_a_comment(theme):
    line = 'title = "# not a comment"'
    document = _document(theme, "python", line)
    assert _token_at(theme, document, 0, line.index("#")) == "add"        # string


def test_a_quote_inside_a_comment_does_not_open_a_string(theme):
    line = '# it is 5 o\'clock and this " is not a string'
    document = _document(theme, "python", line)
    assert _token_at(theme, document, 0, line.index('"')) == "dim"        # comment
    assert _token_at(theme, document, 0, len(line) - 1) == "dim"


def test_a_double_slash_inside_a_javascript_string_is_not_a_comment(theme):
    line = 'const url = "https://example.com"'
    document = _document(theme, "js", line)
    assert _token_at(theme, document, 0, line.index("//")) == "add"


# --- the ordinary cases ----------------------------------------------------------


def test_keywords_comments_strings_and_numbers(theme):
    line = 'def go():  # start\n    return "x", 42'
    document = _document(theme, "python", line)
    assert _token_at(theme, document, 0, 0) == "accent"                  # def
    assert _token_at(theme, document, 0, 11) == "dim"                    # comment
    assert _token_at(theme, document, 1, 4) == "accent"                  # return
    assert _token_at(theme, document, 1, 12) == "add"                    # "x"
    assert _token_at(theme, document, 1, 17) == "tool"                   # 42


def test_a_decorator_is_marked_as_structure(theme):
    document = _document(theme, "python", "@property\ndef x(self): pass")
    assert _token_at(theme, document, 0, 0) == "tool"


# --- multi-line spans ------------------------------------------------------------


def test_a_docstring_colours_every_line_it_spans(theme):
    document = _document(theme, "python",
                         'def f():\n    """One.\n\n    Two.\n    """\n    return 1')
    # Line 2 is blank and has no character to colour; that it is *skipped over* rather
    # than ending the span is what line 3 proves.
    for line in (1, 3, 4):
        assert _token_at(theme, document, line, 4) == "add", f"line {line}"
    # And it ends: the line after the closing quotes is code again.
    assert _token_at(theme, document, 5, 4) == "accent"


def test_a_keyword_inside_a_docstring_is_not_highlighted(theme):
    """The span pass claims the range, and the alternation skips what it claimed."""
    document = _document(theme, "python", '"""\nreturn is a word here\n"""')
    assert _token_at(theme, document, 1, 0) == "add"


def test_a_c_style_block_comment_spans_lines(theme):
    document = _document(theme, "clike", "/* one\n   two */\nint x = 1;")
    assert _token_at(theme, document, 1, 3) == "dim"
    assert _token_at(theme, document, 2, 0) == "accent"                  # int


def test_an_unterminated_span_does_not_leak_past_the_document(theme):
    document = _document(theme, "python", '"""never closed\nstill inside')
    assert _token_at(theme, document, 1, 0) == "add"


def test_an_unterminated_quote_stops_at_its_own_line(theme):
    """A stray apostrophe must not colour the rest of the file.

    This is why the string fragments exclude a newline: with ``.`` matching everything
    the next thousand lines would turn into one string as the user typed.
    """
    document = _document(theme, "python", "x = 'oops\ny = 1")
    assert _token_at(theme, document, 1, 0) == ""


# --- data formats ----------------------------------------------------------------


def test_a_json_key_is_told_apart_from_a_json_value(theme):
    line = '{"name": "asomedeck"}'
    document = _document(theme, "json", line)
    assert _token_at(theme, document, 0, 2) == "tool"                    # key
    assert _token_at(theme, document, 0, 10) == "add"                    # value


def test_a_markdown_heading_is_structure_not_a_comment(theme):
    document = _document(theme, "markdown", "# Title\nbody text")
    assert _token_at(theme, document, 0, 0) == "tool"
    assert _token_at(theme, document, 1, 0) == ""


def test_a_toml_table_header_and_key(theme):
    document = _document(theme, "toml", "[deck]\nname = \"x\"")
    assert _token_at(theme, document, 0, 0) == "tool"
    assert _token_at(theme, document, 1, 0) == "tool"


# --- markup, css and sql ---------------------------------------------------------


def test_a_tag_an_attribute_and_a_value_are_three_different_things(theme):
    line = '<body class="dark">'
    document = _document(theme, "markup", line)
    assert _token_at(theme, document, 0, 1) == "accent"                  # the tag name
    assert _token_at(theme, document, 0, line.index("class")) == "tool"  # the attribute
    assert _token_at(theme, document, 0, line.index('"dark"')) == "add"  # the value


def test_a_doctype_does_not_open_a_string_on_its_quote(theme):
    """The doctype runs to the angle bracket in one piece, so the quote inside it is
    never seen as the start of anything."""
    line = '<!DOCTYPE html PUBLIC "-//W3C//DTD">'
    document = _document(theme, "markup", line + "\n<p>text")
    assert _token_at(theme, document, 0, line.index('"')) == "tool"
    assert _token_at(theme, document, 1, 1) == "accent"                  # <p, unaffected


def test_a_css_hash_is_a_selector_on_a_rule_line_and_a_colour_on_a_property_line(theme):
    """The only honest way to tell #fff from #fff is the line it is on: an id opens a
    block and a colour sets a property, so the brace decides."""
    document = _document(theme, "css", "#fff {\n  color: #fff;\n}")
    assert _token_at(theme, document, 0, 0) == "accent"                  # the selector
    assert _token_at(theme, document, 1, len("  color: ")) == "tool"     # the colour


def test_a_url_in_css_is_not_a_line_comment(theme):
    line = "  background: url(http://cdn/x.png);"
    document = _document(theme, "css", line)
    assert _token_at(theme, document, 0, line.index("//")) == ""


def test_a_doubled_quote_is_an_escape_in_sql_not_two_strings(theme):
    """SQL escapes a quote by doubling it, so the -- inside this one is not a comment."""
    line = "note TEXT DEFAULT 'it''s -- really'"
    document = _document(theme, "sql", line)
    assert _token_at(theme, document, 0, line.index("--")) == "add"


def test_sql_keywords_are_case_blind(theme):
    document = _document(theme, "sql", "select id from t;\nSELECT id FROM t;")
    assert _token_at(theme, document, 0, 0) == "accent"
    assert _token_at(theme, document, 1, 0) == "accent"


def test_a_postgres_cast_is_not_a_bind_parameter(theme):
    line = "SELECT '2026-01-01'::date AS d"
    document = _document(theme, "sql", line)
    assert _token_at(theme, document, 0, line.index("::date") + 2) == ""


# --- the two shells this app actually runs ---------------------------------------


def test_a_cmdlet_keeps_its_noun(theme):
    """One token. With the keyword list ahead of it this would be the keyword "Exit"
    with an unpainted "-PSSession" hanging off it, which reads as a typo."""
    line = "Exit-PSSession"
    document = _document(theme, "powershell", line)
    assert _token_at(theme, document, 0, line.index("PSSession")) == "accent"


def test_a_powershell_operator_and_a_parameter_are_told_apart(theme):
    """Both are a hyphen after a space, so only the listing order separates them."""
    line = "if ($a -eq $b) { Get-Item -Path . }"
    document = _document(theme, "powershell", line)
    assert _token_at(theme, document, 0, line.index("-eq")) == "accent"
    assert _token_at(theme, document, 0, line.index("-Path")) == "tool"


def test_a_backtick_escaped_quote_stays_inside_the_string(theme):
    """PowerShell escapes with a backtick, not a backslash."""
    line = 'Write-Host "a `" b" -ForegroundColor Yellow'
    document = _document(theme, "powershell", line)
    assert _token_at(theme, document, 0, line.index("b")) == "add"


def test_an_at_prefixed_rem_is_all_comment(theme):
    """The comment rule is anchored at the start of the line, so it has to be listed
    before the @ rule -- the other way round the @ is claimed as meta and the comment
    itself goes unpainted."""
    line = "@REM build the deck"
    document = _document(theme, "batch", line)
    assert _token_at(theme, document, 0, 0) == "dim"
    assert _token_at(theme, document, 0, len(line) - 1) == "dim"


def test_a_batch_loop_variable_keeps_both_percent_signs(theme):
    line = "echo %%~nxf"
    document = _document(theme, "batch", line)
    assert _token_at(theme, document, 0, line.index("%%")) == "tool"


# --- the two rules that changed for the languages riding on existing grammars -----


def test_a_jsonc_comment_is_a_comment_but_a_url_is_not(theme):
    """.jsonc exists for the // that json does not have; the // in a URL starts later
    than the quote that opened the string, and leftmost wins."""
    line = '{"url": "https://x"} // trailing'
    document = _document(theme, "json", line)
    assert _token_at(theme, document, 0, line.index("//", 20)) == "dim"
    assert _token_at(theme, document, 0, line.index("https")) == "add"


def test_a_c_directive_is_structure_but_a_stray_hash_is_not(theme):
    """.php rides on the clike grammar, and PHP comments with '#'. Naming the
    directives leaves that unpainted instead of colouring it as structure."""
    document = _document(theme, "clike", "#include <stdio.h>\n# TODO not a directive")
    assert _token_at(theme, document, 0, 0) == "tool"
    assert _token_at(theme, document, 1, 0) == ""


# --- the fallback ----------------------------------------------------------------


def test_an_unknown_language_paints_nothing(theme):
    """"plain" is a real answer, not a failure -- colouring a file by a grammar it is
    not written in reads as a bug in the file."""
    document = _document(theme, "plain", 'def go(): # "hi"')
    assert all(_token_at(theme, document, 0, column) == ""
               for column in range(len('def go(): # "hi"')))


def test_every_grammar_compiles_and_survives_odd_input(theme):
    """No grammar may raise on a line, however unbalanced.

    A highlighter that throws takes the paint event with it, and the pane renders empty
    with a traceback in the log -- from a file the user merely opened.
    """
    awkward = '"\'`/*#[](){}\\ \t\n"""\n<<<<<<< HEAD\n\x7f'
    for language in GRAMMARS:
        _document(theme, language, awkward)


def test_the_markup_attribute_rule_does_not_rescan_body_text(theme):
    r"""A lookbehind is the only thing keeping this linear.

    Without ``(?<![\w.:-])`` the attribute rule is attempted from every letter of every
    word and re-reads to the end of the line from each -- quadratic, and measured at
    97ms on one paragraph against a tenth of a millisecond with it. No correctness test
    can see that, and a paragraph of prose is what an .html file is mostly made of.
    """
    _ = theme
    paragraph = "word " * 800
    start = time.perf_counter()
    list(GRAMMARS["markup"].master.finditer(paragraph))
    assert time.perf_counter() - start < 0.05
