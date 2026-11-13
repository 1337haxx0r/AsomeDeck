"""Generate the Windows version resource embedded in the built exe.

Right-clicking the exe and reading Properties -> Details is the only way to tell one build
from another once it has been copied somewhere and renamed, and without this the fields
are blank. Generated from ``asomedeck.__version__`` rather than written out, for the same
reason pyproject reads it: a hand-maintained copy is one that eventually disagrees.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from asomedeck import __version__  # noqa: E402

TEMPLATE = """\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, 0),
    prodvers=({major}, {minor}, {patch}, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'AsomeDeck'),
        StringStruct('FileDescription', 'AsomeDeck - your terminals in one window'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'AsomeDeck'),
        StringStruct('OriginalFilename', 'AsomeDeck-{version}.exe'),
        StringStruct('ProductName', 'AsomeDeck'),
        StringStruct('ProductVersion', '{version}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def write(path: pathlib.Path | None = None) -> pathlib.Path:
    major, minor, patch = (int(part) for part in __version__.split("."))
    target = path or pathlib.Path(__file__).resolve().parent / "version_info.txt"
    target.write_text(
        TEMPLATE.format(major=major, minor=minor, patch=patch, version=__version__),
        encoding="utf-8",
    )
    return target


if __name__ == "__main__":
    print(write())
