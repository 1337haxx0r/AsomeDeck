# Third-party notices

AsomeDeck's own code is under the MIT licence in [`LICENSE`](LICENSE). It runs on the
software below, each under its own licence. Only ConPTY is checked into this repository;
the rest is installed by pip, and travels inside the Windows executable and the macOS app.

| Component | Version in 0.10.0 | Licence | Source |
|---|---|---|---|
| Python | 3.13.15 | PSF License | <https://www.python.org> |
| Qt for Python (PySide6-Essentials, shiboken6), and the Qt libraries it ships | 6.11.2 | LGPL-3.0 | <https://download.qt.io/official_releases/QtForPython/>, <https://download.qt.io/official_releases/qt/> |
| pyte | 0.8.2 | LGPL-3.0 | <https://github.com/selectel/pyte> |
| wcwidth | 0.8.3 | MIT | <https://github.com/jquast/wcwidth> |
| ConPTY (`conpty.dll`, `OpenConsole.exe`) | 1.24.260512001 | MIT, in [`assets/conpty/LICENSE`](assets/conpty/LICENSE) | <https://github.com/microsoft/terminal> |
| PyInstaller bootloader (executable only) | 6.22.2 | GPL-2.0 with the bootloader exception | <https://github.com/pyinstaller/pyinstaller> |

## The LGPL components

Qt, Qt for Python and pyte are used unmodified, as libraries AsomeDeck imports. The LGPL lets
you swap them for versions of your own, and this repository is how: running from source uses
whatever versions are installed, and the executable is rebuilt from any compatible ones with
the two commands under *Build a Windows executable* in the README. Their source is at the
links above, and the licence texts are at <https://www.gnu.org/licenses/lgpl-3.0.html> and
<https://www.gnu.org/licenses/gpl-3.0.html>.
