"""Windows onedir release: self-check, first setup, start, and stop."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import webbrowser

_CURRENT_STAGE = "startup"


def _bootstrap_paths() -> tuple[Path, Path]:
    if bool(getattr(sys, "frozen", False)):
        # Alembic loads revision files from bundled resources by path. Keep
        # those resources read-only in the installed application directory.
        sys.dont_write_bytecode = True
        root = Path(getattr(sys, "_MEIPASS"))
    else:
        root = Path(__file__).resolve().parents[1]
    backend = root / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    return root, backend


def _asset_references(index_path: Path) -> list[Path]:
    content = index_path.read_text(encoding="utf-8")
    references = re.findall(r'(?:src|href)="([^"]+)"', content)
    return [
        index_path.parent / reference.lstrip("/")
        for reference in references
        if reference.startswith(("assets/", "/assets/"))
    ]


def _manifest(root: Path) -> dict[str, object]:
    manifest_path = root / "release_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("required_alembic_revision") != "20261019_0004":
        raise RuntimeError("Release manifest target revision is unexpected.")
    if not re.fullmatch(r"[a-z0-9-]{8,80}", str(manifest.get("build_id", ""))):
        raise RuntimeError("Release manifest build identity is missing or invalid.")
    return manifest


def _payload_spec(root: Path):
    from app.core.postgres_payload_import import PayloadSpec

    path = root / "postgresql_payload_spec.json"
    return PayloadSpec.from_mapping(json.loads(path.read_text(encoding="utf-8")))


def run_self_check() -> dict[str, object]:
    global _CURRENT_STAGE
    root, backend = _bootstrap_paths()
    _CURRENT_STAGE = "resource-paths"
    result: dict[str, object] = {
        "mode": "release-self-check",
        "build_id": "loading",
        "database_connection": "not-attempted",
        "migration": "not-run",
        "persistent_settings_write": "not-performed",
        "development_env_read": "not-performed",
        "checks": {},
    }
    checks = result["checks"]
    assert isinstance(checks, dict)

    _CURRENT_STAGE = "frontend-assets"
    index = root / "frontend" / "dist" / "index.html"
    if not index.is_file():
        raise RuntimeError("Bundled frontend index.html is missing.")
    assets = _asset_references(index)
    if not assets or any(not asset.is_file() for asset in assets):
        raise RuntimeError("One or more bundled frontend assets are missing.")
    checks["frontend"] = {"status": "ok", "asset_count": len(assets)}

    _CURRENT_STAGE = "alembic-resources"
    if not (backend / "alembic.ini").is_file() or not (backend / "alembic" / "versions").is_dir():
        raise RuntimeError("Bundled Alembic resources are missing.")
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    manifest = _manifest(root)
    result["build_id"] = str(manifest["build_id"])
    migration_config = Config(str(backend / "alembic.ini"))
    scripts = ScriptDirectory.from_config(migration_config)
    revisions = list(scripts.walk_revisions())
    heads = scripts.get_heads()
    expected = str(manifest["required_alembic_revision"])
    if heads != [expected] or scripts.get_revision(expected) is None:
        raise RuntimeError("Bundled Alembic graph does not match the release manifest.")
    checks["alembic"] = {"status": "ok", "revision_count": len(revisions), "target": expected}

    _CURRENT_STAGE = "postgresql-payload"
    spec = _payload_spec(root)
    if spec.version != str(manifest.get("postgresql_version")) or spec.architecture != str(manifest.get("postgresql_architecture")):
        raise RuntimeError("PostgreSQL support specification does not match the release manifest.")
    if (root / "postgresql").exists():
        raise RuntimeError("PostgreSQL binaries must be supplied by the user, not bundled in ClassPilot.")
    checks["postgresql"] = {
        "status": "user-selection-required",
        "version": spec.version,
        "package_revision": spec.package_revision,
        "architecture": spec.architecture,
        "official_download_page": spec.official_download_page,
        "sha256_source": spec.sha256_source,
        "binary_payload_bundled": False,
    }

    _CURRENT_STAGE = "fastapi-import"
    from app.core.config import ReleaseSettingsUnavailable, get_settings
    from app.core.runtime import RUNTIME_PATHS
    from app.main import app

    if not RUNTIME_PATHS.frozen:
        raise RuntimeError("Release resources were not resolved in frozen mode.")
    if app.title != "ClassPilot API":
        raise RuntimeError("FastAPI application loaded with an unexpected title.")
    try:
        get_settings()
    except ReleaseSettingsUnavailable:
        pass
    else:
        raise RuntimeError("Self-check unexpectedly loaded persistent release settings.")
    checks["backend"] = {"status": "ok", "fastapi": "loaded"}
    _CURRENT_STAGE = "uvicorn-config"
    import uvicorn

    uvicorn_config = uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None, access_log=False)
    if uvicorn_config.host != "127.0.0.1":
        raise RuntimeError("Uvicorn release configuration is not loopback-only.")
    checks["uvicorn"] = {
        "status": "config-ok",
        "host": "127.0.0.1",
        "listener_start_stop": "not-tested",
    }
    return result


def run_postgres_executable_probe(state_root: Path) -> dict[str, object]:
    """Read-only diagnostic: execute only the three PostgreSQL --version probes."""
    root, _ = _bootstrap_paths()
    from app.core.postgres_runtime import PostgresPayload, PostgresPrototype
    from app.core.release_settings import ReleaseSettingsStore

    resolved_state = state_root.expanduser().resolve()
    runtime = PostgresPrototype(
        PostgresPayload(resolved_state / "runtime" / "postgresql"),
        resolved_state,
        ReleaseSettingsStore(resolved_state / "config" / "settings.dpapi"),
    )
    runtime._verify_payload()
    spec = _payload_spec(root)
    baseline: list[dict[str, object]] = []
    for name in ("initdb.exe", "postgres.exe", "pg_ctl.exe"):
        executable = runtime.payload.executable(name)
        env = os.environ.copy()
        for key in tuple(env):
            if key.upper().startswith("PG"):
                env.pop(key, None)
        env["PATH"] = str(executable.parent) + os.pathsep + env.get("PATH", "")
        try:
            # This intentionally mirrors the previous frozen-process behavior:
            # PyInstaller's bootloader DLL directory is inherited by children.
            # Only --version is run; it cannot initialize or start a cluster.
            completed = subprocess.run(
                [str(executable), "--version"],
                cwd=executable.parent,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=10,
            )
            code = completed.returncode & 0xFFFFFFFF
            stdout = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
            match = re.search(r"\bPostgreSQL\)\s+(\d+(?:\.\d+)+)\b", stdout)
            baseline.append(
                {
                    "component": name,
                    "exit_code": code,
                    "exit_code_hex": f"0x{code:08X}",
                    "reported_version": match.group(1) if match else None,
                }
            )
        except subprocess.TimeoutExpired:
            baseline.append({"component": name, "result": "timeout"})
        except OSError as error:
            baseline.append({"component": name, "result": type(error).__name__})
    return {
        "mode": "postgres-executable-preflight",
        "status": "ok",
        "database_connection": "not-attempted",
        "cluster_start": "not-attempted",
        "migration": "not-run",
        "version": spec.version,
        "components": ["initdb.exe", "postgres.exe", "pg_ctl.exe"],
        "previous_inherited_dll_search": baseline,
        "current_standard_dll_search": "all_version_probes_passed",
    }


def _user_event_name() -> str:
    from app.core.postgres_runtime import _windows_user_sid

    return "Local\\ClassPilotStop-" + _windows_user_sid().replace("-", "_")


def _service_mutex_name() -> str:
    from app.core.postgres_runtime import _windows_user_sid

    return "Local\\ClassPilot-" + _windows_user_sid().replace("-", "_")


def _service_mutex_is_held() -> bool:
    """Check the per-user single-instance mutex without taking ownership."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateMutexW(None, False, _service_mutex_name())
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        result = kernel32.WaitForSingleObject(handle, 0)
        if result == 0x00000102:  # WAIT_TIMEOUT: another process owns the mutex.
            return True
        if result == 0:  # We acquired a free mutex; release only our ownership.
            kernel32.ReleaseMutex(handle)
            return False
        if result == 0x00000080:  # WAIT_ABANDONED: prior owner exited without release.
            kernel32.ReleaseMutex(handle)
            return False
        raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)


class _NamedObjects:
    def __init__(self, mutex_name: str, event_name: str):
        import ctypes
        from ctypes import wintypes

        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel32.CreateMutexW.restype = wintypes.HANDLE
        self.kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel32.CreateEventW.restype = wintypes.HANDLE
        self.kernel32.GetLastError.restype = wintypes.DWORD
        self.kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.mutex = self.kernel32.CreateMutexW(None, True, mutex_name)
        if not self.mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        self.already_running = ctypes.get_last_error() == 183
        self.event = None
        if not self.already_running:
            self.event = self.kernel32.CreateEventW(None, True, False, event_name)
            if not self.event:
                self.close()
                raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self.event:
            self.kernel32.CloseHandle(self.event)
            self.event = None
        if self.mutex:
            self.kernel32.ReleaseMutex(self.mutex)
            self.kernel32.CloseHandle(self.mutex)
            self.mutex = None


def _acquire_location_operation_lock():
    lock = _NamedObjects(_service_mutex_name(), _user_event_name())
    if lock.already_running:
        lock.close()
        raise RuntimeError("ClassPilot 正在執行；請先停止目前服務，再切換資料位置。")
    return lock


def _request_stop(event_name: str) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.SetEvent.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenEventW(0x0002, False, event_name)
    if not handle:
        return False
    try:
        return bool(kernel32.SetEvent(handle))
    finally:
        kernel32.CloseHandle(handle)


def _safe_report(path: Path | None, **values: object) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")


def _migrate_exact_release_revision(settings) -> None:
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from app.core.config import configure_release_settings
    from app.core.runtime import RUNTIME_PATHS

    configure_release_settings(settings)
    root = RUNTIME_PATHS.resource_root
    manifest = _manifest(root)
    target = str(manifest["required_alembic_revision"])
    config = Config(str(RUNTIME_PATHS.alembic_ini))
    scripts = ScriptDirectory.from_config(config)
    if scripts.get_heads() != [target] or scripts.get_revision(target) is None:
        raise RuntimeError("Migration graph differs from the pinned manifest revision.")
    config.cmd_opts = SimpleNamespace(x=["database=release"])
    command.upgrade(config, target)


def _show_postgres_zip_picker(spec) -> Path | None:
    import tkinter as tk
    from tkinter import filedialog

    selected: dict[str, Path | None] = {"path": None}
    root = tk.Tk()
    root.title("ClassPilot PostgreSQL 設定")
    root.resizable(False, False)
    root.geometry("610x255")
    frame = tk.Frame(root, padx=22, pady=18)
    frame.pack(fill="both", expand=True)
    tk.Label(
        frame,
        text=f"請選取 PostgreSQL {spec.version} ({spec.package_revision}) Windows x64 binaries ZIP。",
        justify="left",
        anchor="w",
        wraplength=560,
    ).pack(fill="x", pady=(0, 8))
    tk.Label(
        frame,
        text="ClassPilot 會先檢查官方 SHA-256、必要檔案、版本、授權 notices 和 runtime。檢查失敗時不會建立資料庫。",
        justify="left",
        anchor="w",
        wraplength=560,
    ).pack(fill="x", pady=(0, 8))
    tk.Label(
        frame,
        text=spec.official_download_page,
        justify="left",
        anchor="w",
        fg="#1558a6",
        wraplength=560,
    ).pack(fill="x", pady=(0, 18))

    actions = tk.Frame(frame)
    actions.pack(fill="x")

    def open_download_page() -> None:
        webbrowser.open(spec.official_download_page)

    def choose_file() -> None:
        path = filedialog.askopenfilename(
            parent=root,
            title="選取 PostgreSQL binaries ZIP",
            filetypes=[("PostgreSQL binaries ZIP", "*.zip"), ("所有檔案", "*.*")],
        )
        if path:
            selected["path"] = Path(path)
            root.destroy()

    tk.Button(actions, text="開啟官方下載頁", command=open_download_page).pack(side="left")
    tk.Button(actions, text="選取 ZIP 並繼續", command=choose_file).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    return selected["path"]


def _confirm_prototype_setup(spec, result, state_root: Path) -> bool:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    accepted = messagebox.askyesno(
        "確認隔離原型設定",
        "PostgreSQL ZIP 檢查通過。\n\n"
        f"版本：{result.version} Windows x64\n"
        f"SHA-256：{result.sha256}\n\n"
        "接下來只會在本次隔離暫存目錄建立 PostgreSQL cluster、原型專用帳戶和資料庫，"
        "並套用 schema revision 20261019_0004。\n"
        "不會使用 ClassPilot 正式資料位置或既有資料庫。\n\n"
        f"暫存位置：{state_root}\n\n"
        "是否繼續？",
        parent=root,
    )
    root.destroy()
    return accepted


def _provision(
    state_root: Path | None,
    report_file: Path | None,
    archive_path: Path | None,
) -> int:
    import tempfile

    from app.core.postgres_runtime import PostgresPayload, PostgresPrototype, PROTOTYPE_DATABASE, secure_directory
    from app.core.postgres_payload_import import validate_and_import_postgres_zip
    from app.core.release_settings import ReleaseSettingsStore
    from app.core.runtime import RUNTIME_PATHS

    temp_root = Path(tempfile.gettempdir()).resolve()
    if state_root is None:
        state_root = temp_root / f"ClassPilot-Stage2-{__import__('secrets').token_hex(8)}"
    state_root = state_root.resolve()
    if state_root.parent != temp_root:
        raise RuntimeError("Prototype state must be a new directory inside the system temporary directory.")
    if state_root.exists():
        raise RuntimeError("Prototype state directory already exists; refusing to overwrite it.")

    spec = _payload_spec(RUNTIME_PATHS.resource_root)
    if archive_path is None:
        archive_path = _show_postgres_zip_picker(spec)
    if archive_path is None:
        _safe_report(report_file, status="cancelled", database_created=False, migration="not-run")
        _report_window(
            "ClassPilot PostgreSQL 設定",
            "你已取消選取檔案；沒有匯入 payload、建立資料庫或執行 migration。",
        )
        return 2

    payload_root = state_root / "payload" / "postgresql"
    try:
        result = validate_and_import_postgres_zip(
            archive_path,
            payload_root,
            spec,
            restrict_directory=lambda path: (secure_directory(state_root), secure_directory(path)),
        )
    except Exception:
        if (
            state_root.parent == temp_root
            and not state_root.is_symlink()
            and not getattr(state_root, "is_junction", lambda: False)()
        ):
            import shutil

            shutil.rmtree(state_root, ignore_errors=True)
        raise
    if not _confirm_prototype_setup(spec, result, state_root):
        _safe_report(
            report_file,
            status="payload-validated-setup-cancelled",
            version=result.version,
            architecture=result.architecture,
            sha256=result.sha256,
            database_created=False,
            migration="not-run",
        )
        _report_window(
            "ClassPilot PostgreSQL 設定",
            "PostgreSQL ZIP 已驗證。沒有建立資料庫或執行 migration。\n\n"
            f"已驗證的暫存 payload：{result.payload_root}",
        )
        return 2

    settings_store = ReleaseSettingsStore(state_root / "config" / "settings.dpapi")
    runtime = PostgresPrototype(PostgresPayload(payload_root), state_root, settings_store)
    settings = runtime.provision(migrate=_migrate_exact_release_revision)
    port = settings.postgres_port
    revision = "20261019_0004"
    runtime.stop_owned_cluster()
    _safe_report(
        report_file,
        status="provisioned",
        database=PROTOTYPE_DATABASE,
        revision=revision,
        port=port,
        credential_storage="Windows DPAPI current-user protected",
        admin_password="not-persisted",
    )
    _report_window(
        "ClassPilot PostgreSQL 原型",
        "隔離 PostgreSQL 原型設定完成。\n"
        f"資料庫：{PROTOTYPE_DATABASE}\n"
        f"Schema revision：{revision}\n"
        "PostgreSQL 已安全停止。",
    )
    return 0


def _start(
    state_root: Path,
    report_file: Path | None,
    *,
    open_browser: bool,
    on_ready=None,
) -> int:
    _bootstrap_paths()
    from app.core.config import configure_release_settings
    from app.core.release_setup import completed_release_instance_id
    from app.core.release_settings import ReleaseSettingsStore
    from app.core.runtime import configure_release_data_root

    state_root = state_root.resolve()
    configure_release_data_root(state_root)
    instance_id = completed_release_instance_id(state_root)
    settings_store = ReleaseSettingsStore(state_root / "config" / "settings.dpapi")
    settings = settings_store.load()
    names = _NamedObjects(
        "Local\\ClassPilot-" + __import__("app.core.postgres_runtime", fromlist=["_windows_user_sid"])._windows_user_sid().replace("-", "_"),
        _user_event_name(),
    )
    if names.already_running:
        opened = _open_existing_instance(state_root)
        if opened and on_ready is not None:
            on_ready()
        _safe_report(
            report_file,
            status="already-running" if opened else "failed",
            failed_check=None if opened else "single-instance",
            error_message=None if opened else "ClassPilot 已由另一個資料位置啟動；請先停止目前實例再切換資料位置。",
        )
        names.close()
        return 0 if opened else 2

    runtime = _release_postgres_runtime(
        state_root, settings_store, instance_id=instance_id
    )
    api_thread = None
    server = None
    try:
        settings, _postgres_process = runtime.start_existing(settings)
        configure_release_settings(settings)
        _bootstrap_paths()
        from app.main import app
        import uvicorn
        from app.core.postgres_runtime import available_loopback_port

        last_error = None
        readiness_deadline = time.monotonic() + 40
        for _ in range(12):
            port = available_loopback_port()
            if port is None:
                raise RuntimeError("Could not bind an API loopback port in this environment.")
            config = uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_config=None,
                access_log=False,
            )
            server = uvicorn.Server(config)
            api_thread = threading.Thread(target=server.run, name="ClassPilotAPI", daemon=True)
            api_thread.start()
            while time.monotonic() < readiness_deadline and api_thread.is_alive():
                if server.started:
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/ready", timeout=2) as response:
                            if response.status == 200:
                                last_error = None
                                break
                    except HTTPError as error:
                        reason = error.headers.get("X-ClassPilot-Readiness-Reason", "unknown")
                        if reason == "schema_mismatch":
                            last_error = "schema_mismatch"
                            break
                        last_error = reason
                    except (URLError, TimeoutError, OSError):
                        last_error = "api_not_ready"
                time.sleep(0.15)
            if server.started and last_error is None:
                break
            server.should_exit = True
            api_thread.join(timeout=10)
            if last_error == "schema_mismatch":
                raise RuntimeError("Readiness rejected the database schema revision.")
            if api_thread.is_alive():
                raise RuntimeError(f"ClassPilot API did not become ready ({last_error or 'startup_timeout'}).")
            server = None
            api_thread = None
        if server is None or api_thread is None or not server.started:
            raise RuntimeError(f"ClassPilot API did not become ready ({last_error or 'startup_failed'}).")

        url = f"http://127.0.0.1:{port}/"
        _write_running_instance(state_root, port)
        _safe_report(report_file, status="ready", url=url, host="127.0.0.1", port=port, migration="not-run")
        if open_browser:
            webbrowser.open(url)
        if on_ready is not None:
            on_ready()
        while api_thread.is_alive() and not _stop_requested(names.event):
            time.sleep(0.2)
        server.should_exit = True
        api_thread.join(timeout=30)
        if api_thread.is_alive():
            raise RuntimeError("The API server did not stop cleanly.")
        _remove_running_instance(state_root, port)
        return 0
    finally:
        if server is not None:
            server.should_exit = True
        if api_thread is not None and api_thread.is_alive():
            api_thread.join(timeout=30)
        runtime.stop_owned_cluster()
        names.close()


def _release_postgres_runtime(state_root: Path, settings_store, *, instance_id: str):
    """Bind daily startup to the formal release identity and installed payload."""
    from app.core.postgres_runtime import PostgresPayload, PostgresPrototype
    from app.core.release_setup import (
        RELEASE_ADMIN_ROLE,
        RELEASE_APP_ROLE,
        RELEASE_DATABASE,
        RELEASE_OWNER_KIND,
    )

    return PostgresPrototype(
        PostgresPayload(state_root / "runtime" / "postgresql"),
        state_root,
        settings_store,
        database_name=RELEASE_DATABASE,
        app_role=RELEASE_APP_ROLE,
        admin_role=RELEASE_ADMIN_ROLE,
        owner_kind=RELEASE_OWNER_KIND,
        instance_id=instance_id,
    )


def _running_instance_file(state_root: Path) -> Path:
    return state_root / "running-instance.json"


def _write_running_instance(state_root: Path, port: int) -> None:
    _safe_report(
        _running_instance_file(state_root),
        pid=os.getpid(),
        executable=str(Path(sys.executable).resolve()),
        port=port,
        host="127.0.0.1",
    )


def _open_existing_instance(state_root: Path) -> bool:
    """Open only a readiness-checked instance belonging to this executable."""
    url = _existing_instance_url(state_root)
    if url is None:
        return False
    webbrowser.open(url)
    return True


def _existing_instance_url(state_root: Path) -> str | None:
    """Return a URL only when the recorded process is this executable and ready."""
    try:
        value = json.loads(_running_instance_file(state_root).read_text(encoding="utf-8"))
        pid = int(value["pid"])
        port = int(value["port"])
        executable = str(Path(value["executable"]).resolve())
        if value.get("host") != "127.0.0.1" or executable.casefold() != str(Path(sys.executable).resolve()).casefold():
            return None
        from app.core.postgres_runtime import _windows_process_image, _windows_process_is_alive

        if not _windows_process_is_alive(pid):
            return None
        process_image = _windows_process_image(pid)
        if process_image is None or process_image.casefold() != executable.casefold():
            return None
        with urlopen(f"http://127.0.0.1:{port}/ready", timeout=2) as response:
            if response.status != 200:
                return None
        return f"http://127.0.0.1:{port}/"
    except Exception:
        return None


def _remove_running_instance(state_root: Path, port: int) -> None:
    path = _running_instance_file(state_root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if int(value.get("pid", -1)) == os.getpid() and int(value.get("port", -1)) == port:
            path.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return


def _stop_requested(event_handle) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    return kernel32.WaitForSingleObject(event_handle, 0) == 0


def _report_window(title: str, message: str, *, error: bool = False) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        if error:
            messagebox.showerror(title, message, parent=root)
        else:
            messagebox.showinfo(title, message, parent=root)
        root.destroy()
    except Exception:
        return


def _release_location_store():
    from app.core.postgres_runtime import secure_directory
    from app.core.release_location import ReleaseLocationStore, locator_path
    from app.core.runtime import RUNTIME_PATHS

    return ReleaseLocationStore(
        locator_path(RUNTIME_PATHS.local_app_data),
        protect_directory=secure_directory,
    )


def _release_setup_manager(state_root: Path | None = None):
    from app.core.release_setup import ReleaseSetupManager
    from app.core.runtime import RUNTIME_PATHS
    from app.core.release_location import default_data_root

    if not RUNTIME_PATHS.frozen:
        raise RuntimeError("First-run release setup is available only in the frozen Windows application.")
    if state_root is None:
        location = _release_location_store().load()
        state_root = location.root if location is not None else default_data_root(RUNTIME_PATHS.local_app_data)
    from app.core.runtime import configure_release_data_root

    configure_release_data_root(state_root)
    return ReleaseSetupManager(
        state_root=state_root,
        install_root=Path(sys.executable).resolve().parent,
        temp_root=Path(__import__("tempfile").gettempdir()),
        resource_root=RUNTIME_PATHS.resource_root,
    )


def _release_migration(settings) -> None:
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from app.core.config import configure_release_settings
    from app.core.runtime import RUNTIME_PATHS

    configure_release_settings(settings)
    config = Config(str(RUNTIME_PATHS.alembic_ini))
    scripts = ScriptDirectory.from_config(config)
    target = "20261019_0004"
    if scripts.get_heads() != [target] or scripts.get_revision(target) is None:
        raise RuntimeError("Bundled migration graph does not match the approved release target.")
    config.cmd_opts = SimpleNamespace(x=["database=release"])
    command.upgrade(config, target)


def _complete_release_first_setup(manager, location_store, state_root: Path, archive: Path | None) -> object:
    """Shared confirmed setup path used by the GUI and isolated frozen test mode."""
    global _CURRENT_STAGE
    # Commit the locator only after setup is complete; interrupted roots can be selected again.
    _CURRENT_STAGE = "first-setup"
    try:
        settings = manager.provision(archive, migration=_release_migration)
    except Exception:
        try:
            state = manager._read_state()
            if state is not None:
                _CURRENT_STAGE = str(state.get("stage") or _CURRENT_STAGE)
        except Exception:
            pass
        raise
    _CURRENT_STAGE = "write-completion-location"
    location_store.save(state_root, setup_complete=True)
    return settings


def _run_isolated_first_setup_test(
    state_root: Path | None,
    archive_path: Path | None,
    report_file: Path | None,
    *,
    confirmed: bool,
) -> int:
    """Run the same first-setup path as the wizard, fenced to a new temp root."""
    global _CURRENT_STAGE
    import tempfile

    if not bool(getattr(sys, "frozen", False)) or os.name != "nt":
        raise RuntimeError("隔離首次設定測試只能由 Windows 凍結版 ClassPilot.exe 執行。")
    if not confirmed or state_root is None or archive_path is None or report_file is None:
        raise RuntimeError("隔離首次設定測試需要明確確認、ZIP、資料根目錄及報告路徑。")

    temp_root = Path(tempfile.gettempdir()).resolve()
    selected_state = state_root.expanduser().resolve(strict=False)
    selected_parent = selected_state.parent
    expected_report = selected_parent / "setup-report.json"
    if (
        selected_parent.parent != temp_root
        or not selected_parent.name.startswith("ClassPilot-Frozen-Setup-")
        or selected_state != selected_parent / "state"
        or report_file.expanduser().resolve(strict=False) != expected_report.resolve(strict=False)
        or selected_parent.exists()
        or selected_parent.is_symlink()
        or getattr(selected_parent, "is_junction", lambda: False)()
    ):
        raise RuntimeError(
            "隔離測試只接受尚不存在的 %TEMP%\\ClassPilot-Frozen-Setup-<id>\\state，"
            "並將所有測試狀態限制在該暫存目錄。"
        )

    resource_root, _ = _bootstrap_paths()
    from app.core.postgres_runtime import secure_directory
    from app.core.release_location import ReleaseLocationStore, locator_path

    manager = _release_setup_manager(selected_state)
    _CURRENT_STAGE = "isolated-temp-preflight"
    if manager.assess_data_root().kind != "new":
        raise RuntimeError("隔離測試資料位置不是新的空資料夾；沒有執行首次設定。")
    path_issues = manager.preflight()
    if path_issues:
        from app.core.path_compatibility import find_unsupported_cjk_paths, format_path_issues

        raise RuntimeError(format_path_issues(find_unsupported_cjk_paths(path_issues)))

    selected_parent.mkdir()
    secure_directory(selected_parent)
    simulated_local_app_data = selected_parent / "simulated-localappdata"
    location_store = ReleaseLocationStore(
        locator_path(simulated_local_app_data),
        protect_directory=secure_directory,
    )
    _CURRENT_STAGE = "frozen-first-setup"
    settings = _complete_release_first_setup(
        manager,
        location_store,
        selected_state,
        archive_path.expanduser().resolve(),
    )
    if manager.setup_status() != "complete":
        raise RuntimeError("首次設定沒有留下有效的完成狀態。")
    location = location_store.load()
    if location is None or not location.setup_complete or location.root != selected_state:
        raise RuntimeError("隔離測試定位資訊未指向剛完成設定的測試目錄。")
    if (selected_state / "data" / "postgresql" / "postmaster.pid").exists():
        raise RuntimeError("首次設定結束後仍有 PostgreSQL PID；測試資料已保留供診斷。")

    manifest = _manifest(resource_root)
    spec = _payload_spec(resource_root)
    _safe_report(
        report_file,
        status="complete",
        mode="frozen-isolated-first-setup",
        build_id=str(manifest["build_id"]),
        executable=str(Path(sys.executable).resolve()),
        state_root=str(selected_state),
        database=settings.postgres_db,
        postgres_version=spec.version,
        postgres_payload_sha256=spec.sha256,
        revision=json.loads(manager.state_file.read_text(encoding="utf-8")).get("revision"),
        migration="20261019_0004-applied-by-explicit-first-setup-path",
        development_env="not-read",
        real_local_app_data="not-used",
        postgres_cluster_stopped=True,
    )
    _CURRENT_STAGE = "frozen-first-setup-complete"
    return 0


def _run_setup_dialog(root, update_status, on_location_changed=None) -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    from app.core.path_compatibility import find_unsupported_cjk_paths, format_path_issues
    from app.core.release_location import default_data_root, install_data_root, validate_data_root
    from app.core.runtime import RUNTIME_PATHS

    location_store = _release_location_store()
    try:
        recorded = location_store.load()
    except Exception:
        recorded = None
    initial = recorded.root if recorded else default_data_root(RUNTIME_PATHS.local_app_data)
    value = tk.StringVar(value=str(initial))
    assessment_text = tk.StringVar(value="選擇資料夾後，按「檢查資料夾」查看是否可安全使用。")
    assessment_kind = {"value": "unknown"}
    dialog = tk.Toplevel(root)
    dialog.title("ClassPilot 資料位置與首次設定")
    dialog.geometry("760x590")
    dialog.minsize(640, 540)
    dialog.transient(root)
    dialog.grab_set()
    frame = tk.Frame(dialog, padx=20, pady=18)
    frame.pack(fill="both", expand=True)
    tk.Label(frame, text="選擇 ClassPilot 資料位置", font=("Segoe UI", 14, "bold")).pack(anchor="w")
    operation = tk.StringVar(value="existing" if recorded and recorded.setup_complete else "new")
    tk.Label(frame, text="操作：", anchor="w").pack(anchor="w", pady=(10, 2))
    tk.Radiobutton(
        frame, text="使用另一份既有 ClassPilot 資料", variable=operation, value="existing",
        command=lambda: inspect(),
    ).pack(anchor="w")
    tk.Radiobutton(
        frame, text="在空資料夾建立新的資料環境", variable=operation, value="new",
        command=lambda: inspect(),
    ).pack(anchor="w")
    tk.Label(
        frame,
        text="資料搬移：尚未提供。建立新資料不會帶入目前資料；原資料仍保留。",
        justify="left", wraplength=700, anchor="w",
    ).pack(fill="x", pady=(2, 8))
    tk.Label(
        frame,
        text=("程式安裝位置與資料位置分開。資料位置會保存設定、DPAPI 保護的憑證、PostgreSQL runtime、"
              "資料庫、日誌及備份。預設位置為 %LOCALAPPDATA%\\ClassPilot。"),
        justify="left", wraplength=700, anchor="w",
    ).pack(fill="x", pady=(8, 12))
    path_row = tk.Frame(frame)
    path_row.pack(fill="x")
    entry = tk.Entry(path_row, textvariable=value)
    entry.pack(side="left", fill="x", expand=True)

    def browse() -> None:
        selected = filedialog.askdirectory(parent=dialog, title="選擇 ClassPilot 資料目錄", initialdir=value.get())
        if selected:
            value.set(selected)
            assessment_kind["value"] = "unknown"
            assessment_text.set("資料位置已變更；請重新檢查。")

    tk.Button(path_row, text="瀏覽…", command=browse).pack(side="left", padx=(8, 0))
    def use_sibling_data() -> None:
        value.set(str(install_data_root(Path(sys.executable))))
        assessment_kind["value"] = "unknown"
        assessment_text.set(
            "已按目前 ClassPilot.exe 的實際位置選擇其下的 Data 資料夾。"
            "若程式位於 Program Files，一般使用者通常沒有寫入權限；設定時會以目前使用者權限實際檢查，"
            "失敗請改選可寫入的位置，不要以管理員身分執行 ClassPilot。"
        )

    tk.Button(path_row, text="程式旁的 Data", command=use_sibling_data).pack(side="left", padx=(8, 0))
    tk.Button(path_row, text="檢查資料夾", command=lambda: inspect()).pack(side="left", padx=(8, 0))
    tk.Label(frame, textvariable=assessment_text, justify="left", wraplength=700, anchor="w", height=5).pack(fill="x", pady=12)
    tk.Label(
        frame,
        text=("僅支援本機固定磁碟。含中文、日文或韓文的程式／資料／暫存路徑目前不支援；空格可用。"
              "不會搬移資料，也不會清空非 ClassPilot 檔案。"),
        justify="left", wraplength=700, anchor="w",
    ).pack(fill="x", pady=(0, 10))
    buttons = tk.Frame(frame)
    buttons.pack(side="bottom", fill="x")
    action = {"button": None}

    def invalidate_assessment(*_args) -> None:
        assessment_kind["value"] = "unknown"
        assessment_text.set("資料位置或操作已變更；請重新檢查。")
        if action["button"] is not None:
            action["button"].configure(state="disabled")

    def inspect() -> None:
        try:
            chosen = Path(value.get()).expanduser()
            if recorded is not None and recorded.setup_complete and chosen.resolve(strict=False) == recorded.root.resolve(strict=False) and not chosen.exists():
                assessment_kind["value"] = "invalid"
                assessment_text.set(
                    f"已記錄的資料位置目前不存在：{chosen}\n請重新連接原磁碟，或明確選擇另一個有效既有資料目錄／新的空資料夾。"
                    "ClassPilot 不會在遺失的位置建立空白資料。"
                )
                if action["button"] is not None:
                    action["button"].configure(state="disabled")
                return
            issues = validate_data_root(chosen, install_root=Path(sys.executable).resolve().parent)
            manager = _release_setup_manager(chosen)
            cjk_issues = manager.preflight()
            if cjk_issues:
                issues.extend([format_path_issues(find_unsupported_cjk_paths(cjk_issues))])
            if issues:
                assessment_kind["value"] = "invalid"
                assessment_text.set("\n".join(issues))
                if action["button"] is not None:
                    action["button"].configure(state="disabled")
                return
            assessment = manager.assess_data_root()
            assessment_kind["value"] = assessment.kind
            assessment_text.set(assessment.message)
            if operation.get() == "new":
                assessment_text.set(assessment.message + "\n建立新資料不會帶入目前資料；原資料仍保留。")
            if assessment.kind == "incomplete" and assessment.safe_to_resume:
                assessment_text.set(assessment.message + "\n只有經確認的恢復步驟可繼續。")
            if action["button"] is not None:
                permitted = operation.get() == "existing" and assessment.kind == "existing" or (
                    operation.get() == "new" and (
                        assessment.kind == "new" or assessment.kind == "incomplete" and assessment.safe_to_resume
                    )
                )
                action["button"].configure(
                    state="normal" if permitted else "disabled",
                    text=("使用並切換至此資料" if operation.get() == "existing" else
                          "安全恢復" if assessment.safe_to_resume else "建立新的資料環境"),
                )
        except Exception as error:
            assessment_kind["value"] = "invalid"
            assessment_text.set(f"檢查失敗（{type(error).__name__}）；未修改資料。請確認路徑並重試。")
            if action["button"] is not None:
                action["button"].configure(state="disabled")

    def continue_setup() -> None:
        chosen = Path(value.get()).expanduser().resolve(strict=False)
        kind = assessment_kind["value"]
        selected_operation = operation.get()
        if (selected_operation == "existing" and kind != "existing") or (
            selected_operation == "new" and kind not in {"new", "incomplete"}
        ):
            return
        if kind == "incomplete" and selected_operation != "new":
            return
        manager = _release_setup_manager(chosen)
        if selected_operation == "existing":
            if not messagebox.askyesno(
                "確認使用既有資料",
                f"將啟動此 ClassPilot 自有 PostgreSQL，核對實例、目前使用者的 DPAPI 憑證、版本及 schema，然後正常停止。\n\n資料位置：{chosen}\n"
                "不會重新初始化、覆寫憑證或執行 migration。若憑證或 schema 不相容，設定會停止。是否繼續？",
                parent=dialog,
            ):
                return
            try:
                problems = validate_data_root(
                    chosen, install_root=Path(sys.executable).resolve().parent,
                    write_probe=True, allow_probe_nonempty=True,
                )
                if problems:
                    raise RuntimeError("\n".join(problems))
                operation_lock = _acquire_location_operation_lock()
                try:
                    manager.verify_existing_schema()
                    location_store.save(chosen, setup_complete=True)
                finally:
                    operation_lock.close()
            except Exception as error:
                messagebox.showerror("無法使用既有資料", f"{type(error).__name__}: {error}\n\n資料未被刪除或重新初始化。", parent=dialog)
                return
            dialog.destroy()
            update_status(f"已記錄資料位置：{chosen}。既有資料與 schema 已通過唯讀檢查。")
            if on_location_changed:
                on_location_changed(chosen)
            return
        if not messagebox.askyesno(
            "確認建立新的資料環境" if kind == "new" else "確認安全恢復",
            f"程式位置：{Path(sys.executable).resolve().parent}\n資料位置：{chosen}\n"
            f"PostgreSQL executable：{manager.payload_root / 'bin' / 'initdb.exe'}\n"
            f"暫存位置：{manager.temp_root}\n目標 schema revision：20261019_0004\n\n"
            "資料會留在此位置。建立新資料不會帶入目前資料；原資料仍保留。確認後將匯入 PostgreSQL ZIP、建立專用資料庫並套用此明確 revision。"
            "恢復只會按已識別狀態安全續行，不會清除或重新初始化。是否繼續？",
            parent=dialog,
        ):
            return
        try:
            problems = validate_data_root(
                chosen, install_root=Path(sys.executable).resolve().parent, write_probe=True,
            )
            if problems:
                raise RuntimeError("\n".join(problems))
            archive = None
            if kind == "new" and not manager.payload_root.exists():
                spec = _payload_spec(manager.resource_root)
                if not messagebox.askyesno(
                    "選取 PostgreSQL ZIP",
                    "請先從官方下載頁取得支援的 PostgreSQL ZIP，再選取檔案。\n\n"
                    f"版本：{spec.version} EDB package {spec.package_revision}\n架構：Windows x64\n"
                    f"檔名：{spec.filename}\n下載頁：{spec.official_download_page}\n\n"
                    "ClassPilot 會驗證雜湊、檔案、授權 notices 與 runtime，不會自動下載或安裝系統 runtime。",
                    parent=dialog,
                ):
                    return
                selected = filedialog.askopenfilename(
                    parent=dialog, title="選取 PostgreSQL binaries ZIP",
                    filetypes=[("PostgreSQL binaries ZIP", "*.zip"), ("所有檔案", "*.*")],
                )
                if not selected:
                    return
                archive = Path(selected)
            update_status("正在檢查 PostgreSQL 並設定專用資料庫；請勿關閉視窗。")
            root.update_idletasks()
            operation_lock = _acquire_location_operation_lock()
            try:
                _complete_release_first_setup(manager, location_store, chosen, archive)
            finally:
                operation_lock.close()
        except Exception as error:
            messagebox.showerror(
                "首次設定未完成",
                f"{type(error).__name__}: {error}\n\n資料不會自動刪除或重新初始化。設定未完成時不會切換既有有效定位；如要恢復，請重新選取該資料夾或改選空資料夾。",
                parent=dialog,
            )
            update_status(f"首次設定未完成；資料已保留於 {chosen}。")
            return
        dialog.destroy()
        messagebox.showinfo("首次設定完成", "ClassPilot 專屬資料庫已設定完成。按「啟動」開始使用。", parent=root)
        update_status(f"首次設定完成。資料位置：{chosen}。日常啟動不會執行 migration。")
        if on_location_changed:
            on_location_changed(chosen)

    tk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right")
    action["button"] = tk.Button(buttons, text="檢查資料夾後繼續", command=continue_setup, state="disabled")
    action["button"].pack(side="right", padx=(0, 8))
    value.trace_add("write", invalidate_assessment)
    inspect()
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)


def run_control_panel() -> int:
    """No-argument release entry: one-user control panel and first-run wizard."""
    _bootstrap_paths()
    import tkinter as tk
    from tkinter import messagebox

    try:
        from app.core.release_location import default_data_root
        from app.core.release_location import validate_data_root
        from app.core.runtime import RUNTIME_PATHS, configure_release_data_root

        location_issue = None
        try:
            location = _release_location_store().load()
        except Exception as error:
            location = None
            location_issue = (
                f"資料位置記錄無法讀取（{type(error).__name__}）。不會自動使用預設資料；"
                "請重新選取有效既有資料或明確選擇空資料夾。"
            )
        if location is not None:
            configure_release_data_root(location.root)
        manager = _release_setup_manager(
            default_data_root(RUNTIME_PATHS.local_app_data)
            if location is None
            else location.root
        )
        setup_status = manager.setup_status()
        if location_issue:
            setup_status = "locator-invalid"
        elif location is None:
            setup_status = "location-unrecorded"
        elif not location.setup_complete:
            setup_status = "location-incomplete"
        elif not location.root.is_dir():
            setup_status = "location-unavailable"
        location_problems = []
        if location is not None and location.setup_complete and location.root.is_dir():
            location_problems = validate_data_root(
                location.root,
                install_root=Path(sys.executable).resolve().parent,
                write_probe=True,
                allow_probe_nonempty=True,
            )
            if location_problems:
                setup_status = "location-unavailable"
    except Exception as error:
        _report_window("ClassPilot", f"{type(error).__name__}: {error}", error=True)
        return 1

    root = tk.Tk()
    root.title("ClassPilot")
    root.geometry("560x300")
    root.minsize(500, 260)
    current_location = location.root if location is not None else default_data_root(RUNTIME_PATHS.local_app_data)
    status = tk.StringVar(value=(
        location_issue
        if setup_status == "locator-invalid"
        else "尚未記錄資料位置；首次設定會先檢查預設位置或可選的既有資料。"
        if setup_status == "location-unrecorded"
        else f"已記錄資料位置，但首次設定未完成：{current_location}。請安全診斷或改選空資料夾。"
        if setup_status == "location-incomplete"
        else f"已記錄的資料位置目前無法使用：{current_location}。請重新連接磁碟或重新選取資料位置；不會建立預設空白資料。"
        if setup_status == "location-unavailable"
        else "尚未完成首次設定。"
        if setup_status == "not-configured"
        else "ClassPilot 已設定。網站就緒後會自動開啟瀏覽器。"
        if setup_status == "complete"
        else f"偵測到未完成設定，資料位置：{manager.state_root}。重試前會先檢查現況。"
    ))
    if location_problems:
        status.set(status.get() + "\n" + "\n".join(location_problems))
    tk.Label(root, text="ClassPilot", font=("Segoe UI", 18, "bold")).pack(anchor="w", padx=22, pady=(18, 6))
    build_id = str(_manifest(RUNTIME_PATHS.resource_root).get("build_id", "unknown"))
    tk.Label(root, text=f"Build ID：{build_id}", anchor="w").pack(fill="x", padx=22, pady=(0, 6))
    tk.Label(root, textvariable=status, wraplength=510, justify="left", anchor="w").pack(fill="x", padx=22, pady=(0, 12))
    location_label = tk.StringVar(value=(
        f"已記錄資料位置：{current_location}"
        if location is not None
        else f"尚未記錄資料位置；預設建議位置：{current_location}"
    ))
    tk.Label(root, textvariable=location_label, wraplength=510, justify="left", anchor="w").pack(fill="x", padx=22, pady=(0, 6))
    tk.Label(
        root,
        text=(
            "自我檢查會讀取封裝資源，不連資料庫。\n"
            "更新檢查目前未提供；請勿以重裝或程式回退代替資料庫備份／還原。\n"
            "解除安裝只移除程式和捷徑，使用者資料預設保留。"
        ),
        wraplength=510,
        justify="left",
        anchor="w",
    ).pack(fill="x", padx=22, pady=8)
    actions = tk.Frame(root)
    actions.pack(fill="x", padx=22, pady=12)
    service_processes: list[subprocess.Popen[bytes]] = []
    close_after_stop = {"requested": False}
    stop_deadline = {"value": 0.0}
    stop_signal = {"sent": False}
    pending_after_stop = {"callback": None}
    starting = {"value": False}
    start_button = {"widget": None}

    def finish_start(message: str) -> None:
        starting["value"] = False
        if start_button["widget"] is not None:
            start_button["widget"].configure(state="normal")
        status.set(message)

    def start_service() -> None:
        if starting["value"]:
            return
        starting["value"] = True
        if start_button["widget"] is not None:
            start_button["widget"].configure(state="disabled")
        try:
            stop_signal["sent"] = False
            from app.core.release_location import require_completed_location, validate_data_root

            recorded_location = require_completed_location(_release_location_store())
            path_problems = validate_data_root(
                recorded_location.root,
                install_root=Path(sys.executable).resolve().parent,
                write_probe=True,
                allow_probe_nonempty=True,
            )
            if path_problems:
                raise RuntimeError("\n".join(path_problems))
            current = _release_setup_manager()
            current_status = current.setup_status()
            if current_status != "complete":
                messagebox.showinfo("需要首次設定", "請先完成首次設定，再啟動 ClassPilot。", parent=root)
                finish_start("尚未完成首次設定。")
                return
            if _open_existing_instance(current.state_root):
                finish_start("ClassPilot 已就緒；已開啟現有網站。")
                return
            report = current.state_root / "logs" / f"control-start-{__import__('secrets').token_hex(8)}.json"
            process = subprocess.Popen(
                [
                    str(Path(sys.executable).resolve()),
                    "--start",
                    "--state-root",
                    str(current.state_root),
                    "--report-file",
                    str(report),
                ],
                cwd=Path(sys.executable).resolve().parent,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            service_processes.append(process)
            status.set("正在啟動 ClassPilot；完成 readiness 後會開啟瀏覽器。")
            wait_for_start(process, report)
        except Exception as error:
            try:
                from app.core.postgres_runtime import _sanitize_postgres_output

                error_text = _sanitize_postgres_output(f"{type(error).__name__}: {error}")[:1200]
            except Exception:
                error_text = type(error).__name__
            messagebox.showerror("ClassPilot 啟動失敗", error_text, parent=root)
            finish_start("ClassPilot 未能啟動；可再次按「啟動」重試。")

    def wait_for_start(process: subprocess.Popen[bytes], report: Path) -> None:
        if report.is_file():
            try:
                result = json.loads(report.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                result = {}
            if result.get("status") in {"ready", "already-running"}:
                finish_start("ClassPilot 已就緒；網站已在瀏覽器開啟。")
                return
            if result.get("status") == "failed":
                diagnostic = str(result.get("error_message", "未取得安全診斷摘要"))
                try:
                    from app.core.postgres_runtime import _sanitize_postgres_output

                    diagnostic = _sanitize_postgres_output(diagnostic)[:1200]
                except Exception:
                    diagnostic = "啟動失敗；請查看資料目錄中的安全診斷日誌。"
                messagebox.showerror(
                    "ClassPilot 啟動失敗",
                    f"階段：{result.get('failed_check', 'unknown')}\n"
                    f"診斷：{diagnostic}\n\n"
                    "資料未被自動清除或重新初始化。",
                    parent=root,
                )
                finish_start("ClassPilot 未能啟動；資料已保留，可再次按「啟動」重試。")
                return
        if process.poll() is not None:
            finish_start("ClassPilot 啟動程序已結束；請查看資料目錄中的安全診斷日誌，可再次重試。")
            return
        root.after(250, lambda: wait_for_start(process, report))

    def stop_and_close() -> None:
        from app.core.postgres_runtime import _windows_user_sid

        try:
            manager_now = _release_setup_manager()
        except Exception:
            manager_now = None
        signalled = _request_stop("Local\\ClassPilotStop-" + _windows_user_sid().replace("-", "_"))
        if signalled or any(process.poll() is None for process in service_processes):
            if manager_now is None:
                status.set("已送出停止要求，但資料位置記錄失效，無法確認服務完全停止。請修復或重新選取資料位置後確認狀態。")
                return
            stop_signal["sent"] = signalled
            status.set(
                "正在安全停止網站與 PostgreSQL，完成後關閉控制視窗。"
                if signalled
                else "服務正在啟動；待停止通道就緒後會送出停止要求。"
            )
            close_after_stop["requested"] = True
            pending_after_stop["callback"] = None
            stop_deadline["value"] = time.monotonic() + 60
            wait_for_stop()
        else:
            root.destroy()

    def wait_for_stop() -> None:
        if not stop_signal["sent"]:
            from app.core.postgres_runtime import _windows_user_sid

            stop_signal["sent"] = _request_stop(
                "Local\\ClassPilotStop-" + _windows_user_sid().replace("-", "_")
            )
        try:
            manager_now = _release_setup_manager()
            external_running = _running_instance_file(manager_now.state_root).exists()
        except Exception as error:
            status.set(f"無法確認目前服務所用資料位置（{type(error).__name__}）；不會切換資料位置。")
            pending_after_stop["callback"] = None
            close_after_stop["requested"] = False
            stop_signal["sent"] = False
            return
        for process in service_processes:
            if process.poll() is not None:
                try:
                    process.wait(timeout=0)
                except subprocess.TimeoutExpired:
                    pass
        try:
            mutex_held = _service_mutex_is_held()
        except Exception as error:
            status.set(f"無法確認 ClassPilot 單一實例狀態（{type(error).__name__}）；不會切換資料位置。")
            pending_after_stop["callback"] = None
            close_after_stop["requested"] = False
            stop_signal["sent"] = False
            return
        alive = any(process.poll() is None for process in service_processes) or external_running or mutex_held
        if alive:
            if time.monotonic() >= stop_deadline["value"]:
                status.set("停止尚未確認；ClassPilot 保留現況，請稍後檢查狀態。")
                close_after_stop["requested"] = False
                pending_after_stop["callback"] = None
                stop_signal["sent"] = False
                return
            root.after(250, wait_for_stop)
        else:
            status.set("ClassPilot 已安全停止。")
            stop_signal["sent"] = False
            callback = pending_after_stop["callback"]
            pending_after_stop["callback"] = None
            if callback is not None:
                root.after(100, callback)
            else:
                root.after(350, root.destroy if close_after_stop["requested"] else lambda: None)

    def launch_service() -> None:
        start_service()

    def close_window() -> None:
        active_service = any(process.poll() is None for process in service_processes)
        try:
            manager_now = _release_setup_manager()
            active_service = active_service or _running_instance_file(manager_now.state_root).exists()
        except Exception:
            pass
        if active_service:
            messagebox.showinfo("ClassPilot 正在執行", "請使用「停止並關閉」，先安全停止服務。", parent=root)
            return
        root.destroy()

    def run_self_check_dialog() -> None:
        try:
            result = run_self_check()
            messagebox.showinfo(
                "ClassPilot 自我檢查",
                "封裝資源檢查完成。\n"
                f"PostgreSQL payload 狀態：{result['checks']['postgresql']['status']}\n"
                "沒有連線資料庫或執行 migration。",
                parent=root,
            )
        except Exception as error:
            messagebox.showerror(
                "ClassPilot 自我檢查未通過",
                f"{type(error).__name__}: {error}",
                parent=root,
            )

    def first_setup() -> None:
        try:
            def setup_finished(chosen: Path) -> None:
                location_changed(chosen)
                status.set("首次設定完成，正在啟動 ClassPilot。")
                launch_service()

            _run_setup_dialog(root, status.set, setup_finished)
        except Exception as error:
            messagebox.showerror("首次設定尚未開始", f"{type(error).__name__}: {error}", parent=root)

    def location_changed(chosen: Path) -> None:
        location_label.set(f"目前資料位置：{chosen}")
        status.set("資料位置已通過檢查並記錄。按「啟動」使用目前資料。")

    def open_location_settings() -> None:
        try:
            selected = _release_location_store().load()
            manager_now = _release_setup_manager(selected.root if selected else None)
            active_process = any(process.poll() is None for process in service_processes)
            active_marker = _running_instance_file(manager_now.state_root).exists()
            if _service_mutex_is_held() and not active_process and not active_marker:
                messagebox.showerror(
                    "無法確認目前資料位置",
                    "偵測到另一個 ClassPilot 服務正在啟動或使用其他資料位置。請在該服務的控制視窗安全停止後，再管理資料位置。",
                    parent=root,
                )
                return
            if active_process or active_marker:
                if not messagebox.askyesno(
                    "先停止 ClassPilot",
                    "切換或設定資料位置前，必須先停止目前 ClassPilot 後端與專屬 PostgreSQL。\n\n是否安全停止並繼續？",
                    parent=root,
                ):
                    return
                stop_and_close_for_location()
                return
            _run_setup_dialog(root, status.set, location_changed)
            selected = _release_location_store().load()
            if selected is not None:
                location_label.set(f"目前資料位置：{selected.root}")
        except Exception as error:
            messagebox.showerror("無法開啟資料位置設定", f"{type(error).__name__}: {error}", parent=root)

    def stop_and_close_for_location() -> None:
        from app.core.postgres_runtime import _windows_user_sid

        stop_signal["sent"] = _request_stop(
            "Local\\ClassPilotStop-" + _windows_user_sid().replace("-", "_")
        )
        close_after_stop["requested"] = False
        pending_after_stop["callback"] = lambda: _run_setup_dialog(root, status.set, location_changed)
        status.set("正在停止目前網站與 PostgreSQL；停止確認前不會切換資料位置。")
        stop_deadline["value"] = time.monotonic() + 60
        wait_for_stop()

    if setup_status != "complete":
        tk.Button(actions, text="首次設定…", command=first_setup).pack(side="left")
    tk.Button(actions, text="自我檢查", command=run_self_check_dialog).pack(side="left", padx=(8, 0))
    start_button["widget"] = tk.Button(actions, text="啟動", command=launch_service)
    start_button["widget"].pack(side="left", padx=(8, 0))
    tk.Button(actions, text="資料位置…", command=open_location_settings).pack(side="left", padx=(8, 0))
    tk.Button(actions, text="停止並關閉", command=stop_and_close).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", close_window)
    if setup_status == "complete" and location is not None and location.setup_complete:
        root.after(120, launch_service)
    root.mainloop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--self-check", action="store_true")
    modes.add_argument("--probe-postgres", action="store_true")
    modes.add_argument("--provision-prototype", action="store_true")
    modes.add_argument("--isolated-first-setup-test", action="store_true")
    modes.add_argument("--start", action="store_true")
    modes.add_argument("--stop", action="store_true")
    modes.add_argument("--setup", action="store_true")
    modes.add_argument("--update-check", action="store_true")
    modes.add_argument("--diagnose-existing-cluster", action="store_true")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--postgres-zip", type=Path)
    parser.add_argument("--report-file", type=Path)
    parser.add_argument("--confirm-isolated-temp", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--diagnostic-report-dir", type=Path)
    parser.add_argument("--port", type=int)
    parser.add_argument("--expected-port", type=int)
    parser.add_argument("--expected-instance-id-prefix")
    parser.add_argument("--confirm-existing-cluster-diagnostic", action="store_true")
    args = parser.parse_args(argv)
    selected_state = args.state_root.expanduser().resolve(strict=False) if args.state_root else None
    diagnostic_report = (
        args.diagnostic_report_dir.expanduser().resolve(strict=False)
        if args.diagnostic_report_dir else None
    )
    if args.diagnose_existing_cluster:
        temp_root = Path(tempfile.gettempdir()).resolve()
        if (
            not args.confirm_existing_cluster_diagnostic
            or selected_state is None
            or not selected_state.is_absolute()
            or args.port is None
            or args.expected_port is None
            or args.port != args.expected_port
            or diagnostic_report is None
            or diagnostic_report.parent != temp_root
            or not diagnostic_report.name.startswith("ClassPilot-Existing-Cluster-Diagnostic-")
            or diagnostic_report.exists()
            or args.expected_instance_id_prefix is None
            or (
                args.report_file is not None
                and args.report_file.expanduser().resolve(strict=False)
                != (diagnostic_report / "diagnostic-report.json").resolve(strict=False)
            )
        ):
            parser.error(
                "--diagnose-existing-cluster requires an explicit --state-root, matching --port and "
                "--expected-port, --expected-instance-id-prefix, --confirm-existing-cluster-diagnostic, "
                "and a new direct child of %TEMP% named ClassPilot-Existing-Cluster-Diagnostic-<id>."
            )
    if args.isolated_first_setup_test:
        selected_state = args.state_root.expanduser().resolve(strict=False) if args.state_root else None
        selected_parent = selected_state.parent if selected_state else None
        safe_report = args.report_file.expanduser().resolve(strict=False) if args.report_file else None
        if (
            not args.confirm_isolated_temp
            or selected_state is None
            or args.postgres_zip is None
            or safe_report is None
            or selected_parent is None
            or selected_parent.parent != Path(tempfile.gettempdir()).resolve()
            or not selected_parent.name.startswith("ClassPilot-Frozen-Setup-")
            or selected_state != selected_parent / "state"
            or safe_report != (selected_parent / "setup-report.json").resolve(strict=False)
            or selected_parent.exists()
        ):
            parser.error(
                "--isolated-first-setup-test requires --confirm-isolated-temp, an explicit ZIP, "
                "a new %TEMP%\\ClassPilot-Frozen-Setup-<id>\\state root, and that root's setup-report.json."
            )
    if not any((args.self_check, args.probe_postgres, args.provision_prototype, args.isolated_first_setup_test, args.start, args.stop, args.setup, args.update_check, args.diagnose_existing_cluster)):
        if bool(getattr(sys, "frozen", False)):
            return run_control_panel()
        args.self_check = True

    try:
        if args.diagnose_existing_cluster:
            _bootstrap_paths()
            from app.core.postgres_runtime import diagnose_release_cluster_start

            try:
                report = diagnose_release_cluster_start(
                    state_root=selected_state,
                    port=args.port,
                    expected_port=args.expected_port,
                    report_root=diagnostic_report,
                    expected_instance_id_prefix=args.expected_instance_id_prefix,
                )
            except Exception as error:
                from app.core.postgres_runtime import _sanitize_postgres_output

                safe_message = _sanitize_postgres_output(str(error))[:1000]
                if args.report_file is None:
                    _report_window("ClassPilot PostgreSQL 受控診斷未完成", safe_message or type(error).__name__)
                return 1
            if args.report_file is None:
                _report_window(
                    "ClassPilot PostgreSQL 受控診斷完成",
                    f"{report.get('status')}；資料庫已正常停止。診斷報告：{diagnostic_report / 'diagnostic-report.json'}",
                )
            return 0
        if args.self_check:
            report = run_self_check()
            report["status"] = "ok"
            _safe_report(args.report_file, **report)
            if args.report_file is None:
                _report_window("ClassPilot 封裝自我檢查", "前端、後端、PostgreSQL payload 與 Alembic 資源已載入。")
            return 0
        if args.probe_postgres:
            if args.state_root is None:
                parser.error("--probe-postgres requires an explicitly selected data root.")
            report = run_postgres_executable_probe(args.state_root)
            _safe_report(args.report_file, **report)
            if args.report_file is None:
                _report_window(
                    "ClassPilot PostgreSQL 檢查",
                    "三個 PostgreSQL 執行檔均通過唯讀版本探測；未連線資料庫、未啟動 cluster、未執行 migration。",
                )
            return 0
        if args.stop:
            _bootstrap_paths()
            event_name = _user_event_name()
            stopped = _request_stop(event_name)
            _safe_report(args.report_file, status="stop-signalled" if stopped else "not-running")
            if args.report_file is None:
                _report_window("ClassPilot", "已通知服務停止。" if stopped else "ClassPilot 目前沒有執行中的服務。")
            return 0
        if args.setup:
            return run_control_panel()
        if args.update_check:
            _report_window(
                "ClassPilot 更新資訊",
                "目前沒有設定更新來源或安全更新流程；本版本不提供自動更新。請保留資料，等待經核准的安裝更新流程。",
            )
            return 0
        _bootstrap_paths()
        if args.provision_prototype:
            return _provision(args.state_root, args.report_file, args.postgres_zip)
        if args.isolated_first_setup_test:
            return _run_isolated_first_setup_test(
                args.state_root,
                args.postgres_zip,
                args.report_file,
                confirmed=args.confirm_isolated_temp,
            )
        if args.start:
            if args.state_root is None:
                parser.error("Prototype start requires the isolated --state-root reported during setup.")
            return _start(args.state_root, args.report_file, open_browser=not args.no_browser)
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        message = re.sub(r"(?i)(password|secret|postgres(?:ql)?://)[^\s,;]*", r"\1=<redacted>", message)
        report = {"status": "failed", "failed_check": _CURRENT_STAGE, "error_type": type(error).__name__, "error_message": message}
        _safe_report(args.report_file, **report)
        if args.report_file is None:
            _report_window("ClassPilot", message, error=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
