"""Parsing git's machine-readable status (spec 14.6).

The parser is pure, so this is headless. porcelain=v2 was chosen precisely because it is
specified and stable -- these cases pin the fields the sidebar actually renders.
"""

from __future__ import annotations

from asomedeck.git_status import GitState, parse_branches, parse_porcelain_v2

CLEAN = """# branch.oid abc123
# branch.head main
# branch.upstream origin/main
# branch.ab +0 -0
"""

DIRTY = """# branch.oid abc123
# branch.head feat/reconcile
# branch.ab +2 -0
1 .M N... 100644 100644 100644 aaa bbb src/reconcile/sum.py
1 M. N... 100644 100644 100644 ccc ddd src/reconcile/models.py
1 .D N... 100644 100644 000000 eee fff docs/old.md
? tests/test_fx.py
"""


def test_clean_tree():
    state = parse_porcelain_v2(CLEAN)
    assert state.known and state.clean
    assert state.branch == "main"
    assert state.summary == "clean"
    assert state.files == ()


def test_dirty_tree_counts_and_files():
    state = parse_porcelain_v2(DIRTY)
    assert state.known and not state.clean
    assert state.branch == "feat/reconcile"
    assert len(state.files) == 4
    paths = [p for _m, p in state.files]
    assert "src/reconcile/sum.py" in paths
    assert "tests/test_fx.py" in paths


def test_the_summary_counts_paths_and_says_so():
    """It used to read ``+3 -1``, which in every other tool means lines in a diff.

    They were never lines. ``+3`` was three changed paths and ``-1`` was one deleted
    file, so a three-hundred-line edit and a new empty file each counted as one. The
    chip claimed to be a measurement it was not making.
    """
    state = parse_porcelain_v2(DIRTY)
    assert state.changed == 4
    assert state.summary == "4 changed"
    assert "+" not in state.summary and "-" not in state.summary


def test_a_deleted_file_still_counts_as_a_change():
    """It is not excluded now that additions and deletions are not split apart."""
    state = parse_porcelain_v2(DIRTY)
    assert ("D", "docs/old.md") in state.files or any(m == "D" for m, _ in state.files)
    assert state.changed == len(state.files)


def test_one_change_is_not_pluralised_wrongly():
    state = parse_porcelain_v2("# branch.head main\n? new.py\n")
    assert state.summary == "1 changed"


def test_untracked_files_are_reported_as_added():
    state = parse_porcelain_v2("# branch.head main\n? new.py\n")
    assert state.files == (("A", "new.py"),)
    assert not state.clean


def test_detached_head_is_labelled_not_left_in_parentheses():
    state = parse_porcelain_v2("# branch.head (detached)\n")
    assert state.branch == "detached"


def test_renames_report_the_new_path():
    line = ('2 R. N... 100644 100644 100644 aaa bbb R100 '
            'src/new_name.py\tsrc/old_name.py\n')
    state = parse_porcelain_v2("# branch.head main\n" + line)
    assert state.files[0][1] == "src/new_name.py"


def test_unmerged_paths_are_marked():
    line = ("u UU N... 100644 100644 100644 100644 aaa bbb ccc "
            "src/conflict.py\n")
    state = parse_porcelain_v2("# branch.head main\n" + line)
    assert state.files == (("U", "src/conflict.py"),)


def test_empty_output_is_known_and_clean():
    # git exited 0 with nothing to say: a repository with no branch line yet.
    assert parse_porcelain_v2("").known is True


def test_unknown_state_renders_nothing():
    # What a non-repository produces. The delegate omits the chip rather than
    # implying a clean tree nobody checked.
    state = GitState()
    assert state.known is False
    assert state.summary == ""


# --- branches --------------------------------------------------------------------------
#
# The branch chip's dropdown. Tab-separated because a branch name cannot contain a tab --
# git refuses to create one -- so the split is exact where splitting on whitespace would
# break both "ahead 1, behind 3" and every name with a space in it that git does allow.

BRANCHES = (
    "*\tpane-roster\t[ahead 2]\t3 hours ago\n"
    " \tmaster\t\t2 days ago\n"
    " \tfix/tab-strip\t[ahead 1, behind 3]\t3 weeks ago\n"
)


def test_the_checked_out_branch_is_the_one_marked():
    branches = parse_branches(BRANCHES)
    assert [b.name for b in branches] == ["pane-roster", "master", "fix/tab-strip"]
    assert [b.current for b in branches] == [True, False, False]


def test_the_track_loses_its_brackets_and_keeps_its_words():
    # "[ahead 2]" is how `git branch -vv` punctuates it for somebody reading a column of
    # them. In a menu row beside the name the brackets are noise.
    assert [b.track for b in parse_branches(BRANCHES)] == [
        "ahead 2", "", "ahead 1, behind 3"]


def test_a_branch_with_no_upstream_has_an_empty_track_not_a_missing_field():
    # The field is always emitted; it is the value that is empty. Dropping a row for
    # being short would hide every branch that has never been pushed.
    branch = parse_branches(" \tlocal-only\t\t5 minutes ago\n")[0]
    assert branch.name == "local-only" and branch.track == ""


def test_a_slash_in_the_name_survives():
    assert parse_branches(" \tfeat/a/b/c\t\tnow\n")[0].name == "feat/a/b/c"


def test_the_relative_date_is_carried_whole():
    assert parse_branches(BRANCHES)[1].when == "2 days ago"


def test_no_commits_yet_is_an_empty_list_and_not_an_error():
    # `for-each-ref refs/heads` in a repository with no commit prints nothing and exits
    # 0. The menu says so; it is not a failure to report.
    assert parse_branches("") == ()


def test_a_short_line_is_dropped_rather_than_unpacked():
    # Belt and braces against a git that formats differently: a Branch built out of
    # missing fields is a menu entry naming nothing.
    assert parse_branches("* only-two\tfields\n") == ()
