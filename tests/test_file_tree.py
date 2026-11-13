"""The sidebar's file tree (ui/file_tree.py).

Two things are worth pinning here and neither is the painting. The first is laziness: a
directory must not be read until it is expanded, or opening a deck with a
``node_modules`` in it stalls the window. The second is the git marks, which are the
reason this model exists at all rather than ``QFileSystemModel`` -- and the way they
fail is a path that does not match, which on Windows means case and separators.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6.QtCore")

from PySide6.QtCore import QModelIndex  # noqa: E402

from asomedeck.platform_ import IS_WINDOWS  # noqa: E402
from asomedeck.ui.file_tree import TYPE_ROLE, FileDelegate, FileTreeModel  # noqa: E402
from asomedeck.ui.theme import Theme  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "deep").mkdir()
    (tmp_path / "src" / "deep" / "buried.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture
def model(app, repo):
    _ = app
    model = FileTreeModel()
    model.set_root(str(repo))
    return model


#: The invalid index: "the root", the same singleton file_tree.py keeps for the reason.
ROOT = QModelIndex()


def _fetch(model, index=ROOT):
    if model.canFetchMore(index):
        model.fetchMore(index)
    return model.rowCount(index)


def _child(model, parent, name):
    for row in range(model.rowCount(parent)):
        index = model.index(row, 0, parent)
        if index.data() == name:
            return index
    raise AssertionError(f"no child named {name}")


# --- laziness --------------------------------------------------------------------


def test_nothing_is_read_until_it_is_asked_for(model):
    """A fresh root has no rows, and says it has more to fetch.

    This is the whole performance story: opening a deck costs one scandir of the top
    level, whatever the repository weighs.
    """
    assert model.rowCount(QModelIndex()) == 0
    assert model.canFetchMore(QModelIndex())


def test_fetching_the_root_lists_it_directories_first(model):
    assert _fetch(model) == 2                        # src/ and README.md; .git is hidden
    names = [model.index(row, 0, QModelIndex()).data() for row in range(2)]
    assert names == ["src", "README.md"]


def test_generated_directories_never_appear(model):
    _fetch(model)
    names = {model.index(row, 0, QModelIndex()).data() for row in range(model.rowCount())}
    assert ".git" not in names


def test_a_subdirectory_is_not_scanned_until_it_is_expanded(model):
    _fetch(model)
    src = _child(model, QModelIndex(), "src")
    assert model.rowCount(src) == 0
    assert model.canFetchMore(src)
    assert _fetch(model, src) == 2                    # deep/, app.py


def test_a_directory_claims_children_before_it_is_scanned(model):
    """Otherwise it draws without an expand arrow and cannot be opened at all."""
    _fetch(model)
    assert model.hasChildren(_child(model, QModelIndex(), "src"))


def test_a_file_never_claims_children(model):
    _fetch(model)
    assert not model.hasChildren(_child(model, QModelIndex(), "README.md"))


def test_parent_walks_back_to_the_root(model):
    _fetch(model)
    src = _child(model, QModelIndex(), "src")
    _fetch(model, src)
    app = _child(model, src, "app.py")
    assert model.parent(app) == src
    assert not model.parent(src).isValid()


# --- git marks -------------------------------------------------------------------


def test_a_changed_file_gets_its_mark(model, repo):
    model.set_marks(str(repo), (("M", "src/app.py"),))
    assert model.mark_for(str(repo / "src" / "app.py")) == "M"


def test_marks_survive_the_separator_and_case_difference(model, repo):
    """git reports ``src/app.py``; a scan produces ``...\\src\\app.py``.

    A mark that does not match is a mark that silently never appears, so this is the case
    that matters.

    The case half is Windows only, and deliberately so rather than by oversight. There the
    two spellings genuinely arrive differently -- git and a directory scan disagree about
    the drive letter's case -- and ``os.path.normcase`` folds them together. On POSIX
    normcase is the identity function, and it does not need to be anything else: both paths
    are built from the one root string the deck was registered with, so there is no
    second speller to disagree with. Upper-casing the root here would be inventing a
    mismatch the app cannot produce, and the fix for it would be a case-insensitive
    comparison that macOS has no way to do correctly anyway -- APFS can be configured
    either way and the path alone does not say which.
    """
    root = str(repo).upper() if IS_WINDOWS else str(repo)
    model.set_marks(root, (("M", "src/app.py"),))
    assert model.mark_for(str(repo / "src" / "app.py")) == "M"


def test_an_unchanged_file_has_no_mark(model, repo):
    model.set_marks(str(repo), (("M", "src/app.py"),))
    assert model.mark_for(str(repo / "README.md")) == ""


def test_a_folder_shows_that_something_inside_it_changed(model, repo):
    """A collapsed directory has to carry the news, or the mark is invisible."""
    model.set_marks(str(repo), (("M", "src/deep/buried.py"),))
    assert model.mark_for(str(repo / "src")) == "·"
    assert model.mark_for(str(repo / "src" / "deep")) == "·"


def test_the_root_itself_is_never_marked(model, repo):
    model.set_marks(str(repo), (("M", "src/app.py"),))
    assert model.mark_for(str(repo)) == ""


def test_a_new_status_replaces_the_previous_one(model, repo):
    model.set_marks(str(repo), (("M", "src/app.py"),))
    model.set_marks(str(repo), ())
    assert model.mark_for(str(repo / "src" / "app.py")) == ""
    assert model.mark_for(str(repo / "src")) == ""


def test_marks_reach_the_model_through_the_role(model, repo):
    from asomedeck.ui.file_tree import MARK_ROLE, PATH_ROLE

    _fetch(model)
    model.set_marks(str(repo), (("A", "README.md"),))
    readme = _child(model, QModelIndex(), "README.md")
    assert readme.data(MARK_ROLE) == "A"
    assert os.path.basename(readme.data(PATH_ROLE)) == "README.md"


# --- refresh ---------------------------------------------------------------------


def test_a_new_file_appears_without_collapsing_the_tree(model, repo):
    _fetch(model)
    src = _child(model, QModelIndex(), "src")
    _fetch(model, src)
    (repo / "src" / "written_by_an_agent.py").write_text("x\n", encoding="utf-8")

    model.refresh()
    names = {model.index(row, 0, src).data() for row in range(model.rowCount(src))}
    assert "written_by_an_agent.py" in names


def test_an_unchanged_listing_emits_nothing(model, repo):
    """refresh() runs on every git poll, so a quiet tree must stay completely still.

    A model that resets on each poll would drop the selection and collapse the tree
    every few seconds, which is worse than not refreshing at all.
    """
    _fetch(model)
    src = _child(model, QModelIndex(), "src")
    _fetch(model, src)

    signals = []
    for signal in (model.rowsInserted, model.rowsRemoved, model.modelReset):
        signal.connect(lambda *a: signals.append(a))

    model.refresh()
    assert signals == []


def test_an_unexpanded_directory_is_not_walked_by_refresh(model, repo):
    """Laziness has to survive the refresh, or it was never laziness."""
    _fetch(model)
    src = _child(model, QModelIndex(), "src")
    model.refresh()
    assert model.canFetchMore(src)


def test_a_deleted_file_disappears(model, repo):
    _fetch(model)
    (repo / "README.md").unlink()
    model.refresh()
    names = {model.index(row, 0, QModelIndex()).data()
             for row in range(model.rowCount(QModelIndex()))}
    assert "README.md" not in names


def test_switching_decks_replaces_the_whole_tree(model, tmp_path):
    _fetch(model)
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "only.txt").write_text("x", encoding="utf-8")

    model.set_root(str(other))
    assert model.rowCount(QModelIndex()) == 0        # lazy again
    assert _fetch(model) == 1
    assert model.index(0, 0, QModelIndex()).data() == "only.txt"


# --- file type -------------------------------------------------------------------
#
# Hue says what a file is, value still says whether git has touched it. Both signals
# share one run of text, and the second one is the older rule -- the marks down the
# right-hand side only read because everything unchanged stays quiet.


@pytest.fixture
def typed(app, tmp_path):
    _ = app
    for name in ("app.py", "build.ps1", "tsconfig.json", "README.md",
                 "logo.png", "Dockerfile"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    (tmp_path / "src").mkdir()
    model = FileTreeModel()
    model.set_root(str(tmp_path))
    _fetch(model)
    return model


def _bucket(model, name):
    return _child(model, ROOT, name).data(TYPE_ROLE)


def test_the_type_is_resolved_once_at_scan_time_and_carried_on_the_role(typed):
    assert _bucket(typed, "app.py") == "python"
    assert _bucket(typed, "build.ps1") == "powershell"
    assert _bucket(typed, "tsconfig.json") == "json"
    assert _bucket(typed, "README.md") == "markdown"


def test_a_named_file_with_no_extension_is_still_typed(typed):
    # files.language_for's whole-name table. "Dockerfile" is the case it exists for.
    assert _bucket(typed, "Dockerfile") == "shell"


def test_a_directory_has_no_type(typed):
    # Nothing to colour: a folder is not a kind of file, and the tree keeps the tier it
    # always painted them in.
    assert _bucket(typed, "src") == ""


def test_a_file_nothing_claims_reports_no_bucket(typed):
    assert _bucket(typed, "logo.png") == "plain"


def test_the_four_families_come_out_as_four_colours(typed):
    theme = Theme("dark")
    hues = {name: theme.file_color(_bucket(typed, name)).name()
            for name in ("app.py", "build.ps1", "tsconfig.json", "README.md")}
    assert len(set(hues.values())) == 4, hues


def test_an_untyped_file_looks_exactly_as_it_did_before_there_were_hues(typed):
    theme = Theme("dark")
    assert theme.file_color(_bucket(typed, "logo.png")).name() == theme.hex("text")
    assert theme.file_color(_bucket(typed, "logo.png"),
                            muted=True).name() == theme.hex("dim")


def test_git_still_decides_which_of_two_python_files_is_the_loud_one(typed):
    # The rule this colouring had to survive. Same hue, two tiers, and the changed one
    # is the one that stands out -- otherwise the marks stop being what the eye finds.
    theme = Theme("dark")
    changed = theme.file_color("python").getHsv()[2]
    untouched = theme.file_color("python", muted=True).getHsv()[2]
    assert changed > untouched


def test_a_marked_folder_is_still_the_loud_one(app):
    """Not a hypothetical. git reports a wholly untracked directory as one entry --
    `? build/` -- so a folder reaches this with a real mark, and the tree painted those
    at full contrast before there were any file hues."""
    _ = app
    delegate = FileDelegate(Theme("dark"))
    theme = Theme("dark")

    assert delegate.name_color(True, "A", "").name() == theme.hex("text")
    assert delegate.name_color(True, "", "").name() == theme.hex("dim2")


def test_a_folder_with_something_buried_in_it_stays_quiet(app):
    """`·` says a change is somewhere below, which is not the same as this folder being
    the change -- it was never the bright tier and is not now."""
    _ = app
    assert FileDelegate(Theme("dark")).name_color(True, "·", "").name() == \
        Theme("dark").hex("dim2")


def test_a_folder_takes_no_hue_from_its_name(app):
    """A directory called `src.py` is still a directory."""
    _ = app
    delegate = FileDelegate(Theme("dark"))
    assert delegate.name_color(True, "", "python") == delegate.name_color(True, "", "")


def test_a_file_takes_its_hue_and_git_decides_the_tier(app):
    _ = app
    delegate = FileDelegate(Theme("dark"))
    theme = Theme("dark")

    assert delegate.name_color(False, "M", "python") == theme.file_color("python")
    assert delegate.name_color(False, "", "python") == \
        theme.file_color("python", muted=True)
    # A file under a folder that is merely dirty is not itself changed.
    assert delegate.name_color(False, "·", "python") == \
        theme.file_color("python", muted=True)


def test_a_rename_retypes_the_file(typed, tmp_path):
    # The bucket is resolved in _Node.__init__ and never recomputed, which is only safe
    # because a rename rebuilds the node.
    (tmp_path / "app.py").rename(tmp_path / "app.ps1")
    typed.refresh()

    assert _bucket(typed, "app.ps1") == "powershell"
