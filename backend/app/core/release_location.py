"""Per-user release data-location record, separate from ClassPilot data."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Callable
import shutil


LOCATION_FORMAT = 1


class LocationError(RuntimeError):
    """A safe, non-secret location or locator diagnostic."""


@dataclass(frozen=True)
class DataLocation:
    root: Path
    setup_complete: bool


def default_data_root(local_app_data: Path) -> Path:
    return local_app_data / "ClassPilot"


def install_data_root(executable: Path) -> Path:
    """Return the explicitly supported Data directory inside the app directory."""
    return executable.resolve().parent / "Data"


def locator_path(local_app_data: Path) -> Path:
    # This stable per-user anchor is not inside the selectable data directory.
    return local_app_data / "ClassPilotLauncher" / "location.json"


class ReleaseLocationStore:
    """Store only the selected path and completion bit; never store credentials."""

    def __init__(self, path: Path, *, protect_directory: Callable[[Path], None] | None = None):
        self.path = path
        self.protect_directory = protect_directory or (lambda _: None)

    def load(self) -> DataLocation | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise LocationError("ClassPilot 的資料位置記錄無法讀取；資料未被修改。請重新選取資料位置。") from None
        if (
            not isinstance(value, dict)
            or value.get("format") != LOCATION_FORMAT
            or value.get("product") != "ClassPilot"
            or not isinstance(value.get("data_root"), str)
            or not Path(value["data_root"]).is_absolute()
            or not isinstance(value.get("setup_complete"), bool)
        ):
            raise LocationError("ClassPilot 的資料位置記錄格式不相容；資料未被修改。請重新選取資料位置。")
        return DataLocation(Path(value["data_root"]), value["setup_complete"])

    def save(self, root: Path, *, setup_complete: bool) -> None:
        root = root.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.protect_directory(self.path.parent)
        payload = {
            "format": LOCATION_FORMAT,
            "product": "ClassPilot",
            "data_root": str(root),
            "setup_complete": setup_complete,
        }
        fd, name = tempfile.mkstemp(prefix=".location-", suffix=".tmp", dir=self.path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


def is_local_fixed_drive(path: Path) -> bool:
    if os.name != "nt":
        return True
    import ctypes

    drive = path.resolve(strict=False).drive
    if not drive:
        return False
    return ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\") == 3


def validate_data_root(
    root: Path,
    *,
    install_root: Path,
    windows_root: Path | None = None,
    minimum_free_bytes: int = 1_000_000_000,
    drive_checker: Callable[[Path], bool] = is_local_fixed_drive,
    write_probe: bool = False,
    allow_probe_nonempty: bool = False,
) -> list[str]:
    """Validate a user-selected fixed-drive path without changing its contents."""
    original = root.expanduser().absolute()
    target = original.resolve(strict=False)
    install = install_root.resolve(strict=False)
    windows = (windows_root or Path(os.environ.get("WINDIR", "C:\\Windows"))).resolve(strict=False)
    problems: list[str] = []
    if not target.is_absolute() or not target.drive:
        return [f"資料目錄必須是本機磁碟上的完整路徑：{target}"]
    if not drive_checker(target):
        problems.append(f"目前只支援本機固定磁碟；此位置未通過檢查：{target}")
    if target == Path(target.anchor) or target == windows or windows in target.parents:
        problems.append(f"不能將 Windows 或磁碟根目錄作為 ClassPilot 資料位置：{target}")
    protected_roots = [
        Path(value).resolve(strict=False)
        for value in (
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("ProgramData"),
        )
        if value
    ]
    install_data_exception = target == install / "Data"
    if any(
        target == protected or protected in target.parents
        for protected in protected_roots
        if not (install_data_exception and (install == protected or protected in install.parents))
    ):
        problems.append(f"不能將 Windows 受保護的程式／共用資料位置作為使用者資料目錄：{target}")
    sync_roots = [
        Path(value).resolve(strict=False)
        for value in (
            os.environ.get("OneDrive"),
            os.environ.get("OneDriveCommercial"),
            os.environ.get("OneDriveConsumer"),
            os.environ.get("Dropbox"),
            os.environ.get("GoogleDrive"),
        )
        if value
    ]
    known_sync_folder_names = {"onedrive", "dropbox", "google drive", "googledrive"}
    if any(target == sync_root or sync_root in target.parents for sync_root in sync_roots) or any(
        part.casefold() in known_sync_folder_names for part in target.parts
    ):
        problems.append(f"不支援已知同步資料夾內的 ClassPilot 資料位置：{target}")
    if not install_data_exception and (
        target == install or target in install.parents or install in target.parents
    ):
        problems.append(f"資料目錄不可與程式安裝位置重疊：{target}")
    for parent in (original, *original.parents):
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            problems.append(f"不支援連結或 junction 路徑，避免影響路徑外檔案：{parent}")
            break
    if problems:
        return problems
    probe_base = target
    while not probe_base.exists() and probe_base.parent != probe_base:
        probe_base = probe_base.parent
    if not probe_base.is_dir():
        return [f"資料目錄路徑中有檔案阻擋建立資料夾：{probe_base}"]
    try:
        free_bytes = shutil.disk_usage(probe_base).free
    except OSError:
        return [f"無法檢查資料磁碟可用空間：{probe_base}"]
    required = minimum_free_bytes if not target.exists() or not any(target.iterdir()) else 100_000_000
    if free_bytes < required:
        problems.append(f"資料磁碟空間不足：{probe_base}，至少需要 {required // 1_000_000} MB 可用空間。")
    if target.exists() and not target.is_dir():
        problems.append(f"資料位置已被同名檔案佔用：{target}")
    if write_probe and not problems:
        try:
            target.mkdir(parents=True, exist_ok=True)
            if not any(target.iterdir()) or allow_probe_nonempty:
                fd, name = tempfile.mkstemp(prefix=".classpilot-write-check-", dir=target)
                try:
                    os.close(fd)
                finally:
                    Path(name).unlink(missing_ok=True)
        except OSError:
            problems.append(f"目前 Windows 使用者無法在資料位置建立或移除權限檢查檔：{target}。請檢查目錄權限或改選位置。")
    return problems


def require_completed_location(store: ReleaseLocationStore) -> DataLocation:
    """Resolve the launcher anchor without falling back to the default root."""
    location = store.load()
    if location is None:
        raise LocationError("尚未記錄 ClassPilot 資料位置；請先完成首次設定。")
    if not location.setup_complete:
        raise LocationError(
            f"資料位置已記錄但首次設定未完成：{location.root}。請先診斷或安全恢復。"
        )
    if not location.root.is_dir():
        raise LocationError(
            f"已記錄的資料位置目前不存在或磁碟離線：{location.root}。"
            "請重新連接磁碟或重新選取有效資料；ClassPilot 不會改用預設位置建立空白資料。"
        )
    return location
