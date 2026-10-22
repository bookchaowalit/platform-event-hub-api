"""Put the hub's script directories on sys.path, as `python infra/scripts/services/event_hub.py` does."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "infra/scripts/services", ROOT / "infra/tests/support"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
