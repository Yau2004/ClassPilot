"""Compare PostgreSQL launch contexts, then exercise true frozen first setup and HTTP.

The host Python process is only an experiment coordinator. The frozen executable
performs its own ZIP import, initdb, role/database creation, exact migration, and
setup completion. Every cluster lives under a newly-created system temporary root.
"""

from __future__ import annotations

from datetime import datetime
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ZIP_SHA256 = "e2246ba91d22345bc3d017586c09ede52d9df180b1eeb480f050445f1cad84e2"
EXPECTED_REVISION = "20261019_0004"
DIRECT_PREFIX = "ClassPilot-Runner-Compare-"
FROZEN_PREFIX = "ClassPilot-Frozen-Setup-"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_digest(root: Path) -> tuple[tuple[str, int, str], ...]:
    return tuple(
        (
            path.relative_to(root).as_posix(),
            path.stat().st_size,
            _sha256(path),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def _payload_binary_hashes(payload_root: Path) -> dict[str, str]:
    return {
        name: _sha256(payload_root / name)
        for name in (
            "bin/initdb.exe", "bin/postgres.exe", "bin/pg_ctl.exe",
            "bin/libcrypto-3-x64.dll", "bin/libssl-3-x64.dll",
        )
    }


def _process_diagnostics(state_root: Path) -> list[dict[str, object]]:
    path = state_root / "logs" / "postgres-process-diagnostics.jsonl"
    if not path.is_file():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [row for row in rows if isinstance(row, dict)]


def _clean_release_environment(test_local_app_data: Path) -> dict[str, str]:
    keep = {
        "SystemRoot", "WINDIR", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE",
        "HOMEPATH", "APPDATA", "PROGRAMDATA", "PROCESSOR_ARCHITECTURE",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in {item.upper() for item in keep}}
    windows_root = Path(env.get("SystemRoot") or env.get("WINDIR") or r"C:\Windows")
    env["SystemRoot"] = str(windows_root)
    env["WINDIR"] = str(windows_root)
    env["PATH"] = os.pathsep.join((str(windows_root / "System32"), str(windows_root)))
    env["LOCALAPPDATA"] = str(test_local_app_data)
    return env


def _run_direct_initdb(
    *,
    state_root: Path,
    archive: Path,
    spec,
) -> dict[str, object]:
    from app.core.postgres_payload_import import validate_and_import_postgres_zip
    from app.core.postgres_runtime import (
        _OwnedProcessJob,
        _external_postgres_environment,
        _process_diagnostic_rows,
        _probe_executable_version,
        _sanitize_postgres_output,
        _windows_process_descendants,
        secure_directory,
    )

    state_root.mkdir(parents=True)
    secure_directory(state_root)
    payload_root = state_root / "runtime" / "postgresql"
    imported = validate_and_import_postgres_zip(
        archive,
        payload_root,
        spec,
        restrict_directory=lambda path: (secure_directory(state_root), secure_directory(path)),
    )
    for name in ("initdb.exe", "postgres.exe", "pg_ctl.exe"):
        _probe_executable_version(
            payload_root / "bin" / name,
            expected_version=spec.version,
            state_root=state_root,
        )
    version_probe_records = [
        row for row in _process_diagnostic_rows(state_root)
        if row.get("stage") == "executable-preflight"
    ]
    data_dir = state_root / "data" / "postgresql"
    data_dir.parent.mkdir(parents=True)
    password_dir = state_root / "config"
    password_dir.mkdir()
    secure_directory(password_dir)
    password_file = password_dir / "direct-initdb-password.tmp"
    password_file.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
    env, path_summary = _external_postgres_environment(payload_root / "bin")
    executable = payload_root / "bin" / "initdb.exe"
    arguments = [
        "-D", str(data_dir), "-U", "classpilot_diag_admin",
        "--encoding=UTF8", "--locale-provider=icu", "--icu-locale=zh-Hant-HK",
        "--auth-local=scram-sha-256", "--auth-host=scram-sha-256",
        f"--pwfile={password_file}",
    ]
    started = time.monotonic()
    child_processes: dict[int, dict[str, object]] = {}
    process = None
    job = None
    stdout = b""
    stderr = b""
    return_code: int | None = None
    try:
        process = subprocess.Popen(
            [str(executable), *arguments],
            cwd=payload_root / "bin",
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if os.name == "nt":
            job = _OwnedProcessJob()
            job.assign(process)
        deadline = time.monotonic() + 300
        while True:
            for item in _windows_process_descendants(process.pid):
                child_processes[int(item["pid"])] = item
            try:
                stdout, stderr = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    if job is not None:
                        job.close()
                        job = None
                    else:
                        process.terminate()
                    process.wait(timeout=10)
                    raise TimeoutError("Direct initdb exceeded 300 seconds; its owned process job was stopped.")
        return_code = process.returncode & 0xFFFFFFFF
    finally:
        password_file.unlink(missing_ok=True)
        if job is not None:
            job.close()

    combined = stdout + (b"\n" if stdout and stderr else b"") + stderr
    return {
        "runner": "direct-desktop-process",
        "status": "passed" if return_code == 0 else "failed",
        "stage": "cluster-init",
        "executable": str(executable.resolve()),
        "working_directory": str((payload_root / "bin").resolve()),
        "parent_pid": os.getpid(),
        "parent_image": Path(sys.executable).name,
        "process_pid": process.pid if process is not None else None,
        "child_processes": sorted(child_processes.values(), key=lambda item: int(item["pid"])),
        "return_code": return_code,
        "return_code_hex": f"0x{return_code:08X}" if return_code is not None else None,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "dll_search_context": "unfrozen host process; standard Windows DLL search",
        "path_environment": path_summary,
        "version_probes": version_probe_records,
        "version_probes_before_full_initdb": True,
        "safe_output_summary": _sanitize_postgres_output(combined),
        "payload_sha256": imported.sha256,
        "payload_binary_sha256": _payload_binary_hashes(payload_root),
        "archive_sha256": EXPECTED_ZIP_SHA256,
    }


def _run_unfrozen_first_setup(
    *,
    state_root: Path,
    test_root: Path,
    archive: Path,
    install_root: Path,
    resource_root: Path,
) -> dict[str, object]:
    from app.core import config as config_module
    from app.core import runtime as runtime_module
    from app.core.postgres_runtime import secure_directory
    from app.core.release_location import ReleaseLocationStore, locator_path
    from app.core.release_setup import REQUIRED_REVISION, ReleaseSetupManager
    from app.core.runtime import resolve_runtime_paths
    from classpilot_release import _complete_release_first_setup

    release_paths = resolve_runtime_paths(
        frozen=True,
        resource_root=resource_root,
        local_app_data=test_root / "unfrozen-simulated-localappdata",
        state_root=state_root,
    )
    runtime_module.RUNTIME_PATHS = release_paths
    config_module.RUNTIME_PATHS = release_paths
    state_root.parent.mkdir(parents=True)
    secure_directory(state_root.parent)
    manager = ReleaseSetupManager(
        state_root=state_root,
        install_root=install_root,
        temp_root=Path(tempfile.gettempdir()),
        resource_root=resource_root,
        directory_protector=secure_directory,
    )
    if manager.assess_data_root().kind != "new" or manager.preflight():
        raise RuntimeError("Unfrozen comparison root failed its isolated setup preflight.")
    location_store = ReleaseLocationStore(
        locator_path(test_root / "unfrozen-simulated-localappdata"),
        protect_directory=secure_directory,
    )
    started = time.monotonic()
    settings = _complete_release_first_setup(manager, location_store, state_root, archive)
    if manager.setup_status() != "complete" or REQUIRED_REVISION != EXPECTED_REVISION:
        raise RuntimeError("The unfrozen release setup path did not complete the expected revision.")
    if (state_root / "data" / "postgresql" / "postmaster.pid").exists():
        raise RuntimeError("The unfrozen setup path left its PostgreSQL process running.")
    diagnostics = _process_diagnostics(state_root)
    if not diagnostics:
        raise RuntimeError("The unfrozen release runtime did not persist PostgreSQL process diagnostics.")
    return {
        "runner": "unfrozen-classpilot-release-runtime",
        "status": "passed",
        "database": settings.postgres_db,
        "revision": EXPECTED_REVISION,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "host_python": Path(sys.executable).name,
        "development_env_read": False,
        "migration": "20261019_0004-applied-by-shared-first-setup-path",
        "postgres_processes": diagnostics,
        "version_probes_before_full_initdb": all(
            item.get("stage") == "executable-preflight"
            for item in diagnostics[:3]
        ),
        "payload_binary_sha256": _payload_binary_hashes(manager.payload_root),
    }


def _run_frozen_first_setup(
    *,
    executable: Path,
    state_root: Path,
    test_root: Path,
    report_path: Path,
    archive: Path,
    env: dict[str, str],
    owned_processes: list[subprocess.Popen[bytes]],
) -> tuple[subprocess.Popen[bytes], dict[str, object]]:
    process = subprocess.Popen(
        [
            str(executable),
            "--isolated-first-setup-test",
            "--confirm-isolated-temp",
            "--state-root", str(state_root),
            "--postgres-zip", str(archive),
            "--report-file", str(report_path),
        ],
        cwd=Path(tempfile.gettempdir()).resolve(),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    owned_processes.append(process)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if report.get("status") != "complete":
                raise RuntimeError(
                    "Frozen first setup failed at "
                    f"{report.get('failed_check', 'unknown')}: {report.get('error_message', 'no safe summary')}"
                )
            if report.get("build_id") != _frozen_build_id(executable):
                raise RuntimeError("Frozen setup report build ID differs from the executable under test.")
            if report.get("state_root") != str(state_root.resolve()):
                raise RuntimeError("Frozen setup reported an unexpected isolated data root.")
            process.wait(timeout=20)
            if process.returncode != 0:
                raise RuntimeError("Frozen setup reported success but returned a failure exit code.")
            return process, report
        if process.poll() is not None:
            raise RuntimeError("Frozen ClassPilot exited before writing its first-setup report.")
        time.sleep(0.25)
    raise TimeoutError("Frozen first setup did not finish within 600 seconds; its temp root was retained.")


def _frozen_build_id(executable: Path) -> str:
    manifest = executable.parent / "_internal" / "release_manifest.json"
    return str(json.loads(manifest.read_text(encoding="utf-8"))["build_id"])


def _run_frozen_postgres_diagnostic(
    *,
    executable: Path,
    state_root: Path,
    temp_root: Path,
    env: dict[str, str],
) -> dict[str, object]:
    """Exercise the explicit PostgreSQL-only diagnostic on this frozen build."""
    from app.core.release_settings import ReleaseSettingsStore

    setup = json.loads((state_root / "setup-state.json").read_text(encoding="utf-8"))
    instance_id = str(setup["instance_id"])
    if len(instance_id) != 48 or not re.fullmatch(r"[0-9a-f]{48}", instance_id):
        raise RuntimeError("The isolated frozen setup has no valid instance identity.")
    settings = ReleaseSettingsStore(state_root / "config" / "settings.dpapi").load()
    diagnostic_root = temp_root / f"ClassPilot-Existing-Cluster-Diagnostic-{secrets.token_hex(8)}"
    report_file = diagnostic_root / "diagnostic-report.json"
    result = subprocess.run(
        [
            str(executable),
            "--diagnose-existing-cluster",
            "--confirm-existing-cluster-diagnostic",
            "--state-root", str(state_root),
            "--port", str(settings.postgres_port),
            "--expected-port", str(settings.postgres_port),
            "--expected-instance-id-prefix", instance_id[:8],
            "--diagnostic-report-dir", str(diagnostic_root),
            "--report-file", str(report_file),
        ],
        cwd=temp_root,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if not report_file.is_file():
        raise RuntimeError("Frozen PostgreSQL diagnostic did not write its external safe report.")
    report = json.loads(report_file.read_text(encoding="utf-8"))
    if (
        result.returncode != 0
        or report.get("status") != "passed"
        or report.get("build_id") != _frozen_build_id(executable)
        or report.get("migration") != "not-run"
        or report.get("api") != "not-started"
        or report.get("settings_write") != "not-performed"
        or report.get("owned_process_still_running") is not False
        or (state_root / "data" / "postgresql" / "postmaster.pid").exists()
    ):
        safe_summary = str(report.get("safe_failure_summary") or report.get("safe_output_summary") or "no safe diagnostic summary")
        raise RuntimeError(f"Frozen PostgreSQL-only diagnostic failed: {safe_summary[:500]}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare PostgreSQL process contexts and verify frozen ClassPilot setup.")
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--postgres-zip", type=Path)
    parser.add_argument(
        "--frozen-only", action="store_true",
        help="Skip direct/unfrozen setup comparisons and validate setup, HTTP, persistence, and stop using the frozen executable.",
    )
    args = parser.parse_args(argv)
    # Alembic's revision loader imports source files by path; do not leave
    # bytecode in the onedir resources while running the unfrozen comparison.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / "backend"))
    sys.path.insert(0, str(ROOT / "scripts"))

    from app.core.postgres_payload_import import PayloadSpec
    from app.core.path_compatibility import contains_cjk_path_character
    from app.core.postgres_runtime import secure_directory

    archive = (args.postgres_zip or ROOT / "postgresql-18.6-5-windows-x64-binaries.zip").expanduser().resolve()
    executable = (args.executable or ROOT / "dist" / "windows-user-zip" / "ClassPilot" / "ClassPilot.exe").expanduser().resolve()
    if not executable.is_file():
        raise RuntimeError("Build the Windows onedir app before running frozen setup verification.")
    if not archive.is_file() or _sha256(archive) != EXPECTED_ZIP_SHA256:
        raise RuntimeError("The selected PostgreSQL ZIP is missing or its pinned hash differs.")
    install_root = executable.parent
    resource_root = install_root / "_internal"
    install_tree_before = _directory_digest(install_root)
    manifest = json.loads((resource_root / "release_manifest.json").read_text(encoding="utf-8"))
    spec = PayloadSpec.from_mapping(
        json.loads((resource_root / "postgresql_payload_spec.json").read_text(encoding="utf-8"))
    )
    build_id = str(manifest.get("build_id", ""))
    exe_hash_before = _sha256(executable)
    if not build_id or manifest.get("required_alembic_revision") != EXPECTED_REVISION:
        raise RuntimeError("The packaged build ID or migration target is invalid.")

    temp_root = Path(tempfile.gettempdir()).resolve()
    if contains_cjk_path_character(temp_root):
        raise RuntimeError(f"The Windows temporary path is unsupported: {temp_root}")
    nonce = secrets.token_hex(12)
    comparison_root = temp_root / f"{DIRECT_PREFIX}{nonce}"
    frozen_root = temp_root / f"{FROZEN_PREFIX}{nonce}"
    if comparison_root.exists() or frozen_root.exists():
        raise RuntimeError("A supposedly unique isolated verification directory already exists.")
    comparison_root.mkdir()
    secure_directory(comparison_root)

    results: dict[str, object] = {
        "build_id": build_id,
        "executable_sha256_before": exe_hash_before,
        "postgres_archive_sha256": EXPECTED_ZIP_SHA256,
        "postgres_version": spec.version,
        "postgres_architecture": spec.architecture,
        "migration_target": EXPECTED_REVISION,
        "execution_environment": "cleaned Windows user environment; Conda/Python/Node PATH entries not passed to frozen app or PostgreSQL children",
    }
    all_verified = False
    owned_app_processes: list[subprocess.Popen[bytes]] = []
    service_processes: list[subprocess.Popen[bytes]] = []
    report_file = comparison_root / "runner-comparison.json"
    artifact_report = executable.parent.parent / "frozen-integration-report.json"

    try:
        direct = {"status": "skipped"}
        unfrozen_result: dict[str, object] | None = None
        if args.frozen_only:
            results["context_comparisons"] = "skipped by explicit frozen-only mode"
        else:
            direct = _run_direct_initdb(
                state_root=comparison_root / "direct",
                archive=archive,
                spec=spec,
            )
            results["direct_process_experiment"] = direct
            comparison_root.joinpath("direct-result.json").write_text(
                json.dumps(direct, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )

            unfrozen_root = comparison_root / "unfrozen"
            unfrozen_state = unfrozen_root / "state"
            try:
                unfrozen_result = _run_unfrozen_first_setup(
                    state_root=unfrozen_state,
                    test_root=unfrozen_root,
                    archive=archive,
                    install_root=install_root,
                    resource_root=resource_root,
                )
                results["unfrozen_runtime_experiment"] = unfrozen_result
            except Exception as error:
                from app.core.postgres_runtime import _sanitize_postgres_output

                unfrozen_result = {
                    "runner": "unfrozen-classpilot-release-runtime",
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "safe_summary": _sanitize_postgres_output(str(error))[:700],
                }
                results["unfrozen_runtime_experiment"] = unfrozen_result

        state_root = frozen_root / "state"
        setup_report = frozen_root / "setup-report.json"
        env = _clean_release_environment(frozen_root / "simulated-localappdata")
        first_setup_process, first_setup = _run_frozen_first_setup(
            executable=executable,
            state_root=state_root,
            test_root=frozen_root,
            report_path=setup_report,
            archive=archive,
            env=env,
            owned_processes=owned_app_processes,
        )
        results["frozen_first_setup"] = first_setup
        results["frozen_setup_process_pid"] = first_setup_process.pid
        results["frozen_payload_binary_sha256"] = _payload_binary_hashes(
            state_root / "runtime" / "postgresql"
        )
        frozen_processes = _process_diagnostics(state_root)
        if not frozen_processes:
            raise RuntimeError("The frozen release did not persist PostgreSQL process diagnostics.")
        results["frozen_postgres_processes"] = frozen_processes
        results["frozen_version_probes_before_full_initdb"] = all(
            item.get("stage") == "executable-preflight"
            for item in frozen_processes[:3]
        )

        internal_root = resource_root
        postgres_root = state_root / "runtime" / "postgresql"
        results["same_name_openssl_dlls"] = {
            name: {
                "frozen_app_sha256": _sha256(internal_root / name) if (internal_root / name).is_file() else None,
                "postgres_payload_sha256": _sha256(postgres_root / "bin" / name) if (postgres_root / "bin" / name).is_file() else None,
                "same_bytes": (
                    _sha256(internal_root / name) == _sha256(postgres_root / "bin" / name)
                    if (internal_root / name).is_file() and (postgres_root / "bin" / name).is_file()
                    else None
                ),
            }
            for name in ("libcrypto-3-x64.dll", "libssl-3-x64.dll")
        }
        if not args.frozen_only:
            if direct.get("payload_binary_sha256") != results["frozen_payload_binary_sha256"]:
                raise RuntimeError("Direct and frozen setup used different PostgreSQL executable or DLL bytes.")
            if not isinstance(unfrozen_result, dict) or unfrozen_result.get("status") != "passed":
                raise RuntimeError("The unfrozen shared first-setup path did not complete; see the safe experiment report.")
            if unfrozen_result.get("payload_binary_sha256") != results["frozen_payload_binary_sha256"]:
                raise RuntimeError("Unfrozen and frozen setup used different PostgreSQL executable or DLL bytes.")

        required_tools = {"initdb.exe", "postgres.exe", "pg_ctl.exe"}
        process_contexts = [("frozen", frozen_processes)]
        if not args.frozen_only:
            process_contexts[:0] = [
                ("direct", direct.get("version_probes", [])),
                ("unfrozen", unfrozen_result.get("postgres_processes", [])),
            ]
        for label, records in process_contexts:
            used_tools = {
                str(item.get("component", "")).casefold()
                for item in records if isinstance(item, dict)
            }
            if not required_tools.issubset(used_tools):
                raise RuntimeError(f"The {label} setup did not record all PostgreSQL executable version probes.")
            version_rows = [
                item for item in records
                if isinstance(item, dict) and item.get("stage") == "executable-preflight"
            ]
            if any(
                item.get("return_code") != 0
                or f"PostgreSQL) {spec.version}" not in str(item.get("safe_output_summary", ""))
                for item in version_rows
            ):
                raise RuntimeError(f"One or more {label} PostgreSQL --version probes failed or reported another version.")

        service_env = _clean_release_environment(frozen_root / "simulated-localappdata")

        def start(label: str) -> tuple[subprocess.Popen[bytes], dict[str, object]]:
            report = frozen_root / f"{label}-start.json"
            process = subprocess.Popen(
                [
                    str(executable), "--start", "--state-root", str(state_root),
                    "--no-browser", "--report-file", str(report),
                ],
                cwd=temp_root,
                env=service_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            service_processes.append(process)
            owned_app_processes.append(process)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if report.is_file():
                    result = json.loads(report.read_text(encoding="utf-8"))
                    if result.get("status") == "ready":
                        if result.get("migration") != "not-run":
                            raise RuntimeError("Daily startup reported a migration attempt.")
                        return process, result
                    raise RuntimeError(
                        "Frozen daily startup failed: "
                        f"{result.get('failed_check', 'unknown')} / {result.get('error_message', 'no safe summary')}"
                    )
                if process.poll() is not None:
                    raise RuntimeError("Frozen ClassPilot exited before reporting HTTP readiness.")
                time.sleep(0.25)
            raise TimeoutError("Frozen ClassPilot did not report readiness within 90 seconds.")

        def stop(process: subprocess.Popen[bytes], label: str) -> None:
            report = frozen_root / f"{label}-stop.json"
            result = subprocess.run(
                [str(executable), "--stop", "--report-file", str(report)],
                cwd=temp_root,
                env=service_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=25,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            process.wait(timeout=45)
            details = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {}
            if (
                result.returncode != 0
                or details.get("status") != "stop-signalled"
                or (state_root / "running-instance.json").exists()
                or (state_root / "data" / "postgresql" / "postmaster.pid").exists()
            ):
                raise RuntimeError("The isolated frozen ClassPilot processes did not stop cleanly.")

        process, first = start("first")
        base_url = str(first["url"]).rstrip("/")
        with urlopen(base_url + "/ready", timeout=5) as response:
            if response.status != 200 or json.load(response).get("status") != "ready":
                raise RuntimeError("Frozen readiness endpoint did not return ready.")
        with urlopen(base_url + "/", timeout=5) as response:
            if response.status != 200 or "text/html" not in response.headers.get("Content-Type", ""):
                raise RuntimeError("Frozen frontend was not served over HTTP.")

        student_body = {
            "name": "ClassPilot Synthetic Packaging Sample",
            "age": 8,
            "gender": "male",
            "support_need_status": "none_reported",
            "general_notes": "Synthetic release test only",
        }
        student_request = Request(
            base_url + "/students",
            data=json.dumps(student_body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(student_request, timeout=10) as response:
            student = json.load(response)
            if response.status != 201:
                raise RuntimeError("Synthetic student creation failed.")
        student_id = student["id"]

        lesson_body = {
            "topic": "Frozen packaging verification lesson",
            "session_date": datetime.now(ZoneInfo("Asia/Hong_Kong")).date().isoformat(),
            "start_time": "10:00:00",
            "end_time": "11:00:00",
            "participant_ids": [student_id],
        }
        lesson_request = Request(
            base_url + "/sessions",
            data=json.dumps(lesson_body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(lesson_request, timeout=10) as response:
            lesson = json.load(response)
            if response.status != 201:
                raise RuntimeError("Synthetic lesson creation failed.")
        lesson_id = lesson["id"]

        record_request = Request(
            base_url + f"/sessions/{lesson_id}/participant-records",
            data=json.dumps(
                {"participants": [{
                    "student_id": student_id,
                    "attendance_status": "present",
                    "evaluation": "Synthetic frozen packaging evaluation only",
                }]}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="PATCH",
        )
        with urlopen(record_request, timeout=10) as response:
            saved = json.load(response)
            if response.status != 200:
                raise RuntimeError("Synthetic attendance/evaluation update failed.")
        saved_participant = saved["participants"][0]
        if (
            saved_participant["attendance_status"] != "present"
            or saved_participant["evaluation"] != "Synthetic frozen packaging evaluation only"
        ):
            raise RuntimeError("Synthetic participant record did not round-trip.")
        stop(process, "first")

        process, second = start("second")
        with urlopen(str(second["url"]).rstrip("/") + f"/sessions/{lesson_id}", timeout=10) as response:
            reopened = json.load(response)
        persisted = reopened["participants"][0]
        if (
            persisted["student"]["name"] != "ClassPilot Synthetic Packaging Sample"
            or persisted["attendance_status"] != "present"
            or persisted["evaluation"] != "Synthetic frozen packaging evaluation only"
        ):
            raise RuntimeError("Synthetic data did not persist across frozen service restart.")
        stop(process, "second")

        diagnostic = _run_frozen_postgres_diagnostic(
            executable=executable,
            state_root=state_root,
            temp_root=temp_root,
            env=service_env,
        )
        results["frozen_postgres_only_diagnostic"] = {
            "status": diagnostic["status"],
            "build_id": diagnostic["build_id"],
            "port": diagnostic["port"],
            "listener": diagnostic["listener"],
            "sql": diagnostic["sql"],
            "migration": diagnostic["migration"],
            "api": diagnostic["api"],
            "settings_write": diagnostic["settings_write"],
            "owned_process_still_running": diagnostic["owned_process_still_running"],
        }

        exe_hash_after = _sha256(executable)
        if exe_hash_after != exe_hash_before:
            raise RuntimeError("The frozen executable changed while this build was under test.")
        if _directory_digest(install_root) != install_tree_before:
            raise RuntimeError("The frozen/unfrozen test changed files under the read-only application directory.")
        results["application_directory_unchanged"] = True
        results["executable_sha256_after"] = exe_hash_after
        results["http"] = "ready; frontend served from frozen build"
        results["synthetic_records"] = "student, lesson, attendance, evaluation persisted after restart"
        results["daily_migration"] = "not-run"
        results["stop_and_restart"] = "passed"
        results["comparison_root"] = str(comparison_root)
        results["frozen_root"] = str(frozen_root)
        report_file.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        all_verified = args.frozen_only or (
            direct.get("status") == "passed"
            and isinstance(unfrozen_result, dict)
            and unfrozen_result.get("status") == "passed"
        )
        if not all_verified:
            raise RuntimeError("One or more launch-context comparison baselines failed; frozen HTTP results are recorded separately.")
        print(f"build_id={build_id}; frozen_exe_sha256={exe_hash_before}")
        if not args.frozen_only:
            print("direct_initdb=passed; unfrozen_shared_setup=passed; frozen_shared_first_setup=passed")
        else:
            print("frozen_shared_first_setup=passed; other_context_comparisons=skipped")
        print("exact_revision=20261019_0004; frozen_http=ready; synthetic_data=persisted_after_restart")
        print("daily_migration=not-run; frozen_stop=passed; process_tree_diagnostics=recorded")
        print("frozen_postgres_only_diagnostic=passed; normal_stop=passed")
        return 0
    finally:
        for process in service_processes:
            if process.poll() is None:
                stop_report = frozen_root / "failure-cleanup-stop.json"
                subprocess.run(
                    [str(executable), "--stop", "--report-file", str(stop_report)],
                    cwd=temp_root,
                    env=_clean_release_environment(frozen_root / "simulated-localappdata"),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=25,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                try:
                    process.wait(timeout=45)
                except subprocess.TimeoutExpired:
                    pass
        for process in owned_app_processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        report_file.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if all_verified:
            for owned_root, prefix in ((comparison_root, DIRECT_PREFIX), (frozen_root, FROZEN_PREFIX)):
                if (
                    owned_root.parent != temp_root
                    or not owned_root.name.startswith(prefix)
                    or owned_root.is_symlink()
                    or getattr(owned_root, "is_junction", lambda: False)()
                ):
                    raise RuntimeError("An isolated temp root changed identity and was not removed.")
                if owned_root.exists():
                    shutil.rmtree(owned_root)
            print("isolated_temporary_data=removed_after_verified_stop")
            artifact_report.write_text(
                json.dumps(results, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"safe_integration_report={artifact_report}")
        else:
            print(f"isolated_runner_comparison_root_retained={comparison_root}")
            if frozen_root.exists():
                print(f"isolated_frozen_setup_root_retained={frozen_root}")


if __name__ == "__main__":
    raise SystemExit(main())
