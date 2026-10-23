from pathlib import Path
import json
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.release_location import (
    LocationError,
    ReleaseLocationStore,
    default_data_root,
    install_data_root,
    locator_path,
    require_completed_location,
    validate_data_root,
)
from app.core.release_setup import RELEASE_OWNER_KIND, REQUIRED_REVISION, ReleaseSetupManager


def test_location_record_is_separate_from_data_and_has_independent_completion(tmp_path: Path) -> None:
    local = tmp_path / "Local App Data"
    data = tmp_path / "D Drive" / "ClassPilotData"
    store = ReleaseLocationStore(locator_path(local))

    store.save(data, setup_complete=False)
    recorded = store.load()

    assert recorded is not None
    assert recorded.root == data.resolve()
    assert recorded.setup_complete is False
    assert store.path == local / "ClassPilotLauncher" / "location.json"
    content = json.loads(store.path.read_text(encoding="utf-8"))
    assert "password" not in json.dumps(content).casefold()
    assert not data.exists()

    store.save(data, setup_complete=True)
    assert store.load().setup_complete is True


def test_malformed_or_invalid_locator_fails_closed(tmp_path: Path) -> None:
    path = locator_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"format":99}', encoding="utf-8")
    with pytest.raises(LocationError, match="格式不相容"):
        ReleaseLocationStore(path).load()


def test_daily_location_never_falls_back_when_record_is_missing_or_offline(tmp_path: Path) -> None:
    store = ReleaseLocationStore(locator_path(tmp_path))
    with pytest.raises(LocationError, match="尚未記錄"):
        require_completed_location(store)
    missing_root = tmp_path / "D Drive" / "Old ClassPilot Data"
    store.save(missing_root, setup_complete=True)
    with pytest.raises(LocationError, match="不會改用預設位置"):
        require_completed_location(store)


def test_data_root_classification_distinguishes_empty_unrelated_and_incomplete(tmp_path: Path) -> None:
    manager = lambda path: ReleaseSetupManager(
        state_root=path,
        install_root=tmp_path / "Install",
        temp_root=tmp_path / "Temp",
        resource_root=ROOT / "packaging" / "windows",
        directory_protector=lambda _: None,
    )
    missing = manager(tmp_path / "Missing").assess_data_root()
    assert missing.kind == "new"

    empty = tmp_path / "Existing Empty"
    empty.mkdir()
    assert manager(empty).assess_data_root().kind == "new"

    unrelated = tmp_path / "Other Files"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("keep", encoding="utf-8")
    assessment = manager(unrelated).assess_data_root()
    assert assessment.kind == "unrelated"
    assert "空資料夾" in assessment.message
    assert (unrelated / "keep.txt").read_text(encoding="utf-8") == "keep"

    incomplete = tmp_path / "Partial ClassPilot"
    incomplete.mkdir()
    (incomplete / "setup-state.json").write_text(
        '{"format":1,"product":"ClassPilot","status":"in-progress","stage":"cluster-init"}',
        encoding="utf-8",
    )
    assessment = manager(incomplete).assess_data_root()
    assert assessment.kind == "incomplete"
    assert assessment.safe_to_resume is False


def test_only_known_precluster_stage_is_classified_as_safe_resume(tmp_path: Path) -> None:
    root = tmp_path / "Partial ClassPilot"
    manager = ReleaseSetupManager(
        state_root=root,
        install_root=tmp_path / "Install",
        temp_root=tmp_path / "Temp",
        resource_root=ROOT,
        directory_protector=lambda _: None,
    )
    manager._write_state("in-progress", stage="payload-verified", instance_id="b" * 48)
    assessment = manager.assess_data_root()
    assert assessment.kind == "incomplete"
    assert assessment.safe_to_resume is True


def test_cluster_init_retry_only_accepts_matching_empty_data_directory(tmp_path: Path) -> None:
    state_root = tmp_path / "ClusterInit"
    manager = ReleaseSetupManager(
        state_root=state_root,
        install_root=tmp_path / "Install",
        resource_root=ROOT / "packaging" / "windows",
        directory_protector=lambda _: None,
    )
    instance_id = "d" * 48
    manager.data_root.mkdir(parents=True)
    (state_root / "instance-owner.json").write_text(
        json.dumps({"kind": RELEASE_OWNER_KIND, "instance_id": instance_id}),
        encoding="utf-8",
    )
    manager._write_state("in-progress", stage="cluster-init", instance_id=instance_id)

    assessment = manager.assess_data_root()

    assert assessment.kind == "incomplete"
    assert assessment.safe_to_resume is True

    (manager.data_root / "partial-initdb-file").write_text("keep", encoding="utf-8")
    assert manager._is_safe_empty_cluster_init_resume(manager._read_state()) is False
    assert (manager.data_root / "partial-initdb-file").read_text(encoding="utf-8") == "keep"


def test_complete_structure_can_be_selected_for_reuse_without_reinit_or_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "Existing ClassPilot"
    manager = ReleaseSetupManager(
        state_root=root,
        install_root=tmp_path / "Install",
        temp_root=tmp_path / "Temp",
        resource_root=ROOT / "packaging" / "windows",
        directory_protector=lambda _: None,
    )
    spec = json.loads((ROOT / "packaging" / "windows" / "postgresql_payload_spec.json").read_text(encoding="utf-8"))
    payload = manager.payload_root
    (payload / "bin").mkdir(parents=True)
    for binary in ("initdb.exe", "postgres.exe", "pg_ctl.exe"):
        (payload / "bin" / binary).touch()
    (payload / "payload-manifest.json").write_text(
        json.dumps({
            "version": spec["version"],
            "package_revision": spec["package_revision"],
            "architecture": spec["architecture"],
            "archive_sha256": spec["sha256"],
        }),
        encoding="utf-8",
    )
    manager.data_root.mkdir(parents=True)
    (manager.data_root / "PG_VERSION").write_text("18\n", encoding="ascii")
    manager.settings_store.settings_file.parent.mkdir(parents=True)
    manager.settings_store.settings_file.write_bytes(b"CPDP1protected-test")
    manager._write_state("complete", stage="ready", instance_id="c" * 48)
    (root / "instance-owner.json").write_text(
        json.dumps({"kind": RELEASE_OWNER_KIND, "instance_id": "c" * 48}),
        encoding="utf-8",
    )

    from app.core.config import ReleaseSettings
    monkeypatch.setattr(manager.settings_store, "load", lambda: ReleaseSettings(
        postgres_user="classpilot_user", postgres_db="classpilot", postgres_password="synthetic-secret"
    ))
    assessment = manager.assess_data_root()

    assert assessment.kind == "existing"
    assert REQUIRED_REVISION == "20261019_0004"
    assert (manager.data_root / "PG_VERSION").read_text(encoding="ascii").strip() == "18"
    assert manager.setup_status() == "complete"


def test_existing_schema_check_is_read_only_and_never_invokes_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import postgres_runtime

    manager = ReleaseSetupManager(
        state_root=tmp_path / "Existing",
        install_root=tmp_path / "Install",
        temp_root=tmp_path / "Temp",
        resource_root=tmp_path / "Resources",
        directory_protector=lambda _: None,
    )
    manager.resource_root.mkdir(parents=True)
    monkeypatch.setattr(manager, "setup_status", lambda: "complete")
    from types import SimpleNamespace

    settings = SimpleNamespace(postgres_db="classpilot", postgres_user="classpilot_user")
    monkeypatch.setattr(manager.settings_store, "load", lambda: settings)
    monkeypatch.setattr(
        manager,
        "_read_state",
        lambda: {"status": "complete", "revision": "20261019_0004"},
    )
    monkeypatch.setattr(manager, "_owner_identity_matches", lambda _state: True)
    manager.data_root.mkdir(parents=True)
    (manager.data_root / "PG_VERSION").write_text("18\n", encoding="ascii")
    (manager.resource_root / "postgresql_payload_spec.json").write_text(
        '{"version":"18.6"}', encoding="utf-8"
    )

    calls: list[object] = []

    class FakeRuntime:
        def __init__(self, *args, **kwargs):
            calls.append(("create", kwargs))

        def _verify_payload(self):
            calls.append(("payload",))

        def start_existing(self, selected, *, persist_settings=True):
            calls.append(("start", selected, persist_settings))
            return settings, object()

        def _verify_migrated_schema(self, selected):
            calls.append(("schema", selected))

        def stop_owned_cluster(self):
            calls.append(("stop",))

    monkeypatch.setattr(postgres_runtime, "PostgresPrototype", FakeRuntime)
    manager.verify_existing_schema()

    assert ("start", settings, False) in calls
    assert ("schema", settings) in calls
    assert ("stop",) in calls
    assert all("migrate" not in str(call).casefold() for call in calls)


def test_data_path_checks_drive_overlap_and_write_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = tmp_path / "Install"
    root = tmp_path / "Data with spaces"
    assert validate_data_root(
        root,
        install_root=install,
        windows_root=tmp_path / "Windows",
        drive_checker=lambda _: True,
    ) == []
    assert validate_data_root(
        install / "Data",
        install_root=install,
        windows_root=tmp_path / "Windows",
        drive_checker=lambda _: True,
    ) == []
    protected_root = tmp_path / "Program Files"
    monkeypatch.setenv("ProgramFiles", str(protected_root))
    protected = protected_root / "ClassPilot Data"
    assert validate_data_root(
        protected,
        install_root=install,
        windows_root=tmp_path / "Windows",
        drive_checker=lambda _: True,
    )
    assert any(
        "同步資料夾" in issue
        for issue in validate_data_root(
            tmp_path / "OneDrive" / "ClassPilotData",
            install_root=install,
            windows_root=tmp_path / "Windows",
            drive_checker=lambda _: True,
        )
    )
    assert validate_data_root(
        root,
        install_root=install,
        windows_root=tmp_path / "Windows",
        drive_checker=lambda _: False,
    )
    assert validate_data_root(
        root,
        install_root=install,
        windows_root=tmp_path / "Windows",
        drive_checker=lambda _: True,
        write_probe=True,
    ) == []
    assert list(root.iterdir()) == []


def test_app_and_sibling_data_directories_are_allowed_without_nesting(tmp_path: Path) -> None:
    parent = tmp_path / "ClassPilot"
    install = parent / "App"
    data = parent / "Data"

    assert validate_data_root(
        data,
        install_root=install,
        windows_root=tmp_path / "Windows",
        drive_checker=lambda _: True,
    ) == []


def test_program_directory_data_path_uses_executable_location_and_only_exact_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.core.release_location as release_location

    install = tmp_path / "Program Files" / "ClassPilot"
    executable = install / "ClassPilot.exe"
    (tmp_path / "unrelated working directory").mkdir()
    monkeypatch.chdir(tmp_path / "unrelated working directory")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))

    data = install_data_root(executable)

    assert data == install / "Data"
    assert "Data\\Data" not in str(data)
    assert str(install_data_root(Path(r"D:\Program Files\ClassPilot\ClassPilot.exe"))) == (
        r"D:\Program Files\ClassPilot\Data"
    )
    assert validate_data_root(
        data,
        install_root=install,
        windows_root=tmp_path / "Windows",
        drive_checker=lambda _: True,
    ) == []
    assert validate_data_root(
        install,
        install_root=install,
        windows_root=tmp_path / "Windows",
        drive_checker=lambda _: True,
    )
    assert validate_data_root(
        install / "OtherData",
        install_root=install,
        windows_root=tmp_path / "Windows",
        drive_checker=lambda _: True,
    )

    def access_denied(*_args, **_kwargs):
        raise PermissionError("synthetic access denied")

    monkeypatch.setattr(release_location.tempfile, "mkstemp", access_denied)
    problems = validate_data_root(
        data,
        install_root=install,
        windows_root=tmp_path / "Windows",
        drive_checker=lambda _: True,
        write_probe=True,
    )
    assert any("目前 Windows 使用者無法" in issue for issue in problems)
