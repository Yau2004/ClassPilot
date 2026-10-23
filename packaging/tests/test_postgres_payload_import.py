from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.postgres_payload_import import (  # noqa: E402
    PayloadImportError,
    PayloadSpec,
    validate_and_import_postgres_zip,
)


def _x64_pe() -> bytes:
    data = bytearray(80)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x40)
    data[0x40:0x44] = b"PE\0\0"
    struct.pack_into("<H", data, 0x44, 0x8664)
    return bytes(data)


def _archive(path: Path, *, names: dict[str, bytes] | None = None) -> Path:
    members = {
        "pgsql/bin/postgres.exe": _x64_pe(),
        "pgsql/bin/initdb.exe": _x64_pe(),
        "pgsql/bin/pg_ctl.exe": _x64_pe(),
        "pgsql/server_license.txt": b"PostgreSQL License\n",
        "pgsql/commandlinetools_3rd_party_licenses.txt": b"Third party license notices\n",
        "pgsql/share/extension/README.txt": b"extension resource",
        "pgsql/include/postgresql/server.h": b"must not be imported",
    }
    if names:
        members.update(names)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, content in members.items():
            package.writestr(name, content)
    return path


def _spec(archive: Path, *, sha256: str | None = None, runtime: tuple[str, ...] = ()) -> PayloadSpec:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    return PayloadSpec(
        version="18.6",
        package_revision="5",
        architecture="windows-x64",
        filename="postgresql-18.6-5-windows-x64-binaries.zip",
        sha256=sha256 or digest,
        official_download_page="https://www.postgresql.org/download/windows/",
        sha256_source="https://github.com/EnterpriseDB/edb-installers/issues/733",
        required_runtime_files=runtime,
    )


def _runtime_dir(path: Path, names: tuple[str, ...]) -> Path:
    path.mkdir()
    for name in names:
        (path / name).write_bytes(b"runtime sentinel")
    return path


def test_valid_user_zip_is_verified_and_selectively_imported(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "postgresql.zip")
    state_root = tmp_path / "ClassPilot 匯入 with spaces"
    payload_root = state_root / "payload" / "postgresql"
    runtime = ("vcruntime140.dll", "msvcp140.dll")
    result = validate_and_import_postgres_zip(
        archive,
        payload_root,
        _spec(archive, runtime=runtime),
        runtime_dir=_runtime_dir(tmp_path / "runtime", runtime),
        version_probe=lambda executable, expected: (
            assert_version(executable, expected)
        ),
    )

    assert result.version == "18.6"
    assert result.package_revision == "5"
    assert result.architecture == "windows-x64"
    assert result.payload_root == payload_root
    assert result.payload_root.joinpath("bin", "postgres.exe").is_file()
    assert result.payload_root.joinpath("share", "extension", "README.txt").is_file()
    assert result.payload_root.joinpath("server_license.txt").is_file()
    assert result.payload_root.joinpath("commandlinetools_3rd_party_licenses.txt").is_file()
    assert not result.payload_root.joinpath("include").exists()
    assert not result.payload_root.joinpath("postgresql.zip").exists()
    assert not (state_root / "data" / "postgresql").exists()


def assert_version(executable: Path, expected: str) -> None:
    assert executable.is_file()
    assert expected == "18.6"


def test_wrong_hash_fails_before_creating_any_import_path(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "not-the-pinned-file.zip")
    destination = tmp_path / "state" / "payload" / "postgresql"

    with pytest.raises(PayloadImportError, match="完整性檢查"):
        validate_and_import_postgres_zip(
            archive,
            destination,
            _spec(archive, sha256="0" * 64),
            runtime_dir=tmp_path,
        )

    assert not destination.parent.exists()


def test_missing_runtime_fails_before_extracting_or_creating_destination(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "postgresql.zip")
    destination = tmp_path / "state" / "payload" / "postgresql"
    runtime_dir = _runtime_dir(tmp_path / "runtime", ("vcruntime140.dll",))

    with pytest.raises(PayloadImportError, match="msvcp140.dll"):
        validate_and_import_postgres_zip(
            archive,
            destination,
            _spec(archive, runtime=("vcruntime140.dll", "msvcp140.dll")),
            runtime_dir=runtime_dir,
        )

    assert not destination.parent.exists()


@pytest.mark.parametrize(
    "unsafe_name",
    ["pgsql/bin/../../escape.exe", "pgsql\\bin\\..\\..\\escape.exe", "C:/escape.exe", "/escape.exe"],
)
def test_unsafe_archive_path_is_rejected_without_writing_outside_stage(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    archive = _archive(tmp_path / "hostile.zip", names={unsafe_name: b"outside"})
    destination = tmp_path / "state" / "payload" / "postgresql"

    with pytest.raises(PayloadImportError, match="路徑|絕對路徑"):
        validate_and_import_postgres_zip(
            archive,
            destination,
            _spec(archive),
            runtime_dir=tmp_path,
            version_probe=assert_version,
        )

    assert not destination.exists()
    assert not (tmp_path / "escape.exe").exists()


def test_missing_license_notice_rejects_archive_before_database_setup(tmp_path: Path) -> None:
    archive = tmp_path / "missing-notice.zip"
    _archive(
        archive,
        names={"pgsql/commandlinetools_3rd_party_licenses.txt": b""},
    )
    destination = tmp_path / "state" / "payload" / "postgresql"

    with pytest.raises(PayloadImportError, match="授權 notices"):
        validate_and_import_postgres_zip(
            archive,
            destination,
            _spec(archive),
            runtime_dir=tmp_path,
            version_probe=assert_version,
        )

    assert not destination.exists()
    assert not (tmp_path / "state" / "data" / "postgresql").exists()


def test_wrong_executable_version_cleans_staging_and_preserves_archive(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "postgresql.zip")
    destination = tmp_path / "state" / "payload" / "postgresql"

    def wrong_version(_executable: Path, _expected: str) -> None:
        raise PayloadImportError("version mismatch")

    with pytest.raises(PayloadImportError, match="version mismatch"):
        validate_and_import_postgres_zip(
            archive,
            destination,
            _spec(archive),
            runtime_dir=tmp_path,
            version_probe=wrong_version,
        )

    assert archive.is_file()
    assert not destination.exists()
    assert not list(destination.parent.glob(".classpilot-pg-import-*"))


def test_import_refuses_to_overwrite_existing_payload(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "postgresql.zip")
    destination = tmp_path / "payload"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("preserve", encoding="utf-8")

    with pytest.raises(PayloadImportError, match="已存在"):
        validate_and_import_postgres_zip(
            archive,
            destination,
            _spec(archive),
            runtime_dir=tmp_path,
            version_probe=assert_version,
        )

    assert sentinel.read_text(encoding="utf-8") == "preserve"
