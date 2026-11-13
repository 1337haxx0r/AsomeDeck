# Design notes

What each part of AsomeDeck does and why it was built that way, written down as it was
built. The [README](../README.md) is the short version.

References such as §9 or "spec §14.7" point to the original design spec, which is not
published.

## Run

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m asomedeck
```

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m asomedeck
```

Chords are written `Ctrl+…` throughout this file and in `config.json`. **On macOS that
means Cmd** — Qt maps it, so `Ctrl+Shift+T` is ⇧⌘T there and the menus say so. The one
place the two genuinely differ is the clipboard, below.

| | |
|---|---|
| `Ctrl+Shift+T` | new session |
| `Ctrl+Shift+D` | split right |
| `Ctrl+Shift+E` | split down |

Splitting and closing act on the pane you are **typing in**. That needs saying because it did not: focus lands on the terminal widget inside a pane and Qt tells no ancestor, so `Pane.focusInEvent` never fired and the active pane stayed wherever it was last set — usually the newest. `PaneTree` watches `QApplication.focusChanged` and walks up to the owning pane instead.

| `Ctrl+Shift+W` | close the focused pane (or the ✕ in its header) |
| `Ctrl+Alt+Z` | zoom the focused pane to fill the grid, and back |
| `Ctrl+Alt+←` / `Ctrl+Alt+→` | previous / next deck |
| `Ctrl+Shift+B` | collapse the decks panel to a rail |
| `Ctrl+Alt+B` | collapse the session panel to a rail |
| `Ctrl+Shift+C` / `Ctrl+Shift+V` | copy and paste in a terminal (macOS: plain `Cmd+C` / `Cmd+V`) |
| `F2` | rename the selected deck, with the decks list focused |

The two at the bottom are the exceptions to everything above them. The rest come from the
command registry, fire wherever focus is, and can be rebound by id in `config.json`; these
two are scoped to a widget and cannot. That is what lets them be short. `Ctrl+C` is SIGINT
and has to stay SIGINT, so on Windows the clipboard takes the shifted forms and
`TerminalView` handles them itself — while on macOS the command modifier is Cmd, which is
not a control byte at all, so there the plain chord is both correct and native and the
Shift is dropped; `F2` is a function key `keys.py` encodes, so binding it window-wide would take
it away from every shell in the app. `Ctrl+S` in the editor is scoped for the same reason —
delivered to a terminal it is XOFF.

`Ctrl+Shift+Insert` pastes too, and so does the middle mouse button.

## Menus on the right button

**A terminal.** Copy, paste, select all, clear scrollback, and the three pane commands.
Copy is greyed when nothing is selected and clear is greyed when there is no scrollback to
clear — including on the alternate screen, where the history that exists is the one from
before `vim` started and has nothing to do with what is on screen.

The rule that makes it correct is the one selection already followed: **a program that
asked for mouse tracking owns the right button**, and `Shift` is the override. Inside `vim`
or `less` a right-click is a command, and a menu appearing over it swallows the click the
program was waiting for. Windows delivers the context-menu event on the release, by which
point the button reports have already gone to the program, so declining is the whole of
what pass-through needs.

The split and close rows are the window's *own* `QAction`s rather than copies — the trick
`TitleBar.set_actions` already uses. Each shows the chord that actually fires it, including
one `config.json` has rebound, and `_refresh_commands` greying an action greys the row.

**A pane's title bar.** Hide pane, show hidden panes, and the four pane commands. Nothing
runs in a title bar, so unlike the terminal there is nobody to defer to and the right
button is unconditionally ours.

The four lower rows act on whichever pane is *active*, so opening the menu makes the pane
you clicked the active one first. The press already does that — `Pane.mousePressEvent`
fires on it, and Windows does not deliver the context-menu event until the release — but
four rows depending on the order a platform happens to deliver two mouse events in is not
a thing to leave to chance, and doing it explicitly is also what lets a test open the menu
without sending a press at all.

**A deck row.** Colours, rename, change shell, remove. All four act on the row that was
clicked, which is newer than it sounds: rename and the shell picker were written against
`self.deck` and so could only ever reach the deck already open. That is why the
colour picker shipped alone — putting rename beside it meant refactoring it to take a
`deck_id` first.

Renaming changes this app's label for a folder and never the folder. Two checkouts of one
repository are both called `app` on disk and need not be here.

## Shells

On Windows: Git Bash, PowerShell 7, PowerShell, cmd — in that preference order. On macOS:
whatever `$SHELL` names, then zsh, bash, `/bin/sh`. A pty has no window, so every one of
them is the same kind of thing: an argv.

`$SHELL` leads on macOS because somebody who changed their login shell to fish, or to a
Homebrew zsh, meant it — a terminal that opens a different shell than every other terminal
on the machine is wrong in a way that is annoying to diagnose and trivial to avoid.

**None of them is required, including Git.** `shells.default()` returns the first one
`available()` actually finds, and `shells.FLOOR` is the answer when nothing is — `cmd` on
Windows, `/bin/sh` on macOS, because each platform guarantees exactly one. This
used to be the constant `DEFAULT = GIT_BASH`, and the detector that knew better had no
callers — so on a machine without Git the app started, drew its chrome, and then every
pane spawned a path that was not there, raised `FileNotFoundError` inside the pty, painted
one line of red and closed itself. The sidebar advertised "Git Bash" throughout; the
status bar, listing `available()`, was the only thing telling the truth.

Set `ASOMEDECK_GIT_ROOT` if Git is somewhere other than `C:\Program Files\Git`. It is read
at import, so it has to be set before launch.

Each deck stores its own choice in `deck.launcher_id` — a column that had been
written since the schema existed and read by nothing. The sidebar footer's "change" picks
it, and offers only installed shells: that menu is the last place that should hand back
the failure it exists to prevent. Panes already running keep the shell they started with,
because a live pty cannot become a different program.

`shells.resolve()` covers the case a layout outlives its machine — pinned to Git Bash,
reopened after Git was uninstalled. It substitutes an installed shell and logs the swap.
`by_id()` deliberately does not: it answers *what is this pane running*, and a pane that
mislabels itself is worse than one that failed honestly.

Unattended self-test of launch → render → shutdown, against a throwaway database:

```powershell
.venv\Scripts\python.exe -m asomedeck --smoke 14
.venv\Scripts\python.exe -m asomedeck --smoke 12 --crash
```

Checks:

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check src tests tools
.venv\Scripts\python.exe tools\probe_termview.py   # the terminal, end to end
.venv\Scripts\python.exe tools\probe_files.py      # the file tree and editor, end to end
.venv\Scripts\python.exe tools\probe_zoom.py       # zoom, against live pseudo-consoles
```

Logs: `%LOCALAPPDATA%\AsomeDeck\asomedeck.log`. A built exe has no console to print to, so
that file is the whole record — and a native fault goes to `asomedeck-fault.log` beside it,
because a dump aimed at a stderr that does not exist is written to nowhere.

## Panes are named by what is running in them

Programs announce themselves through OSC 0/2, and the emulator has captured that since the
terminal was written — `Emulator.titleChanged` reached `TerminalView` and stopped there, so
a pane running Claude Code still called itself "Git Bash". It is wired through now, to the
pane header, the tab strip and the session panel.

Observed rather than assumed: Claude Code sends `✳ Claude Code`, and MSYS bash sends
`MINGW64:/d/PY/AsomeDeck`.

That second one is why this is not simply "show the title". Bash is reporting the
directory, which the pane footer already displays and the deck header displays again;
printing it a third time in the one field that could say what is *running* is worse than
the pane number it replaces. So `main_window.pane_title` drops a title that is only a shell
announcing its cwd, and keeps everything else verbatim. It never invents a name — the same
constraint chrome.py states for every other field.

## Scrolling back, and something to drag

`DEFAULT_SCROLLBACK` is five thousand lines and the wheel has always reached them, but
nothing said where in them you were, or that there was anything above the fold at all.
Every pane now carries a vertical bar.

Both halves of what it needs were written long before anything called them:
`Emulator.total_lines` and `TerminalView.scroll_offset` had no callers in `src/`, `tests/`
or `tools/`. `probe_files.py` names that pattern as the failure this codebase keeps finding
in itself, and this is one instance of it closed.

**The gutter is reserved whether or not the bar is showing.** That is the decision the rest
follows from. A bar that took its width from the grid only while visible would resize the
emulator *and the pseudo-console* the first time scrollback passed one screenful, and again
every time it crossed back — which is the reflow `resizeEvent` and `tools/probe_zoom.py`
already exist to prevent. So the bar is an overlay on a permanent gutter, and appearing
costs a running shell nothing. The price, stated rather than hidden: every pane is one
column narrower than it was, including panes that never scroll.

**It stands down in the alternate screen.** `DeckScreen.index` declines to append to history
while a full-screen program is up, but the history from *before* `vim` started is still
there and `total_lines` still counts it — so a visible bar would offer to scroll into
scrollback that has nothing to do with what is on screen. `wheelEvent` already refused
that, but only when the program had also asked for mouse tracking.

**`_scroll` has one writer now.** Three places used to assign it — the wheel, output
arriving, and a keystroke going to the shell — and a bar has to hear about all three.
`_set_scroll` is the single door, and the clamping moved in with it.

## Decks expand to show their terminals

Click the chevron on a deck row and its terminals appear beneath it, each with the name
the program gave itself, its state dot, and how long it has been running. Clicking one
switches to that deck and focuses that pane.

Flattened into the existing `QListView` rather than modelled as a tree. The sidebar is a
hand-painted delegate, and a `QTreeView` brings its own indentation, branch indicators and
selection behaviour, all of which would fight that design for no gain.

The consequence worth knowing: **a row index is no longer a deck index.** Anything
selecting or repainting a deck by position had to learn that, and
`DeckListModel.deck_at` exists so callers that only handle decks say so — a
right-click on a terminal row previously offered to remove "a deck" and would have named
the wrong one.

A deck that is not open shows what is true of it rather than rows implying otherwise: it
has no sessions, only a saved arrangement, so it says how many terminals come back when it
opens.

Updating a terminal's uptime or state repaints in place; only a change in the *number* of
terminals resets the model. A reset drops the selection, and this runs once a second.

## A deck without a folder

**File → New empty deck…** makes a deck with no folder behind it: terminals, a layout, a
shell and a colour, and nothing underneath. For the shells that are not about a checkout —
a machine you keep an ssh session to, a scratch REPL, whatever is running while you work
on something else — a row over a folder picked only because the dialog insisted on one was
never telling the truth.

What it does not have, it says it does not have rather than showing an empty version of
it. The line under the name reads `no folder`; the branch chip and the change count are
absent, as they already are for a folder that is not a repository; **Files** is switched
off for it, and so is *Open in editor*, which with nothing selected only offers to show
that tree; the palette lists its terminals but no files. Panes start in your home
directory unless they were sent somewhere, and a pane sent somewhere remembers it across a
restart exactly as it does in any other deck.

There is no claims board either, and nothing is lost by that: the board exists because two
agents in one working tree cannot see each other, and this deck has no tree for them to
share.

The row is stored the way every other deck is, with an empty `path`. That is one state and
not two — the column stayed `NOT NULL` rather than becoming nullable, so everything that
walks, polls or joins a path asks the single question `if deck.path:`.

## Cloning a repository

**File → Clone repository…**, also on the sidebar's **+**, is for a deck whose folder does not
exist yet. Pick a folder, give the address of a repository, and the clone goes into a new folder
inside the one you picked, named after the repository unless you change it. When it lands it
becomes a deck and opens, exactly as if you had added that folder by hand.

The folder starts filled in: where your last clone went, otherwise beside the deck you have open,
since repositories on one machine tend to live side by side. As you type, the form says where
the clone will go or why it cannot go there. An existing folder is only accepted if it is empty.

It needs Git. Without it the entry is greyed out, and hovering says why. The check is repeated
each time a menu opens, so installing Git does not take a restart if it lands on the `PATH` this
app already has, or in `C:\Program Files\Git`.

The dialog is not modal, because a large repository can take a long time and your terminals
should not wait for it. There is one at a time; asking again brings the running one forward.

**Cancel** stops the clone and deletes what it had written. Nothing else can be deleted that way:
a clone refuses to start in a folder with anything in it, so whatever is there afterwards is its
own. Quitting the app does the same.

**A repository that needs a login.** A credential helper with its own window, which is what Git
for Windows sets up, works as usual. Anything that would ask in a terminal fails straight away
with a message saying so, because there is no terminal behind the dialog to ask in; clone those
from a terminal pane. `GIT_TERMINAL_PROMPT=0` is what turns an endless wait into that message.

**An address is treated as untrusted**, since it is often pasted. It goes after `--`, so it cannot
be read as an option like `--upload-pack=`, which runs a command; the form refuses a leading dash
as well. `ext::` addresses, which are command lines, are left to Git's own default of refusing
them, and a test holds that. A password in the address stays out of the log, though Git itself
will still store the address in the clone's `.git/config`.

## The branch is a control, not a caption

The deck header reads `pane-roster   |   clean   |   3 panes`, and only the first of those
three is the start of a question. A count of changed paths and a count of panes are
complete answers; a branch name is where somebody wonders *what else is there*. So it is
lifted out of the joined string into a chip, and clicking it drops the repository's local
branches — the one you are on first and checked, then git's own `--sort=-committerdate`,
each with its `ahead 2` where there is one and its age in the tooltip.

**Asked, never polled.** `git_status.py` runs one `git status` every three seconds against
a working tree somebody's agents are using, and `--no-optional-locks` is up there because
that contention is real. A second process per tick, for a list wanted about once an hour,
would be the same mistake with a worse excuse — so `GitBranches.ask` runs on the click and
the poll loop is untouched. The answer carries the path it was asked about, because a deck
can be switched in the time git takes and a branch list for the folder you just left is
worse than none.

**It does not check anything out.** A window full of agents mid-edit is the worst place to
move a working tree from, and the terminal that can do it properly — and tell you why it
refused — is one click away. What the rows do instead is copy the name, which is what you
were going to paste into that terminal anyway: a dropdown of rows that do nothing at all
is the button-that-looks-like-it-works this chrome refuses everywhere else.

Capped at 25 rows. A repository that has been alive for years has hundreds of branches and
a menu that tall cannot be used; what the cap drops is the coldest end of git's own sort,
and the last row says how many.

**The two `+` buttons are one style now.** The sidebar's is a bordered box that outlines
in the accent on hover; the tab strip's was a bare label whose entire hover was one grey
going one shade lighter, sitting a few hundred pixels away under the same glyph. Both wear
`#IconButton`. What they *do* stays different and deliberately so: a deck can be two things
so that one has to ask which first, and a terminal is one thing — putting a menu in front
of the commonest gesture in the window to make the pair symmetric would be symmetry bought
with a click.

## Files, and an editor beside the terminal

The sidebar has two modes. **Files** shows the open deck's tree, marked up with what
git says about each path -- `M`, `A`, `?`, and a `·` on any folder with a change buried
inside it. Clicking a file opens it in a pane next to the terminals.

The marks are why this is not a `QFileSystemModel`. The tree's value in this window is
answering *what did the agent in that pane just touch*, and `git_status.py` already parses
that answer every poll; a stock model cannot carry it without a proxy layer that costs more
than the model saved. Two smaller reasons came with it: `QFileSystemModel` installs a
filesystem watcher per visited directory, which on a repository with a `node_modules` is
thousands of handles for information git already gives us, and its filtering happens after
the fact, so the noise directories flicker in before being hidden.

The model is lazy -- a directory is read when it is expanded, never before -- so opening a
deck costs one `scandir` of the top level whatever the repository weighs.

**A filename is coloured by what kind of file it is**, from the same
`files.language_for` table the editor's highlighter reads -- so the tree cannot decide
`Dockerfile.dev` is one thing while the editor opens it as another. Five hues and not
thirteen: the question a 272px column answers at a glance is *is this code, a script,
config, a page or a doc*, and a rainbow answers it worse by making the column itself the
noise. Anything the table does not claim -- an image, an archive, a `.lock` -- is left the
grey it always was.

The older rule survives underneath it. **Hue says what the file is; brightness still says
whether git has touched it**, because the marks down the right-hand side only read when
everything unchanged stays quiet. Both tiers are measured rather than picked: `dim`, the
grey these names used to be, sits at 5.7:1 against the dark background and 5.3:1 against
the light one, and `theme.MUTE` is tuned so the quiet tier lands beside it in both. The
first attempt was two steps further down and put the light theme's muted green at 2.7:1 --
under the 3:1 floor `_readable_on` holds every other colour in that file to, and fainter
than the grey it replaced. An unchanged file is most of the tree; it is the tier that has
to stay readable.

**One pane, reused.** The first file opens a pane; later clicks replace its contents rather
than splitting again. Browsing a tree opens files as fast as you can click, and a pane per
click buries the terminals the window exists for inside a minute.

**It is an editor, not an IDE.** Syntax colouring, line numbers, undo, block indent, and
`Ctrl+S`. No completion, no symbol index, no language server, no repository-wide search: those
exist one alt-tab away and a bad reimplementation of them earns nothing. What it *is* for
is the case this app is built around -- an agent says it changed `src/foo.py`, and reading
the file does not mean leaving the window.

Three details that are not obvious and were each a decision:

- **`Ctrl+S` is scoped to the widget, not the window.** Every other key in §9 is a window
  shortcut, which fires wherever focus is. `Ctrl+S` delivered to a terminal is XOFF: it
  freezes the output of whatever is running there, and it looks exactly like a hang.
- **A file is not a session.** `EditorPane.is_alive()` is False, so the quit prompt never
  offers to close terminals that are text files and the status bar never counts one. That
  makes an editor pane indistinguishable from an *empty* pane, which is why `Pane.kind`
  exists -- without it the window helpfully launches a shell into every editor it finds.
- **Unsaved changes block quitting**, separately from and before the running-terminals
  prompt. A terminal closing loses scrollback; an editor closing loses something typed.

**Saving cannot damage a file.** `files.py` is pure and headless so the round trip is
testable, and `tests/test_files.py` pins that loading a file and saving it unedited
produces byte-identical output. Each of the following is a *default* of `open()` that would
fail that: the locale encoding turning UTF-8 accents to mojibake, text mode adding a CR to
every line of a LF file, a UTF-8 BOM decoded into a stray character at offset 0, and
Python's own `utf-16` codec re-emitting a big-endian file in little-endian. The write goes
to a sibling temp file and is moved into place, so a save interrupted partway cannot leave
the source truncated.

The one deliberate exception, stated rather than hidden and asserted by a test: a file with
*mixed* line endings is normalised to whichever ending was in the majority.

**The pane follows the file.** A clean buffer whose file changed on disk reloads itself --
that is the whole point when the terminal beside it is what changed it. A *dirty* one only
raises a banner offering reload or keep-mine: choosing between two versions is the user's,
and doing it for them destroys work.

## Busy or waiting, and how that is known

Spec 14.3 cut this once. Under docking the app could see that a process was alive and
nothing else, so `StatusDot` shipped with LIVE and DEAD and left RUNNING and WAITING
defined but unreachable — a launcher had to "opt into title_patterns", which nobody could
build, because a foreign window's caption was the only source.

Owning the byte stream changes what is observable. Observed by running it:

```
startup   "✳ Claude Code"
working   "● What you can do"      a spinner frame; the glyph changes
done      "✳ What you can do"
```

`term/activity.py` claims each state for a different reason, weakest claim last:

1. Nothing running — **dead**.
2. The title changed a moment ago — **running**. A program animating its own title is
   doing something, whatever the glyph is. The only state claimed from observation rather
   than the program's word, and the one that survives a spinner set changing.
3. Settled, and starts with a mark meaning ready — **waiting**. The program said so.
4. Anything else — **live**. Alive, and no claim about what it is doing.

Rule 2 preceding rule 3 is what makes reading `✳` safe: that glyph is in the spinner
sequence as well as at rest, but a frame does not last, and the ready reading applies only
to a title that has held still for `SETTLE_SECONDS`.

A shell that never sets a title stays **live** — not idle. It is not saying, which is a
different thing, and the dot does not invent the difference.

### What the sidebar shows, which is not the same question

Those four states answer "what is this pane doing *now*", which is the wrong question for a
row you glance at while working somewhere else. By the time you look, the turn that
finished over there is over: the pane is back to **live**, indistinguishable from a shell
nobody has touched, and the one thing worth telling you has already scrolled past.

So the two states worth acting on are *sticky*. Each is set by an event, outranks whatever
the pane is doing now, and is cleared by looking at the pane:

- **A turn ended** — the pane settled on the ready mark after a run of renames. Green.
- **The program rang the bell** — `emulator.py` already parsed BEL and beeped; it now says
  which pane rang, too. Amber, and it outranks green, because one is asking and the other
  is only reporting.

Both survive the process exiting. A run that rang and then died is exactly the case worth
keeping — the count beside the deck reads zero and the dot is the only thing left
saying so.

**Ending a turn is not the same as starting up ready**, and the settle rule alone cannot
tell them apart: Claude Code names itself `✳ Claude Code` once at launch and rests there,
which is the same mark, held just as still, as the one a finished turn ends on. What
differs is the journey — a spinner renames the pane on every frame, a launch renames it
once. `TURN_TITLE_CHANGES` is that threshold, and without it restoring a deck with four
saved sessions announced four finished turns.

**Looking** means all four of: the active pane in its tree, in the tree that is on screen,
not hidden, in a foreground window. Drop any one and the flags start lying — every deck
has an "active" pane of its own that nobody is watching, and a turn that finished while the
app sat behind a browser is the case most worth keeping.

### One dot for a whole deck

A collapsed deck row answers one question — *does this need me?* — so the loudest pane
wins: attention, then finished, then anything alive. **Running collapses into live here and
nowhere else.** Across a list of decks the difference between working and idle is noise,
since neither is asking for anything, and spending a third colour on it would dilute the
two that are. Expand the deck and the session rows still draw the distinction.

Hue answers *does this want me*; motion answers *is it live*. Running and idle therefore
share the accent and differ only by the pulse, which leaves green and amber as the only two
colours worth crossing the room for. Green does not pulse: a finished turn is at rest, and
a resting dot that keeps moving reads as work still going on.

Two things had to be fixed before any of this could show. The deck dot was painted from
`"live" if row.live_count else ("dead" if not row.session_count else "live")`, whose two
branches both yield `live` whenever there is a session — so the busy tally computed for it
never changed a pixel, and the dot was a restatement of the count beside it. And the tick
walked the visible deck's tree alone, so panes elsewhere were never re-read and their
state froze at whatever it was when you switched away. A status light for the decks you
are *not* looking at is the whole point, so the loop now covers the ones you are not
looking at.

## Panes that stay out of each other's way

Panes cannot talk to each other. Each is a shell on its own pseudo-console with no channel
to any other, and the one thing crossing a pane boundary runs the wrong way for this: the
window reads a pane's title and shows a dot, and the pane learns nothing.

That does not matter until two of those shells are running agents, because panes are *not*
isolated where it counts. A pane launches into its deck's folder unless it was sent
somewhere else, and several decks may sit over one checkout — so the panes around it are
editing the same working tree whichever of those is true. Two agents editing one file is
not a race this app can see, let alone arbitrate.

So there is a board. `.asomedeck/board.md`, in the checkout, seeded the first time a
terminal starts there: an agent reads it before it starts, writes a line claiming what it
is taking, and deletes the line when it is done.

```
- pane 3 (designer) -- src/asomedeck/ui/term_view.py, term/keys.py -- reflow on resize
```

Deliberately a file and not a message bus. Panes publishing to each other means something
that outlives a pane and owns the sessions — the broker process this app keeps naming
as the v2 question and keeps not building. A file needs no broker, survives the crash that
takes every pty with it, and can be read by the person watching. Nothing enforces it: the
board is a convention with a place to live, and that is the whole of it.

**Each pane is told which pane it is.** The environment is the only channel into a pty that
is not the byte stream, so the shell is spawned with three variables:

| | |
|---|---|
| `ASOMEDECK_PANE_ID` | this pane's number, the one in its header |
| `ASOMEDECK_PROJECT` | the deck it belongs to (the name is the stored one; see `board.py`) |
| `ASOMEDECK_BOARD` | the full path to the board |

Empty values are left out rather than set empty. If the board could not be written — a
read-only share, most likely — `ASOMEDECK_BOARD` is absent instead of naming a file that is
not there, and you get terminals that work and no board, rather than a launch that refuses.
A deck with no folder is the same absence for a different reason: no working tree, no
board, no variable naming one.

**And three things are taken back out.** A frozen app carries its own Qt, and PyInstaller
and PySide6 arrange that by writing into `os.environ` — `QT_PLUGIN_PATH`,
`QML2_IMPORT_PATH`, the bootloader's `_PYI_*`, and on Windows a bundle directory prepended
to `PATH`. All of it is correct for us and wrong for anything we start. The 0.7.1 bundle
exported its plugin path into every pane, which was enough that this repository's own test
suite could not run in one of this app's own terminals: Qt loaded the bundle's platform
plugin alongside the venv's and initialised neither.

`env.py` is the answer and `PtySession` is the single door it goes through, so the cleanup
cannot be true of one pty backend and forgotten on the other. It cannot be done by
snapshotting the environment before Qt loads — PyInstaller's runtime hooks run before the
first line of our code, so the value is already overwritten by the time anything of ours
could read it. The question is asked of each path entry instead: *does this point inside
our own installation*. That is also what leaves a `QT_PLUGIN_PATH` the **user** set
untouched, and what keeps `PATH` a working `PATH` while dropping the one entry we added
to it.

**Pane numbers now survive a restart**, which they did not before. That is what makes a
claim mean anything the next morning: the number is saved with the arrangement, so pane 3
comes back as pane 3 rather than as whatever the counter reached first. A layout written by
an older build has no numbers in it and still opens; a hand-edited one that repeats a
number gets the duplicate reassigned, because two panes answering to one number is worse
than one pane answering to a new one.

**Panes can be named, and the names are listed beside the board.** Right-click a pane's
header and choose *Name pane…* to call it `designer`, `tests`, whatever role it has. The name
shows in the header, the tab, the sidebar and the status bar, and is saved with the layout.
It is the window's, not the program's: whatever agent you start in the pane — Claude Code,
Codex, anything — the pane is called the same thing, and nothing that agent does can rename
it.

Names are unique among the panes over **one folder**, and only there. Two decks over one
checkout cannot both have a `designer`; two checkouts each can, and never meet. That scope
is the roster's: `.asomedeck/panes.md` lists every pane running over the folder, from
every deck pointed at it, by number, name and deck. AsomeDeck rewrites it whenever a
terminal there starts, stops or is renamed, and empties it when the app quits, because the
terminals end with it. Agents read it and never write it — the opposite of the board.

```
| pane | name | deck |
|---:|---|---|
| 3 | designer | AsomeDeck |
| 5 | tests | AsomeDeck 2 |
```

The number stays the identity. It is in the environment, which cannot change after a shell
starts; a name can be changed at any time, so an agent looks its own up in the roster from
`$ASOMEDECK_PANE_ID` rather than being told it at spawn. A name starts with a letter, holds
letters, digits and single `-` or `_`, and is lowercased as it is kept — nothing in it can
be read as a pane number or break a claim line. A saved layout whose name another pane over
the folder already holds loses it on opening, for the reason a duplicated pane number is
renumbered.

**The directory ignores itself.** `.asomedeck/.gitignore` holds `*`, which covers the file
naming it, so git reports nothing there at all. Coordination scratch must not turn every
checkout it touches permanently dirty — least of all in the sidebar's own git chip, which
counts untracked paths.

**One step is yours.** An agent finds the board because `AGENTS.md` tells it to, and
AsomeDeck will not edit your `AGENTS.md` — it is your file and it is usually in git. The
paragraph to paste is seeded beside the board, at `.asomedeck/agents-md-snippet.md`.
`AGENTS.md` because a pane runs whatever agent you start in it and that is the file most of
them read; Claude Code reads `CLAUDE.md`, and a `CLAUDE.md` holding the line `@AGENTS.md`
pulls the same instructions in. Builds up to 0.9.1 seeded `claude-md-snippet.md` instead;
an untouched copy of it is removed the next time a terminal starts there, and an edited one
is left alone. The board and the roster also explain themselves in full, so an agent
pointed straight at `$ASOMEDECK_BOARD` learns the protocol without any of it.

What is still on you: a session cannot outlive the app, but the file does, so a claim can
be left behind by a pane that is no longer running. The sidebar shows which panes are live.
Nothing sweeps the stale ones yet.

## One pane, filling the grid

`Ctrl+Alt+Z`, the `⤢` in a pane's header, or a double-click on that header gives the focused
pane the whole terminal area. The same again puts the grid back at the proportions it had.
The sidebar, tab strip and status bar stay where they are — this zooms the panes, not the
window, and the tab strip is what you switch panes with while zoomed.

**The pane never leaves the splitter tree.** Zoom walks from the pane up to the root hiding
everything off that path. Lifting it out into a stacked layout is the obvious alternative and
it breaks two things at once: `to_tree` serialises the live widget tree, so a pane parented
elsewhere vanishes from the saved arrangement, and `_splitter_of` falls back to the root for a
widget with no splitter ancestor, so the next split would land at the root instead of beside
its neighbour.

Three things Qt does here that had to be measured rather than assumed, each of which decided
part of the design:

- **A hidden child reports a size of 0.** `[299, 300, 299]` becomes `[0, 900, 0]`, and the
  layout save is debounced by 1.2 s, so an unrelated drag a moment earlier lands the write
  *while* zoomed. The damage is quieter than it sounds and worse for it: `_apply_sizes`
  rejects any list holding a zero, so the arrangement does not come back collapsed — it comes
  back with no proportions at all. `_serialise` reports the sizes recorded before the zoom.
- **`splitterMoved` does not fire on `hide`/`show`/`setSizes`.** So zoom cannot trigger a save
  by itself, and `layoutChanged` stays honest: it means what `to_tree` returns is different,
  and zoom changes nothing it returns.
- **A hidden widget's resize events are deferred and delivered on `show`** — two of them,
  `-1 → 0` and then `0 → real`. `grid_size` clamps a zero extent to its 2×1 floor, so taking
  that first event at face value resized the emulator *and the pseudo-console* to two columns
  and back on every unzoom. pyte discards what no longer fits, so a full-screen program lost
  its frame permanently. `TerminalView.resizeEvent` declines a zero extent for that reason,
  and `tests/test_term_view.py` asserts the pty is not resized behind a hidden pane — it fails
  with `[(2, 23), (52, 23)]` if the guard is removed.

`tools/probe_zoom.py` is the version of that with nothing stood in for: three real shells on
three real ConPTYs, zoomed and unzoomed, checking that the two panes which were hidden come
back reporting the grid they went in with.

Restoring **root outward** is the other half, and it is the half that is easy to get wrong
because nothing about the code says so. `_zoom_sizes` is filled leaf-to-root by the walk that
hides, and unzoom reads it backwards: handing an inner splitter its children back while the
root is still collapsed lays them out at the zoomed width and corrects them a moment later,
which is a pty resize each way for a pane that never moved. Measured on `H[0 V[1 2]]` at
1200px, unzooming pane 1 — leaf-first takes panes 1 and 2 through 149 columns and back to 74;
root-first resizes each of them once, to the width they are keeping.

With both in place a pane that was only ever a bystander reflows **zero** times, and the
pane that was zoomed reflows once each way, which is the feature rather than churn.

Two smaller things the same shape: the zoom button is `NoFocus`, because a focused
`QPushButton` answers Space by clicking itself — so the chord that zoomed the pane would
unzoom it again — and the window puts the keyboard back into the pane afterwards, because Qt
gives click focus to the nearest ancestor that accepts it, which is the `Pane` and not the
terminal inside it. Without that you enlarge a shell and then cannot type into it.

**Zoom is not saved.** It is a focus mode, not an arrangement — it survives switching decks,
because each deck keeps its own live tree, and a restart comes back as the grid. Splitting
or opening a session while zoomed returns to the grid first, so `Ctrl+Shift+D` cannot appear
to do nothing; and activating a hidden pane moves the zoom to it rather than dropping out,
which is what keeps the tab strip working as a switcher.

`Ctrl+Alt+Z` and not `Ctrl+Shift+Z`, which is what it wants to be. That chord is Windows' redo
and `QPlainTextEdit` claims it — along with `Ctrl+Z` and `Ctrl+Y` — by accepting the
`ShortcutOverride`, so a window action bound to it is simply dead whenever an editor pane has
focus. Zoom would have worked in a terminal and silently done nothing in a file: §9's `Ctrl+B`
complaint, aimed at the editor instead of a shell.

## Panes out of the way

Zoom gives one pane everything. This is the other direction: **Hide pane**, on the
right-click menu of a pane's title bar, takes one pane off the grid and lets its siblings
have the space. Its tab stays in the strip, dashed and reading `pwsh · hidden`, and
clicking it brings the pane back.

**The shell keeps running.** That is the whole difference from closing the pane, and the
reason the feature exists — a dev server, a watcher, a long `tail -f` had no third option
between taking up half the window and being killed. Nothing is resized while a pane is
hidden either, so the program inside it does not discover it has been tucked away.

Coming back is the tab and only the tab, which is deliberate. `PaneTree.set_active` shows
a hidden pane, and the tab strip already routed a click into `set_active` — so the way
back needed no new control, and there is no second thing to find. It is also why **the
last visible pane cannot be hidden**: an empty grid has nothing on it to right-click, so
the menu that emptied it could not undo it. The row greys out, and greying is all there is
to fall back on, because hiding deliberately has no `Command` and therefore no chord.
Closing enforces the same invariant from the other side: closing the last *visible*
pane brings the hidden ones back rather than arriving at an empty grid round the back.

The interesting part is that hiding and zoom are the same Qt call. Both come down to
`hide()` on a splitter child, and run as two independent passes they fight — with a
concrete bug rather than a theoretical one: unzoom used to `show()` every child of every
splitter it had collapsed, which would put a pane the user had deliberately hidden back on
the grid. So both reasons a widget is off the grid go through one predicate,
`_has_visible_pane`, and unzoom asks it instead of showing unconditionally.

That predicate also settles a case hiding has and zoom does not. A splitter is worth
showing only for its contents, so a branch whose panes are *all* hidden is hidden itself —
otherwise it keeps its share of the parent and hiding a pane inside a nested branch
reclaims nothing.

Everything the zoom section had to measure applies here unchanged, and is reused rather
than rediscovered: a hidden child reports a size of 0, so `_serialise` reads the
proportions recorded before the hide and a save landing mid-hide still describes the grid;
show and hide are walked root outward, so a bystander pane reflows zero times.
`tools/probe_zoom.py` now gates both, on three real shells — including the composition:
zoom something else, unzoom it, and the hidden pane is still hidden and still 51 columns
wide.

Proportions come back **when the last hidden pane does**, not one at a time. A record is
taken when the first pane under a splitter goes; applying it while a second is still
hidden would hand space to a widget that is not on screen, and reconciling it against
what is left means guessing which of those sizes have been dragged since. Waiting until
the set empties gives back exactly the grid that was there before any of it started.

**Hiding is not saved**, for zoom's reason and stated the same way: it is a view of the
arrangement, not the arrangement. `to_tree` gains nothing, `hiddenChanged` is a signal of
its own rather than a `layoutChanged`, and a restart comes back showing every pane.

The tab says `hidden` in words and not only in colour. A dimmed tab reads as a dead
session, which is the opposite of what happened — so the sub-text carries the state and
the dashed border is only the glance-level cue. The status dot is left alone for the same
reason: it reports whether the shell is alive, and it is.

## Side panels collapse to a rail

Both panels shrink to 44px rather than disappearing, which hands about 450px back to the
terminals. The left rail keeps the two things the panel is for — the `+` button and one dot
per deck, lit where sessions are running, clickable to switch — so collapsing does not
cost you deck switching. The state persists per install in `app_meta`.

The width lives in the stylesheet, not in `setMaximumWidth`. `#Sidebar` pins `min-width`
*and* `max-width`, so code setting a maximum would be arguing with QSS and losing; a
`collapsed` property with a matching rule is what moves it, plus an explicit repolish and
`updateGeometry`, because Qt neither restyles on a property change nor reconsiders a cached
size hint by itself. `tests/test_chrome.py` asserts the rendered width for that reason.

## Version

`asomedeck.__version__` is the only place it is written. `pyproject.toml` declares
`dynamic = ["version"]` and reads that attribute; the status bar shows it, the startup log
line leads with it, and the exe's Properties → Details pane is generated from it at build
time.

Deliberately not `importlib.metadata.version("asomedeck")`. That reads the installed
distribution's metadata, which exists in a source checkout and does not inside a
PyInstaller bundle — the version would have been right everywhere except the build anybody
actually ships.

`tests/test_version.py` pins that pyproject has not grown a literal copy again. A
duplicated version does not fail loudly; it drifts, and you find out from a bug report
citing a number that was never released.

## Two platforms

The port to macOS cost four files and no architecture, and that is not luck. Owning a byte
stream instead of somebody else's window is what made it possible, and
`tests/test_import_boundary.py` had been enforcing the boundary since before there was a
second platform to test it — its comment read *"Everything else is portable Qt and Python,
which is not an accident — it is what makes a non-Windows port conceivable at all."*

**Where the platform lives.** Five files, and the test suite fails if it spreads:

| | |
|---|---|
| `platform_.py` | which OS, and where per-user files go. The only place `sys.platform` is read |
| `term/conpty.py` | picks a backend and exports `ConptySession`. Nothing above it knows there are two |
| `term/unixpty.py` | the Unix pty. The only file allowed `fcntl`, `termios` or `pty` |
| `term/windowspty.py` | the Windows pty: what the pseudo-console calls in `winapi.py` mean |
| `winapi.py` | the Win32 surface. The only file allowed `ctypes` |

`term/ptysession.py` holds the session and its reader thread — the parts that never knew
what kind of pty they had, which turned out to be almost all of them.

**The keyboard is the one real difference.** Qt on macOS swaps Control and Meta: physical
Cmd arrives as `ControlModifier`, physical Ctrl as `MetaModifier`. Keeping that swap is
what makes every `Ctrl+Shift+…` in `commands.py` bind to a native Cmd chord and render as
⇧⌘T in the menus, for free. So the swap stays, and `term/keys.py` compensates in the one
place it matters — it reads `MetaModifier` as the terminal's control key.

That is what gives macOS the behaviour a terminal should have:

| | |
|---|---|
| physical `Ctrl+C` | SIGINT, as everywhere else since before Qt existed |
| `Cmd+C` / `Cmd+V` | copy and paste — no Shift needed, because Cmd is not a control byte |
| every other `Cmd` chord | declined by the encoder, so it reaches the window's actions |

On Windows the clipboard keeps the shifted forms it has to: `Ctrl+C` is SIGINT there and
cannot be anything else. `ui/term_view.py` is where that one divergence is written down.

**Everything else is a default, not a branch.** macOS gets a native title bar because
`MainWindow`'s `frameless` parameter defaults to `IS_WINDOWS` and the whole Win32 path was
already gated on it — `nativeEvent` and `restore_native_behaviours` were skippable because
the tests already skipped them. The title bar row survives, minus its three window buttons;
it is also where the menus live, so deleting it would have taken those with it.

Shells come from `$SHELL` first, then zsh, bash and `/bin/sh` as the floor. Config and state
go to `~/Library/Application Support/AsomeDeck`, logs to `~/Library/Logs/AsomeDeck` — and
`tests/test_platform.py` pins the Windows paths against literals, because a rewrite that
moved `%APPDATA%` by one directory would look exactly like a user losing every deck they
had registered.

## Build a macOS app

```bash
.venv/bin/python -m pip install -e ".[build]"
bash packaging/build_mac.sh
```

`dist/AsomeDeck-0.10.0.app`, ~83 MB, arm64. The bundle is named for the version it is,
so builds accumulate side by side instead of one silently replacing the one you are still
running; everything inside keeps the plain name, including what the Dock and the menu bar
show. It is a **onedir** bundle, inverting the onefile
choice the Windows build makes on purpose — a `.app` is already a directory the user drags
as one icon, so onefile buys nothing there while still paying the unpack-to-temp cost on
every launch, and a onefile signature covers only the outer stub, which notarization cannot
see past.

Signing is chosen by environment, not by editing the script:

| | |
|---|---|
| nothing set | ad-hoc. PyInstaller signs it, which on Apple Silicon is mandatory — an unsigned arm64 binary will not execute at all |
| `ASOMEDECK_CODESIGN_IDENTITY` | a Developer ID; adds the hardened runtime, `packaging/entitlements.plist` and a secure timestamp |
| `+ ASOMEDECK_NOTARY_PROFILE` | submits to `notarytool`, waits, and staples the ticket |

An ad-hoc build runs here. On someone else's Mac, macOS 15 says *"Apple could not verify
… is free of malware"* and they must allow it once in **System Settings → Privacy &
Security** — the right-click-to-open bypass was removed in Sequoia. Handing it over by USB,
`scp` or git avoids that entirely, because none of those set the quarantine attribute that
triggers it. Only notarization removes the warning for a download.

`assets/icon/asomedeck.icns` is built by `tools/make_icon.py` from the same master as the
`.ico`, so the two platforms' icons cannot drift.

## Build a Windows executable

```powershell
.venv\Scripts\python.exe -m pip install -e ".[build]"
.venv\Scripts\python.exe -m PyInstaller packaging\asomedeck.spec --noconfirm
```

One file, `dist\AsomeDeck-0.10.0.exe`, ~42 MB, no console window.

The exe is named for the version it is, the same way the `.app` is, so two builds sit in
`dist\` side by side instead of one silently replacing the one you are still running.
Everything inside keeps the plain name — the window title, the taskbar entry, and every
string in the version resource except `OriginalFilename`, which is meant to record the
name the binary was built as.

Onefile unpacks its payload to a temp directory on every launch, so it starts slower than
running from source — measured here at **~2.0–2.4 s against ~1.0 s**. That is survivable
only because there is no browser in the bundle; the spec excludes QtWebEngine explicitly,
which is the packaging half of the rule `tests/test_import_boundary.py` pins in the source.

Two things the spec has to say out loud, each of which fails late and confusingly:

- It freezes `packaging/entry.py`, not `src/asomedeck/__main__.py`. The latter is a module
  of relative imports, and PyInstaller runs its script as top-level `__main__` with no
  parent package, so freezing it directly dies on `ImportError` before the window appears.
- It inserts `SPECPATH` on `sys.path`, because PyInstaller executes a spec with the spec's
  own directory absent from it.

A windowed build has no standard streams at all — `sys.stderr` is `None`, and anything
that writes to it unguarded (`faulthandler.enable()` did) raises on launch. Running the exe
from a shell will not catch that: a shell hands the child its own streams. Test it the way
it is launched, with `Start-Process` or Explorer.

The icon is generated, not hand-exported:

```powershell
.venv\Scripts\python.exe tools\make_icon.py assets\icon\source.png
```

Nine sizes from 16 to 256 in one `.ico`, the small ones drawn from a tighter crop — at
16 px the margin and corner radius eat the tile before the artwork gets any of it.

## Why it is not a docking host

The original design (spec v0.3–0.4) reparented real terminal windows — Git Bash, cmd,
PowerShell — into the app with `SetParent`. That shipped and worked: milestones 1 through
4 were built on it. It was removed anyway.

What it bought was fidelity. Git Bash rendered by Git Bash, exactly as it renders
standalone, which was §1's premise.

What it cost, all of it load-bearing:

- **HWND discovery.** Polling `EnumWindows` for a window the launched process might not
  own. Terminals sort into tiers by how they manage their window, and Windows Terminal
  is undockable outright because it hands off and exits.
- **Style capture and a detach contract.** Restore the original styles exactly or hand
  the user a live shell they can neither see nor close.
- **Hidden launches.** Terminals had to start invisible or they flashed on the desktop
  before being adopted — eleven of those at once is a light show.
- **A geometry watchdog**, re-snapping children that drifted.
- **The MSYS pty ceiling.** mintty is a Cygwin program with a ~128-pty pool. Killing
  mintty orphaned the shell it forked, still holding its pty, and once enough leaked
  *every* new terminal failed with `Could not fork child process: There are no available
  terminals` — a message that looks nothing like its cause.

A pseudo-console removes every one of those, because none of them were about terminals.
They were about borrowing another process's window.

**The trade accepted:** this renders xterm.js's interpretation of the shell's output, not
the terminal you would otherwise have run. If a specific terminal's font rendering or
features matter to you, that is a real loss.

The tier system went with it. §5 sorted terminals into A/B/C by how they manage windows;
with no window, `cmd`, PowerShell and bash are the same kind of thing — an argv. §6.1's
`conhost.exe` prefix, which existed to stop Windows 11 handing a process to Windows
Terminal, is likewise moot: we no longer want a window to be handed.

## Why it is not a browser either

The docking backend's replacement rendered the shell with [xterm.js](https://xtermjs.org/)
in a `QWebEngineView`. That shipped and worked — its probe passed all seven checks. It was
replaced anyway, and the reason is smaller and more measurable than last time.

Three panes, same machine, same shells:

| | processes | resident |
|---|---|---|
| xterm.js in QtWebEngine | 12 | 572 MB |
| painted by Qt | 8 | 210 MB |

The web view's share scaled per pane, because each one got its own render process. That is
what made restoring a layout something to be rationed rather than just done, and it is why
`PySide6` can now be `PySide6-Essentials` — `QtWebEngineWidgets` and `QtWebChannel` were the
only PySide6-Addons modules this app ever imported, and that is 358 MB of the 632 MB
PySide6 installs. It also removes a page load from in front of every shell.

**The trade accepted:** AsomeDeck is now the terminal emulator, and the escape-sequence long
tail is where terminals earn their reputation. [pyte](https://github.com/selectel/pyte)
supplies the parser and the screen model; scrollback, the alternate screen, key encoding,
mouse reporting and selection are ours. Two consequences worth naming:

- **Throughput.** pyte is pure Python and parses about 1.3 MB/s here, against tens of MB/s
  for xterm.js. Output is drained on a timer with a per-frame cap, so a shell dumping tens
  of megabytes scrolls rather than freezing — but it scrolls at about a megabyte a second.
- **pyte does not implement the alternate screen at all.** It parses `?1049h`, stores the
  mode and does nothing; `?1049l` never restores. Every full-screen program relies on it, so
  `term/emulator.py` implements it, and `probe_termview.py` check 9 exists to keep it honest.

## Why it is not pywinpty

The pseudo-console was reached through [pywinpty](https://github.com/andfoy/pywinpty) up to
0.8.2. `term/windowspty.py` now calls the API itself, and there were two reasons.

**A window on every launch.** A pseudo-console needs no console in the process that creates
it, but pywinpty allocated one when it found none, and hid it a moment later. A windowed exe
never has a console, so the first shell of every launch showed a blank console window, titled
`AsomeDeck.AsomeDeck.Shell.1`, before the app's own window had painted. It appeared on 7 of 7
launches of the exe and never from a terminal, which already gives a process a console. Now
that no console is allocated, none appears, and `tests/test_windowspty.py` holds that from a
process with no console of its own.

**A speed limit.** Its reader forwarded output through a loopback socket with a sleep per read:

| | pywinpty | direct |
|---|---|---|
| `seq 1 5000` into the scrollback | 3.2 s | 0.68 s |
| `seq 1 200000`, bytes only | unfinished at 90 s | 26 s |

**The host did not change.** pywinpty's wheel carried Microsoft's `conpty.dll` and
`OpenConsole.exe`, and those two files are what actually hosts a shell, so they moved to
`assets/conpty` rather than leaving with it. Without them Windows' own host takes over and
works, but on Windows 11 22H2 it re-renders output rather than passing it through: `vim` gets
no alternate screen, and a native program cannot claim the mouse. See
[`assets/conpty/README.md`](../assets/conpty/README.md).

## What persists, and what cannot

Decks live in SQLite at `%APPDATA%\AsomeDeck\state.db` (spec §9). Each deck owns a
pane tree; switching sets the current page of a `QStackedWidget`, so nothing starts or
stops — spec §4's core invariant.

**A zoomed pane is not saved either**, and unlike a session that is a choice rather than a
limit: zoom is a focus mode, and reopening a deck into one pane with the others hidden
would look like the arrangement had been lost. What it does need is care in the other
direction — see "One pane, filling the grid" for why `to_tree` reports the pre-zoom sizes.

**Sessions are not saved.** A pty belongs to this process and dies with it. Unlike the
docking model, that is now *solvable* rather than impossible: a broker process holding
the ConPTYs would outlive us. It is a v2 question, and nothing here pretends otherwise.

**Arrangements are saved, and refilled.** `layout.tree_json` stores the splitter tree, its
proportions, each pane's shell and each pane's number. Reopening rebuilds the arrangement
and starts a fresh shell in every pane, at the same paths.

The number is saved because it stopped being ours alone: it names a pane in the header, in
the tab strip and in the log, and the shell is now told it as `ASOMEDECK_PANE_ID`. A claim
left on the board has to still mean the same slot after a restart — see "Panes that stay
out of each other's way".

An editor pane is stored as its path and *does* come back exactly as it was -- reopening a
file is cheap and exact in a way reopening a session is not. If the file has since been
deleted, the pane gets a terminal instead of an empty rectangle with no explanation.

Restoring is **immediate**, and used to be sequential. Each shell waited for the previous
one to report itself running, because every pane carried a web view and starting a dozen
render processes at once made the window unresponsive for as long as it took; above six
panes it asked first. Measured after the renderer became native: twelve panes are issued in
126 ms and all twelve shells are live 0.48 s later, so both the queue and the question it
asked are gone. Set `restore_sessions` to `0` in `app_meta` to always restore empty.

A crash still takes every session with it, and the next start says so (§8, mitigation 2).

## Frameless window needs a reserved gutter

**Windows only.** macOS gets an ordinary native title bar instead — traffic lights, resize,
zoom and Mission Control all handled by a window manager that already works — because
`MainWindow.frameless` defaults to `IS_WINDOWS` and everything below is gated on it. None
of the following runs there. See [Two platforms](#two-platforms).

Spec §14.7 wants a custom title bar with native resize borders. A frameless window has no
non-client area, so those borders live inside the client rect.

Two things had to be true, and neither was obvious:

A probe (since removed with the docking backend it depended on) measured that with a
terminal flush to the frame, **all eight edges and corners** were owned by the child rather
than the window — Windows sends `WM_NCHITTEST` to the window under the cursor, so our
handler never ran. At a **6px gutter, none were**. That mattered acutely under docking.

Worth knowing before relying on any of it: `frameless.py`'s `hit_test` and `native_event`
are **never called** — nothing overrides `nativeEvent`, and nothing ever has. Resizing works
only because `restore_native_behaviours` puts `WS_THICKFRAME` back. So the gutter reserves
space for a path that does not execute, and now that no web view can swallow a hit-test
either, the honest options are to wire the handler up or to take the gutter back. Neither
belongs in a terminal rewrite, so it is recorded rather than done.

And Qt's `FramelessWindowHint` clears `WS_THICKFRAME`, which Windows consults *before* it
will run a sizing loop — so a hit-test correctly answering `HTBOTTOMRIGHT` resized
nothing. `winapi.add_window_styles` puts the styles back; the frame stays hidden via
`WM_NCCALCSIZE`.

Dragging is Qt-side via `startSystemMove` rather than answering `HTCAPTION`. Windows
routes caption presses as non-client, so Qt would never see them and every button in the
title bar would stop working.

## Deferred checks

- [ ] **Mixed-DPI drag.** Both displays here are ~96 DPI, so the condition is
  unreproducible. Set one to 150% and drag the window across the boundary. Lower risk again
  now: the terminal is a plain `QWidget` that scales with the window, where a foreign window
  hosted at a different DPI was the thing most likely to break docking.
- [ ] **Live-in-it use.** Rendering quality and input feel are what a probe cannot judge:
  `Ctrl+C` a long command, run `vim` and `less`, paste, scroll back, select text. This
  matters more than it did — with a hand-written emulator it is not a formality but the
  actual test, and it is the check most likely to find something.
- [ ] **Frameless drag and Aero snap.** The probe settles hit-testing, not whether
  dragging by the title bar still snaps.

## Layout

```
src/asomedeck/
  __main__.py      entry point, smoke mode
  app.py           QApplication, logging, crash reporting
  db.py            SQLite: decks, layouts, app_meta
  board.py         the claims board, and what a pane tells the shell inside it
  git_status.py    branch and diff polled, branches asked for: via git rather than
                   any terminal
  git_clone.py     clone a repository: where to, whether it can, and clearing up after
  shells.py        what to run in a pane
  platform_.py     which OS, and where per-user files go: the only sys.platform read
  winapi.py        the only Win32: frameless window messages, pseudo-console calls
  term/conpty.py   picks a pty backend and exports ConptySession
  term/ptysession.py  the session and its reader thread, on either backend
  term/unixpty.py  a shell on a Unix pty: the only fcntl/termios/pty
  term/windowspty.py  a shell on a pseudo-console, through winapi.py
  term/emulator.py VT bytes -> a screen: pyte, plus the scrollback and alternate
                   screen it does not provide
  term/keys.py     key and mouse events -> the bytes a shell expects
  files.py         listing, encodings, newlines: the file round trip, headless
  ui/term_view.py  the terminal widget: paint, input, selection, scrollback
  ui/term_pane.py  one terminal: a view and a pty wired together
  ui/file_tree.py  the deck's file tree, lazy, annotated from git
  ui/editor.py     one file as a pane body: gutter, dirty state, Ctrl+S
  ui/highlight.py  syntax colouring, one alternation per language
  ui/clone_dialog.py  the clone form, and the one clone it runs
  ui/              theme, chrome, delegates, pane tree, frameless
assets/fonts/      drop Roboto and Roboto Mono here; see the README there
assets/icon/       source artwork, plus the generated .ico and .png
packaging/         PyInstaller spec and the entry point it freezes
tools/make_icon.py builds the multi-size .ico from the artwork
tools/probe_*.py   end-to-end gates: assert arrival, not just correctness
```
