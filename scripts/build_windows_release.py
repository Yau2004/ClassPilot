"""Build the frontend and a reproducible PyInstaller onedir Windows app."""

from __future__ import annotations

import argparse
import importlib.metadata
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
SPEC = ROOT / "packaging" / "windows" / "classpilot.spec"
EXPECTED_PYINSTALLER = "6.22.3"


def verify_locked_packages(lock_path: Path) -> None:
    for raw_line in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)", line)
        if not match:
            raise RuntimeError(f"Unsupported lock entry in {lock_path.name}.")
        package, expected = match.groups()
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as error:
            raise RuntimeError(
                f"Locked package {package} is missing from the build environment."
            ) from error
        if actual != expected:
            raise RuntimeError(
                f"Locked package {package} has version {actual}; expected {expected}."
            )


def run_frontend_build() -> None:
    if os.name == "nt":
        command = ["cmd.exe", "/d", "/c", "npm run build"]
    else:
        command = ["npm", "run", "build"]
    subprocess.run(command, cwd=FRONTEND, check=True)


def write_build_report(output: Path) -> None:
    app_dir = output / "ClassPilot"
    executable = app_dir / "ClassPilot.exe"
    manifest = json.loads((app_dir / "_internal" / "release_manifest.json").read_text(encoding="utf-8"))
    app_files = [path for path in app_dir.rglob("*") if path.is_file()]
    report = {
        "build_id": manifest["build_id"],
        "architecture": "windows-x64",
        "python": platform.python_version(),
        "pyinstaller": importlib.metadata.version("pyinstaller"),
        "application": "ClassPilot/ClassPilot.exe",
        "application_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "application_bytes": sum(path.stat().st_size for path in app_files),
        "setup": None,
    }
    (output / "build-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-frontend-build", action="store_true")
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (3, 12):
        parser.error("Build with Python 3.12 from the steam-classroom environment.")
    if os.name == "nt" and platform.machine().casefold() not in {"amd64", "x86_64"}:
        parser.error("Build Windows x64 on a 64-bit AMD64 Python environment.")
    try:
        verify_locked_packages(ROOT / "requirements-packaging-lock.txt")
        verify_locked_packages(ROOT / "backend" / "requirements-runtime-lock.txt")
    except RuntimeError as error:
        parser.error(str(error))
    if importlib.metadata.version("pyinstaller") != EXPECTED_PYINSTALLER:
        parser.error(f"Use PyInstaller {EXPECTED_PYINSTALLER} from the packaging lock.")

    if not args.skip_frontend_build:
        run_frontend_build()
    if not (FRONTEND / "dist" / "index.html").is_file():
        parser.error("The frontend production build is missing.")

    output = ROOT / "dist" / "windows-user-zip"
    work = ROOT / "build" / "pyinstaller-user-zip"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(output),
            "--workpath",
            str(work),
            str(SPEC),
        ],
        cwd=ROOT,
        check=True,
    )
    write_build_report(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
