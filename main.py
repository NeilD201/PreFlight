"""Start the checker with python3 main.py from this folder."""

import sys
from pathlib import Path

# The package lives in this folder; alias it as pcb_checker whatever the folder is named.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
