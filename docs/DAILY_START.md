# 日常啟動 ClassPilot

## 日常使用

1. 雙擊專案根目錄的 `start_classpilot.bat`。
2. 等待啟動視窗顯示服務已就緒，瀏覽器會自動開啟 `http://127.0.0.1:8000`。
3. 使用期間保留啟動視窗。
4. 完成後雙擊 `stop_classpilot.bat`，或在啟動視窗按 Ctrl+C。

日常啟動不安裝套件、不建置前端，也不執行資料庫 migration。Readiness 會唯讀檢查 PostgreSQL 連線及 Alembic revision `20261019_0004`；revision 較舊時不會回報就緒，需先另行審核及套用 migration。日誌位於 `%LOCALAPPDATA%\ClassPilot\<專案識別碼>\logs\service.log`，不記錄資料庫連線字串或學生敏感欄位。

## 首次設定前提

日常入口需要已存在的 `steam-classroom` Conda 環境、專案根目錄 `.env`、可連線的 PostgreSQL 開發資料庫，以及已建置的 `frontend\dist`。

若自動探索不到 Conda，可設定使用者環境變數 `CONDA_BAT` 指向現有 `conda.bat`；不需要修改全域 PATH。首次安裝請看[Windows 首次安裝](INSTALL_WINDOWS.md)，更新請看[Windows 更新](UPDATE_WINDOWS.md)。

## 常見狀況

- 缺少 `.env`：依 `.env.example` 建立並完成 PostgreSQL 設定；啟動器不會建立或覆寫 `.env`。
- 資料庫不可達：確認 PostgreSQL 服務及 `.env` 中的開發資料庫設定。
- Schema 尚未就緒：若啟動器指出需要 revision `20261019_0004`，先確認目標資料庫並取得核准，再以明確 Alembic 目標升級；啟動器不會自動 migration。
- 8000 埠被其他程式使用：啟動器會停止嘗試並保留該程式，不會終止它；請先關閉佔用程式再啟動。
- 重複雙擊：若相同專案的服務已就緒，會重用它並開啟瀏覽器。
- 缺少前端 build：首次設定時在 `frontend` 執行 `npm ci` 及 `npm run build`；日常啟動不會執行這些指令。
