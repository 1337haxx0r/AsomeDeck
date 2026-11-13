# AsomeDeck

A multi-terminal deck: your terminals tiled in one window, grouped by project.

Each project is a **deck** with its own grid of terminals. Switch decks and the whole
arrangement switches with you, with nothing restarted, and the layout comes back the next
time you open the app.

It also works well for running several AI coding agents in one project, side by side. Panes
can be named, each one knows its own number, and a plain-text board in the project folder
lets the agents claim what they are working on, so two of them do not edit the same file
at once. See [Several agents in one project](#several-agents-in-one-project).

<!-- screenshot: docs/screenshot.png -->

Runs on Windows and macOS.

## Download

**Windows:** `AsomeDeck-<version>.exe` from [Releases](https://github.com/1337haxx0r/AsomeDeck/releases).
It is a single file with no installer. It is not code-signed, so Windows SmartScreen may warn
you the first time you run it.

**macOS:** there is no download yet. [Build the app](#build) yourself.

## Run from source

You need Python 3.13.

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

## What it does

- **Decks.** A deck is usually a folder (**File → Add deck…**). An empty deck, with no
  folder, is for ssh sessions and scratch shells (**File → New empty deck…**). **File →
  Clone repository…** clones a repository and opens it as a new deck.
- **Splits, zoom and hide.** Split a pane right or down. Zoom one pane to fill the grid and
  back. Hide a pane while its shell keeps running, and click its tab to bring it back.
- **Shells.** On Windows: Git Bash, PowerShell 7, PowerShell or cmd, whichever are
  installed. On macOS: your `$SHELL`. Each deck has its own choice.
- **At a glance.** A pane is titled by the program running in it. Dots on panes and decks
  show what is running, turn green when an agent finishes a turn, and turn amber when a
  program rings the bell to ask for you. The bell works with any program. Finished turns
  are read from Claude Code's title for now.
- **Files and a small editor.** The sidebar's **Files** mode shows the deck's tree, marked
  with what git says about each file. Click a file to open it beside the terminals. The
  editor has syntax colouring, undo and `Ctrl+S`, and reloads when the file changes on
  disk. It is not an IDE.
- **Git.** The deck header shows the branch and how many files have changed. Clicking the
  branch lists the local branches and copies the one you pick. It never checks anything
  out.
- **Command palette** (`Ctrl+Shift+K`) for commands, decks, terminals and files. Dark and
  light themes.

Running terminals are not saved. A terminal ends when the app does, and the next start opens
fresh shells in the same layout, in the same folders.

## Keyboard

On macOS, `Ctrl` here means `Cmd`.

| | |
|---|---|
| `Ctrl+Shift+T` | new terminal |
| `Ctrl+Shift+D` / `Ctrl+Shift+E` | split right / split down |
| `Ctrl+Shift+W` | close the pane |
| `Ctrl+Alt+Z` | zoom the pane to fill the grid, and back |
| `Ctrl+Alt+←` / `Ctrl+Alt+→` | previous / next deck |
| `Ctrl+Shift+B` / `Ctrl+Alt+B` | collapse the decks panel / the session panel |
| `Ctrl+Shift+K` | command palette |
| `Ctrl+Shift+C` / `Ctrl+Shift+V` | copy / paste in a terminal (macOS: `Cmd+C` / `Cmd+V`) |
| `F2` | rename the selected deck |

Right-click a terminal, a pane's header or a deck for everything else.

## Settings

`config.json` holds the theme and any shortcuts you have changed. Shortcuts are set by
command id:

```json
{
  "theme": "light",
  "keys": { "pane.zoom": "Ctrl+Alt+M" }
}
```

| | Windows | macOS |
|---|---|---|
| `config.json`, and `state.db` (decks and layouts) | `%APPDATA%\AsomeDeck\` | `~/Library/Application Support/AsomeDeck/` |
| Log | `%LOCALAPPDATA%\AsomeDeck\asomedeck.log` | `~/Library/Logs/AsomeDeck/` |

Set `ASOMEDECK_GIT_ROOT` before launching if Git is somewhere other than
`C:\Program Files\Git`.

## Several agents in one project

Panes over one folder share one working tree. Two agents editing the same file at once is a
race nothing on screen will show you. AsomeDeck gives them somewhere to coordinate: plain
files in a `.asomedeck/` folder in the project, which git ignores.

- **`board.md`** is a claims board. An agent reads it before it starts, adds a line for what
  it is taking, and deletes the line when it is done:

  ```
  - pane 3 (designer) -- src/app/view.py -- reflow on resize
  ```

- **`panes.md`** lists the terminals running over the folder right now: number, name and
  deck. AsomeDeck keeps it current, and agents only read it.
- **Pane names.** Right-click a pane's header and choose **Name pane…**. Then you can tell
  an agent to "check with designer", and it looks the name up in `panes.md`.
- **Environment.** Every shell starts with `ASOMEDECK_PANE_ID` (its pane number),
  `ASOMEDECK_PROJECT` (its deck) and `ASOMEDECK_BOARD` (the board's full path).

**One step is yours.** Agents find the board because `AGENTS.md` tells them to, and
AsomeDeck never edits your `AGENTS.md`. Paste the paragraph it leaves in
`.asomedeck/agents-md-snippet.md` into it. Codex and most other agents read `AGENTS.md`.
Claude Code reads `CLAUDE.md`, so give it one holding the line `@AGENTS.md`.

Nothing enforces a claim. AsomeDeck does not start, direct or message your agents: the board
is an agreement with a place to live, and it works with any agent that reads `AGENTS.md`.

## Build

**Windows:** one exe, `dist\AsomeDeck-<version>.exe`.

```powershell
.venv\Scripts\python.exe -m pip install -e ".[build]"
.venv\Scripts\python.exe -m PyInstaller packaging\asomedeck.spec --noconfirm
```

**macOS:** an arm64 app, `dist/AsomeDeck-<version>.app`, signed ad hoc unless you give it a
Developer ID.

```bash
.venv/bin/python -m pip install -e ".[build]"
bash packaging/build_mac.sh
```

Signing, notarization and the details of both builds are in the
[design notes](docs/design-notes.md#build-a-macos-app).

## Development

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
Get-ChildItem tests\test_*.py | ForEach-Object { .venv\Scripts\python.exe -m pytest -q $_.FullName }
.venv\Scripts\python.exe -m ruff check src tests tools
```

```bash
for f in tests/test_*.py; do QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q "$f"; done
.venv/bin/python -m ruff check src tests tools
```

- **Offscreen**, so no windows open. Without it, a run has been seen to hang.
- **One file at a time.** That takes a few minutes. The whole suite in one process takes
  close to an hour, and crashes on exit after printing its results.
- **Four tests fail offscreen on Windows**, in `test_chrome.py`, `test_term_render.py` and
  `test_theme.py`, because offscreen there has no fonts. They pass with
  `QT_QPA_PLATFORM=windows`.
- `python -m asomedeck --smoke 14` launches, renders and shuts down unattended, against a
  throwaway database. `tools/probe_*.py` are end-to-end checks against real shells.

[`docs/design-notes.md`](docs/design-notes.md) explains why each part is built the way it is.
Its last section maps the source tree.

## Licence

MIT, in [`LICENSE`](LICENSE). The libraries it runs on keep their own licences; see
[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md).
