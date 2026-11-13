"""Entry point for the frozen build.

``src/asomedeck/__main__.py`` cannot be the script PyInstaller freezes. It is written as a
module inside a package and imports its siblings relatively (``from .app import ...``);
PyInstaller runs the script it is given as top-level ``__main__``, which has no parent
package, so every one of those imports fails before the app starts:

    ImportError: attempted relative import with no known parent package

This file exists to be that top-level script and to reach the real one by absolute import,
which is the same thing ``python -m asomedeck`` does through the package machinery.
"""

from __future__ import annotations

import sys

from asomedeck.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
