"""Deck registry and layout persistence (spec 9).

Headless: sqlite3 is stdlib and none of this needs Qt, so it runs anywhere the rest of
the win32-free suite does.

Paths are built with ``proj()`` rather than written as literals. They used to be
``proj("alpha")``, which is not a path at all on macOS -- ``abspath`` reads it as a
relative name and hangs the whole thing off the working directory, so ``add_deck``
stored ``/Users/.../AsomeDeck/C:\\dev\\alpha`` and every assertion about the folder name
failed. What these tests are actually about is what the database does with a path, not
which operating system spelled it, so the spelling is now the platform's.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from asomedeck.db import CLEAN_SHUTDOWN, SCHEMA_VERSION, Database
from asomedeck.platform_ import IS_WINDOWS

_ROOT = "C:\\dev" if IS_WINDOWS else "/dev"
_SEP = "\\" if IS_WINDOWS else "/"


def proj(*parts: str) -> str:
    """An absolute deck path in the running platform's spelling."""
    return _SEP.join((_ROOT, *parts))


@pytest.fixture
def db(tmp_path):
    with Database(tmp_path / "state.db") as database:
        yield database


def test_schema_version_is_recorded(db):
    # Against the constant, not a literal: a migration that forgets to stamp the file
    # is the bug worth catching, and a hardcoded number only ever catches the migration
    # itself -- once, on the run that adds it.
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_add_and_list_decks(db):
    db.add_deck(proj("alpha"), "git-bash")
    db.add_deck(proj("beta"), "git-bash")
    names = [p.name for p in db.decks()]
    assert names == ["alpha", "beta"]


def test_name_defaults_to_the_folder(db):
    assert db.add_deck(proj("ledger-api"), "git-bash").name == "ledger-api"


def test_explicit_name_wins(db):
    assert db.add_deck(proj("x"), "git-bash", name="Ledger").name == "Ledger"


def test_a_folder_can_carry_more_than_one_deck(db):
    """Schema 3. Two tasks in one repository used to be one row whose panes were told
    apart by eye, or two clones of the same repository."""
    first = db.add_deck(proj("alpha"), "git-bash")
    second = db.add_deck(proj("alpha"), "pwsh", name="alpha 2")
    assert first.id != second.id
    assert [p.launcher_id for p in db.decks()] == ["git-bash", "pwsh"]


def test_the_decks_on_a_folder_are_found_together(db):
    db.add_deck(proj("alpha"), "git-bash")
    db.add_deck(proj("alpha"), "pwsh", name="alpha 2")
    db.add_deck(proj("beta"), "git-bash")
    assert [p.name for p in db.decks_at(proj("alpha"))] == ["alpha", "alpha 2"]


def test_paths_differing_only_by_case_name_one_folder(db):
    """Windows spells one directory several ways. Two rows over it are now allowed, but
    they have to be *found* together however the second was typed -- otherwise the window
    cannot tell an add that needs asking about from one that does not."""
    db.add_deck(proj("Alpha"), "git-bash")
    db.add_deck(proj("ALPHA").lower(), "git-bash", name="Alpha 2")
    assert len(db.decks_at(proj("alpha").upper())) == 2


def test_a_folder_with_nothing_on_it_answers_with_nothing(db):
    assert db.decks_at(proj("never-added")) == []


# --- migrating a schema-2 database to 3 -------------------------------------------------

#: What every state.db on a machine that has run a released build looks like. Written out
#: rather than built with an old Database, because the point is to migrate a file this
#: code did not create -- a fixture that called the current constructor would be testing
#: the migration against its own output.
_V2 = """
CREATE TABLE project (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
    launcher_id TEXT NOT NULL, sort_order INTEGER NOT NULL, last_opened TEXT,
    accent TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX project_path_nocase ON project(path COLLATE NOCASE);
CREATE TABLE layout (
    project_id INTEGER PRIMARY KEY REFERENCES project(id) ON DELETE CASCADE,
    tree_json TEXT NOT NULL
);
CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT);
PRAGMA user_version = 2;
"""


@pytest.fixture
def upgraded(tmp_path):
    """A schema-2 database with two decks, two layouts and an accent, opened as 3."""
    path = tmp_path / "state.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(_V2)
    raw.execute("INSERT INTO project VALUES (1, 'Api', ?, 'git-bash', 0, NULL, '#ff0000')",
                (proj("api"),))
    raw.execute("INSERT INTO project VALUES (2, 'Web', ?, 'pwsh', 1, NULL, '')",
                (proj("web"),))
    raw.execute("INSERT INTO layout VALUES (1, '{\"type\": \"pane\", \"pane_id\": 4}')")
    raw.execute("INSERT INTO layout VALUES (2, '{\"type\": \"pane\"}')")
    raw.commit()
    raw.close()
    with Database(path) as database:
        yield database


def test_the_upgrade_keeps_every_deck(upgraded):
    assert [(p.id, p.name, p.launcher_id) for p in upgraded.decks()] == [
        (1, "Api", "git-bash"), (2, "Web", "pwsh")]


def test_the_upgrade_keeps_the_accent_column(upgraded):
    """v2's own migration, still applied. A rebuild that copied the columns it remembered
    rather than the ones the table has would silently drop this."""
    assert upgraded.deck_by_id(1).accent == "#ff0000"


def test_the_upgrade_keeps_every_layout(upgraded):
    """The rebuild drops and recreates the table `layout` points at. With foreign keys
    enabled -- and __init__ enables them -- that DROP would cascade every saved
    arrangement in the database away."""
    assert upgraded.load_layout(1) == {"type": "pane", "pane_id": 4}
    assert upgraded.load_layout(2) == {"type": "pane"}


def test_the_upgrade_leaves_no_unique_index_on_the_path(upgraded):
    unique = [row["name"] for row in upgraded.conn.execute("PRAGMA index_list(project)")
              if row["unique"]]
    assert unique == []


def test_the_upgrade_leaves_the_cascade_working(upgraded):
    """Foreign keys go off for the swap. Leaving them off would cost nothing visible until
    the first deck removal orphaned its layout forever."""
    assert upgraded.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    upgraded.remove_deck(1)
    assert upgraded.conn.execute(
        "SELECT COUNT(*) FROM layout WHERE project_id = 1").fetchone()[0] == 0


def test_an_upgraded_database_accepts_a_second_deck_on_a_folder(upgraded):
    second = upgraded.add_deck(proj("api"), "zsh", name="Api 2")
    assert second.id != 1
    assert len(upgraded.decks_at(proj("api"))) == 2


def test_the_upgrade_records_the_new_version(upgraded):
    assert upgraded.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_opening_an_upgraded_database_again_changes_nothing(upgraded):
    """The rebuild is guarded on the constraint, not on the version. A second open must
    not copy the table again -- and on a fresh database it must not run at all."""
    before = [(p.id, p.name) for p in upgraded.decks()]
    with Database(upgraded.path) as reopened:
        assert [(p.id, p.name) for p in reopened.decks()] == before


def test_paths_are_normalised(db):
    deck = db.add_deck(proj("alpha", "..", "alpha", "."), "git-bash")
    assert deck.path.rstrip(_SEP).lower() == proj("alpha").lower()


def test_a_deck_can_have_no_folder(db):
    deck = db.add_deck("", "git-bash", name="Scratch")
    assert deck.path == ""
    assert [p.path for p in db.decks()] == [""]


def test_no_folder_does_not_become_the_working_directory(db):
    """The bug this exists to stop. `_clean` runs `os.path.abspath`, and the absolute
    form of "" is wherever the process happens to be standing -- `/` for a bundle opened
    from the Dock, which would arrive in the sidebar as a deck over the whole disk."""
    assert db.add_deck("", "git-bash", name="Scratch").path != os.getcwd()


def test_a_deck_with_no_folder_is_not_found_by_a_folder_lookup(db):
    """`decks_at` answers "which decks are on this folder". A deck on none is not on
    the process working directory either, whatever `_clean` would have made of ""."""
    db.add_deck("", "git-bash", name="Scratch")
    assert db.decks_at(os.getcwd()) == []


def test_a_deck_with_no_folder_and_no_name_still_has_one(db):
    """There is no basename to fall back on, and a nameless row is an invisible one."""
    assert db.add_deck("", "git-bash").name


def test_sort_order_is_assigned_and_reorderable(db):
    a = db.add_deck(proj("a"), "git-bash")
    b = db.add_deck(proj("b"), "git-bash")
    c = db.add_deck(proj("c"), "git-bash")
    assert [p.sort_order for p in db.decks()] == [0, 1, 2]
    db.reorder([c.id, a.id, b.id])
    assert [p.name for p in db.decks()] == ["c", "a", "b"]


def test_remove_deck(db):
    deck = db.add_deck(proj("alpha"), "git-bash")
    db.remove_deck(deck.id)
    assert db.decks() == []


def test_deck_by_id_finds_it_and_survives_a_rename(db):
    """By id and not by path, because the sidebar hands its callers a row whose path is
    the display form -- backslashes turned to forward slashes for the delegate."""
    deck = db.add_deck(proj("alpha"), "git-bash")

    assert db.deck_by_id(deck.id).name == "alpha"
    db.rename_deck(deck.id, "Work")
    assert db.deck_by_id(deck.id).name == "Work"
    assert db.deck_by_id(deck.id).path == deck.path


def test_deck_by_id_answers_none_for_one_that_is_gone(db):
    deck = db.add_deck(proj("alpha"), "git-bash")
    db.remove_deck(deck.id)
    assert db.deck_by_id(deck.id) is None


def test_rename_and_relaunch(db):
    deck = db.add_deck(proj("alpha"), "git-bash")
    db.rename_deck(deck.id, "Alpha Prime")
    db.set_launcher(deck.id, "pwsh")
    stored = db.decks()[0]
    assert (stored.name, stored.launcher_id) == ("Alpha Prime", "pwsh")


def test_accent_defaults_to_empty_and_round_trips(db):
    deck = db.add_deck(proj("alpha"), "git-bash")
    assert deck.accent == ""
    assert db.decks()[0].accent == ""

    db.set_accent(deck.id, "#7fa6b8")
    assert db.decks()[0].accent == "#7fa6b8"

    # Clearing is the same call with nothing in it, not a second method. "" is what the
    # column stores for "use the theme accent", so there is no third state to represent.
    db.set_accent(deck.id, "")
    assert db.decks()[0].accent == ""


def test_accent_column_is_added_to_a_v1_database(tmp_path):
    """A database written before the column existed must survive being opened.

    Built by hand rather than by checking in a fixture file: what is being pinned is that
    _migrate notices a table without the column, and the only way to have one is to create
    it the way version 1 did.
    """
    path = tmp_path / "state.db"
    old = sqlite3.connect(str(path))
    old.executescript(r"""
        CREATE TABLE project (
            id          INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            path        TEXT NOT NULL UNIQUE,
            launcher_id TEXT NOT NULL,
            sort_order  INTEGER NOT NULL,
            last_opened TEXT
        );
        INSERT INTO project (name, path, launcher_id, sort_order)
        VALUES ('alpha', 'C:\dev\alpha', 'git-bash', 0);
        PRAGMA user_version = 1;
    """)
    old.commit()
    old.close()

    with Database(path) as database:
        assert database.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        stored = database.decks()
        # The row is still there, and reads back without an accent rather than failing to
        # construct -- Deck(**dict(row)) is what would break on a mismatch.
        assert [(p.name, p.accent) for p in stored] == [("alpha", "")]
        database.set_accent(stored[0].id, "#8ab06a")
        assert database.decks()[0].accent == "#8ab06a"


def test_migrating_twice_is_harmless(tmp_path):
    # The guard is on the column, not the version, so opening an already-migrated file
    # must not re-run the ALTER -- that is a "duplicate column name" on every launch.
    path = tmp_path / "state.db"
    with Database(path) as first:
        deck = first.add_deck(proj("alpha"), "git-bash")
        first.set_accent(deck.id, "#c08fd0")
    with Database(path) as second:
        assert second.decks()[0].accent == "#c08fd0"


def test_touch_records_last_opened(db):
    deck = db.add_deck(proj("alpha"), "git-bash")
    assert db.decks()[0].last_opened is None
    db.touch(deck.id)
    assert db.decks()[0].last_opened is not None


# --- layout ---------------------------------------------------------------------------


def test_layout_round_trips(db):
    deck = db.add_deck(proj("alpha"), "git-bash")
    tree = {"type": "split", "orientation": "h",
            "children": [{"type": "pane", "launcher_id": "git-bash"}]}
    db.save_layout(deck.id, tree)
    assert db.load_layout(deck.id) == tree


def test_saving_a_layout_twice_replaces_it(db):
    deck = db.add_deck(proj("alpha"), "git-bash")
    db.save_layout(deck.id, {"v": 1})
    db.save_layout(deck.id, {"v": 2})
    assert db.load_layout(deck.id) == {"v": 2}


def test_missing_layout_is_none_not_an_error(db):
    deck = db.add_deck(proj("alpha"), "git-bash")
    assert db.load_layout(deck.id) is None


def test_unreadable_layout_is_ignored_rather_than_fatal(db):
    deck = db.add_deck(proj("alpha"), "git-bash")
    db.conn.execute("INSERT INTO layout VALUES (?, ?)", (deck.id, "{not json"))
    db.conn.commit()
    # A corrupt layout must not stop the deck opening.
    assert db.load_layout(deck.id) is None


def test_removing_a_deck_removes_its_layout(db):
    deck = db.add_deck(proj("alpha"), "git-bash")
    db.save_layout(deck.id, {"v": 1})
    db.remove_deck(deck.id)
    # Requires PRAGMA foreign_keys; without it the cascade is silently inert.
    assert db.conn.execute("SELECT COUNT(*) FROM layout").fetchone()[0] == 0


# --- app_meta and the clean-shutdown marker -------------------------------------------


def test_meta_round_trips_and_upserts(db):
    assert db.get_meta("missing") is None
    assert db.get_meta("missing", "fallback") == "fallback"
    db.set_meta("theme", "dark")
    db.set_meta("theme", "light")
    assert db.get_meta("theme") == "light"


def test_first_ever_run_is_not_reported_as_a_crash(db):
    # No marker yet means no previous run, which is not the same as a dirty one.
    assert db.begin_run() is False


def test_clean_shutdown_is_detected(db):
    db.begin_run()
    db.end_run()
    assert db.begin_run() is True


def test_a_run_that_never_ended_is_reported_as_dirty(db):
    db.begin_run()
    db.end_run()
    db.begin_run()          # started...
    assert db.begin_run() is False   # ...and never ended: the terminals died with it
    assert db.get_meta(CLEAN_SHUTDOWN) == "0"
