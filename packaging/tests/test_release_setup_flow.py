from pathlib import Path
import importlib.util
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.path_compatibility import PathIssue, contains_cjk_path_character, format_path_issues
from app.core.release_setup import (
    RELEASE_OWNER_KIND,
    REQUIRED_REVISION,
    ReleaseSetupError,
    ReleaseSetupManager,
    completed_release_instance_id,
    release_path_issues,
)
from app.core.release_settings import ReleaseSettingsStore


BUILD_SCRIPT = ROOT / "scripts" / "build_windows_installer.py"
sys.path.insert(0, str(ROOT / "scripts"))
import classpilot_release


def test_path_checker_rejects_cjk_but_allows_spaces() -> None:
    assert contains_cjk_path_character(r"C:\Users\陳先生\AppData\Local")
    assert not contains_cjk_path_character(r"C:\Users\Teacher User\AppData\Local")


def test_path_preflight_checks_install_local_data_postgres_executable_data_and_temp(tmp_path: Path) -> None:
    rows = release_path_issues(
        install_root=tmp_path / "Install Path",
        state_root=tmp_path / "Local Data",
        payload_root=tmp_path / "Local Data" / "runtime" / "postgresql",
        temp_root=tmp_path / "Temp Path",
    )
    labels = [label for label, _ in rows]
    assert labels == [
        "ClassPilot 程式安裝位置",
        "ClassPilot 使用者資料位置",
        "PostgreSQL binaries 解壓位置",
        "PostgreSQL executable 位置",
        "PostgreSQL 資料目錄",
        "Microsoft runtime 檢查位置",
        "系統暫存位置",
    ]
    assert rows[3][1].name == "initdb.exe"
    assert rows[5][1] == Path("C:\\Windows") / "System32"


def test_path_message_distinguishes_editable_install_location_from_local_data(tmp_path: Path) -> None:
    install_message = format_path_issues([
        PathIssue("ClassPilot 程式安裝位置", tmp_path / "ClassPilot 中文"),
    ])
    assert "返回安裝位置頁" in install_message
    assert "空格可保留" in install_message

    local_data = format_path_issues([
        PathIssue("ClassPilot 使用者資料位置", tmp_path / "使用者資料"),
        PathIssue("PostgreSQL executable 位置", tmp_path / "使用者資料" / "initdb.exe"),
    ])
    assert "沒有安全的替代位置" in local_data
    assert "不要改 Windows 帳戶" in local_data


def test_daily_runtime_uses_completed_release_identity_and_imported_payload(tmp_path: Path) -> None:
    state_root = tmp_path / "isolated LocalAppData" / "ClassPilot"
    instance_id = "a" * 48
    state_root.mkdir(parents=True)
    (state_root / "setup-state.json").write_text(
        '{"format":1,"product":"ClassPilot","status":"complete",'
        f'"revision":"{REQUIRED_REVISION}","instance_id":"{instance_id}"}}',
        encoding="utf-8",
    )
    (state_root / "instance-owner.json").write_text(
        '{"kind":"' + RELEASE_OWNER_KIND + '","instance_id":"' + instance_id + '"}',
        encoding="utf-8",
    )

    verified_id = completed_release_instance_id(state_root)
    runtime = classpilot_release._release_postgres_runtime(
        state_root,
        ReleaseSettingsStore(state_root / "config" / "settings.dpapi"),
        instance_id=verified_id,
    )

    assert runtime.database_name == "classpilot"
    assert runtime.app_role == "classpilot_user"
    assert runtime.admin_role == "classpilot_admin"
    assert runtime.owner_kind == RELEASE_OWNER_KIND
    assert runtime.instance_id == instance_id
    assert runtime.payload.root == state_root / "runtime" / "postgresql"
    assert runtime.data_dir == state_root / "data" / "postgresql"


def test_daily_runtime_refuses_incomplete_or_mismatched_instance_identity(tmp_path: Path) -> None:
    state_root = tmp_path / "ClassPilot"
    state_root.mkdir()
    with pytest.raises(ReleaseSetupError, match="設定狀態無法讀取"):
        completed_release_instance_id(state_root)


def test_cjk_local_appdata_fails_before_any_database_or_settings_write(tmp_path: Path) -> None:
    local = tmp_path / "LocalAppData 學生"
    manager = ReleaseSetupManager(
        state_root=local / "ClassPilot",
        install_root=tmp_path / "Program Files" / "ClassPilot",
        temp_root=tmp_path / "Temp",
        resource_root=ROOT,
        directory_protector=lambda _: None,
        runtime_dir=tmp_path / "runtime",
    )
    issues = manager.preflight()
    assert any(label == "ClassPilot 使用者資料位置" for label, _ in issues)
    assert any(label == "PostgreSQL executable 位置" for label, _ in issues)
    with pytest.raises(ReleaseSetupError, match="中文、日文或韓文"):
        manager.provision(None, migration=lambda _: pytest.fail("migration must not run"))
    assert not local.exists()


def test_first_run_status_is_read_only_when_local_state_is_missing(tmp_path: Path) -> None:
    local = tmp_path / "Local Data with spaces"
    manager = ReleaseSetupManager(
        state_root=local / "ClassPilot",
        install_root=tmp_path / "Install Path",
        temp_root=tmp_path / "Temp Path",
        resource_root=ROOT,
        directory_protector=lambda _: None,
    )
    assert manager.setup_status() == "not-configured"
    assert not local.exists()


def test_setup_state_never_accepts_wrong_format_or_marks_incomplete_as_complete(tmp_path: Path) -> None:
    root = tmp_path / "ClassPilot"
    manager = ReleaseSetupManager(
        state_root=root,
        install_root=tmp_path / "Install",
        temp_root=tmp_path / "Temp",
        resource_root=ROOT,
        directory_protector=lambda _: None,
    )
    root.mkdir()
    manager._write_state("in-progress", stage="cluster-init", instance_id="a" * 48)
    assert manager.setup_status() == "incomplete"
    with pytest.raises(RuntimeError, match="沒有重新初始化或刪除資料"):
        manager.provision(None, migration=lambda _: pytest.fail("migration must not run"))


def test_failed_new_setup_keeps_existing_location_recorded(tmp_path: Path) -> None:
    old_root = tmp_path / "Existing Data"
    new_root = tmp_path / "New Data"

    class Store:
        saved: list[tuple[Path, bool]] = []

        def load(self):
            from app.core.release_location import DataLocation

            return DataLocation(old_root, True)

        def save(self, root: Path, *, setup_complete: bool) -> None:
            self.saved.append((root, setup_complete))

    class Manager:
        def provision(self, archive, *, migration):
            raise RuntimeError("synthetic failure")

        def _read_state(self):
            return None

    store = Store()
    with pytest.raises(RuntimeError, match="synthetic failure"):
        classpilot_release._complete_release_first_setup(Manager(), store, new_root, None)

    assert store.saved == []
    assert store.load().root == old_root


def test_failed_first_setup_does_not_record_incomplete_root(tmp_path: Path) -> None:
    class Store:
        saved: list[tuple[Path, bool]] = []

        def load(self):
            return None

        def save(self, root: Path, *, setup_complete: bool) -> None:
            self.saved.append((root, setup_complete))

    class Manager:
        def provision(self, archive, *, migration):
            raise RuntimeError("synthetic failure")

        def _read_state(self):
            return None

    store = Store()
    with pytest.raises(RuntimeError, match="synthetic failure"):
        classpilot_release._complete_release_first_setup(
            Manager(), store, tmp_path / "Incomplete Data", None
        )

    assert store.saved == []


def test_successful_new_setup_switches_location_after_provision(tmp_path: Path) -> None:
    old_root = tmp_path / "Existing Data"
    new_root = tmp_path / "New Data"

    class Store:
        def __init__(self) -> None:
            self.current = old_root
            self.saved: list[tuple[Path, bool]] = []

        def load(self):
            from app.core.release_location import DataLocation

            return DataLocation(self.current, True)

        def save(self, root: Path, *, setup_complete: bool) -> None:
            self.saved.append((root, setup_complete))
            self.current = root

    class Manager:
        def __init__(self, store: Store) -> None:
            self.store = store

        def provision(self, archive, *, migration):
            assert self.store.load().root == old_root
            return object()

        def _read_state(self):
            return None

    store = Store()
    classpilot_release._complete_release_first_setup(Manager(store), store, new_root, None)

    assert store.saved == [(new_root, True)]
    assert store.load().root == new_root


def test_control_panel_exposes_one_start_and_keeps_data_location_available() -> None:
    source = (ROOT / "scripts" / "classpilot_release.py").read_text(encoding="utf-8")

    assert 'text="啟動", command=launch_service' in source
    assert 'text="啟動並開啟網站"' not in source
    assert 'text="開啟已啟動的網站"' not in source
    assert 'text="資料位置…", command=open_location_settings' in source
    assert "資料搬移：尚未提供" in source
    assert "建立新資料不會帶入目前資料；原資料仍保留" in source
    assert 'configure(state="disabled")' in source
    assert "_service_mutex_is_held()" in source


def test_unrelated_port_process_is_never_accepted_as_classpilot_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import postgres_runtime

    state = tmp_path / "State"
    state.mkdir()
    classpilot_release._running_instance_file(state).write_text(
        __import__("json").dumps({
            "pid": 123,
            "executable": str(Path(sys.executable).resolve()),
            "port": 8123,
            "host": "127.0.0.1",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(postgres_runtime, "_windows_process_is_alive", lambda _pid: True)
    monkeypatch.setattr(postgres_runtime, "_windows_process_image", lambda _pid: r"C:\Other\service.exe")

    assert classpilot_release._existing_instance_url(state) is None


def test_installer_source_check_does_not_compile_or_install_tools(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("classpilot_installer_build", BUILD_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    source = (ROOT / "packaging" / "installer" / "ClassPilotSetup.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in source
    assert "ArchitecturesAllowed=x64os" in source
    assert "ArchitecturesInstallIn64BitMode=x64os" in source
    assert "DefaultDirName={localappdata}\\Programs\\ClassPilot" in source
    assert "DisableDirPage=no" in source
    assert "ExtraDiskSpaceRequired=94_371_840" in source
    assert "此選項只變更程式位置" in source
    assert "GetSpaceOnDisk(DriveRoot, True, FreeMB, TotalMB)" in source
    assert "CreateDir(ProbeDir)" in source
    assert "目標資料夾已有檔案，為避免覆寫而停止" in source
    assert "程式安裝位置" in source
    assert "RELEASE_PATH_LIMITATION.txt" in source
    assert "ClassPilot.exe" in source
    assert "ClassPilotPrototype.exe" not in source
    assert "postgresql-18.6-5-windows-x64-binaries.zip" not in source
    assert "UninstallDelete" not in source
    assert 'Name: "{app}\\Data"; Flags: uninsneveruninstall' in source
    assert 'Excludes: "ClassPilot.exe,Data\\*"' in source
    assert "UninstalledAll=ClassPilot 已成功解除安裝" in source
    assert "UninstalledMost=ClassPilot 解除安裝完成" in source
    assert "首次資料設定必須在一般使用者權限下進行" in source
    assert "[Run]" not in source
    assert "更新檢查、資料相容性、備份與資料庫遷移確認流程尚未完成" in source


def test_inno_compiler_version_comes_from_compiler_output(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location("classpilot_installer_version", BUILD_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(command)
        assert kwargs["timeout"] == 10
        return SimpleNamespace(returncode=0, stdout="7.1.0\n")

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.inno_compiler_version(Path("ISCC.exe")) == "7.1.0"
    assert calls == [["ISCC.exe", "--version"]]
