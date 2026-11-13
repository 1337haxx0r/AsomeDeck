"""Syntax colouring for the editor pane, in five colours and no dependency.

Pygments would do this for four hundred languages. It is also 1.5 MB, a second licence,
and a full lexer run per repaint -- and the value here is not fidelity, it is *shape*:
seeing at a glance where the strings and comments are in a file you are peeking at
between two terminals. Five roles carry that, and they map onto tokens the theme already
defines, so a theme switch takes the code with it.

The one thing a naive highlighter always gets wrong is a delimiter inside another
delimiter -- ``"# not a comment"`` coloured as a comment, ``# a " quote`` starting a
string that runs to the end of the file. Applying a list of regexes in sequence cannot
fix that, because each one sees the whole line and none of them knows what the others
matched.

So each grammar compiles to *one* alternation with named groups, and ``finditer`` scans
it left to right. Whatever starts first wins and the scan resumes after it, which is
what a lexer does and is the reason both cases above come out right. Multi-line
constructs -- docstrings, ``/* */``, fenced code -- run first, in a pass that carries
state across blocks the way ``QSyntaxHighlighter`` intends, and the alternation skips
what that pass already claimed.
"""

from __future__ import annotations

import re

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

from .theme import Theme

#: Token role -> (theme token, italic, bold). Five roles, because five is what reading a
#: file at a glance actually needs; a palette of twenty is a design, not information.
ROLES: dict[str, tuple[str, bool, bool]] = {
    "comment": ("dim", True, False),
    "string": ("add", False, False),
    "number": ("tool", False, False),
    "keyword": ("accent", False, False),
    #: Decorators, markdown headings, mapping keys -- the structure of the file rather
    #: than the code in it.
    "meta": ("tool", False, True),
}

_PY_KEYWORDS = (
    "False None True and as assert async await break case class continue def del elif "
    "else except finally for from global if import in is lambda match nonlocal not or "
    "pass raise return try while with yield"
).split()

_JS_KEYWORDS = (
    "abstract any as async await boolean break case catch class const constructor "
    "continue declare default delete do else enum export extends extends false finally "
    "for from function get if implements import in instanceof interface let new null "
    "number of private protected public readonly return set static string super switch "
    "this throw true try type typeof undefined var void while yield"
).split()

_CLIKE_KEYWORDS = (
    "auto bool break case catch chan char class const constexpr continue default defer "
    "delete do double else enum extern false float fn for func go goto if impl import "
    "inline int interface let long map match mod mut namespace new nullptr operator "
    "package private protected pub public range register return self short signed "
    "sizeof static struct switch template this throw trait true try type typedef "
    "typename union unsigned use using var virtual void volatile where while"
).split()

_SHELL_KEYWORDS = (
    "alias case cd do done elif else esac exit export fi for function if in local "
    "readonly return set shift source then trap unset until while"
).split()

_SQL_KEYWORDS = (
    "add all alter and any as asc begin between by cascade case cast check coalesce "
    "collate column commit constraint create cross current_date current_timestamp "
    "database default delete desc distinct drop else end escape except exists explain "
    "extract false fetch first foreign from full grant group having if ilike in index "
    "inner insert intersect into is join key left like limit not null nulls offset on "
    "or order outer over partition primary procedure references rename replace return "
    "returning right rollback row rows select set some table temporary then to "
    "transaction trigger true truncate union unique update using values view when where "
    "window with"
).split()

_PS_KEYWORDS = (
    "begin break catch class continue data do dynamicparam else elseif end enum exit "
    "filter finally for foreach from function hidden if in param process return static "
    "switch throw trap try until using while workflow"
).split()

#: The approved verbs, so that a cmdlet is told from Content-Type by its verb rather than
#: by the shape of the hyphen.
_PS_VERBS = (
    "add clear close compare connect convert convertfrom convertto copy disable "
    "disconnect enable enter exit export find foreach format get grant group import "
    "initialize install invoke join measure mount move new open out pop protect publish "
    "push read receive register remove rename repair reset resolve restart restore "
    "resume revoke save search select send set show sort split start step stop suspend "
    "sync test trace unblock uninstall unprotect unpublish unregister update use wait "
    "where write"
).split()

_PS_OPERATORS = (
    "and as band bnot bor bxor ccontains ceq cne contains creplace eq f ge gt in is "
    "isnot join le like lt match ne not notcontains notin notlike notmatch or replace "
    "shl shr split xor"
).split()

_BATCH_KEYWORDS = (
    "call cd cls color copy defined del dir do echo else endlocal equ errorlevel exist "
    "exit find findstr for geq goto gtr if in leq lss mkdir move neq not pause popd "
    "pushd rd ren rmdir set setlocal shift start title type verify"
).split()

#: Shared fragments. Each is written so that it cannot run away past its own line: an
#: unterminated quote colours to the end of the line and stops, rather than swallowing
#: the rest of the file the moment somebody types a apostrophe.
_DQ = r'"(?:\\.|[^"\\\n])*"?'
_SQ = r"'(?:\\.|[^'\\\n])*'?"
_BQ = r"`(?:\\.|[^`\\\n])*`?"
_NUM = r"\b(?:0[xXbBoO][0-9a-fA-F_]+|\d[\d_]*(?:\.\d*)?(?:[eE][+-]?\d+)?)\b"

#: A single-quoted string that escapes its quote by doubling it rather than with a
#: backslash. SQL and PowerShell both do this, and reading the backslash as an escape is
#: what makes a Windows path like 'C:\temp\' swallow the rest of the line.
_SQ2 = r"'(?:''|[^'\n])*'?"

#: PowerShell escapes with a backtick, so `" stays inside the string and \" does not.
_PS_DQ = r'"(?:`.|[^"`\n])*"?'

#: _NUM ends in \b, which forbids a trailing letter: it cannot match "10px" at all. CSS
#: is the one place where the unit is part of the token.
_CSS_NUM = r"(?<![\w.-])[+-]?(?:\d*\.\d+|\d+)(?:%|[a-zA-Z]+)?"


def _words(words: list[str], ignore_case: bool = False) -> str:
    """The alternation for a keyword list, longest first so ``in`` cannot shadow ``int``.

    ``(?i:...)`` is a *scoped* flag group rather than a flag on the whole pattern,
    because SQL, batch and PowerShell are case-blind, the other five grammars are not,
    and every one of them ends up in the same ``master`` alternation.
    """
    body = "|".join(sorted(set(words), key=len, reverse=True))
    return rf"\b(?i:{body})\b" if ignore_case else rf"\b(?:{body})\b"


class Grammar:
    """One language: the alternation, and any multi-line spans it can open."""

    __slots__ = ("master", "spans", "span_role")

    def __init__(self, parts: list[tuple[str, str]],
                 spans: tuple[tuple[str, str], ...] = (),
                 span_role: str = "string") -> None:
        # Group names must be unique, so a role appearing more than once is numbered and
        # mapped back by splitting on the underscore.
        pattern = "|".join(f"(?P<{role}_{i}>{expr})" for i, (role, expr) in enumerate(parts))
        self.master = re.compile(pattern)
        self.spans = spans
        self.span_role = span_role


def _python() -> Grammar:
    return Grammar(
        [
            ("comment", r"#[^\n]*"),
            ("string", rf"(?:[rRbBfFuU]{{0,2}})(?:{_DQ}|{_SQ})"),
            ("meta", r"^\s*@[\w.]+"),
            ("keyword", _words(_PY_KEYWORDS)),
            ("number", _NUM),
        ],
        spans=(('"""', '"""'), ("'''", "'''")),
    )


def _js() -> Grammar:
    return Grammar(
        [
            ("comment", r"//[^\n]*"),
            ("string", rf"{_DQ}|{_SQ}|{_BQ}"),
            ("meta", r"^\s*@[\w.]+"),
            ("keyword", _words(_JS_KEYWORDS)),
            ("number", _NUM),
        ],
        spans=(("/*", "*/"),),
        span_role="comment",
    )


def _clike() -> Grammar:
    return Grammar(
        [
            ("comment", r"//[^\n]*"),
            # The directives by name rather than ^#\w+, because .php rides on this
            # grammar and PHP's "# comment" would otherwise come out bold-meta -- a
            # miscolour, which is worse than the no-colour it gets this way.
            ("meta", r"^\s*#\s*(?:endregion|nullable|include|ifndef|warning|pragma"
                     r"|region|define|import|ifdef|endif|error|undef|elif|else|line"
                     r"|if)\b"),
            ("string", rf"{_DQ}|{_SQ}"),
            ("keyword", _words(_CLIKE_KEYWORDS)),
            ("number", _NUM),
        ],
        spans=(("/*", "*/"),),
        span_role="comment",
    )


def _shell() -> Grammar:
    return Grammar(
        [
            ("comment", r"#[^\n]*"),
            ("string", rf"{_DQ}|{_SQ}"),
            ("meta", r"\$\{?\w+\}?"),
            ("keyword", _words(_SHELL_KEYWORDS)),
            ("number", _NUM),
        ]
    )


def _json() -> Grammar:
    return Grammar(
        [
            ("comment", r"//[^\n]*"),          # .jsonc, and tsconfig.json
            # A key is a string with a colon after it. Checked before the plain string
            # alternative, so the two are told apart at the same start position.
            ("meta", r'"(?:\\.|[^"\\\n])*"(?=\s*:)'),
            ("string", _DQ),
            ("keyword", r"\b(?:true|false|null)\b"),
            ("number", _NUM),
        ]
    )


def _yaml() -> Grammar:
    return Grammar(
        [
            ("comment", r"#[^\n]*"),
            ("meta", r"^\s*-?\s*[\w.\-/]+(?=\s*:)"),
            ("string", rf"{_DQ}|{_SQ}"),
            ("keyword", r"\b(?:true|false|null|yes|no|on|off)\b"),
            ("number", _NUM),
        ]
    )


def _toml() -> Grammar:
    return Grammar(
        [
            ("comment", r"[#;][^\n]*"),
            ("meta", r"^\s*(?:\[[^\]\n]*\]|[\w.\-\"]+(?=\s*=))"),
            ("string", rf"{_DQ}|{_SQ}"),
            ("keyword", r"\b(?:true|false)\b"),
            ("number", _NUM),
        ]
    )


def _markdown() -> Grammar:
    return Grammar(
        [
            ("meta", r"^\s{0,3}#{1,6}\s.*|^\s*(?:[-*+]|\d+\.)\s|^\s*>"),
            ("string", r"`[^`\n]*`"),
            ("keyword", r"\*\*[^*\n]+\*\*|__[^_\n]+__"),
            ("comment", r"\[[^\]\n]*\]\([^)\n]*\)"),
        ],
        spans=(("```", "```"),),
    )


def _markup() -> Grammar:
    return Grammar(
        [
            # A doctype or a processing instruction is one thing, not a tag with
            # attributes: it runs to the closing angle bracket in a single piece, so the
            # quotes inside <!DOCTYPE html PUBLIC "-//W3C//..."> never open a string.
            ("meta", r"<!(?i:doctype)[^>\n]*|<\?[\w:-]*|\?>|<!\[CDATA\[|\]\]>"),
            ("keyword", r"</?[A-Za-z_][\w.:-]*|/?>"),
            ("string", rf"{_DQ}|{_SQ}"),
            # An attribute is a name with an equals sign after it. The lookbehind is not
            # cosmetic: without it this rule is attempted from every letter of every word
            # of body text and re-reads to the end of the line from each -- quadratic,
            # and measurably 97ms on one long paragraph against 0.1ms with it.
            ("meta", r"(?<![\w.:-])[A-Za-z_:][\w.:-]*(?=\s*=)"),
            ("number", r"&#?\w+;"),
        ],
        spans=(("<!--", "-->"),),
        span_role="comment",
    )


def _css() -> Grammar:
    return Grammar(
        [
            # SCSS and LESS have // comments and plain CSS does not, but the lookbehind
            # is the part that matters: without it url(http://cdn/x.png) turns the rest
            # of the line into a comment.
            ("comment", r"(?<!:)//[^\n]*"),
            ("string", rf"{_DQ}|{_SQ}"),
            ("meta", r"@[\w-]+|\$[\w-]+|![a-zA-Z-]+"),
            # Everything before the brace on a line that opens a block is the selector.
            # This is also the answer to #fff: an id sits on a line that opens a rule and
            # a colour sits on a line that sets a property, so the brace says which.
            # Listed before the property rule, or "a:hover {" reads a: as a property.
            ("keyword", r"^[^{};@\n]+(?=\{)"),
            # The colon is eaten with the property name, so "background:red" cannot then
            # read ":red" as a pseudo-class.
            ("meta", r"(?:^|(?<=[{;]))\s*[\w-]+\s*:"),
            # The comma catches a selector list whose brace is on a later line, and must
            # precede the hex colour below or "#fff," becomes one.
            ("keyword", r"#[\w-]+(?=\s*,)|(?<!\w)\.[a-zA-Z_-][\w-]*"
                        r"|::?[a-zA-Z-]+(?:\([^)\n]*\))?|\[[^\]\n]{0,80}\]"),
            ("number", r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])"),
            ("number", _CSS_NUM),
        ],
        spans=(("/*", "*/"),),
        span_role="comment",
    )


def _sql() -> Grammar:
    return Grammar(
        [
            ("comment", r"--[^\n]*"),
            # _SQ2, not _SQ: SQL doubles a quote to escape it, and a backslash is just a
            # backslash, so 'C:\' is a closed string rather than an open one.
            ("string", _SQ2),
            # Quoted identifiers -- "col" in standard SQL, `col` in MySQL, [col] in
            # T-SQL -- and :name / @name bind parameters. The two colon guards keep
            # Postgres's ::cast from reading as a parameter named "date".
            ("meta", rf"{_DQ}|{_BQ}|\[[^\]\n]{{0,80}}\]|(?<!:)[@:]\w+(?!:)"),
            ("keyword", _words(_SQL_KEYWORDS, ignore_case=True)),
            ("number", _NUM),
        ],
        spans=(("/*", "*/"),),
        span_role="comment",
    )


def _powershell() -> Grammar:
    return Grammar(
        [
            ("comment", r"#[^\n]*"),
            ("string", rf"{_PS_DQ}|{_SQ2}"),
            ("meta", r"\$(?:\{[^}\n]*\}|[\w:]+)"),
            # Verb-Noun before the keyword list, or Exit-PSSession comes out as the
            # keyword "exit" with an unpainted "-PSSession" hanging off it.
            ("keyword", _words(_PS_VERBS, ignore_case=True) + r"-\w+"),
            # -eq and friends before -Path and friends: both start at a hyphen after a
            # space, and whichever is listed first claims it.
            ("keyword", "-" + _words(_PS_OPERATORS, ignore_case=True)),
            ("meta", r"(?<=\s)-[A-Za-z]\w*"),
            ("keyword", _words(_PS_KEYWORDS, ignore_case=True)),
            ("number", _NUM),
        ],
        spans=(("<#", "#>"),),
        span_role="comment",
    )


def _batch() -> Grammar:
    return Grammar(
        [
            # REM only opens a comment at the start of a line, and :: is a label the
            # parser is meant to trip over -- both are comments to a reader. Listed
            # before the @ rule below, which would otherwise claim the @ of "@REM" and
            # leave the text of the comment unpainted.
            ("comment", r"^\s*(?:@?(?i:rem)\b|::)[^\n]*"),
            ("string", r'"[^"\n]*"?'),          # cmd has no escape character at all
            # The ~ is load-bearing: without it %%~nxf matches from the second % only.
            ("meta", r"%[A-Za-z_][^%\n]*%|%%~?[a-zA-Z]\w*|%~[\w$]*|%[*\d]"
                     r"|![A-Za-z_]\w*!"),
            ("meta", r"^\s*(?:@|:[\w.-]+)"),
            ("keyword", _words(_BATCH_KEYWORDS, ignore_case=True)),
            ("number", _NUM),
        ]
    )


GRAMMARS: dict[str, Grammar] = {
    "python": _python(),
    "js": _js(),
    "clike": _clike(),
    "shell": _shell(),
    "json": _json(),
    "yaml": _yaml(),
    "toml": _toml(),
    "markdown": _markdown(),
    "markup": _markup(),
    "css": _css(),
    "sql": _sql(),
    "powershell": _powershell(),
    "batch": _batch(),
}


class CodeHighlighter(QSyntaxHighlighter):
    """Colours one document in one language.

    ``set_language("plain")`` is a real state, not a failure: a file whose type we do not
    know renders in the foreground colour. Guessing a grammar and colouring the file
    wrongly reads as a bug in the file.
    """

    def __init__(self, document, theme: Theme, language: str = "plain") -> None:
        super().__init__(document)
        self._theme = theme
        self._language = language
        self._formats: dict[str, QTextCharFormat] = {}
        self._build_formats()

    # -- configuration -----------------------------------------------------

    def _build_formats(self) -> None:
        self._formats = {}
        for role, (token, italic, bold) in ROLES.items():
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(self._theme.hex(token)))
            if italic:
                fmt.setFontItalic(True)
            if bold:
                fmt.setFontWeight(QFont.Weight.DemiBold)
            self._formats[role] = fmt

    def set_language(self, language: str) -> None:
        if language == self._language:
            return
        self._language = language
        self.rehighlight()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._build_formats()
        self.rehighlight()

    @property
    def language(self) -> str:
        return self._language

    # -- painting ----------------------------------------------------------

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        grammar = GRAMMARS.get(self._language)
        if grammar is None:
            self.setCurrentBlockState(0)
            return
        covered = self._paint_spans(text, grammar)
        for match in grammar.master.finditer(text):
            start = match.start()
            if any(low <= start < high for low, high in covered):
                continue
            role = (match.lastgroup or "").rsplit("_", 1)[0]
            fmt = self._formats.get(role)
            if fmt is not None:
                self.setFormat(start, match.end() - start, fmt)

    def _paint_spans(self, text: str, grammar: Grammar) -> list[tuple[int, int]]:
        """Colour multi-line constructs and report the ranges they claimed.

        The block state is which span is open at the end of this line, so a docstring
        started on line 4 keeps colouring line 5 without either line seeing the other.
        Zero means none; ``QSyntaxHighlighter`` starts every document at -1, which is why
        anything outside the valid range is read as zero rather than trusted.
        """
        if not grammar.spans:
            self.setCurrentBlockState(0)
            return []

        fmt = self._formats[grammar.span_role]
        covered: list[tuple[int, int]] = []
        state = self.previousBlockState()
        if state not in range(1, len(grammar.spans) + 1):
            state = 0
        position = 0
        span_start = 0

        while True:
            if state:
                closer = grammar.spans[state - 1][1]
                end = text.find(closer, position)
                if end < 0:
                    self.setFormat(span_start, len(text) - span_start, fmt)
                    covered.append((span_start, len(text)))
                    self.setCurrentBlockState(state)
                    return covered
                end += len(closer)
                self.setFormat(span_start, end - span_start, fmt)
                covered.append((span_start, end))
                state, position = 0, end
                continue

            best_at, best_state = -1, 0
            for index, (opener, _closer) in enumerate(grammar.spans, start=1):
                at = text.find(opener, position)
                if at >= 0 and (best_at < 0 or at < best_at):
                    best_at, best_state = at, index
            if best_at < 0:
                self.setCurrentBlockState(0)
                return covered
            span_start, state = best_at, best_state
            position = best_at + len(grammar.spans[best_state - 1][0])
