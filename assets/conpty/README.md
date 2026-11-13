# The pseudo-console host

`conpty.dll` and `OpenConsole.exe` are the program that actually hosts each shell. They are
Microsoft's, built from [microsoft/terminal](https://github.com/microsoft/terminal) and
released under the MIT licence (`LICENSE` here), and they are checked in unmodified.

| | |
|---|---|
| Package | NuGet [`Microsoft.Windows.Console.ConPTY`](https://www.nuget.org/packages/Microsoft.Windows.Console.ConPTY) `1.24.260512001`, the x64 runtime |
| `conpty.dll` | `c46dcd04f52b97f6a8cf53e8f547c85a821660bed18de2b3344afcd4a8389ad6` |
| `OpenConsole.exe` | `47828c3fe080212f69dfdb39ab3673170fcc7445924c76fe003cefd18247dd5d` |

Both carry valid Microsoft Authenticode signatures.

## Why they are here at all

Windows has a host of its own, and `term/windowspty.py` falls back to it if this folder is
missing. It is not used by choice because on Windows 11 22H2 it re-renders what a program
writes rather than passing it through, and some of that does not survive: `vim` gets no
alternate screen, and a native program's request for mouse reporting never reaches the
terminal. These two are the host `pywinpty` carried inside its wheel, so keeping them is what
kept behaviour the same when it was removed.

## Updating

Take both files from one package version: `build/native/runtimes/x64/OpenConsole.exe` and
`runtimes/win-x64/native/conpty.dll`. Update the table, then run `tests/test_windowspty.py` and
open `vim`, `less` and Claude Code in a pane, since what can change is how they render.
