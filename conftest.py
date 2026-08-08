"""Make the source tree importable from a clean checkout without install."""

import sys
from pathlib import Path

SOFTWARE = Path(__file__).resolve().parent / "software"
if str(SOFTWARE) not in sys.path:
    sys.path.insert(0, str(SOFTWARE))
