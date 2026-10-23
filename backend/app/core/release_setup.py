"""Idempotent first-run setup for the single-user Windows release."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
import secrets
import tempfile
from typing import Callable

from app.core.config import ReleaseSettings
from app.core.path_compatibility import find_unsupported_cjk_paths, format_path_issues
from app.core.release_settings import ReleaseSettingsStore
from app.core.runtime import RUNTIME_PATHS


RELEASE_DATABASE = "classpilot"
RELEASE_APP_ROLE = "classpilot_user"
RELEASE_ADMIN_ROLE = "classpilot_admin"
RELEASE_OWNER_KIND = "ClassPilot-PostgreSQL-release"
REQUIRED_REVISION = "20261019_0004"


class ReleaseSetupError(RuntimeError):
    """A non-secret diagnostic for first-run setup."""


@dataclass(frozen=True)
class DataRootAssessment:
    kind: str
    message: str
    safe_to_resume: bool = False


def release_path_issues(
    *,
    install_root: Path,
    state_root: Path,
    payload_root: Path,
    temp_root: Path,
    runtime_dir: Path | None = None,
) -> list[tuple[str, Path]]:
    """Check every path used by PostgreSQL before any cluster initialization."""
    runtime_location = runtime_dir or Path(os.environ.get("WINDIR", "C:\\Windows")) / "System32"
    return [
        ("ClassPilot 程式安裝位置", install_root),
        ("ClassPilot 使用者資料位置", state_root),
        ("PostgreSQL binaries 解壓位置", payload_root),
        ("PostgreSQL executable 位置", payload_root / "bin" / "initdb.exe"),
        ("PostgreSQL 資料目錄", state_root / "data" / "postgresql"),
        ("Microsoft runtime 檢查位置", runtime_location),
        ("系統暫存位置", temp_root),
    ]


def _read_payload_manifest(payload_root: Path, expected_sha256: str) -> dict[str, object]:
    try:
        manifest = json.loads((payload_root / "payload-manifest.json").read_text(encoding="utf-8"))
        if (
            manifest.get("version") != "18.6"
            or manifest.get("package_revision") != "5"
            or manifest.get("architecture") != "windows-x64"
            or manifest.get("archive_sha256") != expected_sha256
            or not (payload_root / "bin" / "initdb.exe").is_file()
            or not (payload_root / "bin" / "postgres.exe").is_file()
            or not (payload_root / "bin" / "pg_ctl.exe").is_file()
        ):
            raise ValueError
        return manifest
    except (OSError, ValueError, TypeError, AttributeError):
        raise ReleaseSetupError(
            "已存在的 PostgreSQL payload 無法驗證；資料保留未動，請先取得診斷協助。"
        ) from None


def completed_release_instance_id(state_root: Path) -> str:
    """Return only a verified completed instance identity for daily startup."""
    state_file = state_root / "setup-state.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ReleaseSetupError(
            f"ClassPilot 設定狀態無法讀取：{state_file}。資料未被修改，請先診斷。"
        ) from None
    instance_id = state.get("instance_id") if isinstance(state, dict) else None
    if (
        not isinstance(state, dict)
        or state.get("format") != 1
        or state.get("product") != "ClassPilot"
        or state.get("status") != "complete"
        or state.get("revision") != REQUIRED_REVISION
        or not isinstance(instance_id, str)
        or re.fullmatch(r"[0-9a-f]{48}", instance_id) is None
    ):
        raise ReleaseSetupError(
            f"ClassPilot 尚未完成首次設定，或設定狀態不相容：{state_file}。"
            "資料未被清除；請先執行診斷。"
        )
    try:
        owner = json.loads((state_root / "instance-owner.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ReleaseSetupError(
            f"ClassPilot 實例識別檔無法讀取：{state_root / 'instance-owner.json'}。"
            "資料未被修改，請先診斷。"
        ) from None
    if (
        not isinstance(owner, dict)
        or owner.get("kind") != RELEASE_OWNER_KIND
        or owner.get("instance_id") != instance_id
    ):
        raise ReleaseSetupError(
            f"ClassPilot 實例識別不符：{state_root}。資料未被修改，請先診斷。"
        )
    return instance_id


class ReleaseSetupManager:
    """Own setup state under an injected per-user root; never guesses a DB target."""

    def __init__(
        self,
        *,
        state_root: Path,
        install_root: Path,
        temp_root: Path | None = None,
        resource_root: Path | None = None,
        runtime_dir: Path | None = None,
        directory_protector: Callable[[Path], None] | None = None,
    ):
        self.state_root = state_root.resolve()
        self.install_root = install_root.resolve()
        self.temp_root = (temp_root or Path(tempfile.gettempdir())).resolve()
        self.resource_root = (resource_root or RUNTIME_PATHS.resource_root).resolve()
        self.payload_root = self.state_root / "runtime" / "postgresql"
        self.data_root = self.state_root / "data" / "postgresql"
        self.state_file = self.state_root / "setup-state.json"
        self.settings_store = ReleaseSettingsStore(self.state_root / "config" / "settings.dpapi")
        self.runtime_dir = runtime_dir or Path(os.environ.get("WINDIR", "C:\\Windows")) / "System32"
        if directory_protector is None:
            from app.core.postgres_runtime import secure_directory

            directory_protector = secure_directory
        self.directory_protector = directory_protector

    def preflight(self) -> list[tuple[str, Path]]:
        candidates = release_path_issues(
            install_root=self.install_root,
            state_root=self.state_root,
            payload_root=self.payload_root,
            temp_root=self.temp_root,
            runtime_dir=self.runtime_dir,
        )
        return [
            (issue.label, issue.path)
            for issue in find_unsupported_cjk_paths(candidates)
        ]

    def _read_state(self) -> dict[str, object] | None:
        if not self.state_file.exists():
            return None
        try:
            value = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise ReleaseSetupError(
                f"設定狀態檔無法辨識：{self.state_file}。資料未被刪除，請先診斷。"
            ) from None
        if not isinstance(value, dict) or value.get("format") != 1:
            raise ReleaseSetupError(
                f"設定狀態檔版本不相容：{self.state_file}。資料未被刪除，請先診斷。"
            )
        return value

    def _owner_identity_matches(self, state: dict[str, object]) -> bool:
        try:
            owner = json.loads((self.state_root / "instance-owner.json").read_text(encoding="utf-8"))
            return (
                owner.get("kind") == RELEASE_OWNER_KIND
                and owner.get("instance_id") == state.get("instance_id")
            )
        except (OSError, json.JSONDecodeError, AttributeError):
            return False

    def _is_safe_empty_cluster_init_resume(self, state: dict[str, object] | None) -> bool:
        """Allow retry only when initdb left its pre-created directory empty."""
        if (
            not state
            or state.get("status") != "in-progress"
            or state.get("stage") != "cluster-init"
            or self.settings_store.settings_file.exists()
            or not self._owner_identity_matches(state)
            or not self.data_root.is_dir()
            or self.data_root.is_symlink()
            or getattr(self.data_root, "is_junction", lambda: False)()
        ):
            return False
        try:
            return next(self.data_root.iterdir(), None) is None
        except OSError:
            return False

    def _write_state(self, status: str, *, stage: str, instance_id: str) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.directory_protector(self.state_root)
        data = {
            "format": 1,
            "product": "ClassPilot",
            "instance_id": instance_id,
            "status": status,
            "stage": stage,
            "revision": REQUIRED_REVISION if status == "complete" else None,
        }
        fd, name = tempfile.mkstemp(prefix=".setup-state-", suffix=".tmp", dir=self.state_root)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_file)
        finally:
            temporary.unlink(missing_ok=True)

    def setup_status(self) -> str:
        state = self._read_state()
        if state is None:
            if self.state_root.exists() and any(self.state_root.iterdir()):
                from app.core.postgres_payload_import import PayloadSpec

                try:
                    spec = PayloadSpec.from_mapping(
                        json.loads((self.resource_root / "postgresql_payload_spec.json").read_text(encoding="utf-8"))
                    )
                    _read_payload_manifest(self.payload_root, spec.sha256)
                    payload_only = (
                        not self.data_root.exists()
                        and not self.settings_store.settings_file.exists()
                        and not (self.state_root / "instance-owner.json").exists()
                    )
                    if payload_only:
                        return "not-configured"
                except (OSError, ValueError, TypeError, ReleaseSetupError):
                    pass
                return "incomplete"
            return "not-configured"
        if state.get("status") == "complete" and state.get("revision") == REQUIRED_REVISION:
            if (
                self.data_root.is_dir()
                and self.settings_store.settings_file.is_file()
                and self._owner_identity_matches(state)
            ):
                try:
                    from app.core.postgres_payload_import import PayloadSpec

                    spec = PayloadSpec.from_mapping(
                        json.loads((self.resource_root / "postgresql_payload_spec.json").read_text(encoding="utf-8"))
                    )
                    _read_payload_manifest(self.payload_root, spec.sha256)
                    settings = self.settings_store.load()
                    if settings.postgres_db == RELEASE_DATABASE and settings.postgres_user == RELEASE_APP_ROLE:
                        return "complete"
                except Exception:
                    return "incomplete"
        return "incomplete"

    def assess_data_root(self) -> DataRootAssessment:
        """Classify a selected root without initializing or changing it."""
        if not self.state_root.exists():
            return DataRootAssessment("new", "資料目錄不存在；確認後可建立新的 ClassPilot 資料環境。")
        if not self.state_root.is_dir() or self.state_root.is_symlink() or getattr(self.state_root, "is_junction", lambda: False)():
            return DataRootAssessment("unrelated", "所選位置不是可安全使用的普通資料夾。請選擇空資料夾。")
        entries = list(self.state_root.iterdir())
        if not entries:
            return DataRootAssessment("new", "資料夾存在但為空；確認後可建立新的 ClassPilot 資料環境。")
        status = self.setup_status()
        if status == "complete":
            try:
                pg_version = (self.data_root / "PG_VERSION").read_text(encoding="ascii").strip()
                spec = json.loads((self.resource_root / "postgresql_payload_spec.json").read_text(encoding="utf-8"))
                expected_major = str(spec["version"]).split(".", 1)[0]
                if pg_version != expected_major:
                    return DataRootAssessment("incomplete", "既有 PostgreSQL 資料版本與目前支援版本不相容；資料未被修改。")
            except (OSError, ValueError, KeyError, TypeError):
                return DataRootAssessment("incomplete", "既有資料缺少可驗證的 PostgreSQL 版本資訊；資料未被修改。")
            return DataRootAssessment("existing", "找到完整的 ClassPilot 資料；可檢查實例、憑證解密及 schema 後重用。")
        if self.settings_store.settings_file.is_file():
            try:
                self.settings_store.load()
            except Exception:
                return DataRootAssessment(
                    "incomplete",
                    "既有 DPAPI 設定無法由目前 Windows 使用者解密或格式不相容；ClassPilot 不會重新產生或覆寫憑證。資料未被修改。",
                )
        state = None
        try:
            state = self._read_state()
        except ReleaseSetupError:
            pass
        safe_resume = bool(
            state
            and state.get("status") == "in-progress"
            and (
                state.get("stage") == "payload-verified"
                and not self.data_root.exists()
                and not self.settings_store.settings_file.exists()
                and not (self.state_root / "instance-owner.json").exists()
                or state.get("stage") in {"create-prototype-roles", "app-role-smoke", "explicit-migration"}
                and self.data_root.is_dir()
                and self.settings_store.settings_file.is_file()
                and self._owner_identity_matches(state)
            )
        )
        safe_resume = safe_resume or self._is_safe_empty_cluster_init_resume(state)
        if state is None and (self.state_root / "runtime" / "postgresql").is_dir():
            try:
                from app.core.postgres_payload_import import PayloadSpec

                spec = PayloadSpec.from_mapping(
                    json.loads((self.resource_root / "postgresql_payload_spec.json").read_text(encoding="utf-8"))
                )
                _read_payload_manifest(self.payload_root, spec.sha256)
                safe_resume = (
                    not self.data_root.exists()
                    and not self.settings_store.settings_file.exists()
                    and not (self.state_root / "instance-owner.json").exists()
                )
            except Exception:
                safe_resume = False
        if safe_resume:
            return DataRootAssessment("incomplete", "找到可識別的 ClassPilot 未完成設定；只可執行受控恢復檢查，不會重新初始化或清除資料。", True)
        if state is not None or any(
            (self.state_root / name).exists()
            for name in ("instance-owner.json", "setup-state.json", "config", "data", "runtime")
        ):
            return DataRootAssessment("incomplete", "找到看似 ClassPilot 但無法安全確認完成狀態的資料；已停止，資料未被修改。可選擇診斷或改選另一個空資料夾。")
        return DataRootAssessment("unrelated", "此資料夾已有非 ClassPilot 或無法識別的內容；為避免覆寫，請選擇另一個空資料夾。")

    def verify_existing_schema(self) -> None:
        """Check each compatibility layer, then read schema without migrating."""
        from app.core.postgres_runtime import (
            PostgresPayload,
            PostgresPrototype,
            PrototypeRuntimeError,
        )
        from app.core.release_settings import (
            ReleaseSettingsDecryptionError,
            ReleaseSettingsFormatError,
        )

        state = self._read_state()
        if (
            state is None
            or state.get("status") != "complete"
            or state.get("revision") != REQUIRED_REVISION
        ):
            raise ReleaseSetupError(
                "既有 ClassPilot 設定狀態未標記為目前版本完成；資料未被修改。"
            )
        if not self._owner_identity_matches(state):
            raise ReleaseSetupError(
                "既有 ClassPilot 實例識別檢查失敗；資料未被修改。"
            )

        runtime = PostgresPrototype(
            PostgresPayload(self.payload_root), self.state_root, self.settings_store,
            database_name=RELEASE_DATABASE, app_role=RELEASE_APP_ROLE,
            admin_role=RELEASE_ADMIN_ROLE, owner_kind=RELEASE_OWNER_KIND,
        )
        try:
            runtime._verify_payload()
        except PrototypeRuntimeError as error:
            reason = str(error)
            folded_reason = reason.casefold()
            if "payload record" in folded_reason or "payload does not match" in folded_reason:
                category = "PostgreSQL payload manifest／架構相容性檢查"
            elif "version" in folded_reason:
                category = "PostgreSQL executable 版本檢查"
            elif "runtime preflight" in folded_reason:
                category = "PostgreSQL 系統 runtime／DLL 檢查"
            elif "executable-preflight" in folded_reason or "could not start" in folded_reason:
                category = "PostgreSQL executable 載入檢查"
            elif "process-wide" in folded_reason or "thread is active" in folded_reason:
                category = "PostgreSQL executable 啟動時的 DLL 搜尋路徑競爭保護"
            else:
                category = "PostgreSQL payload／executable 檢查"
            raise ReleaseSetupError(
                f"{category}失敗；資料未被修改。{reason}"
            ) from None
        try:
            settings = self.settings_store.load()
        except ReleaseSettingsDecryptionError:
            raise ReleaseSetupError(
                "既有設定的 DPAPI 憑證無法由目前 Windows 使用者解密；"
                "沒有重新產生或覆寫憑證，資料未被修改。"
            ) from None
        except ReleaseSettingsFormatError:
            raise ReleaseSetupError(
                "既有設定檔格式或內容無法驗證；沒有覆寫設定，資料未被修改。"
            ) from None
        try:
            pg_version = (self.data_root / "PG_VERSION").read_text(encoding="ascii").strip()
            spec = json.loads((self.resource_root / "postgresql_payload_spec.json").read_text(encoding="utf-8"))
            expected_major = str(spec["version"]).split(".", 1)[0]
        except (OSError, ValueError, KeyError, TypeError):
            raise ReleaseSetupError(
                "既有 PostgreSQL cluster 版本資訊無法讀取；資料未被修改。"
            ) from None
        if pg_version != expected_major:
            raise ReleaseSetupError(
                f"既有 PostgreSQL 主版本不符（資料 {pg_version}、目前支援 {expected_major}）；"
                "沒有啟動或修改資料。"
            )
        if settings.postgres_db != RELEASE_DATABASE or settings.postgres_user != RELEASE_APP_ROLE:
            raise ReleaseSetupError(
                "既有設定中的資料庫識別與此 ClassPilot 發行版不符；資料未被修改。"
            )
        try:
            settings, _ = runtime.start_existing(settings, persist_settings=False)
            runtime._verify_migrated_schema(settings)
        except PrototypeRuntimeError as error:
            detail = str(error)
            if "revision verification" in detail.casefold() or "schema verification" in detail.casefold():
                category = "既有資料庫 schema revision／必要欄位不相容"
            elif "port-conflict" in detail.casefold():
                category = "既有 cluster 的 loopback 連接埠衝突"
            elif "permission" in detail.casefold():
                category = "既有 cluster 的檔案或目錄權限錯誤"
            elif "cluster-version-or-format" in detail.casefold():
                category = "既有 cluster 版本或格式不相容"
            elif "file-or-dependency" in detail.casefold() or "dll-load-failure" in detail.casefold():
                category = "既有 cluster 的執行檔、DLL 或必要檔案載入失敗"
            elif "existing-instance-or-lock" in detail.casefold():
                category = "既有 cluster 程序或鎖定檔狀態未通過"
            else:
                category = "PostgreSQL 執行檢查或唯讀連線失敗"
            raise ReleaseSetupError(
                f"{category}；沒有執行 migration，也沒有重新初始化或刪除資料。{detail}"
            ) from None
        except Exception as error:
            raise ReleaseSetupError(
                f"既有資料相容性檢查在 {type(error).__name__} 階段無法完成；"
                "這不代表資料損壞。沒有執行 migration，也沒有修改資料。"
            ) from None
        finally:
            runtime.stop_owned_cluster()

    def provision(
        self,
        archive_path: Path | None,
        *,
        migration: Callable[[ReleaseSettings], None],
    ) -> ReleaseSettings:
        """Import and provision after preflight; never overwrite unknown state."""
        issues = self.preflight()
        if issues:
            raise ReleaseSetupError(format_path_issues(find_unsupported_cjk_paths(issues)))

        status = self.setup_status()
        if status == "complete":
            return self.settings_store.load()
        previous_state = self._read_state()
        if (
            status == "incomplete"
            and previous_state is not None
            and previous_state.get("status") == "in-progress"
            and self.data_root.is_dir()
            and self.settings_store.settings_file.is_file()
            and self._owner_identity_matches(previous_state)
        ):
            from app.core.postgres_runtime import PostgresPayload, PostgresPrototype

            recovery_runtime = PostgresPrototype(
                PostgresPayload(self.payload_root),
                self.state_root,
                self.settings_store,
                database_name=RELEASE_DATABASE,
                app_role=RELEASE_APP_ROLE,
                admin_role=RELEASE_ADMIN_ROLE,
                owner_kind=RELEASE_OWNER_KIND,
            )
            try:
                recovered, _ = recovery_runtime.start_existing(self.settings_store.load())
                recovery_runtime._verify_migrated_schema(recovered)
            except Exception as error:
                recovery_runtime.stop_owned_cluster()
                raise ReleaseSetupError(
                    f"首次設定狀態仍不完整：{self.state_root}。"
                    f"唯讀恢復檢查未通過（{type(error).__name__}）；資料未被清除，請先診斷。"
                ) from None
            finally:
                recovery_runtime.stop_owned_cluster()
            instance_id = str(previous_state.get("instance_id", ""))
            if not instance_id:
                raise ReleaseSetupError(
                    f"設定狀態缺少實例識別碼：{self.state_root}。資料未被清除，請先診斷。"
                )
            self._write_state("complete", stage="ready", instance_id=instance_id)
            return self.settings_store.load()
        can_resume_before_cluster = (
            previous_state is not None
            and previous_state.get("status") == "in-progress"
            and previous_state.get("stage") == "payload-verified"
            and not self.data_root.exists()
            and not self.settings_store.settings_file.exists()
            and not (self.state_root / "instance-owner.json").exists()
        )
        can_resume_empty_cluster_init = self._is_safe_empty_cluster_init_resume(previous_state)
        if status == "incomplete" and not (can_resume_before_cluster or can_resume_empty_cluster_init):
            raise ReleaseSetupError(
                f"發現未完成或不明的既有設定：{self.state_root}。"
                "ClassPilot 沒有重新初始化或刪除資料；只有已識別且資料目錄空白的 cluster-init 才可重試，其他情況請先診斷。"
            )

        from app.core.postgres_payload_import import PayloadSpec, validate_and_import_postgres_zip
        from app.core.postgres_runtime import PostgresPayload, PostgresPrototype, PrototypeRuntimeError

        spec_path = self.resource_root / "postgresql_payload_spec.json"
        manifest_path = self.resource_root / "release_manifest.json"
        try:
            spec = PayloadSpec.from_mapping(json.loads(spec_path.read_text(encoding="utf-8")))
            release_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raise ReleaseSetupError("封裝缺少 PostgreSQL 或 schema 支援規格。") from None
        if release_manifest.get("required_alembic_revision") != REQUIRED_REVISION:
            raise ReleaseSetupError("封裝目標 schema revision 不符合支援規格。")

        if self.payload_root.exists():
            _read_payload_manifest(self.payload_root, spec.sha256)
        else:
            if archive_path is None:
                raise ReleaseSetupError("請選取 PostgreSQL binaries ZIP。")
            try:
                imported = validate_and_import_postgres_zip(
                    archive_path,
                    self.payload_root,
                    spec,
                    runtime_dir=self.runtime_dir,
                    restrict_directory=self.directory_protector,
                )
            except Exception as error:
                from app.core.postgres_payload_import import PayloadImportError

                if isinstance(error, PayloadImportError):
                    raise ReleaseSetupError(str(error)) from None
                raise ReleaseSetupError(
                    f"PostgreSQL ZIP 檢查失敗（{type(error).__name__}）；尚未初始化資料庫。"
                ) from None
            _read_payload_manifest(self.payload_root, imported.sha256)

        instance_id = (
            str(previous_state["instance_id"])
            if (can_resume_before_cluster or can_resume_empty_cluster_init) and previous_state is not None
            else secrets.token_hex(24)
        )
        if not can_resume_empty_cluster_init:
            self._write_state("in-progress", stage="payload-verified", instance_id=instance_id)
        if (self.data_root.exists() or self.settings_store.settings_file.exists()) and not can_resume_empty_cluster_init:
            raise ReleaseSetupError(
                f"PostgreSQL 資料或憑證已存在：{self.state_root}。"
                "ClassPilot 不會覆寫或重新初始化；請先診斷目前狀態。"
            )

        def record_stage(stage: str) -> None:
            self._write_state("in-progress", stage=stage, instance_id=instance_id)

        self.directory_protector(self.state_root)
        runtime = PostgresPrototype(
            PostgresPayload(self.payload_root),
            self.state_root,
            self.settings_store,
            on_stage=record_stage,
            database_name=RELEASE_DATABASE,
            app_role=RELEASE_APP_ROLE,
            admin_role=RELEASE_ADMIN_ROLE,
            owner_kind=RELEASE_OWNER_KIND,
            instance_id=instance_id,
        )
        try:
            settings = runtime.provision(migrate=migration)
        except Exception as error:
            runtime.stop_owned_cluster()
            if isinstance(error, PrototypeRuntimeError):
                safe_cause = str(error)
            else:
                safe_cause = f"例外類型：{type(error).__name__}"
            raise ReleaseSetupError(
                f"首次設定在 {self.state_root} 的階段 {self._read_state().get('stage', 'unknown')} 停止。"
                f"資料保留，未自動刪除或重建。{safe_cause}"
            ) from None
        finally:
            runtime.stop_owned_cluster()
        self._write_state("complete", stage="ready", instance_id=instance_id)
        return settings
