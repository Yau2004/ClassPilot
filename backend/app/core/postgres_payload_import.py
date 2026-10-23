"""Validate and safely import a user-selected PostgreSQL Windows ZIP."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import tempfile
import zipfile
from collections.abc import Callable, Mapping


class PayloadImportError(ValueError):
    """Safe, user-facing PostgreSQL payload import failure."""


@dataclass(frozen=True)
class PayloadSpec:
    version: str
    package_revision: str
    architecture: str
    filename: str
    sha256: str
    official_download_page: str
    sha256_source: str
    required_runtime_files: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "PayloadSpec":
        try:
            runtime_files = value["required_runtime_files"]
            if not isinstance(runtime_files, list) or not all(
                isinstance(name, str) for name in runtime_files
            ):
                raise TypeError
            return cls(
                version=str(value["version"]),
                package_revision=str(value["package_revision"]),
                architecture=str(value["architecture"]),
                filename=str(value["filename"]),
                sha256=str(value["sha256"]).lower(),
                official_download_page=str(value["official_download_page"]),
                sha256_source=str(value["sha256_source"]),
                required_runtime_files=tuple(runtime_files),
            )
        except (KeyError, TypeError, ValueError):
            raise PayloadImportError("ClassPilot 的 PostgreSQL 支援規格無效。") from None


@dataclass(frozen=True)
class PayloadImportResult:
    payload_root: Path
    version: str
    package_revision: str
    architecture: str
    sha256: str
    included_files: int
    runtime_files: tuple[str, ...]


_REQUIRED_FILES = (
    "bin/postgres.exe",
    "bin/initdb.exe",
    "bin/pg_ctl.exe",
    "server_license.txt",
    "commandlinetools_3rd_party_licenses.txt",
)
_REQUIRED_ARCHIVE_FILES = tuple(f"pgsql/{name}" for name in _REQUIRED_FILES)
_MAX_SELECTED_FILES = 6000
_MAX_SELECTED_BYTES = 200 * 1024 * 1024
_MAX_SINGLE_FILE_BYTES = 100 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise PayloadImportError("無法讀取所選 ZIP 檔案。") from None
    return digest.hexdigest()


def _normalized_member_name(name: str) -> tuple[str, ...]:
    if not name or "\x00" in name:
        raise PayloadImportError("ZIP 內含無效檔名；尚未建立資料庫。")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise PayloadImportError("ZIP 內含絕對路徑；尚未建立資料庫。")
    path = PurePosixPath(normalized)
    if ".." in path.parts or ":" in path.parts:
        raise PayloadImportError("ZIP 內含不安全路徑；尚未建立資料庫。")
    parts = tuple(part for part in path.parts if part not in ("", "."))
    if not parts:
        raise PayloadImportError("ZIP 內含無效檔名；尚未建立資料庫。")
    return parts


def _is_selected(parts: tuple[str, ...]) -> bool:
    name = "/".join(parts)
    if name in _REQUIRED_ARCHIVE_FILES:
        return True
    if len(parts) < 2 or parts[0].casefold() != "pgsql":
        return False
    if parts[1].casefold() not in {"bin", "lib", "share"}:
        return False
    if parts[1].casefold() == "bin":
        filename = parts[-1].casefold()
        if filename == "stackbuilder.exe" or filename.startswith("wx"):
            return False
    return True


def _check_runtime(spec: PayloadSpec, runtime_dir: Path | None) -> tuple[str, ...]:
    if not spec.required_runtime_files:
        return ()
    if runtime_dir is None:
        windows_dir = Path(os.environ.get("WINDIR", "C:\\Windows"))
        runtime_dir = windows_dir / "System32"
    missing = [name for name in spec.required_runtime_files if not (runtime_dir / name).is_file()]
    if missing:
        expected = "、".join(missing)
        raise PayloadImportError(
            f"這台電腦缺少 PostgreSQL 所需的 Microsoft Visual C++ runtime 元件：{expected}。"
            "ClassPilot 尚未匯入 payload，也不會自行安裝系統 runtime。"
            "請由使用者或 IT 按 Microsoft 官方 Visual C++ Redistributable x64 指引處理後重試："
            "https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist?view=msvc-170"
        )
    return spec.required_runtime_files


def _pe_machine(path: Path) -> int:
    try:
        with path.open("rb") as stream:
            header = stream.read(64)
            if len(header) < 64 or header[:2] != b"MZ":
                return 0
            pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
            stream.seek(pe_offset)
            signature = stream.read(6)
            if len(signature) != 6 or signature[:4] != b"PE\0\0":
                return 0
            return struct.unpack_from("<H", signature, 4)[0]
    except (OSError, struct.error):
        return 0


def _probe_postgres_version(executable: Path, expected: str) -> None:
    try:
        # Import lazily: the runtime module also consumes PayloadSpec while
        # validating an already imported payload.
        from app.core.postgres_runtime import PrototypeRuntimeError, _probe_executable_version

        _probe_executable_version(executable, expected_version=expected)
    except PrototypeRuntimeError as error:
        raise PayloadImportError(str(error)) from None


def validate_and_import_postgres_zip(
    archive_path: Path,
    payload_root: Path,
    spec: PayloadSpec,
    *,
    runtime_dir: Path | None = None,
    restrict_directory: Callable[[Path], None] | None = None,
    version_probe: Callable[[Path, str], None] = _probe_postgres_version,
) -> PayloadImportResult:
    """Validate exact pinned ZIP, then extract selected server files atomically."""
    archive = archive_path.expanduser().resolve()
    if not archive.is_file() or archive.suffix.casefold() != ".zip":
        raise PayloadImportError("請選取 PostgreSQL binaries ZIP 檔案。")
    digest = _sha256(archive)
    if digest != spec.sha256:
        raise PayloadImportError(
            f"ZIP 完整性檢查未通過。請從官方下載頁取得支援版本 {spec.version} Windows x64；尚未建立資料庫。"
        )

    runtime_files = _check_runtime(spec, runtime_dir)
    output = payload_root.expanduser().resolve()
    if output.exists():
        raise PayloadImportError("匯入目標已存在；為保護既有檔案，ClassPilot 沒有覆寫它。")
    output.parent.mkdir(parents=True, exist_ok=True)
    if restrict_directory is not None:
        restrict_directory(output.parent)

    staging = Path(tempfile.mkdtemp(prefix=".classpilot-pg-import-", dir=output.parent))
    included_files = 0
    selected_bytes = 0
    required_found: set[str] = set()
    seen_names: set[str] = set()
    try:
        with zipfile.ZipFile(archive) as package:
            for entry in package.infolist():
                parts = _normalized_member_name(entry.filename)
                normalized_name = "/".join(parts)
                folded_name = normalized_name.casefold()
                if folded_name in seen_names:
                    raise PayloadImportError("ZIP 內含重複檔案路徑；尚未建立資料庫。")
                seen_names.add(folded_name)
                if entry.flag_bits & 0x1:
                    raise PayloadImportError("ZIP 內含加密檔案，無法安全驗證。")
                unix_mode = (entry.external_attr >> 16) & 0xFFFF
                if (unix_mode & 0xF000) == 0xA000:
                    raise PayloadImportError("ZIP 內含連結檔案；尚未建立資料庫。")
                if not _is_selected(parts) or entry.is_dir():
                    continue
                if entry.file_size > _MAX_SINGLE_FILE_BYTES:
                    raise PayloadImportError("ZIP 內有超出安全大小限制的元件。")
                included_files += 1
                selected_bytes += entry.file_size
                if included_files > _MAX_SELECTED_FILES or selected_bytes > _MAX_SELECTED_BYTES:
                    raise PayloadImportError("ZIP 內的 PostgreSQL 元件總量超出安全限制。")

                relative_parts = parts[1:] if parts[0].casefold() == "pgsql" else parts
                target = staging.joinpath(*relative_parts).resolve()
                if staging not in target.parents:
                    raise PayloadImportError("ZIP 路徑超出匯入目錄；尚未建立資料庫。")
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(entry, "r") as source, target.open("xb") as destination:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
                required_found.add("/".join(relative_parts))

        missing = sorted(set(_REQUIRED_FILES) - required_found)
        if missing:
            raise PayloadImportError(
                "ZIP 缺少必要 PostgreSQL 執行檔或授權 notices；尚未建立資料庫。"
            )
        for notice in ("server_license.txt", "commandlinetools_3rd_party_licenses.txt"):
            notice_path = staging / notice
            if notice_path.stat().st_size == 0:
                raise PayloadImportError("ZIP 的授權 notices 不完整；尚未建立資料庫。")
            try:
                notice_bytes = notice_path.read_bytes()
            except OSError:
                raise PayloadImportError("ZIP 的授權 notices 無法讀取；尚未建立資料庫。") from None
            try:
                notice_text = notice_bytes.decode("utf-8-sig").casefold()
            except UnicodeError:
                notice_text = notice_bytes.decode("cp1252", errors="replace").casefold()
            if "license" not in notice_text and "licence" not in notice_text:
                raise PayloadImportError("ZIP 的授權 notices 內容無效；尚未建立資料庫。")

        for executable_name in ("postgres.exe", "initdb.exe", "pg_ctl.exe"):
            executable = staging / "bin" / executable_name
            if _pe_machine(executable) != 0x8664:
                raise PayloadImportError(
                    "ZIP 內的 PostgreSQL 執行檔不是有效的 Windows x64 元件。"
                )
        for executable_name in ("postgres.exe", "initdb.exe", "pg_ctl.exe"):
            version_probe(staging / "bin" / executable_name, spec.version)

        payload_manifest = {
            "version": spec.version,
            "package_revision": spec.package_revision,
            "architecture": spec.architecture,
            "archive": spec.filename,
            "archive_sha256": spec.sha256,
            "included_files": included_files,
            "runtime_files": list(runtime_files),
        }
        (staging / "payload-manifest.json").write_text(
            json.dumps(payload_manifest, indent=2) + "\n", encoding="utf-8"
        )
        staging.rename(output)
    except zipfile.BadZipFile:
        raise PayloadImportError("所選檔案不是有效的 ZIP；尚未建立資料庫。") from None
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return PayloadImportResult(
        payload_root=output,
        version=spec.version,
        package_revision=spec.package_revision,
        architecture=spec.architecture,
        sha256=digest,
        included_files=included_files,
        runtime_files=runtime_files,
    )
