"""Isolated, per-user PostgreSQL runtime used by the Windows release prototype."""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import tempfile
from typing import Callable

from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Engine

from app.core.config import ReleaseSettings
from app.core.release_settings import ReleaseSettingsStore

PROTOTYPE_DATABASE = "classpilot_release_proto"
PROTOTYPE_APP_ROLE = "classpilot_app"
PROTOTYPE_ADMIN_ROLE = "classpilot_proto_admin"
MAX_PORT_ATTEMPTS = 12


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _OwnedProcessJob:
    """Kill only child processes assigned to this handle when owner exits."""

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    def __init__(self):
        if os.name != "nt":
            raise PrototypeRuntimeError("Owned process tracking requires Windows.")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self._kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self._kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        self._kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._handle = self._kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = self._kernel32.SetInformationJobObject(
            self._handle,
            self._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        if not self._kernel32.AssignProcessToJobObject(self._handle, process._handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if getattr(self, "_handle", None):
            self._kernel32.CloseHandle(self._handle)
            self._handle = None

    def __del__(self):
        self.close()


class PrototypeRuntimeError(RuntimeError):
    """A safe, user-facing failure with no connection details or credentials."""


_DLL_SEARCH_LOCK = threading.RLock()


class _ExternalWindowsProcess:
    """Restore the normal Windows DLL lookup while creating PostgreSQL children.

    PyInstaller sets the frozen parent's DLL directory to its private runtime.
    Windows children inherit that setting, so an external PostgreSQL executable
    can fail before main() if that directory shadows/masks its own dependencies.
    """

    def __enter__(self) -> "_ExternalWindowsProcess":
        self._lock_acquired = False
        self._changed = os.name == "nt" and bool(getattr(sys, "frozen", False))
        self._reset_succeeded: bool | None = None
        self._restore_succeeded: bool | None = None
        self._secondary_thread_count = max(0, sum(thread.is_alive() for thread in threading.enumerate()) - 1)
        if self._changed:
            current_thread = threading.current_thread()
            if any(
                thread.is_alive() and thread is not current_thread
                for thread in threading.enumerate()
            ):
                raise PrototypeRuntimeError(
                    "PostgreSQL executable launch was refused because another ClassPilot thread is active; "
                    "this Windows DLL search change is process-wide."
                )
            _DLL_SEARCH_LOCK.acquire()
            self._lock_acquired = True
            try:
                import ctypes

                self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                self._kernel32.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
                self._kernel32.SetDllDirectoryW.restype = ctypes.c_int
                if not self._kernel32.SetDllDirectoryW(None):
                    raise OSError(ctypes.get_last_error(), "SetDllDirectoryW failed")
                self._reset_succeeded = True
            except Exception:
                _DLL_SEARCH_LOCK.release()
                self._lock_acquired = False
                raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._changed:
                bundle_dir = getattr(sys, "_MEIPASS", None)
                if not bundle_dir:
                    raise OSError("The frozen application bundle directory is unavailable.")
                self._restore_succeeded = bool(self._kernel32.SetDllDirectoryW(str(bundle_dir)))
                if not self._restore_succeeded:
                    raise OSError("Could not restore the frozen application DLL directory.")
        finally:
            if self._lock_acquired:
                _DLL_SEARCH_LOCK.release()

    def diagnostic(self) -> dict[str, object]:
        bundle_dir = getattr(sys, "_MEIPASS", None)
        return {
            "parent_frozen": bool(getattr(sys, "frozen", False)),
            "bootloader_directory": str(bundle_dir) if bundle_dir else None,
            "set_dll_directory_reset_before_createprocess": self._reset_succeeded,
            "set_dll_directory_restored_after_createprocess": self._restore_succeeded,
            "child_search_state_at_createprocess": (
                "standard-windows-search" if self._changed and self._reset_succeeded
                else "inherited-pyinstaller-runtime" if self._changed
                else "standard-windows-search"
            ),
            "other_python_threads_at_launch": self._secondary_thread_count,
            "dll_search_change_is_process_wide": self._changed,
        }


def _win32_status(returncode: int) -> str:
    unsigned_code = returncode & 0xFFFFFFFF
    return f"{unsigned_code} (0x{unsigned_code:08X})"


def _sanitize_postgres_output(output: bytes | str) -> str:
    """Keep a short diagnostic excerpt while removing credential-shaped values."""
    text = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output
    text = re.sub(r"(?i)postgres(?:ql)?://[^\s\"']+", "postgresql://<redacted>", text)
    text = re.sub(
        r"(?i)\b(password|passwd|secret|token)\s*([=:])\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1\2<redacted>",
        text,
    )
    text = re.sub(r"(?i)--pwfile(?:=|\s+)\S+", "--pwfile=<protected-file>", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(line[:260] for line in lines[-4:])


def _classify_postgres_failure(returncode: int, output: bytes | str = "") -> tuple[str, str]:
    code = returncode & 0xFFFFFFFF
    if code == 0xC0000135:
        return (
            "dll-load-failure",
            "Windows 回報 STATUS_DLL_NOT_FOUND；執行檔或其遞迴相依 DLL 無法載入。",
        )
    folded = _sanitize_postgres_output(output).casefold()
    if "0xc0000135" in folded or "3221225781" in folded:
        return (
            "nested-child-dll-load-failure",
            "PostgreSQL 輸出指出其子程序回報 STATUS_DLL_NOT_FOUND；"
            "父程序 exit code 不足以定位實際缺少的 DLL。",
        )
    if any(token in folded for token in ("address already in use", "could not bind", "another server is already running")):
        return "port-conflict", "PostgreSQL 輸出指出監聽位址或連接埠衝突。"
    if any(token in folded for token in ("permission denied", "access is denied", "operation not permitted")):
        return "permission", "PostgreSQL 輸出指出檔案或目錄權限不足。"
    if any(token in folded for token in ("incompatible with this server", "database files are incompatible", "invalid checkpoint record")):
        return "cluster-version-or-format", "PostgreSQL 輸出指出 cluster 版本或格式不相容。"
    if any(token in folded for token in ("postmaster.pid", "lock file", "another server process")):
        return "existing-instance-or-lock", "PostgreSQL 輸出指出既有程序或鎖定檔。"
    if any(token in folded for token in ("no such file", "could not open file", "cannot open", "failed to load")):
        return "file-or-dependency", "PostgreSQL 輸出指出檔案或相依元件無法開啟。"
    if folded:
        return "postgresql-reported-error", "PostgreSQL 有回報錯誤；下方僅列出遮蔽後的摘要。"
    return "unclassified", "目前沒有足夠的程序輸出確認原因；exit code 本身不能判定根因。"


def _component_failure(
    component: str,
    returncode: int,
    *,
    stage: str,
    output: bytes | str = "",
) -> PrototypeRuntimeError:
    code = returncode & 0xFFFFFFFF
    category, detail = _classify_postgres_failure(code, output)
    summary = _sanitize_postgres_output(output)
    excerpt = f"安全摘要：{summary}" if summary else ""
    return PrototypeRuntimeError(
        f"PostgreSQL 階段 {stage} 的 {component} 失敗；"
        f"exit code {_win32_status(returncode)}；原因分類 {category}。{detail} {excerpt}"
    )


def _external_postgres_environment(bin_dir: Path) -> tuple[dict[str, str], dict[str, object]]:
    """Build one minimal, consistent child environment for all PostgreSQL tools."""
    env = os.environ.copy()
    removed_environment_keys = sorted(
        key for key in env
        if key.upper().startswith(("PG", "PYTHON", "CONDA", "NODE", "UV_"))
        or key.upper() == "VIRTUAL_ENV"
    )
    for key in removed_environment_keys:
        env.pop(key, None)

    windows_root = Path(env.get("SystemRoot") or env.get("WINDIR") or r"C:\Windows")
    system32 = windows_root / "System32"
    inherited_path = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    child_path = [str(bin_dir), str(system32), str(windows_root), str(system32 / "Wbem")]
    unique_path: list[str] = []
    seen: set[str] = set()
    for entry in child_path:
        normalized = ntpath.normcase(ntpath.normpath(entry))
        if normalized not in seen:
            unique_path.append(entry)
            seen.add(normalized)
    env["PATH"] = os.pathsep.join(unique_path)

    def label(entry: str) -> str:
        normalized = ntpath.normcase(ntpath.normpath(entry))
        if normalized == ntpath.normcase(ntpath.normpath(str(bin_dir))):
            return "postgresql-bin"
        if normalized == ntpath.normcase(ntpath.normpath(str(system32))):
            return "windows-system32"
        if normalized == ntpath.normcase(ntpath.normpath(str(windows_root))):
            return "windows-root"
        if normalized == ntpath.normcase(ntpath.normpath(str(system32 / "Wbem"))):
            return "windows-wbem"
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle and (normalized == ntpath.normcase(ntpath.normpath(str(bundle))) or normalized.startswith(ntpath.normcase(ntpath.normpath(str(bundle))) + "\\")):
            return "frozen-runtime"
        return "inherited-user-path"

    inherited_normalized = {ntpath.normcase(ntpath.normpath(entry)) for entry in inherited_path}
    child_normalized = {ntpath.normcase(ntpath.normpath(entry)) for entry in unique_path}
    removed_paths = [entry for entry in inherited_path if ntpath.normcase(ntpath.normpath(entry)) not in child_normalized]
    added_paths = [entry for entry in unique_path if ntpath.normcase(ntpath.normpath(entry)) not in inherited_normalized]
    return env, {
        "removed_environment_key_names": removed_environment_keys,
        "inherited_path_entry_count": len(inherited_path),
        "inherited_path_entry_classes": [label(entry) for entry in inherited_path],
        "child_path_entry_classes": [label(entry) for entry in unique_path],
        "removed_path_entry_count": len(removed_paths),
        "removed_path_entry_classes": [label(entry) for entry in removed_paths],
        "added_path_entry_classes": [label(entry) for entry in added_paths],
        "path_values_logged": False,
    }


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _windows_process_descendants(root_pid: int) -> list[dict[str, object]]:
    """Observe only descendants of a process started by this ClassPilot instance."""
    if os.name != "nt":
        return []
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        return []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        rows: dict[int, tuple[int, str]] = {}
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                rows[int(entry.th32ProcessID)] = (
                    int(entry.th32ParentProcessID),
                    Path(entry.szExeFile).name,
                )
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        descendants: list[dict[str, object]] = []
        frontier = [root_pid]
        visited = {root_pid}
        while frontier:
            parent_id = frontier.pop()
            for pid, (actual_parent, image) in rows.items():
                if actual_parent == parent_id and pid not in visited:
                    visited.add(pid)
                    frontier.append(pid)
                    descendants.append({"pid": pid, "parent_pid": actual_parent, "image": image})
        return descendants
    finally:
        kernel32.CloseHandle(snapshot)


def _write_process_diagnostic(state_root: Path | None, record: dict[str, object]) -> None:
    if state_root is None:
        return
    try:
        log_dir = state_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        safe_record = dict(record)
        if "safe_output_summary" in safe_record:
            safe_record["safe_output_summary"] = _sanitize_postgres_output(
                str(safe_record["safe_output_summary"])
            )
        if "build_id" not in safe_record:
            safe_record["build_id"] = _process_build_id()
        with (log_dir / "postgres-process-diagnostics.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(safe_record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except (OSError, TypeError, ValueError):
        # A diagnostic write failure must not mask the actual PostgreSQL result.
        return


def _process_diagnostic_rows(state_root: Path) -> list[dict[str, object]]:
    path = state_root / "logs" / "postgres-process-diagnostics.jsonl"
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _process_build_id() -> str:
    if not bool(getattr(sys, "frozen", False)):
        return "unfrozen-runtime"
    try:
        manifest = Path(getattr(sys, "_MEIPASS")) / "release_manifest.json"
        value = json.loads(manifest.read_text(encoding="utf-8"))
        build_id = value.get("build_id")
        return str(build_id) if isinstance(build_id, str) and build_id else "unavailable"
    except (AttributeError, OSError, json.JSONDecodeError, TypeError):
        return "unavailable"


def _postgres_log_summary(path: Path, *, start_offset: int = 0) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(max(0, start_offset))
            content = stream.read()
    except OSError:
        return ""
    return _sanitize_postgres_output(content)


def _spawn_postgres_process(
    executable: Path,
    arguments: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdin,
    stdout,
    stderr,
) -> subprocess.Popen[bytes]:
    """Common CreateProcess boundary for every PostgreSQL executable."""
    dll_search = _ExternalWindowsProcess()
    process: subprocess.Popen[bytes] | None = None
    try:
        with dll_search:
            process = subprocess.Popen(
                [str(executable), *arguments],
                cwd=cwd,
                env=env,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except Exception:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        raise
    assert process is not None
    process._classpilot_dll_search = dll_search.diagnostic()
    return process


def _execute_external_postgres_tool(
    executable: Path,
    arguments: list[str],
    *,
    env: dict[str, str],
    stage: str,
    state_root: Path | None = None,
    path_environment: dict[str, object] | None = None,
    cwd: Path | None = None,
    timeout: float = 180,
) -> subprocess.CompletedProcess[bytes]:
    """Launch an owned PostgreSQL tool, observe its process tree, and log safe facts."""
    started = time.monotonic()
    child_processes: dict[int, dict[str, object]] = {}
    process: subprocess.Popen[bytes] | None = None
    job: _OwnedProcessJob | None = None
    stdout = b""
    stderr = b""
    timed_out = False
    actual_cwd = cwd or executable.parent
    dll_search: dict[str, object] = {}
    try:
        process = _spawn_postgres_process(
            executable,
            arguments,
            cwd=actual_cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        dll_search = getattr(process, "_classpilot_dll_search", {})
        if os.name == "nt":
            job = _OwnedProcessJob()
            try:
                job.assign(process)
            except OSError:
                if process.poll() is not None:
                    job.close()
                    job = None
                else:
                    if job is not None:
                        job.close()
                        job = None
                    process.terminate()
                    process.wait(timeout=5)
                    raise PrototypeRuntimeError(
                        f"PostgreSQL 階段 {stage} 無法安全追蹤自有程序樹；元件 {executable.name}。"
                    ) from None
        deadline = time.monotonic() + timeout
        while True:
            for item in _windows_process_descendants(process.pid):
                child_processes[int(item["pid"])] = item
            try:
                stdout, stderr = process.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    timed_out = True
                    if job is not None:
                        job.close()
                        job = None
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    raise PrototypeRuntimeError(
                        f"PostgreSQL 階段 {stage} 的 {executable.name} 超過 {timeout:g} 秒；"
                        "只停止本次啟動且已追蹤的程序樹。"
                    ) from None
    except PrototypeRuntimeError:
        raise
    except OSError as error:
        raise PrototypeRuntimeError(
            f"PostgreSQL 階段 {stage} 執行檔啟動失敗；元件 {executable.name}；"
            f"作業系統錯誤類型 {type(error).__name__}。"
        ) from None
    finally:
        if job is not None:
            job.close()

    assert process is not None
    elapsed_ms = round((time.monotonic() - started) * 1000)
    combined = stdout + (b"\n" if stdout and stderr else b"") + stderr
    safe_output = _sanitize_postgres_output(combined)
    path_env = path_environment or {
        "child_path_entry_count": len([part for part in env.get("PATH", "").split(os.pathsep) if part]),
        "child_path_values_logged": False,
    }
    diagnostic = {
        "stage": stage,
        "component": executable.name,
        "executable": str(executable.resolve(strict=False)),
        "working_directory": str(actual_cwd.resolve(strict=False)),
        "parent_pid": os.getpid(),
        "parent_image": Path(sys.executable).name,
        "process_pid": process.pid,
        "child_processes": sorted(child_processes.values(), key=lambda item: int(item["pid"])),
        "return_code": process.returncode & 0xFFFFFFFF,
        "return_code_hex": f"0x{process.returncode & 0xFFFFFFFF:08X}",
        "duration_ms": elapsed_ms,
        "dll_search": dll_search,
        "path_environment": path_env,
        "safe_output_summary": safe_output,
        "output_values_logged": "redacted-and-truncated",
        "timed_out": timed_out,
    }
    _write_process_diagnostic(state_root, diagnostic)
    return subprocess.CompletedProcess([str(executable), *arguments], process.returncode, stdout, stderr)


@dataclass(frozen=True)
class PostgresPayload:
    root: Path

    @property
    def bin_dir(self) -> Path:
        return self.root / "bin"

    def executable(self, name: str) -> Path:
        path = self.bin_dir / name
        if not path.is_file():
            raise PrototypeRuntimeError(f"PostgreSQL component is missing: {name}.")
        return path


def _windows_user_sid() -> str:
    if os.name != "nt":
        raise PrototypeRuntimeError("The PostgreSQL release prototype requires Windows.")
    import ctypes.wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    token = ctypes.wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        needed = ctypes.wintypes.DWORD()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        sid_string = ctypes.wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(sid_pointer, ctypes.byref(sid_string)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return sid_string.value
        finally:
            kernel32.LocalFree(sid_string)
    finally:
        kernel32.CloseHandle(token)


def secure_directory(path: Path) -> None:
    """Restrict a dedicated runtime tree to the current user and SYSTEM."""
    path.mkdir(parents=True, exist_ok=True)
    sid = _windows_user_sid()
    command = [
        str(Path(os.environ["WINDIR"]) / "System32" / "icacls.exe"),
        str(path),
        "/inheritance:r",
        "/grant:r",
        f"*{sid}:(OI)(CI)F",
        "*S-1-5-18:(OI)(CI)F",
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        raise PrototypeRuntimeError("Could not restrict access to the private runtime directory.")


def available_loopback_port() -> int | None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(("127.0.0.1", 0))
        except OSError:
            return None
        return int(listener.getsockname()[1])


def _run_tool(
    executable: Path,
    arguments: list[str],
    *,
    env: dict[str, str],
    stage: str | None = None,
    state_root: Path | None = None,
    path_environment: dict[str, object] | None = None,
) -> None:
    actual_stage = stage or ("cluster-init" if executable.name.casefold() == "initdb.exe" else "process-start")
    completed = _execute_external_postgres_tool(
        executable,
        arguments,
        env=env,
        stage=actual_stage,
        state_root=state_root,
        path_environment=path_environment,
    )
    if completed.returncode != 0:
        raise _component_failure(
            executable.name,
            completed.returncode,
            stage=actual_stage,
            output=completed.stdout + b"\n" + completed.stderr,
        )


def _probe_executable_version(
    executable: Path,
    *,
    expected_version: str,
    state_root: Path | None = None,
) -> None:
    """Execute a read-only --version probe and reject incompatible binaries."""
    try:
        env, path_environment = _external_postgres_environment(executable.parent)
        completed = _execute_external_postgres_tool(
            executable,
            ["--version"],
            env=env,
            stage="executable-preflight",
            state_root=state_root,
            path_environment=path_environment,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        raise PrototypeRuntimeError(
            f"PostgreSQL 階段 executable-preflight 的 {executable.name} 版本探測逾時；"
            "尚未初始化或啟動資料庫。"
        ) from None
    except OSError as error:
        raise PrototypeRuntimeError(
            f"PostgreSQL 階段 executable-preflight 無法啟動 {executable.name}；"
            f"作業系統錯誤類型 {type(error).__name__}；尚未初始化或啟動資料庫。"
        ) from None
    if completed.returncode != 0:
        raise _component_failure(
            executable.name,
            completed.returncode,
            stage="executable-preflight",
            output=completed.stdout + b"\n" + completed.stderr,
        )
    output = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
    match = re.search(r"\bPostgreSQL\)\s+(\d+(?:\.\d+)+)\b", output)
    if not match or match.group(1) != expected_version:
        reported = match.group(1) if match else "unrecognized"
        raise PrototypeRuntimeError(
            f"PostgreSQL 階段 executable-preflight 的 {executable.name} 版本不符；"
            f"預期 {expected_version}、實際 {reported}；尚未初始化或啟動資料庫。"
        )


def diagnose_release_cluster_start(
    *,
    state_root: Path,
    port: int,
    report_root: Path,
    expected_instance_id_prefix: str,
    expected_port: int,
    readiness_timeout: float = 30,
) -> dict[str, object]:
    """Start and normally stop only one explicitly identified release cluster.

    This deliberately does not use ``start_existing``: that method is the
    application startup path and may select/save a port and probe the database.
    This diagnostic performs no SQL and never writes into the selected state
    root; all diagnostic reports and executable-probe logs go to the explicit
    external temporary report directory.
    """
    from app.core.release_setup import (
        RELEASE_ADMIN_ROLE,
        RELEASE_APP_ROLE,
        RELEASE_DATABASE,
        RELEASE_OWNER_KIND,
        REQUIRED_REVISION,
    )
    from app.core.release_settings import ReleaseSettingsDecryptionError, ReleaseSettingsFormatError
    from app.core.runtime import RUNTIME_PATHS

    root = state_root.expanduser().resolve(strict=True)
    report = report_root.expanduser().resolve(strict=False)
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not root.is_dir() or root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise PrototypeRuntimeError("診斷停止：指定資料根目錄不是可確認的實體資料夾。")
    if report == root or root in report.parents or report == temp_root or temp_root not in report.parents:
        raise PrototypeRuntimeError("診斷停止：報告目錄必須位於資料根目錄以外的暫存子目錄。")
    if port != expected_port or not (1 <= port <= 65535):
        raise PrototypeRuntimeError("診斷停止：指定 port 與核准的固定 port 不符。")
    if not re.fullmatch(r"[0-9a-f]{8,48}", expected_instance_id_prefix):
        raise PrototypeRuntimeError("診斷停止：實例識別前綴格式無效。")

    report.mkdir(parents=True, exist_ok=False)
    report_record: dict[str, object] = {
        "mode": "postgresql-only-existing-cluster-diagnostic",
        "build_id": _process_build_id(),
        "stage": "identity-preflight",
        "state_root": str(root),
        "port": port,
        "listener": "127.0.0.1",
        "sql": "not-run",
        "migration": "not-run",
        "api": "not-started",
        "settings_write": "not-performed",
        "cluster_write_scope": "PostgreSQL runtime writes may occur during start/stop",
    }

    def write_report() -> None:
        safe = dict(report_record)
        if isinstance(safe.get("safe_output_summary"), str):
            safe["safe_output_summary"] = _sanitize_postgres_output(str(safe["safe_output_summary"]))
        (report / "diagnostic-report.json").write_text(
            json.dumps(safe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    try:
        manifest_path = RUNTIME_PATHS.resource_root / "release_manifest.json"
        spec_path = RUNTIME_PATHS.resource_root / "postgresql_payload_spec.json"
        try:
            release_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise PrototypeRuntimeError("診斷停止：封裝版本或 PostgreSQL 規格資源無法讀取。") from None
        report_record["build_id"] = release_manifest.get("build_id", "unavailable")
        if release_manifest.get("required_alembic_revision") != REQUIRED_REVISION:
            raise PrototypeRuntimeError("診斷停止：封裝指定的 schema revision 不符。")

        state_path = root / "setup-state.json"
        owner_path = root / "instance-owner.json"
        try:
            setup = json.loads(state_path.read_text(encoding="utf-8"))
            owner = json.loads(owner_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise PrototypeRuntimeError("診斷停止：完成狀態或實例所有權資料無法讀取。") from None
        instance_id = setup.get("instance_id") if isinstance(setup, dict) else None
        if (
            not isinstance(setup, dict)
            or not isinstance(owner, dict)
            or setup.get("format") != 1
            or setup.get("product") != "ClassPilot"
            or setup.get("status") != "complete"
            or setup.get("revision") != REQUIRED_REVISION
            or not isinstance(instance_id, str)
            or not re.fullmatch(r"[0-9a-f]{48}", instance_id)
            or not instance_id.startswith(expected_instance_id_prefix)
            or owner != {"kind": RELEASE_OWNER_KIND, "instance_id": instance_id}
        ):
            raise PrototypeRuntimeError("診斷停止：資料根目錄的 ClassPilot 實例識別或完成狀態不符。")
        report_record["instance_id_prefix"] = instance_id[:8]
        report_record["setup_revision"] = setup.get("revision")

        payload = PostgresPayload(root / "runtime" / "postgresql")
        data_dir = root / "data" / "postgresql"
        for protected_path in (root / "runtime", payload.root, data_dir.parent, data_dir):
            if protected_path.exists() and (
                protected_path.is_symlink()
                or getattr(protected_path, "is_junction", lambda: False)()
            ):
                raise PrototypeRuntimeError("診斷停止：資料或 payload 路徑包含連結／junction；未啟動程序。")
        required = ("initdb.exe", "postgres.exe", "pg_ctl.exe")
        for name in required:
            payload.executable(name)
        try:
            payload_manifest = json.loads((payload.root / "payload-manifest.json").read_text(encoding="utf-8"))
            pg_version = (data_dir / "PG_VERSION").read_text(encoding="ascii").strip()
        except (OSError, json.JSONDecodeError, UnicodeError):
            raise PrototypeRuntimeError("診斷停止：PostgreSQL payload manifest 或 cluster 版本檔無法讀取。") from None
        if (
            spec.get("version") != "18.6"
            or spec.get("package_revision") != "5"
            or spec.get("architecture") != "windows-x64"
            or payload_manifest.get("version") != spec.get("version")
            or payload_manifest.get("package_revision") != spec.get("package_revision")
            or payload_manifest.get("architecture") != spec.get("architecture")
            or payload_manifest.get("archive_sha256") != spec.get("sha256")
            or pg_version != "18"
            or not data_dir.is_dir()
        ):
            raise PrototypeRuntimeError("診斷停止：PostgreSQL payload 或 cluster 版本／架構不符。")
        report_record["postgresql"] = {
            "version": payload_manifest.get("version"),
            "package_revision": payload_manifest.get("package_revision"),
            "architecture": payload_manifest.get("architecture"),
            "cluster_major": pg_version,
            "binary_sha256": {
                name: _sha256_file(payload.executable(name))
                for name in required
            },
        }

        settings_store = ReleaseSettingsStore(root / "config" / "settings.dpapi")
        try:
            settings = settings_store.load()
        except ReleaseSettingsDecryptionError:
            raise PrototypeRuntimeError("診斷停止：目前 Windows 使用者無法解密受保護設定；沒有覆寫設定。") from None
        except ReleaseSettingsFormatError:
            raise PrototypeRuntimeError("診斷停止：受保護設定缺少或格式不相容；沒有修改設定。") from None
        if (
            settings.postgres_host != "127.0.0.1"
            or settings.postgres_port != port
            or settings.postgres_db != RELEASE_DATABASE
            or settings.postgres_user != RELEASE_APP_ROLE
        ):
            raise PrototypeRuntimeError("診斷停止：受保護設定中的目標資料庫或固定 port 不符。")
        report_record["settings_target"] = {
            "host": settings.postgres_host,
            "port": settings.postgres_port,
            "database": settings.postgres_db,
            "role": settings.postgres_user,
            "credentials_logged": False,
        }

        running_marker = root / "running-instance.json"
        if running_marker.exists():
            try:
                running_info = json.loads(running_marker.read_text(encoding="utf-8"))
                running_pid = int(running_info["pid"])
                running_executable = str(Path(running_info["executable"]).resolve(strict=False))
                marker_valid = (
                    isinstance(running_info, dict)
                    and running_info.get("host") == "127.0.0.1"
                    and 1 <= int(running_info["port"]) <= 65535
                )
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                raise PrototypeRuntimeError("診斷停止：ClassPilot 執行狀態記錄存在但無法確認；未啟動 cluster。") from None
            running = _windows_process_is_alive(running_pid)
            image = _windows_process_image(running_pid) if running else None
            report_record["classpilot_running_marker"] = {
                "pid": running_pid,
                "marker_valid": marker_valid,
                "process_alive": running,
                "image_matches_marker": bool(image and image.casefold() == running_executable.casefold()),
                "action": "none",
            }
            if running or image is None or not marker_valid:
                raise PrototypeRuntimeError("診斷停止：ClassPilot 服務正在運行，或既有程序狀態無法可靠確認。")
            raise PrototypeRuntimeError("診斷停止：有未清除的 ClassPilot 執行狀態記錄；本模式不修改它。")

        other_controls = _windows_processes_named("ClassPilot.exe", exclude_pid=os.getpid())
        if other_controls:
            report_record["other_classpilot_controls"] = other_controls
            raise PrototypeRuntimeError("診斷停止：發現其他 ClassPilot 控制程式；不啟動第二份 cluster。")

        pid_file = data_dir / "postmaster.pid"
        if pid_file.exists():
            try:
                lines = pid_file.read_text(encoding="ascii").splitlines()
                pid = int(lines[0])
            except (OSError, ValueError, IndexError):
                raise PrototypeRuntimeError("診斷停止：cluster 有無法確認的 postmaster.pid；未嘗試啟動。") from None
            alive = _windows_process_is_alive(pid)
            image = _windows_process_image(pid) if alive else None
            report_record["existing_cluster_process"] = {
                "pid": pid,
                "alive": alive,
                "image_matches_payload": bool(image and image.casefold() == str(payload.executable("postgres.exe").resolve()).casefold()),
                "action": "none",
            }
            if alive or image is None:
                raise PrototypeRuntimeError("診斷停止：cluster 已運行或程序所有權無法可靠確認；未啟動第二份程序。")
            raise PrototypeRuntimeError("診斷停止：cluster 留有既有 postmaster.pid；本模式不刪除或修復它。")

        # A process from this exact payload is ambiguous even if the PID file is
        # absent. Fail closed instead of trying to infer which data directory it owns.
        matching_processes = _windows_processes_for_image(payload.executable("postgres.exe"))
        if matching_processes:
            report_record["matching_payload_processes"] = matching_processes
            raise PrototypeRuntimeError("診斷停止：發現使用相同 PostgreSQL payload 的運行程序，無法確認其資料目錄。")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind(("127.0.0.1", port))
        except OSError:
            raise PrototypeRuntimeError("診斷停止：固定 loopback port 已被佔用或不可用；沒有改用其他 port。") from None

        bin_dir = payload.bin_dir.resolve(strict=True)
        for name in required:
            _probe_executable_version(
                payload.executable(name), expected_version=str(spec["version"]), state_root=report
            )
        env, path_environment = _external_postgres_environment(bin_dir)
        report_record["stage"] = "existing-cluster-start"
        report_record["executable"] = str(payload.executable("postgres.exe").resolve())
        report_record["working_directory"] = str(bin_dir)
        report_record["path_environment"] = path_environment
        report_record["parent_pid"] = os.getpid()
        process = _spawn_postgres_process(
            payload.executable("postgres.exe"),
            ["-D", str(data_dir.resolve(strict=True)), "-h", "127.0.0.1", "-p", str(port)],
            cwd=bin_dir,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        report_record["process_pid"] = process.pid
        report_record["dll_search"] = getattr(process, "_classpilot_dll_search", {})
        output_chunks: list[bytes] = []
        output_lock = threading.Lock()

        def drain(stream) -> None:
            try:
                while True:
                    chunk = stream.read1(4096)
                    if not chunk:
                        return
                    with output_lock:
                        output_chunks.append(chunk)
                        total = sum(len(item) for item in output_chunks)
                        while total > 65536 and output_chunks:
                            total -= len(output_chunks.pop(0))
            except OSError:
                return

        drain_threads = [
            threading.Thread(target=drain, args=(process.stdout,), daemon=True),
            threading.Thread(target=drain, args=(process.stderr,), daemon=True),
        ]
        for thread in drain_threads:
            thread.start()

        deadline = time.monotonic() + readiness_timeout
        ready = False
        while time.monotonic() < deadline and process.poll() is None:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    pass
                try:
                    pid_lines = pid_file.read_text(encoding="ascii").splitlines()
                    ready = (
                        int(pid_lines[0]) == process.pid
                        and Path(pid_lines[1]).resolve() == data_dir.resolve()
                        and len(pid_lines) > 3
                        and int(pid_lines[3]) == port
                    )
                except (OSError, ValueError, IndexError):
                    ready = False
                if ready:
                    break
            except OSError:
                time.sleep(0.2)

        if not ready:
            report_record["return_code"] = process.poll()
            if process.poll() is None:
                report_record["stage"] = "diagnostic-stop-after-readiness-failure"
                stop = _execute_external_postgres_tool(
                    payload.executable("pg_ctl.exe"),
                    ["stop", "-D", str(data_dir), "-m", "fast", "-w", "-t", "20"],
                    cwd=bin_dir,
                    env=env,
                    stage="diagnostic-stop-after-readiness-failure",
                    state_root=report,
                    path_environment=path_environment,
                    timeout=30,
                )
                report_record["normal_stop_return_code"] = stop.returncode
                if stop.returncode == 0:
                    process.wait(timeout=25)
                else:
                    report_record["owned_process_still_running"] = process.poll() is None
                    raise _component_failure("pg_ctl.exe", stop.returncode, stage="diagnostic-stop-after-readiness-failure", output=stop.stdout + b"\n" + stop.stderr)
            for thread in drain_threads:
                thread.join(timeout=1)
            with output_lock:
                combined = b"\n".join(output_chunks)
            report_record["safe_output_summary"] = _sanitize_postgres_output(combined)
            if process.poll() is not None:
                report_record["return_code"] = process.returncode & 0xFFFFFFFF
                category, reason = _classify_postgres_failure(process.returncode, combined)
                report_record["failure_category"] = category
                report_record["failure_reason"] = reason
            report_record["stage"] = "startup-failed"
            report_record["status"] = "failed"
            report_record["owned_process_still_running"] = process.poll() is None
            write_report()
            raise PrototypeRuntimeError(
                f"PostgreSQL 診斷啟動未就緒；階段 {report_record.get('stage')}；"
                f"原因 {report_record.get('failure_category', 'readiness-timeout')}；"
                f"安全摘要：{report_record.get('safe_output_summary') or '沒有可用的 PostgreSQL 錯誤輸出。'}；"
                f"自有程序仍運行：{report_record['owned_process_still_running']}。"
            )

        report_record["status"] = "started"
        report_record["stage"] = "diagnostic-normal-stop"
        report_record["postmaster_identity"] = "pid, executable, data path and fixed port matched"
        stop = _execute_external_postgres_tool(
            payload.executable("pg_ctl.exe"),
            ["stop", "-D", str(data_dir), "-m", "fast", "-w", "-t", "20"],
            cwd=bin_dir,
            env=env,
            stage="diagnostic-normal-stop",
            state_root=report,
            path_environment=path_environment,
            timeout=30,
        )
        report_record["stop_return_code"] = stop.returncode
        report_record["safe_output_summary"] = _sanitize_postgres_output(stop.stdout + b"\n" + stop.stderr)
        if stop.returncode != 0:
            report_record["status"] = "stop-failed"
            report_record["owned_process_still_running"] = process.poll() is None
            write_report()
            raise _component_failure("pg_ctl.exe", stop.returncode, stage="diagnostic-normal-stop", output=stop.stdout + b"\n" + stop.stderr)
        try:
            process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            report_record["status"] = "stop-timeout"
            report_record["owned_process_still_running"] = process.poll() is None
            write_report()
            raise PrototypeRuntimeError("PostgreSQL 正常停止逾時；未強制終止自有程序。") from None
        for thread in drain_threads:
            thread.join(timeout=1)
        with output_lock:
            combined = b"\n".join(output_chunks)
        report_record["safe_output_summary"] = _sanitize_postgres_output(combined + b"\n" + stop.stdout + b"\n" + stop.stderr)
        report_record["status"] = "passed"
        report_record["stage"] = "complete"
        report_record["return_code"] = process.returncode
        report_record["owned_process_still_running"] = False
        report_record["postmaster_pid_file_after_stop"] = pid_file.exists()
        if pid_file.exists():
            report_record["status"] = "stop-incomplete"
            raise PrototypeRuntimeError("PostgreSQL 程序已停止，但 postmaster.pid 仍存在；未刪除任何檔案。")
        write_report()
        return report_record
    except Exception:
        if not (report / "diagnostic-report.json").exists():
            report_record.setdefault("status", "preflight-failed")
            report_record.setdefault("safe_error_type", "PrototypeRuntimeError")
            import sys as _sys

            current_error = _sys.exc_info()[1]
            if current_error is not None:
                report_record["safe_failure_summary"] = _sanitize_postgres_output(str(current_error))[:1000]
            write_report()
        raise


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _windows_processes_for_image(executable: Path) -> list[dict[str, object]]:
    """Return matching postgres.exe PIDs and paths; never inspect command lines."""
    if os.name != "nt":
        return []
    # Toolhelp provides process ancestry and image names; exact image paths are
    # queried only for matching postgres.exe entries.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        return [{"status": "process-enumeration-unavailable"}]
    matches: list[dict[str, object]] = []
    expected = str(executable.resolve()).casefold()
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                if Path(entry.szExeFile).name.casefold() == "postgres.exe":
                    image = _windows_process_image(int(entry.th32ProcessID))
                    if image is None:
                        matches.append({"pid": int(entry.th32ProcessID), "status": "image-unavailable"})
                    elif image.casefold() == expected:
                        matches.append({"pid": int(entry.th32ProcessID), "image_matches_payload": True})
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        return matches
    finally:
        kernel32.CloseHandle(snapshot)


def _windows_processes_named(name: str, *, exclude_pid: int) -> list[dict[str, object]]:
    """Find same-named Windows processes without reading their command lines."""
    if os.name != "nt":
        return []
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        return [{"status": "process-enumeration-unavailable"}]
    matches: list[dict[str, object]] = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                pid = int(entry.th32ProcessID)
                if pid != exclude_pid and Path(entry.szExeFile).name.casefold() == name.casefold():
                    image = _windows_process_image(pid)
                    if image is None:
                        matches.append({"pid": pid, "status": "image-unavailable"})
                    else:
                        matches.append({"pid": pid, "image": image})
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        return matches
    finally:
        kernel32.CloseHandle(snapshot)


class PostgresPrototype:
    """Manage only the cluster rooted in the explicitly supplied private path."""

    def __init__(
        self,
        payload: PostgresPayload,
        state_root: Path,
        settings_store: ReleaseSettingsStore,
        *,
        on_stage: Callable[[str], None] | None = None,
        database_name: str = PROTOTYPE_DATABASE,
        app_role: str = PROTOTYPE_APP_ROLE,
        admin_role: str = PROTOTYPE_ADMIN_ROLE,
        owner_kind: str = "ClassPilot-PostgreSQL-prototype",
        instance_id: str | None = None,
    ):
        self.payload = payload
        self.state_root = state_root.resolve()
        self.data_dir = self.state_root / "data" / "postgresql"
        self.settings_store = settings_store
        self.on_stage = on_stage or (lambda _: None)
        self.database_name = database_name
        self.app_role = app_role
        self.admin_role = admin_role
        self.owner_kind = owner_kind
        self.instance_id = instance_id
        self.log_dir = self.state_root / "logs"
        self._job: _OwnedProcessJob | None = None

    def _verify_payload(self) -> None:
        for executable in ("initdb.exe", "pg_ctl.exe", "postgres.exe"):
            self.payload.executable(executable)
        from app.core.postgres_payload_import import PayloadSpec
        from app.core.runtime import RUNTIME_PATHS

        spec_path = RUNTIME_PATHS.resource_root / "postgresql_payload_spec.json"
        manifest_path = self.payload.root / "payload-manifest.json"
        try:
            spec = PayloadSpec.from_mapping(
                json.loads(spec_path.read_text(encoding="utf-8"))
            )
            payload_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raise PrototypeRuntimeError(
                "The imported PostgreSQL payload record is missing or invalid."
            ) from None
        if (
            payload_manifest.get("version") != spec.version
            or payload_manifest.get("package_revision") != spec.package_revision
            or payload_manifest.get("architecture") != spec.architecture
            or payload_manifest.get("archive_sha256") != spec.sha256
        ):
            raise PrototypeRuntimeError(
                "The imported PostgreSQL payload does not match the supported release."
            )
        runtime_dir = Path(os.environ.get("WINDIR", "C:\\Windows")) / "System32"
        missing_runtime = [
            name for name in spec.required_runtime_files if not (runtime_dir / name).is_file()
        ]
        if missing_runtime:
            raise PrototypeRuntimeError(
                "PostgreSQL runtime preflight failed: required system component(s) "
                + ", ".join(missing_runtime)
                + " are missing. ClassPilot does not install system runtimes."
            )
        # Checking a named DLL on disk does not prove Windows can load every
        # executable and transitive dependency. Probe before touching data.
        for executable in ("initdb.exe", "postgres.exe", "pg_ctl.exe"):
            _probe_executable_version(
                self.payload.executable(executable),
                expected_version=spec.version,
                state_root=self.state_root,
            )

    def _verify_owner_marker(self) -> None:
        marker = self.state_root / "instance-owner.json"
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise PrototypeRuntimeError("The private PostgreSQL ownership record is missing or invalid.") from None
        if (
            value.get("kind") != self.owner_kind
            or not isinstance(value.get("instance_id"), str)
            or not re.fullmatch(r"[0-9a-f]{48}", value["instance_id"])
        ):
            raise PrototypeRuntimeError("The private PostgreSQL ownership record is invalid.")

    def provision(self, *, migrate: Callable[[ReleaseSettings], None]) -> ReleaseSettings:
        """Create only a fresh prototype cluster and explicitly migrate its database."""
        self._verify_payload()
        resume_empty_data_dir = False
        marker = self.state_root / "instance-owner.json"
        if self.data_dir.exists():
            try:
                resume_empty_data_dir = (
                    self.data_dir.is_dir()
                    and not self.data_dir.is_symlink()
                    and not getattr(self.data_dir, "is_junction", lambda: False)()
                    and next(self.data_dir.iterdir(), None) is None
                    and self.instance_id is not None
                    and json.loads(marker.read_text(encoding="utf-8"))
                    == {"kind": self.owner_kind, "instance_id": self.instance_id}
                    and not self.settings_store.settings_file.exists()
                )
            except (OSError, ValueError, TypeError):
                resume_empty_data_dir = False
            if not resume_empty_data_dir:
                raise PrototypeRuntimeError(
                    "The prototype data directory is not an empty, matching cluster-init attempt; refusing to initialize or alter it."
                )
        elif marker.exists() or self.settings_store.settings_file.exists():
            raise PrototypeRuntimeError("Prototype state already exists; refusing to overwrite it.")
        secure_directory(self.state_root)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        instance_id = self.instance_id or secrets.token_hex(24)
        if not resume_empty_data_dir:
            self.data_dir.parent.mkdir(parents=True, exist_ok=True)
            self.data_dir.mkdir()
            marker.write_text(
                json.dumps({"kind": self.owner_kind, "instance_id": instance_id}),
                encoding="utf-8",
            )

        admin_password = secrets.token_urlsafe(48)
        app_password = secrets.token_urlsafe(48)
        password_file = self.state_root / "config" / "bootstrap-password.tmp"
        password_file.parent.mkdir(parents=True, exist_ok=True)
        password_file.write_text(admin_password + "\n", encoding="utf-8")
        try:
            secure_directory(self.state_root)
            self.on_stage("cluster-init")
            env, self._process_path_environment = _external_postgres_environment(self.payload.bin_dir)
            self._run_initdb(password_file, env)
        finally:
            password_file.unlink(missing_ok=True)

        port = self._start_new_cluster(admin_password, env)
        settings = ReleaseSettings(
            postgres_host="127.0.0.1",
            postgres_port=port,
            postgres_user=self.app_role,
            postgres_password=app_password,
            postgres_db=self.database_name,
        )
        try:
            self.on_stage("create-prototype-roles")
            self._create_application_database(port, admin_password, app_password)
            self.settings_store.save(settings)
            self.on_stage("app-role-smoke")
            self._app_role_smoke(settings)
            self.on_stage("explicit-migration")
            self._verify_database(settings)
            migrate(settings)
            self._verify_migrated_schema(settings)
        except Exception:
            self.stop_owned_cluster()
            raise
        return settings

    def _run_initdb(self, password_file: Path, env: dict[str, str]) -> None:
        args = [
            "-D",
            str(self.data_dir),
            "-U",
            self.admin_role,
            "--encoding=UTF8",
            "--locale-provider=icu",
            "--icu-locale=zh-Hant-HK",
            "--auth-local=scram-sha-256",
            "--auth-host=scram-sha-256",
            f"--pwfile={password_file}",
        ]
        _run_tool(
            self.payload.executable("initdb.exe"),
            args,
            env=env,
            stage="cluster-init",
            state_root=self.state_root,
            path_environment=getattr(self, "_process_path_environment", None),
        )
        config_path = self.data_dir / "postgresql.conf"
        with config_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write("\nlisten_addresses = '127.0.0.1'\n")
            stream.write("password_encryption = 'scram-sha-256'\n")
            stream.write("logging_collector = off\n")
        hba_path = self.data_dir / "pg_hba.conf"
        hba_path.write_text(
            "# ClassPilot private prototype: IPv4 loopback only, SCRAM password auth.\n"
            "host all all 127.0.0.1/32 scram-sha-256\n",
            encoding="utf-8",
            newline="\n",
        )

    def _start_new_cluster(self, admin_password: str, env: dict[str, str]) -> int:
        # All retries target our one newly-created data directory. No process is
        # queried, stopped, or reconfigured when a candidate is occupied.
        for _ in range(MAX_PORT_ATTEMPTS):
            port = available_loopback_port()
            if port is None:
                raise PrototypeRuntimeError("Could not bind a loopback port in this environment.")
            self.on_stage("postgres-start")
            log_file = self.log_dir / "postgresql-prototype.log"
            stream = log_file.open("ab")
            log_start = stream.tell()
            env, path_environment = _external_postgres_environment(self.payload.bin_dir)
            try:
                process = _spawn_postgres_process(
                    self.payload.executable("postgres.exe"),
                    ["-D", str(self.data_dir), "-h", "127.0.0.1", "-p", str(port)],
                    cwd=self.payload.bin_dir,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=stream,
                )
            except OSError as error:
                stream.close()
                raise PrototypeRuntimeError(
                    f"Could not start the isolated PostgreSQL process ({type(error).__name__})."
                ) from None
            try:
                if self._job is None:
                    self._job = _OwnedProcessJob()
                self._job.assign(process)
            except OSError as error:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if self._job is not None:
                    self._job.close()
                    self._job = None
                raise PrototypeRuntimeError(
                    f"Could not bind the private PostgreSQL process to its owner ({type(error).__name__})."
                ) from None
            stream.close()
            process_started = time.monotonic()
            child_processes: dict[int, dict[str, object]] = {}
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and process.poll() is None:
                for item in _windows_process_descendants(process.pid):
                    child_processes[int(item["pid"])] = item
                if self._admin_can_connect(port, admin_password):
                    self._process = process
                    self._port = port
                    _write_process_diagnostic(
                        self.state_root,
                        {
                            "stage": "postgres-start",
                            "component": "postgres.exe",
                            "executable": str(self.payload.executable("postgres.exe").resolve(strict=False)),
                            "working_directory": str(self.payload.bin_dir.resolve(strict=False)),
                            "parent_pid": os.getpid(),
                            "parent_image": Path(sys.executable).name,
                            "process_pid": process.pid,
                            "child_processes": sorted(child_processes.values(), key=lambda item: int(item["pid"])),
                            "return_code": None,
                            "duration_ms": round((time.monotonic() - process_started) * 1000),
                            "dll_search": getattr(process, "_classpilot_dll_search", {}),
                            "path_environment": path_environment,
                            "safe_output_summary": "",
                            "output_values_logged": "redacted-and-truncated",
                            "timed_out": False,
                        },
                    )
                    return port
                time.sleep(0.2)
            if process.poll() is not None and process.returncode not in (None, 0):
                summary = _postgres_log_summary(log_file, start_offset=log_start)
                category, _ = _classify_postgres_failure(process.returncode, summary)
                _write_process_diagnostic(
                    self.state_root,
                    {
                        "stage": "postgres-start",
                        "component": "postgres.exe",
                        "executable": str(self.payload.executable("postgres.exe").resolve(strict=False)),
                        "working_directory": str(self.payload.bin_dir.resolve(strict=False)),
                        "parent_pid": os.getpid(),
                        "parent_image": Path(sys.executable).name,
                        "process_pid": process.pid,
                        "child_processes": sorted(child_processes.values(), key=lambda item: int(item["pid"])),
                        "return_code": process.returncode & 0xFFFFFFFF,
                        "return_code_hex": f"0x{process.returncode & 0xFFFFFFFF:08X}",
                        "duration_ms": round((time.monotonic() - process_started) * 1000),
                        "failure_category": category,
                        "dll_search": getattr(process, "_classpilot_dll_search", {}),
                        "path_environment": path_environment,
                        "safe_output_summary": summary,
                        "output_values_logged": "redacted-and-truncated",
                        "timed_out": False,
                    },
                )
                if category == "port-conflict":
                    self._job.close()
                    self._job = None
                    continue
                raise _component_failure(
                    "postgres.exe", process.returncode, stage="postgres-start", output=summary
                )
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        failure = getattr(self, "_last_admin_connect_failure", ("unknown", None))
        code_text = f", operating-system code {failure[1]}" if failure[1] is not None else ""
        raise PrototypeRuntimeError(
            "PostgreSQL started but the private readiness connection did not succeed after "
            f"{MAX_PORT_ATTEMPTS} loopback attempts (connection error type {failure[0]}{code_text})."
        )

    def _connect_url(self, *, port: int, user: str, password: str, database: str) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=user,
            password=password,
            host="127.0.0.1",
            port=port,
            database=database,
            query={"connect_timeout": "2"},
        )

    def _admin_can_connect(self, port: int, password: str) -> bool:
        engine = None
        try:
            engine = create_engine(
                self._connect_url(port=port, user=self.admin_role, password=password, database="postgres"),
                pool_pre_ping=True,
            )
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except Exception as error:
            original = getattr(error, "orig", error)
            error_code = getattr(original, "winerror", None)
            if error_code is None:
                error_code = getattr(original, "errno", None)
            self._last_admin_connect_failure = (
                type(error).__name__,
                error_code if isinstance(error_code, int) else None,
            )
            return False
        finally:
            if engine is not None:
                engine.dispose()

    def _create_application_database(self, port: int, admin_password: str, app_password: str) -> None:
        from psycopg import connect, sql

        connection = connect(
            host="127.0.0.1",
            port=port,
            dbname="postgres",
            user=self.admin_role,
            password=admin_password,
            connect_timeout=3,
        )
        try:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                        sql.Identifier(self.app_role),
                        sql.Literal(app_password),
                    )
                )
                cursor.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(self.database_name), sql.Identifier(self.app_role)
                    )
                )
        finally:
            connection.close()

    def _app_role_smoke(self, settings: ReleaseSettings) -> None:
        engine = create_engine(settings.database_url())
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("CREATE TABLE IF NOT EXISTS _classpilot_runtime_smoke (id integer primary key, value text not null)")
                )
                connection.execute(
                    text("INSERT INTO _classpilot_runtime_smoke (id, value) VALUES (1, 'synthetic')")
                )
                value = connection.execute(
                    text("SELECT value FROM _classpilot_runtime_smoke WHERE id = 1")
                ).scalar_one()
                if value != "synthetic":
                    raise PrototypeRuntimeError("The application database role could not read its test row.")
                connection.execute(text("DELETE FROM _classpilot_runtime_smoke WHERE id = 1"))
        except PrototypeRuntimeError:
            raise
        except Exception as error:
            raise PrototypeRuntimeError(
                f"The application database role read/write check failed ({type(error).__name__})."
            ) from None
        finally:
            engine.dispose()

    def _verify_database(self, settings: ReleaseSettings) -> None:
        engine = create_engine(settings.database_url())
        try:
            with engine.connect() as connection:
                actual = connection.execute(text("SELECT current_database()")).scalar_one()
                if actual != self.database_name or actual in {
                    "steam_classroom_dev",
                    "steam_classroom_test",
                }:
                    raise PrototypeRuntimeError("Refusing migration: prototype database identity did not match.")
                revision_table = connection.execute(
                    text("SELECT to_regclass('public.alembic_version')")
                ).scalar_one()
                if revision_table is not None:
                    raise PrototypeRuntimeError("Refusing migration: prototype database already has a revision.")
        except PrototypeRuntimeError:
            raise
        except Exception as error:
            raise PrototypeRuntimeError(
                f"Prototype database preflight failed ({type(error).__name__})."
            ) from None
        finally:
            engine.dispose()

    def _verify_migrated_schema(self, settings: ReleaseSettings) -> None:
        required = {
            "students": {
                "gender",
                "support_need_status",
                "support_need_tags",
                "support_notes",
                "general_notes",
                "is_archived",
            },
            "lesson_sessions": {"id", "session_date", "start_time", "end_time", "course_type", "topic"},
            "session_participants": {"id", "session_id", "student_id", "attendance_status", "evaluation"},
        }
        engine = create_engine(settings.database_url())
        try:
            with engine.connect() as connection:
                db_name = connection.execute(text("SELECT current_database()")).scalar_one()
                revision = connection.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one()
                if db_name != self.database_name or revision != "20261019_0004":
                    raise PrototypeRuntimeError("Prototype migration revision verification failed.")
                for table, expected_columns in required.items():
                    columns = set(
                        connection.execute(
                            text("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:name"),
                            {"name": table},
                        ).scalars()
                    )
                    if not expected_columns.issubset(columns):
                        raise PrototypeRuntimeError(f"Prototype schema verification failed for {table}.")
        finally:
            engine.dispose()

    def start_existing(
        self,
        settings: ReleaseSettings,
        *,
        persist_settings: bool = True,
    ) -> tuple[ReleaseSettings, subprocess.Popen[bytes]]:
        """Start a previously provisioned cluster without running migrations."""
        self._verify_payload()
        self._verify_owner_marker()
        if not self.data_dir.is_dir():
            raise PrototypeRuntimeError("The private PostgreSQL cluster has not been initialized.")
        current = self.settings_store.load()
        if current.postgres_db != self.database_name or current.postgres_user != self.app_role:
            raise PrototypeRuntimeError("The private PostgreSQL settings do not match this prototype.")
        pid_file = self.data_dir / "postmaster.pid"
        if pid_file.is_file():
            try:
                previous_pid = int(pid_file.read_text(encoding="ascii").splitlines()[0])
            except (OSError, ValueError, IndexError):
                raise PrototypeRuntimeError("The existing PostgreSQL process identity is invalid; no files were changed.") from None
            if _windows_process_is_alive(previous_pid):
                raise PrototypeRuntimeError("A process still references this private cluster; it was left untouched.")
        env, path_environment = _external_postgres_environment(self.payload.bin_dir)
        log_file = self.log_dir / "postgresql-prototype.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        last_failure = "PostgreSQL did not become ready."
        for _ in range(MAX_PORT_ATTEMPTS):
            port = available_loopback_port()
            if port is None:
                raise PrototypeRuntimeError("Could not reserve an available loopback port.")
            stream = log_file.open("ab")
            log_start = stream.tell()
            process_started = time.monotonic()
            try:
                process = _spawn_postgres_process(
                    self.payload.executable("postgres.exe"),
                    ["-D", str(self.data_dir), "-h", "127.0.0.1", "-p", str(port)],
                    cwd=self.payload.bin_dir,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=stream,
                )
            finally:
                stream.close()
            child_processes: dict[int, dict[str, object]] = {}
            try:
                self._job = _OwnedProcessJob()
                self._job.assign(process)
            except OSError as error:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if self._job is not None:
                    self._job.close()
                    self._job = None
                raise PrototypeRuntimeError(
                    f"Could not bind the private PostgreSQL process to its owner ({type(error).__name__})."
                ) from None
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and process.poll() is None:
                for item in _windows_process_descendants(process.pid):
                    child_processes[int(item["pid"])] = item
                engine = create_engine(
                    URL.create(
                        "postgresql+psycopg",
                        username=settings.postgres_user,
                        password=settings.postgres_password.get_secret_value(),
                        host="127.0.0.1",
                        port=port,
                        database=settings.postgres_db,
                        query={"connect_timeout": "2"},
                    )
                )
                try:
                    with engine.connect() as connection:
                        actual = connection.execute(text("SELECT current_database()")).scalar_one()
                        if actual == self.database_name:
                            self._process = process
                            self._port = port
                            settings = settings.model_copy(update={"postgres_port": port})
                            if persist_settings:
                                self.settings_store.save(settings)
                            _write_process_diagnostic(
                                self.state_root,
                                {
                                    "stage": "existing-cluster-start",
                                    "component": "postgres.exe",
                                    "executable": str(self.payload.executable("postgres.exe").resolve(strict=False)),
                                    "working_directory": str(self.payload.bin_dir.resolve(strict=False)),
                                    "parent_pid": os.getpid(),
                                    "process_pid": process.pid,
                                    "child_processes": sorted(child_processes.values(), key=lambda item: int(item["pid"])),
                                    "return_code": None,
                                    "duration_ms": round((time.monotonic() - process_started) * 1000),
                                    "parent_image": Path(sys.executable).name,
                                    "dll_search": getattr(process, "_classpilot_dll_search", {}),
                                    "path_environment": path_environment,
                                    "safe_output_summary": "",
                                    "output_values_logged": "redacted-and-truncated",
                                    "timed_out": False,
                                },
                            )
                            return settings, process
                except Exception as error:
                    last_failure = type(error).__name__
                finally:
                    engine.dispose()
                time.sleep(0.2)
            if process.poll() is not None and process.returncode not in (None, 0):
                summary = _postgres_log_summary(log_file, start_offset=log_start)
                category, _ = _classify_postgres_failure(process.returncode, summary)
                _write_process_diagnostic(
                    self.state_root,
                    {
                        "stage": "existing-cluster-start",
                        "component": "postgres.exe",
                        "executable": str(self.payload.executable("postgres.exe").resolve(strict=False)),
                        "working_directory": str(self.payload.bin_dir.resolve(strict=False)),
                        "parent_pid": os.getpid(),
                        "process_pid": process.pid,
                        "child_processes": sorted(child_processes.values(), key=lambda item: int(item["pid"])),
                        "return_code": process.returncode & 0xFFFFFFFF,
                        "return_code_hex": f"0x{process.returncode & 0xFFFFFFFF:08X}",
                        "duration_ms": round((time.monotonic() - process_started) * 1000),
                        "failure_category": category,
                        "parent_image": Path(sys.executable).name,
                        "dll_search": getattr(process, "_classpilot_dll_search", {}),
                        "path_environment": path_environment,
                        "safe_output_summary": summary,
                        "output_values_logged": "redacted-and-truncated",
                        "timed_out": False,
                    },
                )
                if category == "port-conflict":
                    if self._job is not None:
                        self._job.close()
                        self._job = None
                    continue
                if self._job is not None:
                    self._job.close()
                    self._job = None
                raise _component_failure(
                    "postgres.exe", process.returncode, stage="existing-cluster-start", output=summary
                )
            if process.poll() is None:
                self._process = process
                self._port = port
                self.stop_owned_cluster()
                last_failure = "The selected loopback port became unavailable."
            else:
                self._job.close()
                self._job = None
                last_failure = f"PostgreSQL exited during startup ({process.returncode})."
        raise PrototypeRuntimeError(f"The private PostgreSQL cluster did not become ready ({last_failure}).")

    def stop_owned_cluster(self) -> None:
        process = getattr(self, "_process", None)
        if process is None or process.poll() is not None:
            if self._job is not None:
                self._job.close()
                self._job = None
            return
        pid_file = self.data_dir / "postmaster.pid"
        try:
            lines = pid_file.read_text(encoding="ascii").splitlines()
            pid = int(lines[0])
            recorded_dir = Path(lines[1]).resolve()
        except (OSError, ValueError, IndexError):
            raise PrototypeRuntimeError("Could not verify ownership of the PostgreSQL process; it was left untouched.") from None
        if pid != process.pid or recorded_dir != self.data_dir.resolve():
            raise PrototypeRuntimeError("PostgreSQL process identity did not match; it was left untouched.")
        actual_executable = _windows_process_image(process.pid)
        expected_executable = str(self.payload.executable("postgres.exe").resolve())
        if actual_executable is None or actual_executable.casefold() != expected_executable.casefold():
            raise PrototypeRuntimeError("PostgreSQL executable identity did not match; it was left untouched.")
        env, path_environment = _external_postgres_environment(self.payload.bin_dir)
        result = _execute_external_postgres_tool(
            self.payload.executable("pg_ctl.exe"),
            ["stop", "-D", str(self.data_dir), "-m", "fast", "-w", "-t", "20"],
            cwd=self.payload.bin_dir,
            env=env,
            stage="postgres-stop",
            state_root=self.state_root,
            path_environment=path_environment,
            timeout=30,
        )
        if result.returncode != 0:
            raise _component_failure(
                "pg_ctl.exe",
                result.returncode,
                stage="postgres-stop",
                output=result.stdout + b"\n" + result.stderr,
            )
        try:
            process.wait(timeout=25)
        except Exception:
            raise PrototypeRuntimeError("PostgreSQL did not exit after a normal fast shutdown.") from None
        self._job.close()
        self._job = None


def _windows_process_is_alive(pid: int) -> bool:
    if os.name != "nt":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return ctypes.get_last_error() != 87
    try:
        return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
    finally:
        kernel32.CloseHandle(handle)


def _windows_process_image(pid: int) -> str | None:
    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_uint32(len(buffer))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)
