#!/usr/bin/env python3
"""cock-cage entry point for hooks and the `cockcage` command. Standard library only, Python 3.9+."""

import os
import sys

if sys.version_info < (3, 9):
    sys.stderr.write("cock-cage needs Python 3.9 or newer; skipping.\n")
    sys.exit(0)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

from cockcage.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
