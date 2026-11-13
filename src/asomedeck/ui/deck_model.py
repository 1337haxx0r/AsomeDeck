"""Deck rows for the sidebar (spec 14.5).

Everything git-derived here -- branch, diff stat, relative time -- is filled in by
``git_status.py`` (spec 14.6). Until a deck reports, ``DeckRow.git_known`` is False
and the delegate hides those runs rather than painting a plausible-looking zero.

Sessions are counted, not stored. They are runtime state owned by the pane tree, and
duplicating them into a model is how the two get to disagree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

#: Carries the whole row to the delegate in one go, rather than a dozen display roles.
ROW_ROLE = int(Qt.ItemDataRole.UserRole) + 1


@dataclass
class DeckRow:
    name: str
    path: str
    #: Row id in the deck table. 0 for a row that is not persisted.
    deck_id: int = 0
    #: False until git_status.py has answered for this deck, and for a directory that
    #: is not a repository. The delegate omits the branch chip and the count entirely
    #: rather than rendering an empty or invented one.
    git_known: bool = False
    branch: str = ""
    changed: int = 0
    clean: bool = False
    when: str = ""
    session_count: int = 0
    #: Sessions in a state something actually reported. Spec 14.3: with no title patterns
    #: configured this is 0 and the dot falls back to live/dead.
    live_count: int = 0
    #: The loudest thing happening under this deck, from ``activity.rollup``. A count
    #: cannot carry this: "two sessions" and "two sessions, one of which is asking you a
    #: question" are the same number, and the second is the only one worth a glance.
    status: str = "dead"
    #: This deck's own colour, or "" to take the theme's accent. Carried raw, as it was
    #: stored -- Theme.tint turns it into something paintable, and only the delegate and
    #: the rail dot know which theme is live.
    accent: str = ""

    @property
    def path_text(self) -> str:
        """The line under the name, in the sidebar and in the header.

        A deck may have no folder at all, and the empty string is the one thing that must
        not be painted for it: the gap under a deck's name is where a deck says where it
        is, so a blank one reads as a path that failed to load rather than a deck that
        never had one. ``path`` itself stays as it is stored -- everything that joins,
        walks or polls it tests it for emptiness.
        """
        return self.path or "no folder"

    @property
    def diff_text(self) -> str:
        """A count of changed paths, not a diff stat. See GitState.summary."""
        if not self.git_known:
            return ""
        if self.clean:
            return "clean"
        return f"{self.changed} changed"


@dataclass
class SessionRow:
    """One running terminal, shown under its deck when the deck is expanded.

    Deliberately not a DeckRow with extra fields: the two are painted differently, sized
    differently and clicked for different reasons, and a single row type carrying both
    would need a flag at every one of those sites.
    """

    deck_id: int
    pane_id: int
    title: str = ""
    shell: str = ""
    state: str = "dead"
    uptime: str = ""
    pid: str = ""

    @property
    def label(self) -> str:
        return self.title or f"pane {self.pane_id}"


@dataclass
class PlaceholderRow:
    """What an expanded deck shows when it has no live terminals.

    A deck that is not open has no sessions -- only a saved arrangement. Listing that
    arrangement as though it were running would be inventing status, so it says what is
    actually known: how many panes come back when it opens.
    """

    deck_id: int
    text: str


@dataclass
class DeckStore:
    """Ordered deck registry. Backed by SQLite at milestone 4 (spec 9)."""

    rows: list[DeckRow] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.rows)


def rows_from_db(decks) -> DeckStore:
    """Build sidebar rows from the deck table.

    Git fields stay unknown here. git_status.py fills them per deck, asynchronously,
    and until it answers the delegate omits the branch chip rather than implying a clean
    tree nobody checked.
    """
    return DeckStore([
        DeckRow(
            name=p.name,
            path=p.path.replace("\\", "/"),
            deck_id=p.id,
            when=_relative(p.last_opened),
            accent=p.accent,
        )
        for p in decks
    ])


def _relative(timestamp: str | None) -> str:
    """Coarse age for the sidebar. Precision past a day is noise in a deck list."""
    if not timestamp:
        return ""
    try:
        then = datetime.fromisoformat(timestamp)
    except ValueError:
        return ""
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    seconds = (datetime.now(UTC) - then).total_seconds()
    if seconds < 90:
        return "now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


class DeckListModel(QAbstractListModel):
    def __init__(self, store: DeckStore, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._expanded: set[int] = set()
        self._children: dict[int, list] = {}

    @property
    def store(self) -> DeckStore:
        return self._store

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        # Qt calls this with no argument for a flat list; a QModelIndex() default
        # would build one at import time, before any QApplication exists.
        return 0 if parent is not None and parent.isValid() else len(self._display)

    # --- the accordion ---------------------------------------------------------------

    @property
    def _display(self) -> list:
        """Decks, each followed by its children when it is expanded.

        Flattened rather than modelled as a tree: the sidebar is a QListView with a
        hand-painted delegate, and a QTreeView would bring its own indentation, branch
        indicators and selection behaviour to fight with that design for no gain.
        """
        rows: list = []
        for row in self._store.rows:
            rows.append(row)
            if row.deck_id in self._expanded:
                children = self._children.get(row.deck_id) or []
                rows.extend(children or [PlaceholderRow(row.deck_id, "no terminals")])
        return rows

    def is_expanded(self, deck_id: int) -> bool:
        return deck_id in self._expanded

    def set_expanded(self, deck_id: int, expanded: bool) -> None:
        if expanded == (deck_id in self._expanded):
            return
        self.beginResetModel()
        if expanded:
            self._expanded.add(deck_id)
        else:
            self._expanded.discard(deck_id)
        self.endResetModel()

    def set_children(self, deck_id: int, children: list) -> None:
        """Replace what a deck shows when expanded.

        A full reset when the shape changes, and a targeted dataChanged when only the
        contents did -- a reset on every uptime tick would drop the selection once a
        second.
        """
        current = self._children.get(deck_id) or []
        same_shape = (len(current) == len(children)
                      and all(type(a) is type(b) for a, b in zip(current, children, strict=True)))
        self._children[deck_id] = children
        if deck_id not in self._expanded:
            return
        if same_shape:
            # Children sit immediately below their deck, whatever kind they are. This
            # used to look for the first row carrying a pane_id, which no placeholder has
            # -- so a deck that was expanded but not open reset the whole model on
            # every refresh, dropping the selection each time, which is the one thing the
            # dataChanged path exists to avoid.
            first = self.row_for_deck(deck_id)
            if first < 0 or not children:
                # Gone from the store, or showing the static "no terminals" line _display
                # substitutes for an empty list. Neither has anything to repaint.
                return
            self.dataChanged.emit(self.index(first + 1, 0),
                                  self.index(first + len(children), 0), [ROW_ROLE])
            return
        self.beginResetModel()
        self.endResetModel()

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)):
        rows = self._display
        if not index.isValid() or not 0 <= index.row() < len(rows):
            return None
        row = rows[index.row()]
        if role == ROW_ROLE:
            return row
        if role == int(Qt.ItemDataRole.DisplayRole):
            return getattr(row, "name", None) or getattr(row, "label", "")
        if role == int(Qt.ItemDataRole.ToolTipRole):
            return getattr(row, "path", None) or getattr(row, "label", "")
        return None

    def row_at(self, index: QModelIndex):
        """Whatever is at this position -- a deck, a session, or a placeholder.

        Callers that only handle decks have to say so; `_deck_menu` and the
        selection handler both check the type now, because a right-click on a session row
        used to offer to remove a deck it was not.
        """
        rows = self._display
        return rows[index.row()] if index.isValid() and index.row() < len(rows) else None

    def deck_at(self, index: QModelIndex) -> DeckRow | None:
        row = self.row_at(index)
        return row if isinstance(row, DeckRow) else None

    def replace(self, store: DeckStore) -> None:
        self.beginResetModel()
        self._store = store
        self._children = {k: v for k, v in self._children.items()
                          if any(r.deck_id == k for r in store.rows)}
        self._expanded = {k for k in self._expanded
                          if any(r.deck_id == k for r in store.rows)}
        self.endResetModel()

    def row_for_deck(self, deck_id: int) -> int:
        """Where this deck sits *in the flattened list*, for `index()` and selection.

        Not a position in ``store.rows``. Expanding a deck pushes every deck below
        it down by the number of terminals under it, and the two stopped agreeing the
        moment the accordion arrived. Anything that wants the deck itself wants
        ``deck_row`` -- indexing the store with this number reads the wrong deck,
        or walks off the end of the list.
        """
        return next((i for i, r in enumerate(self._display)
                     if isinstance(r, DeckRow) and r.deck_id == deck_id), -1)

    def deck_row(self, deck_id: int) -> DeckRow | None:
        """The deck itself, found by id rather than by position."""
        return next((r for r in self._store.rows if r.deck_id == deck_id), None)

    def deck_count(self) -> int:
        """How many decks there are, which is not ``rowCount``.

        ``rowCount`` counts what the sidebar draws -- decks *and* the terminals under
        the expanded ones -- so a header built from it claimed five decks the moment
        one with four terminals opened.
        """
        return len(self._store.rows)

    def _touch(self, deck_id: int) -> None:
        """Repaint one deck row, found by its position in the flattened list."""
        position = self.row_for_deck(deck_id)
        if position >= 0:
            idx = self.index(position, 0)
            self.dataChanged.emit(idx, idx, [ROW_ROLE])

    def set_git(self, deck_id: int, branch: str, changed: int,
                clean: bool) -> None:
        for row in self._store.rows:
            if row.deck_id == deck_id:
                row.git_known = True
                row.branch, row.changed, row.clean = (branch, changed, clean)
                self._touch(row.deck_id)
                return

    def set_session_count(self, deck_id: int, total: int, live: int = 0,
                          status: str = "dead") -> None:
        """Matched on the id, like set_git above it.

        It matched on ``row.name`` until now, which is a basename and not unique: two
        registered folders both called ``src``, or two checkouts of a repo both called
        ``app``, and the count landed on whichever was listed first. Every other setter
        here already keyed on the id; this one was the exception.

        All three fields are set together because all three are read off the same panes at
        the same moment, and a repaint is only asked for when one of them actually moved.
        This runs for every deck on every tick; unconditionally touching the row meant
        the sidebar repainted once a second whether or not it would look any different.
        """
        for row in self._store.rows:
            if row.deck_id == deck_id:
                if (row.session_count, row.live_count, row.status) == (total, live, status):
                    return
                row.session_count = total
                row.live_count = live
                row.status = status
                self._touch(row.deck_id)
                return
