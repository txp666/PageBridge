"""Framework-independent PageBridge Windows backend."""

from pathlib import Path
import sys

# The source checkout and installed bridge share the same sibling engine.
sys.path.append(str(Path(__file__).resolve().parents[2] / "engine"))
