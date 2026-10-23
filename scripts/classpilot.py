"""Run and stop the local, single-process ClassPilot service on Windows."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import os
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"


def ensure_backend_import_path() -> None:
    backend_path = str(BACKEND_ROOT)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)


ensure_backend_import_path()

HOST = "127.0.0.1"
PORT = 8000
BASE_URL = f"http://{HOST}:{PORT}"
INSTANCE_ID = hashlib.sha256(str(PROJECT_ROOT).casefold().encode("utf-8")).hexdigest()[:16]
STATE_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
STATE_DIR = STATE_ROOT / "ClassPilot" / INSTANCE_ID
LOCK_FILE = STATE_DIR / "service.lock"
STOP_FILE = STATE_DIR / "stop.request"
LOG_DIR = STATE_DIR / "logs"
LOG_FILE = LOG_DIR / "service.log"
REQUIRED_SCHEMA_REVISION = "20261019_0004"


def readiness_failure_message(headers) -> str:
    """Return a safe next step based on the readiness endpoint's reason code."""
    reason = headers.get("X-ClassPilot-Readiness-Reason", "")
    if reason in {"schema_mismatch", "schema_unavailable"}:
        return (
            "資料庫 schema 尚未就緒（需要 revision "
            f"{REQUIRED_SCHEMA_REVISION}）。請先核對並核准資料庫 migration；"
            "啟動器不會自動執行 migration。"
        )
    if reason == "configuration_incomplete":
        return "資料庫設定不完整。請檢查專案根目錄 .env 的 PostgreSQL 欄位；秘密值不會顯示。"
    if reason == "database_unavailable":
        return "無法連線 PostgreSQL。請確認資料庫服務及 .env 連線設定。"
    return "資料庫尚未就緒。請確認 PostgreSQL 可連線及目前核准的 schema migration 已套用。"


def _request(path: str, timeout: float = 1.0):
    request = urllib.request.Request(BASE_URL + path, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, {}, b""


def _is_our_service() -> bool:
    _, headers, _ = _request("/health")
    return (
        headers.get("X-ClassPilot-Service") == "ClassPilot"
        and headers.get("X-ClassPilot-Instance") == INSTANCE_ID
    )


def _ready() -> bool:
    status, headers, _ = _request("/ready", timeout=4.0)
    return (
        status == 200
        and headers.get("X-ClassPilot-Service") == "ClassPilot"
        and headers.get("X-ClassPilot-Instance") == INSTANCE_ID
    )


def _lock_file():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handle = LOCK_FILE.open("a+b")
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        if handle.read(1) == b"":
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return None
    else:
        import fcntl

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return None
    return handle


def _log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as log:
        log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


def create_uvicorn_config():
    """Build the real Uvicorn config without loading or starting the app."""
    ensure_backend_import_path()
    import uvicorn

    return uvicorn.Config(
        "app.main:app",
        host=HOST,
        port=PORT,
        log_level="warning",
        access_log=False,
    )


def _show_start_error(message: str) -> int:
    print(f"\nClassPilot 啟動器\n\n{message}", flush=True)
    return 1


def stop() -> int:
    if not _is_our_service():
        if _request("/health")[0] is not None:
            return _show_start_error("8000 埠上的服務不是此專案的 ClassPilot；沒有停止任何程序。")
        return _show_start_error("目前沒有正在執行的 ClassPilot。")
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STOP_FILE.write_text("stop\n", encoding="utf-8")
    except OSError:
        return _show_start_error("無法送出停止要求。請關閉 ClassPilot 啟動視窗。")

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if not _is_our_service():
            STOP_FILE.unlink(missing_ok=True)
            print("ClassPilot 已停止。")
            return 0
        time.sleep(0.25)
    return _show_start_error("停止要求已送出，但服務尚未確認停止。請稍後檢查啟動視窗。")


def start() -> int:
    if sys.version_info[:2] != (3, 12):
        return _show_start_error("steam-classroom 必須使用 Python 3.12。請依 environment.yml 建立或修復環境。")
    if not (PROJECT_ROOT / ".env").is_file():
        return _show_start_error("找不到專案根目錄的 .env。請先複製 .env.example 為 .env 並完成資料庫設定。")
    if not (PROJECT_ROOT / "frontend" / "dist" / "index.html").is_file():
        return _show_start_error("找不到前端正式建置檔。請在 frontend 執行 npm ci（首次設定）及 npm run build。")

    # A live listener is reused only when the service identifies this exact checkout.
    status, _, _ = _request("/health")
    if status is not None:
        if _is_our_service():
            if _ready():
                webbrowser.open(BASE_URL)
                print("ClassPilot 已在執行，已開啟瀏覽器。")
                return 0
            _, headers, _ = _request("/ready", timeout=4.0)
            return _show_start_error("偵測到本專案的服務，但" + readiness_failure_message(headers))
        return _show_start_error("8000 埠已被其他服務使用。沒有終止或修改該程序；請先關閉佔用程式再啟動 ClassPilot。")

    lock = _lock_file()
    if lock is None:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if _ready():
                webbrowser.open(BASE_URL)
                print("ClassPilot 已由另一個啟動視窗啟動，已開啟瀏覽器。")
                return 0
            time.sleep(0.25)
        return _show_start_error("ClassPilot 已有啟動程序；請稍候，或關閉該啟動視窗後再試。")

    try:
        STOP_FILE.unlink(missing_ok=True)
        from app.core.config import get_settings
        from sqlalchemy import create_engine, text

        try:
            settings = get_settings()
            # Validate required config without printing the URL or secret values.
            settings.database_url("dev")
        except (ValueError, OSError):
            return _show_start_error("資料庫設定不完整。請檢查專案根目錄 .env 的 PostgreSQL 欄位；秘密值不會顯示。")

        engine = create_engine(
            settings.database_url("dev"),
            pool_pre_ping=True,
            connect_args={"connect_timeout": 3},
        )
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            return _show_start_error("無法連線 PostgreSQL。請確認資料庫服務、.env 連線設定及開發資料庫。")
        finally:
            engine.dispose()

        config = create_uvicorn_config()
        import uvicorn

        server = uvicorn.Server(config)

        def run_server() -> None:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as output:
                with redirect_stdout(output), redirect_stderr(output):
                    server.run()

        server_thread = threading.Thread(target=run_server, name="classpilot-server", daemon=True)
        server_thread.start()
        _log("Startup requested on loopback.")

        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            if STOP_FILE.exists():
                server.should_exit = True
                break
            if _ready():
                webbrowser.open(BASE_URL)
                print("ClassPilot 已就緒。保持此視窗開啟；停止時雙擊 stop_classpilot.bat。")
                break
            if not server_thread.is_alive():
                return _show_start_error("ClassPilot 服務提前結束。請查看安全日誌及環境設定。")
            time.sleep(0.25)
        else:
            _, headers, _ = _request("/ready", timeout=4.0)
            server.should_exit = True
            server_thread.join(timeout=10)
            return _show_start_error(
                "服務未能在 40 秒內就緒。" + readiness_failure_message(headers)
            )

        while server_thread.is_alive():
            if STOP_FILE.exists():
                server.should_exit = True
            server_thread.join(timeout=0.5)
        _log("Service stopped.")
        return 0
    finally:
        STOP_FILE.unlink(missing_ok=True)
        lock.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "stop"))
    action = parser.parse_args().action
    return start() if action == "start" else stop()


if __name__ == "__main__":
    raise SystemExit(main())
