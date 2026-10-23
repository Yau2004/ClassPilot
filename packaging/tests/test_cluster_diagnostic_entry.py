from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.core import postgres_runtime


def _release_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "classpilot_release.py"
    spec = importlib.util.spec_from_file_location("classpilot_release_cluster_diagnostic_test", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_diagnostic_cli_requires_explicit_scope_and_confirmation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release = _release_module()
    monkeypatch.setattr(release.tempfile, "gettempdir", lambda: str(tmp_path))

    with pytest.raises(SystemExit):
        release.main(["--diagnose-existing-cluster"])

    assert not (tmp_path / "ClassPilot-Existing-Cluster-Diagnostic-test").exists()


def test_diagnostic_cli_dispatches_only_to_postgres_diagnostic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release = _release_module()
    monkeypatch.setattr(release.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(release, "_bootstrap_paths", lambda: (tmp_path, tmp_path / "backend"))
    monkeypatch.setattr(release, "_report_window", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(release, "_start", lambda *_args, **_kwargs: pytest.fail("regular API startup was called"))

    calls: list[dict[str, object]] = []

    def diagnose(**kwargs):
        calls.append(kwargs)
        return {"status": "passed"}

    monkeypatch.setattr(postgres_runtime, "diagnose_release_cluster_start", diagnose, raising=False)
    state_root = tmp_path / "explicit-state"
    report_root = tmp_path / "ClassPilot-Existing-Cluster-Diagnostic-test"
    result = release.main([
        "--diagnose-existing-cluster",
        "--confirm-existing-cluster-diagnostic",
        "--state-root", str(state_root),
        "--port", "9347",
        "--expected-port", "9347",
        "--expected-instance-id-prefix", "749400bb",
        "--diagnostic-report-dir", str(report_root),
    ])

    assert result == 0
    assert calls == [{
        "state_root": state_root.resolve(),
        "port": 9347,
        "expected_port": 9347,
        "report_root": report_root.resolve(),
        "expected_instance_id_prefix": "749400bb",
    }]


def test_diagnostic_fixed_port_mismatch_is_rejected_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release = _release_module()
    monkeypatch.setattr(release.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(release, "_bootstrap_paths", lambda: pytest.fail("bootstrap must not run"))
    report_root = tmp_path / "ClassPilot-Existing-Cluster-Diagnostic-mismatch"

    with pytest.raises(SystemExit):
        release.main([
            "--diagnose-existing-cluster",
            "--confirm-existing-cluster-diagnostic",
            "--state-root", str(tmp_path / "state"),
            "--port", "9348",
            "--expected-port", "9347",
            "--expected-instance-id-prefix", "749400bb",
            "--diagnostic-report-dir", str(report_root),
        ])

    assert not report_root.exists()
