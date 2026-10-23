"""Interactive, repeatable Windows setup for a local ClassPilot checkout.

This bootstrap uses only the Python standard library. Project dependencies are
installed into the named Conda environment, never into the bootstrap/base
environment. The script intentionally does not run database migrations.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Callable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONDA_ENV_NAME = "steam-classroom"
REQUIRED_SCHEMA_REVISION = "20261019_0004"
Runner = Callable[..., subprocess.CompletedProcess[str]]


class SetupError(RuntimeError):
    """A safe-to-display setup diagnostic."""


def conda_executable_from_bat(bat_path: str | Path) -> Path:
    """Return the discovered batch entry, avoiding direct Conda exe loading."""
    path = Path(bat_path).resolve()
    if not path.is_file():
        raise SetupError("找不到 Conda batch 入口，請確認 Conda 安裝後重試。")
    return path


def windows_batch_command(args: Sequence[str | Path]) -> str:
    """Quote argv for cmd.exe while invoking a known local .bat entry."""
    return "call " + subprocess.list2cmdline([str(item) for item in args])


def parse_version(value: str) -> tuple[int, int, int] | None:
    match = re.search(r"(?:v)?(\d+)\.(\d+)(?:\.(\d+))?", value)
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def node_version_supported(version: tuple[int, int, int]) -> bool:
    major, minor, patch = version
    return (major == 20 and (minor, patch) >= (19, 0)) or major >= 22 and (
        major > 22 or (minor, patch) >= (12, 0)
    )


def find_target_environment(prefixes: Sequence[str | Path]) -> Path | None:
    for prefix in prefixes:
        path = Path(prefix)
        if path.name.casefold() == CONDA_ENV_NAME.casefold():
            return path
    return None


def ensure_env_file(project_root: Path) -> bool:
    """Create .env from the tracked template only when it does not exist."""
    target = project_root / ".env"
    if target.exists():
        return False
    template = project_root / ".env.example"
    if not template.is_file():
        raise SetupError("找不到 .env.example，無法建立本機設定檔。")
    shutil.copyfile(template, target)
    return True


def _run(
    runner: Runner,
    args: Sequence[str | Path],
    *,
    step: str = "設定操作",
    cwd: Path | None = None,
    timeout: int = 1800,
) -> subprocess.CompletedProcess[str]:
    print(f"正在執行：{step}。")
    try:
        string_args = [str(item) for item in args]
        command = string_args
        run_options = {}
        if (
            runner is subprocess.run
            and string_args
            and Path(string_args[0]).suffix.casefold() == ".bat"
        ):
            command = windows_batch_command(string_args)
            run_options["shell"] = True
        result = runner(
            command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **run_options,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupError(
            f"{step}未完成（{type(exc).__name__}）。確認網路與工具狀態後重試；"
            "已完成的步驟會保留。"
        ) from None
    if result.returncode:
        raise SetupError(
            f"{step}失敗（程序結束碼 {result.returncode}）。"
            "為避免輸出私人設定或憑證，未顯示原始安裝輸出；"
            "請確認 Conda／npm 網路及權限後重新執行設定入口。"
        )
    return result


def _tool_version(runner: Runner, executable: str) -> str:
    result = _run(runner, (executable, "--version"), step="檢查工具版本", timeout=20)
    return result.stdout.strip() or result.stderr.strip()


def npm_prefix(node_path: str, npm_path: str) -> tuple[str, ...]:
    """Return an argv-safe npm command that works with Windows npm.cmd."""
    npm_file = Path(npm_path)
    if npm_file.suffix.casefold() in {".cmd", ".bat"}:
        candidates = (npm_file.parent / "node_modules" / "npm" / "bin" / "npm-cli.js",)
        cli = next((candidate for candidate in candidates if candidate.is_file()), None)
        if cli is None:
            raise SetupError(
                "找到了 npm.cmd，但找不到 npm CLI。請修復 Node.js 安裝後重試；"
                "設定程式不會下載或安裝系統軟體。"
            )
        return node_path, str(cli)
    return (npm_path,)


def _conda_prefixes(runner: Runner, conda_exe: Path) -> list[Path]:
    result = _run(
        runner,
        (conda_exe, "env", "list", "--json"),
        step="檢查 Conda 環境",
        timeout=60,
    )
    try:
        payload = json.loads(result.stdout)
        return [Path(prefix) for prefix in payload["envs"]]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise SetupError("Conda 回傳的環境清單格式無法辨識，請檢查 Conda 安裝。") from None


def _verify_existing_environment(
    runner: Runner, conda_exe: Path, prefix: Path
) -> None:
    result = _run(
        runner,
        (
            conda_exe,
            "run",
            "-p",
            prefix,
            "python",
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
        ),
        step="檢查既有 Python 環境",
        timeout=60,
    )
    if result.stdout.strip().splitlines()[-1:] != ["3.12"]:
        raise SetupError(
            "已存在 steam-classroom 環境，但其 Python 不是 3.12。"
            "設定程式不會刪除或重建環境；請先由管理者檢查該環境。"
        )


def main(
    project_root: Path = PROJECT_ROOT,
    *,
    runner: Runner = subprocess.run,
    input_fn=None,
) -> int:
    input_fn = input if input_fn is None else input_fn
    root = project_root.resolve()
    conda_bat = os.environ.get("CLASSPILOT_CONDA_BAT")
    if not conda_bat or not Path(conda_bat).is_file():
        print(
            "找不到本次啟動使用的 Conda 路徑。請重新雙擊 setup_classpilot.bat；"
            "若 Conda 未自動找到，先設定 CONDA_BAT 指向既有 conda.bat。",
            file=sys.stderr,
        )
        return 1

    try:
        conda_exe = conda_executable_from_bat(conda_bat)
        node_path = shutil.which("node")
        npm_path = shutil.which("npm")
        if not node_path or not npm_path:
            raise SetupError(
                "找不到 Node.js 或 npm。請安裝符合 Vite 要求的 Node.js "
                "（20.19+ 或 22.12+），重新開啟 Windows 後再執行設定；"
                "本入口不會下載或安裝系統軟體。"
            )
        node_version_text = _tool_version(runner, node_path)
        node_version = parse_version(node_version_text)
        if node_version is None or not node_version_supported(node_version):
            raise SetupError(
                f"目前 Node.js 版本（{node_version_text or '無法辨識'}）不符合要求。"
                "請使用 20.19+ 或 22.12+，再重新執行。"
            )
        npm_command = npm_prefix(node_path, npm_path)
        npm_version_result = _run(
            runner, (*npm_command, "--version"), step="檢查 npm 版本", timeout=20
        )
        npm_version = npm_version_result.stdout.strip() or npm_version_result.stderr.strip()
        conda_prefixes = _conda_prefixes(runner, conda_exe)
        existing_env = find_target_environment(conda_prefixes)
        if existing_env:
            _verify_existing_environment(runner, conda_exe, existing_env)

        env_exists = (root / ".env").exists()
        planned = [
            f"Conda：{_tool_version(runner, str(conda_exe))}",
            f"Node.js：{node_version_text}；npm：{npm_version}",
            (
                f"保留現有 {CONDA_ENV_NAME}（Python 3.12），並依鎖定檔同步專案套件。"
                if existing_env
                else f"依 environment.yml 建立 {CONDA_ENV_NAME}，再依 requirements-lock.txt 安裝套件。"
            ),
            "依 frontend/package-lock.json 執行 npm ci，並建置正式前端。",
            "只在 .env 不存在時，從 .env.example 建立 .env；不覆寫既有檔案。"
            if not env_exists
            else "保留既有 .env，不覆寫；設定檢查不會顯示其中的值。",
            "最後只讀檢查 PostgreSQL 目標與 schema revision；不建立資料庫、不執行 migration。",
        ]
        print("ClassPilot 首次設定將執行：")
        for item in planned:
            print(f"- {item}")
        print("\n不會安裝系統軟體、修改 base、刪除既有環境或執行資料庫 migration。")
        try:
            answer = input_fn("是否繼續？輸入 Y 確認：").strip().casefold()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消，沒有執行設定。")
            return 0
        if answer not in {"y", "yes"}:
            print("已取消，沒有執行設定。")
            return 0

        if existing_env is None:
            _run(
                runner,
                (conda_exe, "env", "create", "--yes", "--file", root / "environment.yml"),
                step="建立 steam-classroom 環境",
                cwd=root,
            )
        _run(
            runner,
            (
                conda_exe,
                "run",
                "-n",
                CONDA_ENV_NAME,
                "python",
                "-m",
                "pip",
                "install",
                "-r",
                root / "backend" / "requirements-lock.txt",
            ),
            step="同步鎖定的 Python 套件",
            cwd=root,
        )

        created_env = ensure_env_file(root)
        if created_env:
            print("已從 .env.example 建立 .env；請依 docs/INSTALL_WINDOWS.md 設定資料庫。")
        _run(
            runner,
            (*npm_command, "ci"),
            step="依 package-lock.json 安裝前端套件",
            cwd=root / "frontend",
        )
        _run(
            runner,
            (*npm_command, "run", "build"),
            step="建置正式前端",
            cwd=root / "frontend",
        )
        print("Python 套件同步及前端 build 已完成。")

        db_check_args = (
            conda_exe,
            "run",
            "--no-capture-output",
            "-n",
            CONDA_ENV_NAME,
            "python",
            root / "scripts" / "check_db.py",
            "dev",
        )
        print("正在執行：唯讀檢查 PostgreSQL 目標與 schema revision。")
        try:
            result = runner(
                [str(item) for item in db_check_args],
                cwd=str(root / "backend"),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SetupError(
                f"唯讀資料庫檢查未能完成（{type(exc).__name__}）。"
                "請查看設定及 PostgreSQL 狀態後重試。"
            ) from None
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        if result.returncode == 3:
            print(
                "請在本機編輯根目錄 .env，設定 POSTGRES_PASSWORD 後重新執行；"
                "設定值不會顯示在畫面。"
            )
            return 0
        if result.returncode:
            print("請按檢查結果修正資料庫設定或取得明確 migration 核准。", file=sys.stderr)
            return result.returncode
        print("設定完成。日常使用請雙擊 start_classpilot.bat。")
        return 0
    except SetupError as exc:
        print(f"設定未完成：{exc}", file=sys.stderr)
        print("可修正提示中的問題後重新執行；已完成的步驟會保留。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
