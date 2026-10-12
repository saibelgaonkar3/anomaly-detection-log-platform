"""Make `core` / `components` importable when running pytest from anywhere."""

import sys
from pathlib import Path

_FRONTEND_ROOT = Path(__file__).resolve().parent
if str(_FRONTEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_FRONTEND_ROOT))
