# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path(SPECPATH)
source_dir = project_root / "src"

a = Analysis(
    [str(source_dir / "main.py")],
    pathex=[str(source_dir)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="djvu2pdf",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="djvu2pdf",
)
app = BUNDLE(
    coll,
    name="djvu2pdf.app",
    icon=None,
    bundle_identifier="com.djvuconverter.djvu2pdf",
    info_plist={
        "NSHighResolutionCapable": True,
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "DjVu Document",
                "CFBundleTypeRole": "Viewer",
                "LSItemContentTypes": ["com.djvuconverter.djvu"],
                "CFBundleTypeExtensions": ["djvu", "djv"],
            }
        ],
        "UTExportedTypeDeclarations": [
            {
                "UTTypeIdentifier": "com.djvuconverter.djvu",
                "UTTypeDescription": "DjVu document",
                "UTTypeConformsTo": ["public.data"],
                "UTTypeTagSpecification": {
                    "public.filename-extension": ["djvu", "djv"],
                    "public.mime-type": ["image/vnd.djvu", "image/x-djvu"],
                },
            }
        ],
    },
)
