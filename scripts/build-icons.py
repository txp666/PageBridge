#!/usr/bin/env python3
"""Convert the versioned logo into macOS icon representations; no generation API."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets/PageBridge-icon.png"
OUTPUT = ROOT / "assets/PageBridge.icns"


def main():
    if not SOURCE.is_file():
        raise SystemExit("Missing assets/PageBridge-icon.png")
    with tempfile.TemporaryDirectory(prefix="pagebridge-icons-") as temporary:
        iconset = Path(temporary) / "PageBridge.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                pixels = size * scale
                suffix = "@2x" if scale == 2 else ""
                target = iconset / f"icon_{size}x{size}{suffix}.png"
                subprocess.run(["sips", "-z", str(pixels), str(pixels), str(SOURCE),
                                "--out", str(target)], check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUTPUT)], check=True)
    print(OUTPUT)


if __name__ == "__main__":
    main()
