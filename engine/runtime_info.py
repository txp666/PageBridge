"""Build metadata; contains paths and versions, never service credentials."""
import importlib.metadata
import json
import platform
from pathlib import Path
import struct
import sys
import sysconfig

print(json.dumps({
    "python": str(Path(sys.executable).resolve()),
    "pythonVersion": sys.version.split()[0],
    "standaloneBase": sys.base_prefix,
    "sitePackages": sysconfig.get_path("purelib"),
    # On ARM Windows, platform.machine() describes the OS even when this is an
    # x64 interpreter. The wheel platform identifies the process DLL ABI.
    "architecture": "AMD64" if sysconfig.get_platform() == "win-amd64" else platform.machine(),
    "nativeOSArchitecture": platform.machine(),
    "pythonPlatform": sysconfig.get_platform(),
    "processBits": struct.calcsize("P") * 8,
    "versions": {name: importlib.metadata.version(name) for name in ("pdf2zh-next", "babeldoc", "PyMuPDF", "onnxruntime")},
}, indent=2))
