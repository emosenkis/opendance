# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files


ROOT = Path(SPEC).resolve().parents[1]
model = Path(os.environ["OPENDANCE_BUNDLE_MODEL"])
macos = sys.platform == "darwin"
icon = ROOT / "build" / ("opendance.icns" if macos else "opendance.ico")
if not model.is_file():
    raise SystemExit(f"Missing bundled model: {model}")

datas = collect_data_files("opendance") + [
    (str(model), "models"),
    (str(ROOT / "LICENSE"), "."),
]
binaries = []
hiddenimports = [
    "PySide6.QtQuick",
    "PySide6.QtQuickControls2",
    "PySide6.QtSvg",
]
package_data, package_binaries, package_imports = collect_all("ultralytics")
datas += package_data
binaries += package_binaries
hiddenimports += package_imports
package_data, package_binaries, package_imports = collect_all("torchvision")
datas += package_data
binaries += package_binaries
hiddenimports += package_imports

a = Analysis(
    [str(ROOT / "packaging" / "entrypoint.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hooksconfig={},
    runtime_hooks=[],
    excludes=["IPython", "pytest", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

game = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OpenDance",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(icon) if icon.is_file() else None,
)
extractor = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="opendance-extract",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=str(icon) if icon.is_file() else None,
)
player = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="opendance-player",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(icon) if icon.is_file() else None,
)

coll = COLLECT(
    game,
    extractor,
    player,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="OpenDance",
)

if macos:
    app = BUNDLE(
        coll,
        name="OpenDance.app",
        icon=str(icon),
        bundle_identifier="io.github.emosenkis.opendance",
        version=os.environ.get("OPENDANCE_BUNDLE_VERSION", "0.0.0"),
        info_plist={
            "NSCameraUsageDescription": (
                "OpenDance uses the camera locally to score dance poses. "
                "Camera images are not uploaded or saved."
            ),
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "14.0",
        },
    )
