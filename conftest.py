"""Pytest config: make the repo root importable (for adapters/ and src/)."""

import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
