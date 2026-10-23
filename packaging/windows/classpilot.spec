# PyInstaller onedir Windows single-user release prototype; build with scripts/build_windows_release.py.
from pathlib import Path
import json
import sys

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH).resolve().parents[1]
BACKEND = ROOT / "backend"
ALEMBIC = BACKEND / "alembic"

datas = [
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    (str(BACKEND / "alembic.ini"), "backend"),
    (str(ROOT / "packaging" / "windows" / "release_manifest.json"), "."),
    (str(ROOT / "packaging" / "windows" / "postgresql_payload_spec.json"), "."),
]
datas.extend(
    (str(path), str(Path("backend/alembic") / path.relative_to(ALEMBIC).parent))
    for path in ALEMBIC.rglob("*.py")
    if "__pycache__" not in path.parts
)

release_manifest = json.loads(
    (ROOT / "packaging" / "windows" / "release_manifest.json").read_text(encoding="utf-8")
)
if release_manifest.get("postgresql_payload_mode") != "user-selected-zip":
    raise RuntimeError("The release must import a user-selected PostgreSQL ZIP.")

hiddenimports = []
for package in (
    "app",
    "alembic",
    "uvicorn",
    "sqlalchemy.dialects.postgresql",
    "psycopg",
):
    hiddenimports.extend(
        collect_submodules(
            package,
            filter=lambda name: not name.startswith("alembic.testing"),
        )
    )

conda_library_bin = Path(sys.prefix) / "Library" / "bin"
binaries = [
    (str(conda_library_bin / name), ".")
    for name in ("ffi.dll", "tcl86t.dll", "tk86t.dll")
    if (conda_library_bin / name).is_file()
]

a = Analysis(
    [str(ROOT / "scripts" / "classpilot_release.py")],
    pathex=[str(BACKEND)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "httpx",
        "httpx2",
        "tests",
        "sqlite3",
        "sqlalchemy.dialects.sqlite",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ClassPilot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ClassPilot",
)
