"""What to run in a pane.

The tier system is gone. Spec §5 sorted terminals into A (own-window, dockable), B
(console host, dockable with letterboxing) and C (hands off and exits, not dockable at
all) -- and every one of those categories was about *how the terminal manages its
window*. A pseudo-console has no window, so the distinction evaporates: cmd, PowerShell
and bash are now the same kind of thing, an argv.

That also settles what §6.1 called critical. Forcing ``conhost.exe`` in front of a Tier B
launcher existed to stop Windows 11 handing the process to Windows Terminal, leaving our
PID owning no window. We no longer want a window, so there is nothing to hand off.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field

from . import platform_

GIT_ROOT = os.environ.get("ASOMEDECK_GIT_ROOT", r"C:\Program Files\Git")


@dataclass(frozen=True)
class ShellSpec:
    """A program to run on a pty, and how to label it."""

    shell_id: str
    label: str
    argv: tuple[str, ...]
    #: Merged over os.environ at spawn.
    env: dict[str, str] = field(default_factory=dict)

    @property
    def command(self) -> str:
        return os.path.basename(self.argv[0]) if self.argv else ""

    def available(self) -> bool:
        exe = self.argv[0] if self.argv else ""
        return bool(os.path.isfile(exe) or shutil.which(exe))


#: Git Bash. Note this is ``usr/bin/bash.exe`` -- a console-subsystem binary, and the
#: exact one the docking backend had to avoid, because there it produced a conhost window
#: owned by another process. On a pty that is precisely what we want: a plain console
#: program talking over its standard handles.
#:
#: CHERE_INVOKING keeps ``--login`` from cd'ing to ~ and discarding the launch directory.
GIT_BASH = ShellSpec(
    shell_id="git-bash",
    label="Git Bash",
    argv=(os.path.join(GIT_ROOT, "usr", "bin", "bash.exe"), "--login", "-i"),
    env={"MSYSTEM": "MINGW64", "CHERE_INVOKING": "1", "TERM": "xterm-256color"},
)

POWERSHELL = ShellSpec(
    shell_id="powershell",
    label="PowerShell",
    argv=("powershell.exe", "-NoLogo"),
)

PWSH = ShellSpec(
    shell_id="pwsh",
    label="PowerShell 7",
    argv=("pwsh.exe", "-NoLogo"),
)

CMD = ShellSpec(shell_id="cmd", label="cmd", argv=("cmd.exe",))

# --- macOS ---------------------------------------------------------------------------
#
# TERM is set explicitly on all three. Git Bash already needed it on Windows; here it is
# not optional, because a pty we opened ourselves inherits whatever TERM the *app* was
# launched with -- which from Finder is nothing at all, and a shell with no TERM turns off
# colour and every curses program refuses to start.

def _login_shell() -> ShellSpec | None:
    """The shell the user actually logs in with, from ``$SHELL``.

    First in preference order, ahead of the two known paths below, because somebody who
    changed their login shell to fish or to a Homebrew zsh meant it -- and a terminal app
    that opens a different shell than every other terminal on the machine is wrong in a way
    that is annoying to diagnose and trivial to avoid.

    Returns None when SHELL is unset or names something that is not there, which is the
    case in a bare launchd context and in CI.
    """
    path = os.environ.get("SHELL", "").strip()
    if not path or not os.path.isfile(path):
        return None
    name = os.path.basename(path)
    return ShellSpec(
        shell_id="login-shell",
        label=name,
        argv=(path, "-l"),
        env={"TERM": "xterm-256color"},
    )


ZSH = ShellSpec(
    shell_id="zsh",
    label="zsh",
    argv=("/bin/zsh", "-l"),
    env={"TERM": "xterm-256color"},
)

BASH = ShellSpec(
    shell_id="bash",
    label="bash",
    argv=("/bin/bash", "-l"),
    env={"TERM": "xterm-256color"},
)

SH = ShellSpec(
    shell_id="sh",
    label="sh",
    argv=("/bin/sh",),
    env={"TERM": "xterm-256color"},
)

#: Preference order, best first. ``default()`` reads it straight, so this tuple is where
#: "which shell does a fresh pane get" is actually decided.
#:
#: Both platforms' specs stay defined whatever we are running on -- they are dataclasses,
#: not probes, and ``by_id`` has to be able to name a shell from a layout saved on the other
#: machine. Only which ones are *offered* is platform-dependent.
if platform_.IS_WINDOWS:
    ALL: tuple[ShellSpec, ...] = (GIT_BASH, PWSH, POWERSHELL, CMD)
else:
    _LOGIN = _login_shell()
    # Dropped when $SHELL is /bin/zsh anyway, or the picker lists the same shell twice
    # under two names and rebinding one of them appears to do nothing.
    _extra = () if _LOGIN is None or _LOGIN.argv[0] == ZSH.argv[0] else (_LOGIN,)
    ALL = (*_extra, ZSH, BASH, SH)

#: The shell to fall back on when nothing in ALL is installed. ``cmd`` on Windows and
#: ``/bin/sh`` on macOS for the same reason: it is the one the platform guarantees, and a
#: terminal app that cannot name a shell to run has nothing to offer.
FLOOR: ShellSpec = CMD if platform_.IS_WINDOWS else SH


#: Every spec either platform defines, for name lookups. ``resolve`` is what decides
#: whether a named shell can actually be launched here.
_KNOWN: tuple[ShellSpec, ...] = (GIT_BASH, PWSH, POWERSHELL, CMD, ZSH, BASH, SH)


def available() -> list[ShellSpec]:
    """The shells actually installed. Everything here is a pty client, so this is the
    whole of first-run detection -- no registry probing, no tier assignment."""
    return [s for s in ALL if s.available()]


def default() -> ShellSpec:
    """The best shell that is actually installed.

    This was the constant ``DEFAULT = GIT_BASH``, which made Git a hard requirement
    nothing declared. ``available()`` had known better since the beginning and no caller
    consulted it: on a machine without Git every pane spawned a path that was not there,
    raised ``FileNotFoundError`` inside the pty, painted red and closed itself -- with no
    way back, because the shell picker was never wired up either.

    ``ALL`` is in preference order, so Git Bash still wins wherever it exists on Windows and
    ``$SHELL`` wins on macOS. ``FLOOR`` is the answer rather than an error: every platform
    guarantees one shell, and a terminal app that cannot name a shell to run has nothing to
    offer.
    """
    return next(iter(available()), FLOOR)


def by_id(shell_id: str) -> ShellSpec:
    """Look up a shell by id. Unknown ids fall back rather than failing a launch.

    A name lookup, used for labelling as much as for launching -- it answers *what is
    this pane running*, so it does not second-guess whether that shell is present.
    ``resolve()`` is the one that does.
    """
    return next((s for s in _KNOWN if s.shell_id == shell_id), default())


def resolve(shell_id: str) -> ShellSpec:
    """The shell to actually spawn for ``shell_id``, substituting if it is missing.

    Separate from ``by_id`` because a saved layout outlives the machine it was written
    on. A deck pinned to Git Bash, opened after Git is uninstalled, would otherwise
    spawn a path that no longer exists and get red text instead of a terminal. Callers
    log the substitution: silently running something other than what the pane says it is
    running would be worse than the failure it replaces.
    """
    spec = by_id(shell_id)
    return spec if spec.available() else default()
