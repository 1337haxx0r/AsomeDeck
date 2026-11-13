"""Cloning a repository into a deck.

Real git against a real repository on disk wherever git is what is being tested, because what
matters is how git behaves: what it does with an address that looks like an option, whether it
waits for a password, what it leaves behind. No network: a local path is a repository address
like any other.

A slow stand-in replaces git for the cancelling tests, since a local clone finishes before
anybody could press anything.
"""

from __future__ import annotations

import logging
import os
import stat
import subprocess
import sys
import textwrap
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication, QObject, QProcess  # noqa: E402
from PySide6.QtWidgets import QFileDialog  # noqa: E402

from asomedeck import git_clone  # noqa: E402
from asomedeck.commands import by_id  # noqa: E402
from asomedeck.platform_ import IS_WINDOWS  # noqa: E402
from asomedeck.ui.clone_dialog import CloneDialog  # noqa: E402

GIT = git_clone.find_git()
NEEDS_GIT = pytest.mark.skipif(GIT is None, reason="git is not installed")

#: What a job is parented to here, as the dialog is in the app. Without a parent a job belongs to
#: Python, and whichever thread the garbage collector next runs on would destroy it, which Qt
#: answers by aborting the process.
_OWNER = QObject()


def _pump(until, timeout: float = 30.0) -> bool:
    """Run the event loop until ``until()``, which is how a QProcess gets anywhere."""
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        QCoreApplication.processEvents()
        if until():
            return True
        time.sleep(0.005)
    return bool(until())


@pytest.fixture
def source(tmp_path):
    """A repository with one commit, to clone from."""
    repo = tmp_path / "origin-repo"
    repo.mkdir()
    (repo / "marker.txt").write_text("from the source\n", encoding="utf-8")
    identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid"]
    for arguments in (["init", "-q"], ["add", "."], [*identity, "commit", "-qm", "first"]):
        subprocess.run([GIT, *arguments], cwd=repo, check=True, capture_output=True)
    return repo


class Slow(git_clone.CloneJob):
    """A job whose "git" makes a partial clone, including a read-only file, and then waits."""

    @staticmethod
    def _command(git, url, destination):
        script = textwrap.dedent(f"""\
            import os, stat, sys, time
            dest = {destination!r}
            os.makedirs(os.path.join(dest, ".git", "objects"), exist_ok=True)
            pack = os.path.join(dest, ".git", "objects", "pack-1.pack")
            open(pack, "w").close()
            os.chmod(pack, stat.S_IREAD)
            print("Receiving objects:  42% (42/100)", flush=True)
            time.sleep(60)
        """)
        return sys.executable, ["-c", script]


def _record(job) -> dict:
    seen: dict = {"progress": [], "succeeded": [], "failed": [], "cancelled": []}
    job.progress.connect(lambda phase, percent: seen["progress"].append((phase, percent)))
    job.succeeded.connect(seen["succeeded"].append)
    job.failed.connect(seen["failed"].append)
    job.cancelled.connect(lambda: seen["cancelled"].append(True))
    return seen


# --- working out a folder name -----------------------------------------------------------


@pytest.mark.parametrize("url, name", [
    ("https://github.com/owner/repo.git", "repo"),
    ("https://github.com/owner/repo", "repo"),
    ("https://github.com/owner/repo/", "repo"),
    ("https://github.com/owner/repo.git?ref=main", "repo"),
    ("git@github.com:owner/repo.git", "repo"),
    ("git@host.example:repo.git", "repo"),
    ("ssh://git@host.example:2222/owner/repo.git", "repo"),
    ("C:\\src\\repo", "repo"),
    ("C:\\src\\repo\\.git", "repo"),
    ("/srv/git/repo.git", "repo"),
    ("  https://github.com/owner/repo.git  ", "repo"),
    ("https://github.com/owner/dotted.name.git", "dotted.name"),
    ("", ""),
    ("..", ""),
])
def test_a_folder_name_is_worked_out_from_the_address(url, name):
    assert git_clone.repo_folder_name(url) == name


@pytest.mark.parametrize("name", [
    "", "   ", ".", "..", "a/b", "a\\b", "..\\up", "C:", "what?", "trailing.", "trailing ",
    "CON", "nul", "COM1", "lpt9.txt"])
def test_a_folder_name_cannot_escape_the_folder_or_upset_windows(name):
    assert git_clone.name_problem(name)


@pytest.mark.parametrize("name", ["repo", "dotted.name", "with space", "-odd", "CONSOLE"])
def test_ordinary_folder_names_are_left_alone(name):
    assert git_clone.name_problem(name) is None


# --- what can be told before trying ------------------------------------------------------


def test_an_address_starting_with_a_dash_is_refused():
    assert git_clone.url_problem("--upload-pack=calc.exe")


def test_an_address_with_a_line_break_is_refused():
    assert git_clone.url_problem("https://host/repo\nsecond")


def test_a_local_path_with_a_space_is_a_perfectly_good_address():
    assert git_clone.url_problem("C:\\my repos\\thing") is None


def test_a_folder_that_is_not_there_is_a_problem(tmp_path):
    assert git_clone.destination_problem(str(tmp_path / "missing"), "repo")


def test_a_relative_folder_is_a_problem(tmp_path):
    assert git_clone.destination_problem("relative", "repo")


def test_a_new_folder_inside_an_existing_one_is_fine(tmp_path):
    assert git_clone.destination_problem(str(tmp_path), "repo") is None


def test_an_empty_existing_folder_is_fine(tmp_path):
    (tmp_path / "repo").mkdir()
    assert git_clone.destination_problem(str(tmp_path), "repo") is None


def test_a_folder_with_something_in_it_is_a_problem(tmp_path):
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "keep.txt").write_text("mine", encoding="utf-8")
    assert "not empty" in git_clone.destination_problem(str(tmp_path), "repo")


def test_a_file_where_the_folder_would_go_is_a_problem(tmp_path):
    (tmp_path / "repo").write_text("a file", encoding="utf-8")
    assert git_clone.destination_problem(str(tmp_path), "repo")


# --- reading git ---------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("Receiving objects:  42% (420/1000)\r", ("Receiving objects", 42)),
    ("remote: Counting objects:  50% (5/10)\r", ("Counting objects", 50)),
    ("Receiving objects:  10% (1/10)\rReceiving objects:  20% (2/10)\r",
     ("Receiving objects", 20)),
    ("Resolving deltas: 100% (3/3), done.\n", ("Resolving deltas", 100)),
    ("Cloning into 'repo'...\n", None),
])
def test_progress_is_the_latest_percentage(text, expected):
    assert git_clone.parse_progress(text) == expected


def test_a_failure_is_git_s_error_rather_than_its_progress():
    output = ("Cloning into 'x'...\nReceiving objects:  10% (1/10)\r"
              "fatal: repository 'https://host/x' not found\n")
    assert git_clone.failure_message(output) == "fatal: repository 'https://host/x' not found"


def test_a_failure_that_wanted_a_login_says_so_in_plain_words():
    output = ("fatal: could not read Username for 'https://host': "
              "terminal prompts disabled\n")
    assert git_clone.failure_message(output) == git_clone.LOGIN_HINT


def test_a_failure_with_nothing_to_say_still_says_something():
    assert git_clone.failure_message("")


def test_a_password_in_an_address_is_not_written_down():
    assert git_clone.redact("https://me:hunter2@host/repo.git") == "https://host/repo.git"
    assert git_clone.redact("ssh://git@host/repo.git") == "ssh://host/repo.git"
    assert git_clone.redact("git@host:repo.git") == "git@host:repo.git"


# --- how git is run ------------------------------------------------------------------------


def test_the_address_cannot_be_read_as_an_option():
    _program, arguments = git_clone.CloneJob._command("git", "--upload-pack=x", "dest")
    assert arguments[arguments.index("--") + 1] == "--upload-pack=x"


def test_git_is_told_nobody_is_there_to_answer(app):
    assert git_clone.CloneJob._environment().value("GIT_TERMINAL_PROMPT") == "0"


def test_git_speaks_the_language_its_output_is_read_in(app):
    assert git_clone.CloneJob._environment().value("LC_ALL") == "C"


def test_git_gets_the_environment_a_child_should(app, monkeypatch):
    # Not ours. A frozen app's PATH leads with its bundle: see asomedeck/env.py.
    monkeypatch.setattr(git_clone, "for_child", lambda: {"PATH": "cleaned"})
    assert git_clone.CloneJob._environment().value("PATH") == "cleaned"


# --- real clones ---------------------------------------------------------------------------


@NEEDS_GIT
def test_a_clone_lands_where_it_was_sent(app, source, tmp_path):
    destination = tmp_path / "copy"
    job = git_clone.CloneJob(_OWNER)
    seen = _record(job)
    job.start(str(source), str(destination), GIT)
    assert _pump(lambda: seen["succeeded"] or seen["failed"])
    assert seen["succeeded"] == [str(destination)]
    assert (destination / "marker.txt").read_text(encoding="utf-8").strip() == "from the source"
    assert (destination / ".git").is_dir()
    assert not job.running


@NEEDS_GIT
def test_a_clone_can_fill_an_empty_folder(app, source, tmp_path):
    destination = tmp_path / "already-here"
    destination.mkdir()
    job = git_clone.CloneJob(_OWNER)
    seen = _record(job)
    job.start(str(source), str(destination), GIT)
    assert _pump(lambda: seen["succeeded"] or seen["failed"])
    assert seen["succeeded"] == [str(destination)]


@NEEDS_GIT
def test_a_failed_clone_reports_why_and_leaves_nothing(app, tmp_path):
    destination = tmp_path / "copy"
    job = git_clone.CloneJob(_OWNER)
    seen = _record(job)
    job.start(str(tmp_path / "no-such-repository"), str(destination), GIT)
    assert _pump(lambda: seen["failed"] or seen["succeeded"])
    assert "fatal:" in seen["failed"][0]
    assert not destination.exists()


@NEEDS_GIT
def test_an_address_like_an_option_runs_nothing(app, tmp_path):
    # git would run this as the remote's upload-pack if it were read as an option. The dialog
    # refuses a leading dash first, so this is the second lock on the same door.
    witness = tmp_path / "witness.txt"
    job = git_clone.CloneJob(_OWNER)
    seen = _record(job)
    job.start(f"--upload-pack={sys.executable} -c open(r'{witness}','w')",
              str(tmp_path / "copy"), GIT)
    assert _pump(lambda: seen["failed"] or seen["succeeded"])
    assert seen["failed"]
    assert not witness.exists()


@NEEDS_GIT
def test_an_address_that_tries_to_run_a_command_is_stopped_by_git(app, tmp_path):
    """``ext::`` makes the address a command line. Git refuses it unless told otherwise, and a
    pasted address is exactly the case that default is for. Pinned because a clone being one
    paste away from running something is not a thing to find out about later."""
    witness = tmp_path / "witness.txt"
    command = (f"ext::cmd /c type nul > {witness}" if IS_WINDOWS
               else f"ext::sh -c touch% {witness}")
    job = git_clone.CloneJob(_OWNER)
    seen = _record(job)
    job.start(command, str(tmp_path / "copy"), GIT)
    assert _pump(lambda: seen["failed"] or seen["succeeded"])
    assert seen["failed"]
    assert not witness.exists()


def test_a_program_that_is_not_there_is_reported_and_not_raised(app, tmp_path):
    job = git_clone.CloneJob(_OWNER)
    seen = _record(job)
    job.start("https://host/repo.git", str(tmp_path / "copy"),
              str(tmp_path / "no-such-git.exe"))
    assert _pump(lambda: seen["failed"])
    assert "could not be started" in seen["failed"][0]
    assert not job.running


def test_a_password_stays_out_of_the_log(app, tmp_path, caplog):
    job = Slow(_OWNER)
    seen = _record(job)
    with caplog.at_level(logging.DEBUG, logger="asomedeck.git_clone"):
        job.start("https://me:hunter2@host/repo.git", str(tmp_path / "copy"), "unused")
        job.cancel()
        assert _pump(lambda: seen["cancelled"])
    assert "cloning https://host/repo.git" in caplog.text
    assert "hunter2" not in caplog.text


# --- stopping one, and what is deleted -----------------------------------------------------


def _partial_exists(destination) -> bool:
    return (destination / ".git" / "objects" / "pack-1.pack").exists()


def test_cancelling_removes_what_the_clone_made_including_read_only_files(app, tmp_path):
    destination = tmp_path / "copy"
    job = Slow(_OWNER)
    seen = _record(job)
    job.start("https://host/repo.git", str(destination), "unused")
    assert _pump(lambda: seen["progress"], 15)
    assert seen["progress"] == [("Receiving objects", 42)]
    assert _partial_exists(destination)
    pack = destination / ".git" / "objects" / "pack-1.pack"
    assert not os.stat(pack).st_mode & stat.S_IWRITE
    job.cancel()
    assert _pump(lambda: seen["cancelled"], 15)
    assert not destination.exists()
    assert not seen["failed"]
    assert not job.running


def test_cancelling_a_clone_into_an_empty_folder_keeps_the_folder(app, tmp_path):
    # It was there before anybody pressed anything, so it is not this clone's to delete.
    destination = tmp_path / "already-here"
    destination.mkdir()
    job = Slow(_OWNER)
    seen = _record(job)
    job.start("https://host/repo.git", str(destination), "unused")
    assert _pump(lambda: seen["progress"], 15)
    job.cancel()
    assert _pump(lambda: seen["cancelled"], 15)
    assert destination.is_dir()
    assert list(destination.iterdir()) == []


def test_a_folder_with_something_in_it_is_refused_and_untouched(app, tmp_path):
    """The rule that makes clearing up after a cancel safe: nothing here was ever ours."""
    destination = tmp_path / "mine"
    destination.mkdir()
    keep = destination / "keep.txt"
    keep.write_text("do not lose this", encoding="utf-8")
    job = Slow(_OWNER)
    seen = _record(job)
    job.start("https://host/repo.git", str(destination), "unused")
    assert seen["failed"] and "not empty" in seen["failed"][0]
    assert not job.running
    job.cancel()
    assert keep.read_text(encoding="utf-8") == "do not lose this"


def test_cancelling_and_waiting_has_finished_by_the_time_it_returns(app, tmp_path):
    # Quitting goes this way, and nothing runs after it.
    destination = tmp_path / "copy"
    job = Slow(_OWNER)
    seen = _record(job)
    job.start("https://host/repo.git", str(destination), "unused")
    assert _pump(lambda: seen["progress"], 15)
    job.cancel(wait=True)
    assert not destination.exists()


# --- the dialog ----------------------------------------------------------------------------


@pytest.fixture
def dialog(app, tmp_path):
    built = CloneDialog(str(tmp_path), GIT or "git")
    yield built
    built.abandon()
    built.deleteLater()


def _type_url(dialog, text):
    dialog.url.setText(text)
    dialog.url.textEdited.emit(text)


def _type_name(dialog, text):
    dialog.name.setText(text)
    dialog.name.textEdited.emit(text)


def test_an_empty_form_cannot_start_and_does_not_complain(dialog):
    assert not dialog.start_button.isEnabled()
    assert dialog.status.text() == ""


def test_the_folder_comes_filled_in_and_the_address_is_next(dialog, tmp_path):
    assert dialog.folder.text() == str(tmp_path)
    assert dialog.url.text() == ""


def test_the_name_follows_the_address(dialog):
    _type_url(dialog, "https://github.com/owner/first.git")
    assert dialog.name.text() == "first"
    _type_url(dialog, "https://github.com/owner/second.git")
    assert dialog.name.text() == "second"


def test_a_name_somebody_typed_is_not_overwritten(dialog):
    _type_url(dialog, "https://github.com/owner/first.git")
    _type_name(dialog, "mine")
    _type_url(dialog, "https://github.com/owner/second.git")
    assert dialog.name.text() == "mine"


def test_emptying_the_name_hands_it_back_to_the_address(dialog):
    _type_url(dialog, "https://github.com/owner/first.git")
    _type_name(dialog, "mine")
    _type_name(dialog, "")
    assert dialog.name.text() == "first"


def test_it_says_where_the_clone_will_go(dialog, tmp_path):
    _type_url(dialog, "https://github.com/owner/repo.git")
    assert dialog.start_button.isEnabled()
    assert str(tmp_path / "repo") in dialog.status.text()


def test_it_says_why_it_cannot_start(dialog, tmp_path):
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "keep.txt").write_text("mine", encoding="utf-8")
    _type_url(dialog, "https://github.com/owner/repo.git")
    assert not dialog.start_button.isEnabled()
    assert "not empty" in dialog.status.text()
    assert dialog.status.property("tone") == "bad"


def test_an_address_that_is_also_the_destination_cannot_start(dialog, tmp_path):
    """Git reports success for this when the source does not exist: it makes the folder, then
    clones it into itself. It was this test suite that found out."""
    _type_url(dialog, str(tmp_path / "typo"))
    assert dialog.name.text() == "typo"
    assert not dialog.start_button.isEnabled()
    assert "same place" in dialog.status.text()


@pytest.mark.parametrize("url", [
    "https://host/typo", "git@host:typo", "ssh://host/typo", "file:///typo"])
def test_a_remote_address_is_never_the_same_place(url, tmp_path):
    assert git_clone.same_place_problem(url, str(tmp_path / "typo")) is None


def test_a_local_address_elsewhere_is_not_the_same_place(tmp_path):
    assert git_clone.same_place_problem(str(tmp_path / "a" / "x"), str(tmp_path / "x")) is None


def test_the_same_place_is_noticed_whatever_the_spelling(tmp_path):
    assert git_clone.same_place_problem(str(tmp_path) + os.sep + "x" + os.sep, str(tmp_path / "x"))
    if IS_WINDOWS:
        assert git_clone.same_place_problem(str(tmp_path / "X"), str(tmp_path / "x"))


def test_the_job_refuses_it_as_well_without_the_dialog(app, tmp_path):
    destination = tmp_path / "typo"
    job = Slow(_OWNER)
    seen = _record(job)
    job.start(str(destination), str(destination), "unused")
    assert seen["failed"] and "same place" in seen["failed"][0]
    assert not destination.exists()


def test_browse_fills_in_the_folder(dialog, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        lambda *a, **k: elsewhere.as_posix())
    _type_url(dialog, "https://github.com/owner/repo.git")
    dialog.browse.click()
    assert dialog.folder.text() == str(elsewhere)
    assert str(elsewhere / "repo") in dialog.status.text()


def test_giving_up_on_the_picker_changes_nothing(dialog, tmp_path, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "")
    dialog.browse.click()
    assert dialog.folder.text() == str(tmp_path)


@NEEDS_GIT
def test_a_successful_clone_reports_and_closes(dialog, source, tmp_path):
    landed = []
    dialog.cloned.connect(landed.append)
    dialog.show()
    _type_url(dialog, str(source))
    _type_name(dialog, "copy")
    dialog.start_button.click()
    assert _pump(lambda: landed)
    assert landed == [str(tmp_path / "copy")]
    assert not dialog.isVisible()


@NEEDS_GIT
def test_a_failed_clone_goes_back_to_the_form_with_everything_still_in_it(dialog, tmp_path):
    address = str(tmp_path / "elsewhere" / "no-such-repository")
    _type_url(dialog, address)
    dialog.show()
    dialog.start_button.click()
    assert _pump(lambda: not dialog.cloning and dialog.url.isEnabled())
    assert _pump(lambda: "fatal:" in dialog.status.text())
    assert dialog.url.text() == address
    assert dialog.start_button.isEnabled()
    assert dialog.isVisible()
    assert dialog.status.property("tone") == "bad"


def test_cancel_during_a_clone_stops_it_and_then_closes(dialog, tmp_path, monkeypatch):
    monkeypatch.setattr(git_clone.CloneJob, "_command", staticmethod(Slow._command))
    _type_url(dialog, "https://github.com/owner/repo.git")
    dialog.show()
    dialog.start_button.click()
    assert dialog.cloning
    assert not dialog.url.isEnabled()
    assert _pump(lambda: dialog.progress.value() == 42, 15)
    assert "Receiving objects" in dialog.status.text()

    dialog.cancel_button.click()
    assert dialog.isVisible(), "should stay up until the folder has been cleared"
    assert dialog.status.text() == "Cancelling…"

    assert _pump(lambda: not dialog.isVisible(), 15)
    assert not (tmp_path / "repo").exists()


def test_closing_the_dialog_during_a_clone_is_the_same_as_cancel(dialog, tmp_path, monkeypatch):
    monkeypatch.setattr(git_clone.CloneJob, "_command", staticmethod(Slow._command))
    _type_url(dialog, "https://github.com/owner/repo.git")
    dialog.show()
    dialog.start_button.click()
    assert _pump(lambda: dialog.progress.value() == 42, 15)
    dialog.close()
    assert _pump(lambda: not dialog.isVisible() and not (tmp_path / "repo").exists(), 15)


def test_cancel_with_nothing_running_just_closes(dialog):
    dialog.show()
    dialog.cancel_button.click()
    assert not dialog.isVisible()


# --- in the window -------------------------------------------------------------------------


def _clone_entry(window):
    for action in window.sidebar._add_menu.actions():
        if action.text() == by_id("deck.clone").label:
            return action
    raise AssertionError("no clone entry on the add menu")


def _with_git(monkeypatch, path):
    monkeypatch.setattr(git_clone, "find_git", lambda: path)


def test_the_command_is_on_the_file_menu(window):
    assert by_id("deck.clone").menu == "File"
    assert window.command_actions["deck.clone"].text() == by_id("deck.clone").label


def test_without_git_every_way_in_is_greyed_and_says_why(window, monkeypatch):
    _with_git(monkeypatch, None)
    window.sidebar._add_menu.aboutToShow.emit()
    assert not _clone_entry(window).isEnabled()
    assert _clone_entry(window).toolTip() == git_clone.NO_GIT_HINT
    assert not window.command_actions["deck.clone"].isEnabled()
    assert window.command_actions["deck.clone"].toolTip() == git_clone.NO_GIT_HINT
    assert not by_id("deck.clone").enabled(window)


def test_the_commands_refreshing_does_not_undo_the_grey(window, monkeypatch):
    _with_git(monkeypatch, None)
    window._recheck_git()
    window._refresh_commands()
    assert not window.command_actions["deck.clone"].isEnabled()


def test_git_appearing_is_noticed_when_a_menu_opens(window, monkeypatch):
    _with_git(monkeypatch, None)
    window.sidebar._add_menu.aboutToShow.emit()
    assert not _clone_entry(window).isEnabled()
    _with_git(monkeypatch, "git")
    window.title_bar.menus["File"].aboutToShow.emit()
    assert _clone_entry(window).isEnabled()
    assert window.command_actions["deck.clone"].isEnabled()
    assert _clone_entry(window).toolTip() != git_clone.NO_GIT_HINT


def test_without_git_no_dialog_opens(window, monkeypatch):
    _with_git(monkeypatch, None)
    window._clone_deck()
    assert window._clone_dialog is None


def test_the_entry_opens_one_dialog_and_asking_again_shows_the_same_one(window, monkeypatch):
    _with_git(monkeypatch, "git")
    window.sidebar._add_menu.aboutToShow.emit()
    _clone_entry(window).trigger()
    first = window._clone_dialog
    assert first is not None and first.isVisible()
    _clone_entry(window).trigger()
    assert window._clone_dialog is first
    first.reject()
    assert window._clone_dialog is None


def test_it_does_not_stop_the_terminals(window, monkeypatch):
    """A clone can take an hour, and the window should still work for all of it."""
    _with_git(monkeypatch, "git")
    window._clone_deck()
    assert not window._clone_dialog.isModal()
    window._clone_dialog.reject()


def test_the_folder_offered_is_beside_the_open_deck(window):
    assert window._clone_folder() == os.path.dirname(window.deck.path)


def test_a_deck_with_no_folder_offers_home(window, monkeypatch):
    window.deck.path = ""
    assert window._clone_folder() == os.path.expanduser("~")


def test_a_cloned_folder_becomes_the_open_deck(window, tmp_path):
    destination = tmp_path / "parent" / "repo"
    destination.mkdir(parents=True)
    window._on_cloned(str(destination))
    assert os.path.normcase(window.deck.path) == os.path.normcase(str(destination))
    assert window.deck.name == "repo"


def test_the_next_clone_offers_where_the_last_one_went(window, tmp_path):
    parent = tmp_path / "parent"
    destination = parent / "repo"
    destination.mkdir(parents=True)
    window._on_cloned(str(destination))
    assert window._clone_folder() == str(parent)


def test_a_remembered_folder_that_has_gone_is_not_offered(window, tmp_path):
    window.db.set_meta("clone_folder", str(tmp_path / "gone"))
    assert window._clone_folder() != str(tmp_path / "gone")


@NEEDS_GIT
def test_from_the_menu_to_an_open_deck(window, source, tmp_path):
    """The whole of it: the entry, the form, real git, and the deck it ends in."""
    before = len(window.db.decks())
    window._clone_deck()
    dialog = window._clone_dialog
    dialog.folder.setText(str(tmp_path))
    _type_url(dialog, str(source))
    _type_name(dialog, "landed")
    dialog.start_button.click()
    destination = tmp_path / "landed"
    assert _pump(lambda: window._clone_dialog is None)
    assert (destination / "marker.txt").exists()
    assert len(window.db.decks()) == before + 1
    assert os.path.normcase(window.deck.path) == os.path.normcase(str(destination))


def test_quitting_during_a_clone_stops_it_and_clears_up(window, tmp_path, monkeypatch):
    monkeypatch.setattr(git_clone.CloneJob, "_command", staticmethod(Slow._command))
    _with_git(monkeypatch, "git")
    window._clone_deck()
    dialog = window._clone_dialog
    dialog.folder.setText(str(tmp_path))
    _type_url(dialog, "https://github.com/owner/repo.git")
    dialog.start_button.click()
    assert _pump(lambda: dialog.progress.value() == 42, 15)
    process: QProcess = dialog._job._process

    window.close()

    assert process.state() == QProcess.ProcessState.NotRunning
    assert not (tmp_path / "repo").exists()
