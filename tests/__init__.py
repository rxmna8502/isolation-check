"""Test suite for isolation-check."""
from pathlib import Path
import sys

# Ensure src/ is on sys.path for direct unittest discovery
src_dir = str(Path(__file__).parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
