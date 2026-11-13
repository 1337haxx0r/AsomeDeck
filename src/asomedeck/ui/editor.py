"""A file open in a pane, beside the terminal that mentioned it.

This is not an IDE and is not trying to become one. There is no completion, no symbol
index, no language server and no deck-wide search: the user has an editor for that,
one alt-tab away, and reproducing a bad version of it here would earn nothing. What this
earns is the case the whole app exists for -- an agent says it changed ``src/foo.py``,
and reading the file does not mean leaving the window.

So the scope is: open, read, change a line, save. What that *does* have to get exactly
right is the saving, because a pane that quietly rewrites line endings or drops a BOM is
worse than no editor at all. All of that lives in ``files.py``, which is pure and has the
round-trip tests; this module owns only the widget.

It plugs into the pane tree through the same informal interface ``TerminalPane`` answers
-- ``is_alive``, ``focus_terminal``, ``apply_theme``, ``close_session``, ``pid`` -- which
is why the tree needed no new concept to hold one. ``pane_kind`` is the single addition:
``is_alive()`` is False here (a file is not a running session, and counting it as one
would make the quit prompt claim terminals that do not exist), and without a way to tell
the two apart the window would helpfully launch a shell into every editor pane it found
empty.
"""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QFontMetricsF,
    QKeySequence,
    QPainter,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import files
from .highlight import CodeHighlighter
from .theme import GEOM, Theme, styled
from .widgets.labels import LinkLabel, RoleLabel

log = logging.getLogger(__name__)

#: One press of Tab. Spaces rather than a tab character: this codebase is Python, ruff
#: enforces four spaces, and a stray hard tab in a file edited here would be a diff the
#: user did not make.
INDENT = "    "

#: Left and right breathing room around the line numbers.
GUTTER_PAD = 10

#: Save. Deliberately *not* registered on the window like the rest of spec 9's keys:
#: window scope fires wherever focus is, and Ctrl+S sent to a terminal is XOFF -- it
#: freezes the output of whatever is running there, which looks exactly like a hang.
#: Scoped to this widget, it reaches the editor only when the editor has focus.
KEY_SAVE = "Ctrl+S"


def _newline_label(newline: str) -> str:
    return {"\r\n": "CRLF", "\r": "CR"}.get(newline, "LF")


class _Gutter(QWidget):
    """Line numbers. A child of the editor's viewport margin, painted by the editor."""

    def __init__(self, editor: CodeEdit) -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self._editor.gutter_width(), 0)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._editor.paint_gutter(event)


class CodeEdit(QPlainTextEdit):
    """A plain text editor with line numbers, a current-line rule and block indent."""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setObjectName("CodeEdit")
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(
            QFontMetricsF(theme.font("code")).horizontalAdvance(" ") * len(INDENT))
        self.setFont(theme.font("code"))
        # Undo is what makes an accidental edit in a pane you meant to read harmless.
        self.setUndoRedoEnabled(True)

        self._gutter = _Gutter(self)
        self.blockCountChanged.connect(lambda _n: self._resize_gutter())
        self.updateRequest.connect(self._on_update_request)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._resize_gutter()
        self._highlight_current_line()

    # -- gutter ------------------------------------------------------------

    def gutter_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        advance = QFontMetricsF(self._theme.font("gutter")).horizontalAdvance("9")
        return int(advance * digits + GUTTER_PAD * 2 + 0.5)

    def _resize_gutter(self) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _on_update_request(self, rect: QRect, dy: int) -> None:
        self._gutter.scroll(0, dy) if dy else self._gutter.update(
            0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._resize_gutter()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        box = self.contentsRect()
        self._gutter.setGeometry(QRect(box.left(), box.top(),
                                       self.gutter_width(), box.height()))

    def paint_gutter(self, event) -> None:
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), self._theme.c("panel"))
        painter.setFont(self._theme.font("gutter"))

        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()
        current = self.textCursor().blockNumber()
        height = int(self.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                # The line the cursor is on is the one you are looking for when you
                # glance at a gutter, so it is the only one at full contrast.
                painter.setPen(self._theme.c("dim2") if number == current
                               else self._theme.c("label"))
                painter.drawText(0, int(top), self._gutter.width() - GUTTER_PAD, height,
                                 int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                                 str(number + 1))
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            number += 1

    # -- current line ------------------------------------------------------

    def _highlight_current_line(self) -> None:
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(self._theme.c("hover"))
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        cursor = self.textCursor()
        cursor.clearSelection()
        selection.cursor = cursor
        self.setExtraSelections([selection])

    # -- input -------------------------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Tab indents the selection instead of replacing it.

        The default would delete however many lines are selected and leave a tab, which
        in an editor opened to glance at a file is a destructive surprise one keystroke
        away from the arrow keys.
        """
        key = event.key()
        cursor = self.textCursor()
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab) and cursor.hasSelection():
            self._shift_block(cursor, out=key == Qt.Key.Key_Backtab)
            return
        if key == Qt.Key.Key_Backtab:
            self._shift_block(cursor, out=True)
            return
        if key == Qt.Key.Key_Tab:
            cursor.insertText(INDENT)
            return
        super().keyPressEvent(event)

    def _shift_block(self, cursor: QTextCursor, *, out: bool) -> None:
        start, end = sorted((cursor.selectionStart(), cursor.selectionEnd()))
        document = self.document()
        first = document.findBlock(start).blockNumber()
        last = document.findBlock(max(start, end - 1)).blockNumber()

        cursor.beginEditBlock()
        for number in range(first, last + 1):
            block = document.findBlockByNumber(number)
            line = QTextCursor(block)
            if out:
                text = block.text()
                strip = len(text) - len(text.lstrip(" "))
                if not strip:
                    continue
                line.movePosition(QTextCursor.MoveOperation.Right,
                                  QTextCursor.MoveMode.KeepAnchor, min(strip, len(INDENT)))
                line.removeSelectedText()
            else:
                line.insertText(INDENT)
        cursor.endEditBlock()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setFont(theme.font("code"))
        self._highlight_current_line()
        self._gutter.update()


class EditorPane(QWidget):
    """One file, as a pane body.

    ``pane_kind`` and ``is_alive`` together are what stop the window treating this as a
    terminal; everything else on the surface exists because ``Pane`` or ``MainWindow``
    calls it by name on whatever body a pane happens to hold.
    """

    #: Told apart from a terminal by this, not by an isinstance check -- main_window
    #: imports the renderers lazily so a missing backend is a dialog, not a dead window.
    pane_kind = "editor"

    titleChanged = Signal(str)
    dirtyChanged = Signal(bool)
    #: "python · utf-8 · LF · 231 lines", for the pane footer.
    statusChanged = Signal(str)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._path = ""
        self._loaded: files.Loaded | None = None
        self._dirty = False
        #: Set while the buffer is being replaced from disk, so the resulting
        #: textChanged does not mark the file dirty the instant it is opened.
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.banner = self._build_banner()
        self.banner.hide()
        layout.addWidget(self.banner)

        self.edit = CodeEdit(theme, self)
        self.edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.edit, 1)

        self.highlighter = CodeHighlighter(self.edit.document(), theme)

        action = QAction("Save", self)
        action.setShortcut(QKeySequence(KEY_SAVE))
        action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        action.triggered.connect(lambda: self.save())
        self.addAction(action)

    def _build_banner(self) -> QWidget:
        bar = QWidget()
        styled(bar, "EditorBanner")
        bar.setFixedHeight(GEOM["pane_header_h"])
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(12)
        self.banner_label = RoleLabel("paneCmd", "", self._theme)
        self.banner_label.setObjectName("PaneCmd")
        row.addWidget(self.banner_label)
        row.addStretch(1)
        reload_link = LinkLabel("panelKey", "reload", self._theme)
        reload_link.setObjectName("PanelKey")
        reload_link.clicked.connect(lambda: self.reload(force=True))
        row.addWidget(reload_link)
        keep = LinkLabel("panelKey", "keep mine", self._theme)
        keep.setObjectName("PanelKey")
        keep.clicked.connect(self._keep_mine)
        row.addWidget(keep)
        return bar

    # -- pane body surface -------------------------------------------------

    def is_alive(self) -> bool:
        """False, always.

        A file is not a running session. Answering True here would put editor panes into
        ``live_panes()``, and the quit prompt would offer to close terminals that are
        text files.
        """
        return False

    def focus_terminal(self) -> None:
        self.edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def close_session(self, force: bool = False) -> None:
        """Nothing to end. Unsaved work is the window's business, not the pane's."""
        _ = force

    @property
    def pid(self) -> str:
        return ""

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.edit.apply_theme(theme)
        self.highlighter.apply_theme(theme)
        for label in (self.banner_label,):
            label.apply_theme(theme)

    # -- file --------------------------------------------------------------

    @property
    def file_path(self) -> str:
        return self._path

    @property
    def dirty(self) -> bool:
        return self._dirty

    @property
    def title(self) -> str:
        if not self._path:
            return "no file"
        name = os.path.basename(self._path)
        # The bullet is what every editor uses for unsaved, and it survives the tab
        # strip, the pane header and the sidebar without any of them needing to know why.
        return f"● {name}" if self._dirty else name

    def open_path(self, path: str) -> bool:
        """Show a file. False if it could not be opened, or the user backed out.

        The refusals are as much of the feature as the success: a 40 MB log and a PNG
        both reach this method by an ordinary click in the tree, and either one loaded
        into a QTextDocument is a frozen window.
        """
        if self._dirty and os.path.normcase(path) != os.path.normcase(self._path):
            if not self._offer_to_save():
                return False

        kind = files.classify(path)
        if kind != files.TEXT:
            self._show_refusal(path, kind)
            return False
        try:
            loaded = files.load(path)
        except OSError as exc:
            log.warning("cannot open %s: %s", path, exc)
            self._show_message(f"Cannot open {os.path.basename(path)}", str(exc))
            return False

        self._path = path
        self._loaded = loaded
        self.highlighter.set_language(files.language_for(path))
        self._loading = True
        self.edit.setPlainText(loaded.text)
        self._loading = False
        # A freshly loaded buffer has no history worth undoing back into.
        self.edit.document().clearUndoRedoStacks()
        self.edit.moveCursor(QTextCursor.MoveOperation.Start)
        self._set_dirty(False)
        self.banner.hide()
        self._emit_status()
        log.info("opened %s (%s, %s, %s)", path, self.highlighter.language,
                 loaded.encoding, _newline_label(loaded.newline))
        return True

    def save(self) -> bool:
        """Write the buffer back. True if it landed on disk."""
        if not self._path or self._loaded is None:
            return False
        if not self._dirty:
            return True
        try:
            self._loaded = files.save(self._path, self.edit.toPlainText(),
                                      self._loaded.encoding, self._loaded.newline)
        except OSError as exc:
            log.warning("cannot save %s: %s", self._path, exc)
            self._show_message(f"Cannot save {os.path.basename(self._path)}", str(exc))
            return False
        self._set_dirty(False)
        self.banner.hide()
        self._emit_status()
        log.info("saved %s (%d bytes)", self._path, self._loaded.size)
        return True

    def reload(self, *, force: bool = False) -> bool:
        """Re-read from disk. ``force`` discards unsaved changes without asking."""
        if not self._path:
            return False
        if self._dirty and not force:
            return False
        position = self.edit.textCursor().position()
        self._dirty = False          # so open_path does not offer to save what we drop
        if not self.open_path(self._path):
            return False
        cursor = self.edit.textCursor()
        cursor.setPosition(min(position, len(self.edit.toPlainText())))
        self.edit.setTextCursor(cursor)
        return True

    def check_disk(self) -> None:
        """Notice the file changing underneath the buffer.

        Called on the window's tick, so a file an agent rewrites in the terminal next to
        this pane updates by itself. A *clean* buffer reloads silently -- there is
        nothing to lose and a stale view of a file is the failure mode this whole pane
        exists to prevent. A dirty one only raises the banner: choosing between two
        versions is the user's, and doing it for them destroys work.
        """
        if not self._path or self._loaded is None:
            return
        if not files.changed_on_disk(self._path, self._loaded):
            return
        if not os.path.exists(self._path):
            self._raise_banner(f"{os.path.basename(self._path)} was deleted or moved")
            return
        if self._dirty:
            self._raise_banner(f"{os.path.basename(self._path)} changed on disk")
            return
        self.reload(force=True)

    # -- internals ---------------------------------------------------------

    def _on_text_changed(self) -> None:
        if self._loading:
            return
        self._set_dirty(True)

    def _set_dirty(self, dirty: bool) -> None:
        if dirty == self._dirty:
            return
        self._dirty = dirty
        self.dirtyChanged.emit(dirty)
        self.titleChanged.emit(self.title)

    def _emit_status(self) -> None:
        if self._loaded is None:
            self.statusChanged.emit("")
            return
        lines = self.edit.document().blockCount()
        self.statusChanged.emit(
            f"{self.highlighter.language} · {files.encoding_label(self._loaded.encoding)}"
            f" · {_newline_label(self._loaded.newline)} · {lines} lines"
        )
        self.titleChanged.emit(self.title)

    def _raise_banner(self, text: str) -> None:
        self.banner_label.setText(text)
        self.banner.show()

    def _keep_mine(self) -> None:
        """Dismiss the banner and re-fingerprint, so it does not fire again unprompted.

        The buffer stays dirty and the file on disk stays as it is; the next save
        overwrites it, which is what "keep mine" means.
        """
        if self._loaded is not None and os.path.exists(self._path):
            try:
                stat = os.stat(self._path)
                self._loaded = files.Loaded(self._loaded.text, self._loaded.encoding,
                                            self._loaded.newline, stat.st_size,
                                            stat.st_mtime_ns)
            except OSError:
                pass
        self.banner.hide()

    def _offer_to_save(self) -> bool:
        """Save / discard / cancel before losing a dirty buffer. False cancels."""
        name = os.path.basename(self._path) or "this file"
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved changes")
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setText(f"{name} has unsaved changes.")
        save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(save)
        box.exec()
        if box.clickedButton() is save:
            return self.save()
        return box.clickedButton() is discard

    def _show_refusal(self, path: str, kind: str) -> None:
        name = os.path.basename(path)
        if kind == files.TOO_BIG:
            size = os.path.getsize(path) / (1024 * 1024)
            self._show_message(
                f"{name} is {size:.1f} MB",
                f"AsomeDeck opens files up to {files.MAX_BYTES // (1024 * 1024)} MB. "
                "Anything larger belongs in a pager -- try `less` in a terminal pane.",
            )
        elif kind == files.BINARY:
            self._show_message(f"{name} is not a text file",
                               "It contains null bytes, so there is nothing useful to "
                               "show and editing it here would corrupt it.")
        else:
            self._show_message(f"Cannot read {name}", "The file is gone or unreadable.")

    def _show_message(self, text: str, detail: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("AsomeDeck")
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setText(text)
        box.setInformativeText(detail)
        box.exec()
