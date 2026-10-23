"""Check or compile the per-user Setup.exe source with an approved Inno Setup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging" / "installer" / "ClassPilotSetup.iss"
PAYLOAD = ROOT / "dist" / "windows-user-zip" / "ClassPilot"
EXPECTED_INNO_VERSION = "7.1.0"


def find_iscc() -> Path | None:
    explicit = os.environ.get("INNO_SETUP_COMPILER")
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate if candidate.is_file() else None
    found = shutil.which("ISCC.exe") or shutil.which("iscc")
    if found:
        return Path(found)
    candidates = [
        Path.home() / "AppData/Local/Programs/Inno Setup 7/ISCC.exe",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Inno Setup 7" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Inno Setup 7" / "ISCC.exe",
    ]
    return next((path for path in candidates if path.is_file()), None)


def inno_compiler_version(path: Path) -> str | None:
    """Read the version reported by Inno Setup's compiler itself."""
    if os.name != "nt":
        return None
    try:
        result = subprocess.run(
            [str(path), "--version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def validate_source() -> None:
    if not SCRIPT.is_file():
        raise SystemExit("Installer source packaging/installer/ClassPilotSetup.iss is missing.")
    if not (PAYLOAD / "ClassPilot.exe").is_file():
        raise SystemExit("Build the Windows onedir application first with scripts/build_windows_release.py.")
    contents = SCRIPT.read_text(encoding="utf-8")
    required = (
        "PrivilegesRequired=lowest",
        "ArchitecturesAllowed=x64os",
        "ArchitecturesInstallIn64BitMode=x64os",
        "{localappdata}\\Programs\\ClassPilot",
        "DisableDirPage=no",
        "ExtraDiskSpaceRequired=94_371_840",
        "SelectDirDesc=",
        "GetSpaceOnDisk(DriveRoot, True, FreeMB, TotalMB)",
        "ExtractFileDir(ProbeBase)",
        "CreateDir(ProbeDir)",
        "目標資料夾已有檔案，為避免覆寫而停止",
        "預設資料位置含中文、日文或韓文",
        "RELEASE_PATH_LIMITATION.txt",
        "ClassPilot.exe",
        'Name: "{app}\\Data"; Flags: uninsneveruninstall',
        'Excludes: "ClassPilot.exe,Data\\*"',
        "首次資料設定必須在一般使用者權限下進行",
        "CloseApplications=no",
        "RestartApplications=no",
        "UninstalledAll=ClassPilot 已成功解除安裝",
        "UninstalledMost=ClassPilot 解除安裝完成",
    )
    missing = [item for item in required if item not in contents]
    if missing:
        raise SystemExit("Installer source is missing required safeguards or payload entries.")
    forbidden = ("postgresql-18.6-5-windows-x64-binaries.zip", ".env", "UninstallDelete")
    if any(item.casefold() in contents.casefold() for item in forbidden):
        raise SystemExit("Installer source must not embed PostgreSQL ZIP, development .env, or data deletion.")
    if (PAYLOAD / "Data").exists():
        raise SystemExit("The release payload must not contain runtime user data under ClassPilot/Data.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    validate_source()
    if args.check_only:
        print("Installer source and onedir payload checks passed; no Setup.exe was built.")
        return 0
    compiler = find_iscc()
    if compiler is None:
        raise SystemExit(
            f"Inno Setup {EXPECTED_INNO_VERSION} x64 compiler is not installed. No tool was installed. "
            "Request approval to install that exact version before compiling ClassPilotSetup.exe."
        )
    actual_version = inno_compiler_version(compiler)
    if actual_version != EXPECTED_INNO_VERSION:
        raise SystemExit(
            f"Expected Inno Setup {EXPECTED_INNO_VERSION}; found "
            f"{actual_version or 'an unreadable compiler version'}. No installer was built."
        )
    subprocess.run([str(compiler), str(SCRIPT)], cwd=ROOT, check=True)
    report_path = ROOT / "dist" / "windows-user-zip" / "build-report.json"
    setup_path = ROOT / "dist" / "installer" / "ClassPilotSetup.exe"
    if report_path.is_file() and setup_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["setup"] = {
            "artifact": "../installer/ClassPilotSetup.exe",
            "sha256": hashlib.sha256(setup_path.read_bytes()).hexdigest(),
            "bytes": setup_path.stat().st_size,
        }
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print("Built dist/installer/ClassPilotSetup.exe. The installer was not executed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
