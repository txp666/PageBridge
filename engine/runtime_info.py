"""Build metadata; contains paths and versions, never service credentials."""
import importlib.metadata
import json
from pathlib import Path
import sys
import sysconfig

print(json.dumps({
    "python": str(Path(sys.executable).resolve()),
    "pythonVersion": sys.version.split()[0],
    "standaloneBase": sys.base_prefix,
    "sitePackages": sysconfig.get_path("purelib"),
    "architecture": __import__("platform").machine(),
    "versions": {name: importlib.metadata.version(name) for name in ("pdf2zh-next", "babeldoc", "PyMuPDF", "onnxruntime")},
}, indent=2))
