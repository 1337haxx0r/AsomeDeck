"""The deck's files in the sidebar, marked up with what git says about them.

``QFileSystemModel`` does most of this for free and is still the wrong choice here, for
one reason: the marks. The tree's whole value in this window is answering "what did the
agent in that pane just touch", and that answer already exists -- ``git_status.py``
parses it every poll. A stock model cannot carry it without a proxy layer on top, at
which point the "free" model has cost more than the two hundred lines below.

Two smaller reasons come with it. ``QFileSystemModel`` installs a filesystem watcher per
visited directory, which on a repository with a ``node_modules`` is thousands of handles
for information we refresh from git anyway; and its filtering happens after the fact, so
the noise directories flicker in before being hidden.

The model is lazy: a directory is scanned when it is expanded, never before. Opening a
deck therefore costs one ``scandir`` of the top level whatever the repository weighs.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
)

from .. import files
from .theme import Theme

#: Model roles beyond the display name.
MARK_ROLE = Qt.ItemDataRole.UserRole + 1
PATH_ROLE = Qt.ItemDataRole.UserRole + 2
IS_DIR_ROLE = Qt.ItemDataRole.UserRole + 3
#: What kind of file this is, as ``files.language_for`` spells it. Empty for directories.
TYPE_ROLE = Qt.ItemDataRole.UserRole + 4

#: Git marks to theme tokens. Deletions and conflicts read as loss, everything else as
#: work in progress; the letters themselves come from ``git_status.parse_porcelain_v2``.
MARK_TOKENS = {"M": "accent", "A": "add", "?": "add", "D": "del", "U": "del", "R": "accent"}

ROW_H = 22
INDENT = 14

#: The invalid index, meaning "the root". Qt's own signatures default to a fresh
#: ``QModelIndex()`` here; one shared instance says the same thing and keeps ruff's B008
#: (no constructor calls in argument defaults) satisfied. It is a value type holding no
#: model pointer, so sharing it is safe and it can exist before any QApplication.
_NO_PARENT = QModelIndex()


class _Node:
    """One directory or file. Children exist only once the directory is expanded."""

    __slots__ = ("name", "path", "is_dir", "parent", "row", "children", "fetched",
                 "bucket")

    def __init__(self, name: str, path: str, is_dir: bool,
                 parent: _Node | None, row: int) -> None:
        self.name = name
        self.path = path
        self.is_dir = is_dir
        self.parent = parent
        self.row = row
        self.children: list[_Node] = []
        self.fetched = False
        #: Resolved once, here, where the directory was being scanned anyway. The
        #: delegate would otherwise re-derive it for every visible row on every git
        #: poll, which is three lookups and a splitext a second for an answer that
        #: cannot change without the file being renamed -- and a rename rebuilds
        #: the node.
        self.bucket = "" if is_dir else files.language_for(path)


class FileTreeModel(QAbstractItemModel):
    """A lazy tree of one deck's files, annotated from a git status."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._root = _Node("", "", True, None, 0)
        self._marks: dict[str, str] = {}
        #: Directories containing a changed file at any depth, so a collapsed folder
        #: still shows that something inside it moved.
        self._dirty_dirs: set[str] = set()

    # -- contents ----------------------------------------------------------

    @property
    def root_path(self) -> str:
        return self._root.path

    def set_root(self, path: str) -> None:
        """Point the tree at a deck. A reset, because nothing carries over."""
        self.beginResetModel()
        self._root = _Node("", path, True, None, 0)
        self.endResetModel()

    def set_marks(self, root: str, marked: tuple[tuple[str, str], ...]) -> None:
        """Apply a git status. ``marked`` is ``GitState.files``: (mark, repo path).

        Paths arrive relative to the repository root with forward slashes, and are keyed
        here by absolute normalised case -- on Windows the same file reaches this model
        as ``D:\\PY\\x`` from a scan and ``d:/py/x`` from git, and a mark that does not
        match is a mark that never appears.
        """
        self._marks = {}
        self._dirty_dirs = set()
        root_key = os.path.normcase(os.path.normpath(root))
        for mark, relative in marked:
            absolute = os.path.normcase(os.path.normpath(os.path.join(root, relative)))
            self._marks[absolute] = mark
            folder = os.path.dirname(absolute)
            while folder and folder != root_key and folder not in self._dirty_dirs:
                self._dirty_dirs.add(folder)
                parent = os.path.dirname(folder)
                if parent == folder:
                    break
                folder = parent
        self._repaint(self._root)

    def mark_for(self, path: str) -> str:
        key = os.path.normcase(os.path.normpath(path))
        if key in self._marks:
            return self._marks[key]
        return "·" if key in self._dirty_dirs else ""

    def refresh(self) -> None:
        """Re-scan every directory currently expanded, keeping the expansion.

        A directory whose listing is unchanged is left completely alone -- no signal, no
        repaint -- so this can run on the git poll without the tree twitching. Only a
        folder that actually gained or lost an entry is rebuilt.
        """
        self._resync(self._root)

    def _resync(self, node: _Node) -> None:
        if not node.fetched:
            return
        fresh = files.list_dir(node.path)
        if [(entry.name, entry.is_dir) for entry in fresh] == \
                [(child.name, child.is_dir) for child in node.children]:
            for child in node.children:
                self._resync(child)
            return

        index = self._index_of(node)
        if node.children:
            self.beginRemoveRows(index, 0, len(node.children) - 1)
            node.children = []
            self.endRemoveRows()
        if fresh:
            self.beginInsertRows(index, 0, len(fresh) - 1)
            node.children = [_Node(entry.name, entry.path, entry.is_dir, node, row)
                             for row, entry in enumerate(fresh)]
            self.endInsertRows()

    # -- QAbstractItemModel ------------------------------------------------

    def index(self, row: int, column: int, parent=_NO_PARENT) -> QModelIndex:
        if column != 0 or not self.hasIndex(row, column, parent):
            return QModelIndex()
        node = self._node(parent)
        if row >= len(node.children):
            return QModelIndex()
        return self.createIndex(row, 0, node.children[row])

    def parent(self, child: QModelIndex) -> QModelIndex:  # type: ignore[override]
        if not child.isValid():
            return QModelIndex()
        node = child.internalPointer()
        parent = node.parent if node is not None else None
        if parent is None or parent is self._root:
            return QModelIndex()
        return self.createIndex(parent.row, 0, parent)

    def rowCount(self, parent=_NO_PARENT) -> int:  # noqa: N802 - Qt override
        if parent.column() > 0:
            return 0
        return len(self._node(parent).children)

    def columnCount(self, parent=_NO_PARENT) -> int:  # noqa: N802 - Qt override
        _ = parent
        return 1

    def hasChildren(self, parent=_NO_PARENT) -> bool:  # noqa: N802 - Qt override
        node = self._node(parent)
        if not node.is_dir:
            return False
        # True before the scan: a directory is assumed to have contents so that it gets
        # an expand arrow at all. An empty one collapses back on the click, which is a
        # better trade than stat-ing every folder up front to draw an arrow correctly.
        return True if not node.fetched else bool(node.children)

    def canFetchMore(self, parent=_NO_PARENT) -> bool:  # noqa: N802 - Qt override
        node = self._node(parent)
        return node.is_dir and not node.fetched

    def fetchMore(self, parent=_NO_PARENT) -> None:  # noqa: N802 - Qt override
        node = self._node(parent)
        if not node.is_dir or node.fetched:
            return
        node.fetched = True
        entries = files.list_dir(node.path)
        if not entries:
            return
        self.beginInsertRows(parent, 0, len(entries) - 1)
        node.children = [_Node(entry.name, entry.path, entry.is_dir, node, row)
                         for row, entry in enumerate(entries)]
        self.endInsertRows()

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node = index.internalPointer()
        if role == Qt.ItemDataRole.DisplayRole:
            return node.name
        if role == PATH_ROLE:
            return node.path
        if role == IS_DIR_ROLE:
            return node.is_dir
        if role == MARK_ROLE:
            return self.mark_for(node.path)
        if role == TYPE_ROLE:
            return node.bucket
        if role == Qt.ItemDataRole.ToolTipRole:
            return node.path
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    # -- internals ---------------------------------------------------------

    def _node(self, index: QModelIndex) -> _Node:
        if index.isValid():
            node = index.internalPointer()
            if node is not None:
                return node
        return self._root

    def _index_of(self, node: _Node) -> QModelIndex:
        if node is self._root or node.parent is None:
            return QModelIndex()
        return self.createIndex(node.row, 0, node)

    def _repaint(self, node: _Node) -> None:
        """Tell the view a subtree's marks changed, without touching its shape."""
        if node.children:
            top = self.createIndex(0, 0, node.children[0])
            bottom = self.createIndex(len(node.children) - 1, 0, node.children[-1])
            self.dataChanged.emit(top, bottom, [MARK_ROLE])
            for child in node.children:
                self._repaint(child)


class FileDelegate(QStyledItemDelegate):
    """A row: name on the left, git mark on the right.

    Painted rather than styled because a mark needs its own colour and QSS gives an item
    exactly one foreground -- the same reason ``delegates.py`` exists for deck rows.
    """

    MARK_W = 14
    #: The expand arrow, drawn here rather than by QTreeView's branch column. Painting
    #: it keeps it in the design system's glyphs -- the same ``a/v`` pair the deck
    #: accordion uses -- instead of the platform style's triangle, and it is the reason
    #: ``setRootIsDecorated(False)`` is safe: nothing else was drawing one.
    CHEVRON_W = 13

    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self._theme = theme

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt override
        _ = option, index
        return QSize(0, ROW_H)

    def name_color(self, is_dir: bool, mark: str, bucket: str) -> QColor:
        """What colour a row's name is painted, given everything known about it.

        Two signals in one run of text, and neither displaces the other. Hue says what
        kind of file this is; value keeps the rule the tree was built around -- a changed
        file earns full contrast and everything else stays quiet, so the marks down the
        right-hand side are still what the eye lands on.

        A folder has no type to colour and keeps both tiers it already had. Both, because
        git reports a wholly untracked directory as one entry -- ``? build/`` -- so a
        folder can carry a real mark and not only the ``·`` that means something inside it
        moved.

        Split out of ``paint`` because it is the whole of the decision and a QPainter is
        not a thing a test can read a colour back out of.
        """
        changed = bool(mark) and mark != "·"
        if is_dir:
            return self._theme.c("text" if changed else "dim2")
        return self._theme.file_color(bucket, muted=not changed)

    def paint(self, painter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter.save()
        rect: QRect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected:
            painter.fillRect(rect, self._theme.c("sel"))
        elif hovered:
            painter.fillRect(rect, self._theme.c("hover"))

        is_dir = bool(index.data(IS_DIR_ROLE))
        mark = str(index.data(MARK_ROLE) or "")
        painter.setFont(self._theme.font("deckPath" if not is_dir else "panelVal"))

        if is_dir:
            painter.setPen(self._theme.c("dim"))
            painter.drawText(QRect(rect.left(), rect.top(), self.CHEVRON_W, rect.height()),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             "▾" if option.state & QStyle.StateFlag.State_Open else "▸")

        if mark:
            painter.setPen(self._theme.c(MARK_TOKENS.get(mark, "dim")))
            painter.drawText(QRect(rect.right() - self.MARK_W - 6, rect.top(),
                                   self.MARK_W, rect.height()),
                             int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                             mark)

        painter.setPen(self.name_color(is_dir, mark, str(index.data(TYPE_ROLE) or "")))
        text_rect = QRect(rect.left() + self.CHEVRON_W, rect.top(),
                          rect.width() - self.CHEVRON_W - self.MARK_W - 10, rect.height())
        metrics = QFontMetrics(painter.font())
        painter.drawText(text_rect,
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                         metrics.elidedText(str(index.data() or ""),
                                            Qt.TextElideMode.ElideMiddle, text_rect.width()))
        painter.restore()


class FileTree(QTreeView):
    """The sidebar's Files mode."""

    #: A file was chosen. Directories are not emitted -- they expand instead.
    fileActivated = Signal(str)
    #: Right-click on a directory, as ``(absolute path, global point)``. The mirror of
    #: the line above: a *file* is not emitted here, because the only thing this menu
    #: offers is a shell and a file is not a place to start one. The menu itself belongs
    #: to the window, which is the only thing that knows whether a deck is open to
    #: start it in -- this tree paints names and nothing else.
    directoryMenuRequested = Signal(str, object)

    def __init__(self, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("FileTree")
        self._model = FileTreeModel(self)
        self.setModel(self._model)
        self.delegate = FileDelegate(theme, self)
        self.setItemDelegate(self.delegate)
        self.setHeaderHidden(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMouseTracking(True)
        self.setIndentation(INDENT)
        self.setUniformRowHeights(True)
        self.setExpandsOnDoubleClick(False)
        self.setRootIsDecorated(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.clicked.connect(self._on_clicked)

    @property
    def tree_model(self) -> FileTreeModel:
        return self._model

    def set_root(self, path: str) -> None:
        if os.path.normcase(path) == os.path.normcase(self._model.root_path):
            return
        self._model.set_root(path)

    def set_marks(self, root: str, marked: tuple[tuple[str, str], ...]) -> None:
        self._model.set_marks(root, marked)
        self.viewport().update()

    def refresh(self) -> None:
        self._model.refresh()

    def apply_theme(self, theme: Theme) -> None:
        self.delegate.apply_theme(theme)
        self.viewport().update()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Right-click a folder. Anywhere else is ignored rather than accepted.

        Left unaccepted so the event keeps propagating: a right-click on a file, or on the
        empty space below the tree, has nothing here to answer it, and swallowing it would
        make the sidebar the one place in the window where the button does nothing at all.
        """
        index = self.indexAt(event.pos())
        if not index.isValid() or not bool(index.data(IS_DIR_ROLE)):
            return
        path = index.data(PATH_ROLE)
        if not path:
            return
        event.accept()
        self.directoryMenuRequested.emit(str(path), event.globalPos())

    def _on_clicked(self, index: QModelIndex) -> None:
        """One click: expand a directory, open a file.

        Single click rather than double, because this tree is a jump list next to a
        terminal, not a file manager -- and the pane it opens into is reused, so a
        mistaken click costs one more click, not a pane.
        """
        if bool(index.data(IS_DIR_ROLE)):
            self.setExpanded(index, not self.isExpanded(index))
            return
        path = index.data(PATH_ROLE)
        if path:
            self.fileActivated.emit(str(path))
