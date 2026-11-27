import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

binary_roots = [
    Path(sys.prefix) / "Library" / "bin",
    Path(sys.prefix) / "bin",
    Path(sys.base_prefix) / "Library" / "bin",
    Path(sys.base_prefix) / "bin",
]
binaries = []
for name in ("ffi-8.dll", "libexpat.dll", "liblzma.dll", "libbz2.dll"):
    for root in binary_roots:
        candidate = root / name
        if candidate.is_file():
            binaries.append((str(candidate), "."))
            break

datas = collect_data_files(
    "rapidocr_onnxruntime",
    includes=["**/*.onnx", "**/*.yaml", "**/*.yml", "**/*.txt", "**/*.json"],
)
datas += [("assets/icon.ico", "assets")]
hiddenimports = collect_submodules("rapidocr_onnxruntime")

required_models = {
    "ch_PP-OCRv4_det_infer.onnx",
    "ch_PP-OCRv4_rec_infer.onnx",
    "ch_ppocr_mobile_v2.0_cls_infer.onnx",
}
bundled_models = {source.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                  for source, _ in datas if source.lower().endswith(".onnx")}
missing_models = required_models - bundled_models
if missing_models:
    raise RuntimeError(f"Missing RapidOCR models: {sorted(missing_models)}")

a = Analysis(
    ["nuochat/__main__.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="NuoChat",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon="assets/icon.ico",
)
