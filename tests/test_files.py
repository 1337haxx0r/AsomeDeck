"""Listing, classifying and round-tripping files (files.py).

Headless, and the most load-bearing suite in the deck: everything here is what stands
between the editor pane and quietly rewriting somebody's source. The round-trip cases are
the point -- load a file, save it without touching it, and the bytes must be identical.
Every one of them is a real default of ``open()`` that would fail it.
"""

from __future__ import annotations

import codecs
import os

import pytest

from asomedeck import files


@pytest.fixture
def deck(tmp_path):
    """A small tree with the shapes a real repository has."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    for noise in (".git", "__pycache__", "node_modules", ".venv", "asomedeck.egg-info"):
        (tmp_path / noise).mkdir()
    return tmp_path


# --- listing ---------------------------------------------------------------------


def test_directories_come_first_then_files(deck):
    names = [entry.name for entry in files.list_dir(str(deck))]
    assert names == ["src", "tests", ".gitignore", "README.md"]


def test_sorting_ignores_case(tmp_path):
    for name in ("Zebra.py", "apple.py", "Banana.py"):
        (tmp_path / name).touch()
    names = [entry.name for entry in files.list_dir(str(tmp_path))]
    assert names == ["apple.py", "Banana.py", "Zebra.py"]


def test_generated_directories_are_omitted(deck):
    names = {entry.name for entry in files.list_dir(str(deck))}
    assert not names & {".git", "__pycache__", "node_modules", ".venv"}


def test_egg_info_is_omitted_by_suffix(deck):
    assert "asomedeck.egg-info" not in {e.name for e in files.list_dir(str(deck))}


def test_dotfiles_are_shown(deck):
    """.gitignore is a file somebody wrote. Only generated *directories* are hidden."""
    assert ".gitignore" in {entry.name for entry in files.list_dir(str(deck))}


def test_ambiguous_build_directories_are_not_hidden(tmp_path):
    """``dist`` and ``build`` are generated in some decks and hand-written in others.

    The tree cannot tell which, and hiding a directory somebody wrote is the worse of
    the two mistakes -- it looks like the file is missing.
    """
    for name in ("dist", "build", "target", "out"):
        (tmp_path / name).mkdir()
        assert not files.hidden(name)
    assert len(files.list_dir(str(tmp_path))) == 4


def test_an_unreadable_directory_lists_as_empty(tmp_path):
    assert files.list_dir(str(tmp_path / "nope")) == []


def test_entries_carry_a_usable_absolute_path(deck):
    entry = next(e for e in files.list_dir(str(deck)) if e.name == "README.md")
    assert os.path.isfile(entry.path) and not entry.is_dir


# --- classification --------------------------------------------------------------


def test_source_is_text(deck):
    assert files.classify(str(deck / "src" / "app.py")) == files.TEXT


def test_a_null_byte_means_binary(tmp_path):
    target = tmp_path / "icon.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    assert files.classify(str(target)) == files.BINARY


def test_utf16_is_text_despite_its_null_bytes(tmp_path):
    """The NUL heuristic alone reads every UTF-16 file as binary.

    Every ASCII character in UTF-16 carries a null byte, so without the BOM check first
    a UTF-16 source file could not be opened at all.
    """
    target = tmp_path / "wide.txt"
    target.write_bytes(codecs.BOM_UTF16_LE + "hello\n".encode("utf-16-le"))
    assert files.classify(str(target)) == files.TEXT


def test_a_large_file_is_refused_by_size_not_by_reading_it(tmp_path):
    target = tmp_path / "huge.log"
    target.write_bytes(b"x" * (files.MAX_BYTES + 1))
    assert files.classify(str(target)) == files.TOO_BIG


def test_a_missing_file_says_so(tmp_path):
    assert files.classify(str(tmp_path / "gone.txt")) == files.MISSING


# --- newlines --------------------------------------------------------------------


def test_newline_is_counted_not_assumed():
    assert files.detect_newline("a\r\nb\r\nc\r\n") == "\r\n"
    assert files.detect_newline("a\nb\nc\n") == "\n"
    assert files.detect_newline("a\rb\rc\r") == "\r"


def test_a_file_with_no_line_ending_defaults_to_lf():
    assert files.detect_newline("one line") == "\n"


def test_the_majority_wins_in_a_mixed_file():
    assert files.detect_newline("a\r\nb\r\nc\n") == "\r\n"
    assert files.detect_newline("a\nb\nc\r\n") == "\n"


# --- the round trip --------------------------------------------------------------

ROUND_TRIP = {
    "lf": b"first\nsecond\nthird\n",
    "crlf": b"first\r\nsecond\r\nthird\r\n",
    "no trailing newline": b"just this",
    "empty": b"",
    "utf-8 with an accent": "café naïve\n".encode(),
    "utf-8 with a BOM": codecs.BOM_UTF8 + "café\n".encode(),
    "utf-16 little endian": codecs.BOM_UTF16_LE + "wide\r\n".encode("utf-16-le"),
    "utf-16 big endian": codecs.BOM_UTF16_BE + "wide\n".encode("utf-16-be"),
    "windows-1252": b"caf\xe9 \x93quoted\x94\n",
    # Every byte value except NUL and the two line endings: those have cases of their
    # own, and including them here would test the newline rule rather than the encoding
    # fallback this case is for.
    "not decodable as anything": bytes(
        b for b in range(256) if b not in (0x00, 0x0A, 0x0D)),
}


@pytest.mark.parametrize("label", sorted(ROUND_TRIP))
def test_loading_and_saving_unedited_changes_nothing(tmp_path, label):
    """The single most important assertion in the suite.

    Each case is a file the naive path corrupts: text mode adds carriage returns to the
    LF one, plain ``utf-8`` turns the BOM into a stray character, the locale encoding
    mangles the accent, and Python's ``utf-16`` codec byte-swaps the big-endian one.
    """
    original = ROUND_TRIP[label]
    target = tmp_path / "subject.txt"
    target.write_bytes(original)

    loaded = files.load(str(target))
    files.save(str(target), loaded.text, loaded.encoding, loaded.newline)

    assert target.read_bytes() == original


def test_an_edit_keeps_the_files_own_line_ending(tmp_path):
    """A CRLF file edited here stays CRLF -- one changed line, not a whole-file diff."""
    target = tmp_path / "windows.txt"
    target.write_bytes(b"alpha\r\nbeta\r\n")
    loaded = files.load(str(target))
    assert loaded.text == "alpha\nbeta\n"     # the buffer is always LF

    files.save(str(target), loaded.text.replace("beta", "gamma"),
               loaded.encoding, loaded.newline)
    assert target.read_bytes() == b"alpha\r\ngamma\r\n"


def test_mixed_line_endings_are_normalised_and_that_is_deliberate(tmp_path):
    """The one documented case where a save is not byte-identical.

    Tracking endings per line is what it would take to preserve a mixture, and no editor
    the user already has does that either. Asserted rather than left to be discovered.
    """
    target = tmp_path / "mixed.txt"
    target.write_bytes(b"a\r\nb\r\nc\n")
    loaded = files.load(str(target))
    files.save(str(target), loaded.text, loaded.encoding, loaded.newline)
    assert target.read_bytes() == b"a\r\nb\r\nc\r\n"


def test_an_undecodable_file_still_opens(tmp_path):
    """latin-1 maps all 256 byte values, so there is no such thing as a refusal here."""
    target = tmp_path / "mystery.dat"
    target.write_bytes(b"\x81\x8d\x8f\x90\x9d normally undefined in cp1252\n")
    loaded = files.load(str(target))
    assert loaded.encoding == "latin-1"


# --- saving ----------------------------------------------------------------------


def test_saving_leaves_no_temporary_file_behind(tmp_path):
    target = tmp_path / "code.py"
    target.write_text("x = 1\n", encoding="utf-8")
    loaded = files.load(str(target))
    files.save(str(target), "x = 2\n", loaded.encoding, loaded.newline)
    assert [p.name for p in tmp_path.iterdir()] == ["code.py"]
    assert target.read_text(encoding="utf-8") == "x = 2\n"


def test_a_failed_save_cleans_up_and_raises(tmp_path):
    """The temp file must not survive a failure, or the tree fills with debris."""
    target = tmp_path / "code.py"
    target.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(OSError):
        # A directory where the destination file should be: os.replace refuses.
        (tmp_path / "blocked").mkdir()
        files.save(str(tmp_path / "blocked"), "x\n", "utf-8", "\n")
    assert not any(p.name.endswith(".asomedeck-tmp") for p in tmp_path.iterdir())


def test_save_reports_the_new_fingerprint(tmp_path):
    # write_bytes, not write_text: text mode on Windows turns that LF into CRLF, and a
    # test about byte counts must not depend on which platform wrote its fixture.
    target = tmp_path / "code.py"
    target.write_bytes(b"x = 1\n")
    loaded = files.load(str(target))
    after = files.save(str(target), "x = 22222\n", loaded.encoding, loaded.newline)
    assert after.size == len("x = 22222\n")
    assert not files.changed_on_disk(str(target), after)


# --- change detection ------------------------------------------------------------


def test_an_untouched_file_is_not_reported_as_changed(tmp_path):
    target = tmp_path / "code.py"
    target.write_text("x = 1\n", encoding="utf-8")
    assert not files.changed_on_disk(str(target), files.load(str(target)))


def test_a_rewritten_file_is_reported_as_changed(tmp_path):
    target = tmp_path / "code.py"
    target.write_text("x = 1\n", encoding="utf-8")
    loaded = files.load(str(target))
    target.write_text("x = 1 # touched by an agent\n", encoding="utf-8")
    assert files.changed_on_disk(str(target), loaded)


def test_a_deleted_file_counts_as_changed(tmp_path):
    target = tmp_path / "code.py"
    target.write_text("x = 1\n", encoding="utf-8")
    loaded = files.load(str(target))
    target.unlink()
    assert files.changed_on_disk(str(target), loaded)


# --- language --------------------------------------------------------------------


@pytest.mark.parametrize("name,expected", [
    ("app.py", "python"),
    ("index.tsx", "js"),
    ("package.json", "json"),
    ("README.md", "markdown"),
    ("build.sh", "shell"),
    ("pyproject.toml", "toml"),
    ("action.yml", "yaml"),
    ("main.rs", "clike"),
    ("Dockerfile", "shell"),
    ("Makefile", "shell"),
    (".gitignore", "shell"),
    ("page.html", "markup"),
    ("styles.scss", "css"),
    ("q.sql", "sql"),
    ("build.ps1", "powershell"),
    ("run.bat", "batch"),
    ("index.php", "clike"),
    ("mystery.qqq", "plain"),
    ("no_extension", "plain"),
])
def test_language_by_name_and_extension(name, expected):
    assert files.language_for(os.path.join("some", "dir", name)) == expected


def test_a_whole_name_beats_an_extension():
    """``.gitignore`` splits to an extension of ``.gitignore`` and no stem."""
    assert files.language_for(".gitignore") == "shell"


def test_a_named_file_keeps_its_type_under_a_suffix():
    """``.env.local`` splits to (".env", ".local"), so neither lookup hits on its own
    and the file came out plain."""
    assert files.language_for(".env.local") == "shell"
    assert files.language_for(".env.production") == "shell"
    assert files.language_for("Dockerfile.dev") == "shell"


def test_an_extension_beats_a_stem():
    """The other way round, ``makefile.py`` would be coloured as shell."""
    assert files.language_for("makefile.py") == "python"
