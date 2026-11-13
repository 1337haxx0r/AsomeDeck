"""Deck registry and layout persistence (spec 9).

SQLite at ``%APPDATA%\\AsomeDeck\\state.db``. Three tables and no ORM: the whole schema is
two entities and a key-value bag, and the queries below are the entire data layer.

**A deck is not a folder.** It was until schema 3, and the difference is worth stating
because the word does not carry it: a deck is a *workspace over* a folder -- its own
terminals, its own arrangement, its own shell and colour -- and several may sit over one
checkout. Two tasks in one repository were previously one row whose panes had to be told
apart by eye, or two clones of the same repository. What separates two rows on one folder
is ``name``; what they share is the working tree, which is why the claims board and its
pane numbering (``board.py``, ``ui.pane_tree.PaneIds``) are scoped to the folder and not
to the row.

**There is no session table, and there cannot be one.** A session is a live process
holding a pty inside our window tree; nothing on Windows lets that outlive the process
that parents it. What persists is the *shape* -- which panes existed, how they were
arranged, which launcher each held -- so reopening a deck restores the workspace and
relaunches the terminals. You get your arrangement back, not your scrollback. Spec 8 is
explicit that the alternative is a broker process owning the ptys, which is a terminal
multiplexer and the architecture this app discarded.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import platform_

log = logging.getLogger(__name__)

SCHEMA_VERSION = 3

#: Set on clean shutdown, cleared at startup. A run that finds it already cleared did not
#: exit cleanly, which under spec 8 means its terminals died with it (mitigation 2).
CLEAN_SHUTDOWN = "clean_shutdown"

#: The table is still called ``project`` and ``layout`` still keys on ``project_id``,
#: everywhere below, on purpose. Everything a reader sees says deck; a column name is not
#: read by anybody, and renaming it would cost a fourth table rebuild one version after
#: the third -- with the same ``DROP TABLE`` under it, the one that cascades every saved
#: layout away if foreign keys are left on. A migration whose entire benefit is a word
#: nobody looks at is not worth the way it can fail. Read ``project`` here as the storage
#: spelling of ``Deck``.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS project (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    -- Not UNIQUE, since schema 3. Several rows may point at one folder: two decks over
    -- one checkout, each with its own terminals, layout, shell and colour. What stops
    -- them being indistinguishable is `name`, which the sidebar leads with and which
    -- `MainWindow._add_deck` insists on when a second row is made here.
    --
    -- May also be empty, which means a deck with no folder at all: terminals and an
    -- editor over nothing, for work that is not a checkout. NOT NULL is kept rather than
    -- made nullable, because '' and NULL would be the same state spelled two ways and
    -- every reader would have to test for both. `if deck.path:` is the whole test.
    path        TEXT NOT NULL,
    launcher_id TEXT NOT NULL,
    sort_order  INTEGER NOT NULL,
    last_opened TEXT,
    -- The sidebar rail's colour, '' for "use the theme accent". A hex and not an index
    -- into theme.ACCENTS: the picker offers a colour dialog too, and an index could not
    -- hold what comes back from it. Stored once for both themes -- Theme.tint decides what
    -- a stored colour looks like on a cream background.
    accent      TEXT NOT NULL DEFAULT ''
);

-- For finding the decks on a folder, not for forbidding a second one. Windows paths
-- differ only by case, so C:\\dev\\x and C:\\Dev\\X name one directory and `decks_at`
-- has to answer with both rows; the display casing is kept as the user typed it.
CREATE INDEX IF NOT EXISTS project_path_lookup ON project(path COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS layout (
    project_id  INTEGER PRIMARY KEY REFERENCES project(id) ON DELETE CASCADE,
    tree_json   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT);
"""


@dataclass
class Deck:
    id: int
    name: str
    path: str
    launcher_id: str
    sort_order: int
    last_opened: str | None = None
    #: Not optional in the way it looks. ``decks()`` builds these with
    #: ``Deck(**dict(row))``, so a column without a field here is a TypeError on the
    #: first read, not a field that quietly goes missing.
    accent: str = ""


def default_db_path() -> Path:
    return platform_.state_dir() / "state.db"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    """Thin DAO over the three tables. Commits per call; nothing here is hot."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        # Off by default in SQLite, and the layout table's ON DELETE CASCADE is inert
        # without it -- removing a deck would leave its layout orphaned forever.
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # --- schema ---------------------------------------------------------------------

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        self.conn.executescript(_SCHEMA)
        # v2: deck.accent.
        #
        # Guarded on the column and not on `version`, deliberately. CREATE TABLE IF NOT
        # EXISTS above already gives a *fresh* database the column, so a version-only
        # branch would then run an ALTER that fails with "duplicate column name" -- on
        # first launch, for every new user. Asking the table what it has is the only
        # question with the same answer down both paths.
        if "accent" not in self._columns("project"):
            self.conn.execute(
                "ALTER TABLE project ADD COLUMN accent TEXT NOT NULL DEFAULT ''"
            )
            log.info("migrated deck table to schema %d (added accent)", SCHEMA_VERSION)
        # v3: several rows may point at one folder.
        #
        # Guarded on the constraint for the reason v2 is guarded on the column: the
        # CREATE TABLE above already gives a fresh database the loose shape, so a
        # version-only branch would rebuild an empty table on first launch for every new
        # user. Asking the table what it enforces is the question with one answer down
        # both paths.
        if self._path_is_unique():
            self._drop_path_uniqueness()
        if version != SCHEMA_VERSION:
            # Future migrations branch on the old version here. Recorded from the start so
            # the first schema change has something to compare against.
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            log.info("database schema at version %d (%s)", SCHEMA_VERSION, self.path)
        self.conn.commit()

    def _columns(self, table: str) -> set[str]:
        return {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}

    def _path_is_unique(self) -> bool:
        """Whether the deck table still forbids a second row on one folder.

        Both spellings of the constraint answer here, which is why this asks about indexes
        rather than reading the table's DDL. Schema 1 wrote ``path TEXT NOT NULL UNIQUE``,
        which SQLite implements as an automatic index, and schema 1 *also* added
        ``deck_path_nocase`` on top of it. Either one alone is enough to reject the
        second deck, so the question is "is there any unique index over exactly path", not
        "is that particular index present".
        """
        for index in self.conn.execute("PRAGMA index_list(project)"):
            if not index["unique"]:
                continue
            # Quoted, not bound: PRAGMA takes no parameters. The name is SQLite's own,
            # read from the line above, so there is nothing here a user could have written.
            columns = self.conn.execute(f'PRAGMA index_info("{index["name"]}")').fetchall()
            if [column["name"] for column in columns] == ["path"]:
                return True
        return False

    def _drop_path_uniqueness(self) -> None:
        """Rebuild ``deck`` without it. SQLite cannot drop a constraint in place.

        The dangerous part is not the copy, it is ``DROP TABLE``. ``layout`` references
        ``project(id)`` ``ON DELETE CASCADE`` and ``__init__`` has already switched
        foreign keys on, so dropping the table with them enabled would cascade every saved
        arrangement in the database away -- a migration whose cost is the thing the
        database mostly exists to hold. So they go off for the swap and back on after it,
        outside the transaction both times, because the pragma is a silent no-op inside
        one.

        ``executescript`` commits whatever is open before it starts, which is why the
        BEGIN below is the first statement rather than an assumption about what came
        before it.
        """
        self.conn.execute("PRAGMA foreign_keys = OFF")
        try:
            self.conn.executescript("""
                BEGIN;
                CREATE TABLE project_v3 (
                    id          INTEGER PRIMARY KEY,
                    name        TEXT NOT NULL,
                    path        TEXT NOT NULL,
                    launcher_id TEXT NOT NULL,
                    sort_order  INTEGER NOT NULL,
                    last_opened TEXT,
                    accent      TEXT NOT NULL DEFAULT ''
                );
                INSERT INTO project_v3
                    SELECT id, name, path, launcher_id, sort_order, last_opened, accent
                    FROM project;
                DROP TABLE project;
                ALTER TABLE project_v3 RENAME TO project;
                CREATE INDEX IF NOT EXISTS project_path_lookup
                    ON project(path COLLATE NOCASE);
                COMMIT;
            """)
        finally:
            self.conn.execute("PRAGMA foreign_keys = ON")
        # Ids are carried across verbatim, so every layout still points at its deck.
        # Checked rather than asserted in a comment, because a migration that silently
        # orphaned them would look exactly like a user who had never saved a layout.
        orphans = self.conn.execute("PRAGMA foreign_key_check").fetchall()
        if orphans:
            log.error("schema 3 left %d orphaned row(s); layouts may be lost", len(orphans))
        log.info("migrated deck table to schema %d (a folder may hold several decks)",
                 SCHEMA_VERSION)

    # --- decks -------------------------------------------------------------------

    def decks(self) -> list[Deck]:
        rows = self.conn.execute(
            "SELECT * FROM project ORDER BY sort_order, id"
        ).fetchall()
        return [Deck(**dict(row)) for row in rows]

    def decks_at(self, path: str) -> list[Deck]:
        """Every deck pointed at this folder, in sidebar order.

        A list, and that is the whole of what schema 3 changed for callers. It used to be
        ``deck_by_path`` returning one row or None, because one folder was one deck
        and the only use for the answer was to refuse a second.

        Case-insensitive, because two spellings of a Windows path name one directory and a
        caller asking "what is already here" wants both rows whichever way the second was
        typed.
        """
        rows = self.conn.execute(
            "SELECT * FROM project WHERE path = ? COLLATE NOCASE ORDER BY sort_order, id",
            (_clean(path),),
        ).fetchall()
        return [Deck(**dict(row)) for row in rows]

    def deck_by_id(self, deck_id: int) -> Deck | None:
        """The deck with this id, or None if it has been removed.

        The id and not the path, because the sidebar hands its callers a ``DeckRow``
        whose ``path`` is the *display* form -- backslashes turned to forward slashes for
        the delegate -- and looking that up here would miss every deck on the machine.
        """
        row = self.conn.execute(
            "SELECT * FROM project WHERE id = ?", (deck_id,)
        ).fetchone()
        return Deck(**dict(row)) if row else None

    def add_deck(self, path: str, launcher_id: str, name: str | None = None) -> Deck:
        """Register a new deck, over a folder or over nothing. Always a new row.

        It used to answer with the existing row when the folder already had one, and that
        was right while one folder was one deck: "add" on a folder already in the
        sidebar could only be a mistake, and quietly selecting what you meant was kinder
        than an error.

        A folder can now carry several decks -- one per task, each with its own
        terminals, layout and shell -- so an add on a registered folder is a real request
        about half the time, and this layer cannot tell which half. It no longer guesses.
        Deciding needs somebody to ask, which means it belongs to the window
        (``MainWindow._add_deck``), and what reaches here is the decision rather than
        the ambiguity.

        An empty ``path`` is a deck with no folder, and it is the one value that does not
        go through ``_clean``: ``os.path.abspath("")`` is the *process* working directory,
        so cleaning it would quietly register whatever folder the app was launched from --
        ``/``, for a bundle opened from the Dock -- as though somebody had picked it. Such
        a deck needs a name from its caller, there being no basename to take one from.
        """
        path = _clean(path) if path else ""
        name = name or os.path.basename(path.rstrip("\\/")) or path or "Deck"
        order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM project"
        ).fetchone()[0]
        cursor = self.conn.execute(
            "INSERT INTO project (name, path, launcher_id, sort_order) VALUES (?, ?, ?, ?)",
            (name, path, launcher_id, order),
        )
        self.conn.commit()
        log.info("registered deck %s at %s", name, path or "no folder")
        return Deck(cursor.lastrowid, name, path, launcher_id, order, None)

    def remove_deck(self, deck_id: int) -> None:
        self.conn.execute("DELETE FROM project WHERE id = ?", (deck_id,))
        self.conn.commit()

    def rename_deck(self, deck_id: int, name: str) -> None:
        self.conn.execute("UPDATE project SET name = ? WHERE id = ?", (name, deck_id))
        self.conn.commit()

    def set_launcher(self, deck_id: int, launcher_id: str) -> None:
        self.conn.execute(
            "UPDATE project SET launcher_id = ? WHERE id = ?", (launcher_id, deck_id)
        )
        self.conn.commit()

    def set_accent(self, deck_id: int, accent: str) -> None:
        """Colour this deck's sidebar rail. Empty string clears the override."""
        self.conn.execute(
            "UPDATE project SET accent = ? WHERE id = ?", (accent, deck_id)
        )
        self.conn.commit()

    def touch(self, deck_id: int) -> None:
        self.conn.execute(
            "UPDATE project SET last_opened = ? WHERE id = ?", (_now(), deck_id)
        )
        self.conn.commit()

    def reorder(self, deck_ids: list[int]) -> None:
        self.conn.executemany(
            "UPDATE project SET sort_order = ? WHERE id = ?",
            [(order, pid) for order, pid in enumerate(deck_ids)],
        )
        self.conn.commit()

    # --- layout ---------------------------------------------------------------------

    def save_layout(self, deck_id: int, tree: dict) -> None:
        """Store the arrangement. Sessions are not stored; see the module docstring."""
        self.conn.execute(
            "INSERT INTO layout (project_id, tree_json) VALUES (?, ?) "
            "ON CONFLICT(project_id) DO UPDATE SET tree_json = excluded.tree_json",
            (deck_id, json.dumps(tree, separators=(",", ":"))),
        )
        self.conn.commit()

    def load_layout(self, deck_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT tree_json FROM layout WHERE project_id = ?", (deck_id,)
        ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["tree_json"])
        except json.JSONDecodeError:
            # A layout we cannot parse is not worth failing a launch over.
            log.warning("layout for deck %d is unreadable; ignoring", deck_id)
            return None

    def clear_layout(self, deck_id: int) -> None:
        self.conn.execute("DELETE FROM layout WHERE project_id = ?", (deck_id,))
        self.conn.commit()

    # --- app_meta -------------------------------------------------------------------

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM app_meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO app_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    # --- clean shutdown marker (spec 8, mitigation 2) --------------------------------

    def begin_run(self) -> bool:
        """Clear the clean-shutdown marker. Returns True if the previous run was clean.

        A crash takes every embedded terminal with it, so the honest thing on the next
        start is to say so rather than let the user wonder where their shells went.
        """
        was_clean = self.get_meta(CLEAN_SHUTDOWN) == "1"
        self.set_meta(CLEAN_SHUTDOWN, "0")
        return was_clean

    def end_run(self) -> None:
        self.set_meta(CLEAN_SHUTDOWN, "1")


def _clean(path: str) -> str:
    """Absolute, with separators normalised, keeping the user's casing for display."""
    return os.path.normpath(os.path.abspath(os.path.expanduser(path)))
