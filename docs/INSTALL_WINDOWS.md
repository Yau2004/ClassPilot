# Windows 首次安裝

本流程適用於新的 ClassPilot checkout。它不會安裝 Windows 系統軟體、建立 PostgreSQL 帳戶／資料庫、執行 migration 或修改現有 `.env`。首次安裝完成後，日常使用只需根目錄的啟動及停止入口。

## 需要先準備

- Windows 及 Git for Windows，用來下載專案。
- Miniconda 或 Anaconda。安裝後需能找到 `conda.bat`；若自動探索不到，可設定使用者環境變數 `CONDA_BAT` 指向該檔案，不必修改全域 PATH。
- Node.js 20.19+ 或 22.12+，同時提供 npm。這是目前 Vite 8 的執行要求。
- PostgreSQL 服務及一個供 ClassPilot 使用的開發資料庫。一般使用不需要測試資料庫。

在要放置專案的資料夾開啟 PowerShell，下載 repository（下例資料夾名稱刻意含空格）：

```powershell
git clone https://github.com/Yau2004/ClassPilot.git "ClassPilot Project"
```

確認新資料夾根目錄有 `setup_classpilot.bat`、`environment.yml`、`backend`、`frontend` 及 `.env.example`。設定入口以自身位置找專案，不依賴目前工作目錄；支援路徑含空格及中文字。

## 執行首次設定

雙擊根目錄的 `setup_classpilot.bat`。入口會檢查 Conda、Node.js、npm 和目標 Python 環境，列出接下來要做的操作並要求輸入 `Y` 才繼續。

- 若 `steam-classroom` 不存在，會按 `environment.yml` 建立 Python 3.12 環境。
- 若環境已存在，會先確認 Python 3.12；不相容時停止，不會刪除或重建。相容時在該環境內按 `backend/requirements-lock.txt` 同步 Python 套件。
- 在 `frontend` 使用 `npm ci` 按 `package-lock.json` 安裝鎖定依賴，再執行正式 build。
- 只在根目錄 `.env` 不存在時，從 `.env.example` 建立；若已存在，原封不動保留。
- 不會在 Conda `base` 安裝專案套件，也不會建立 `.venv`。

若流程中斷，修正顯示的 prerequisite 或連線問題後再次雙擊即可。完成的環境、套件及 build 會保留；不相容的既有環境需由使用者自行檢查，不會自動移除。安裝輸出會被收起，避免套件來源設定或其他本機資料意外顯示。

## 設定 PostgreSQL

設定入口不建立伺服器、帳戶或資料庫。請由有權管理 PostgreSQL 的人員先準備一個空的開發資料庫及專用應用程式登入角色，不要使用 PostgreSQL 超級使用者供應用程式連線。以 pgAdmin 為例：

1. 在伺服器的 **Login/Group Roles** 建立登入角色（範本預設 `steam_app`），在 pgAdmin 的密碼欄設定密碼並允許登入；不要授予 Superuser、Create role 或 Create database。
2. 建立開發資料庫（範本預設 `steam_classroom_dev`），將 Owner 設為上述應用角色。若組織不允許由應用角色擁有資料庫，DBA 必須明確授予它連線權、schema 使用權及目前應用所需的資料表／sequence 權限；執行已核准 migration 時，遷移角色還需具備該 migration 所需的 schema DDL 權限。
3. 保管好角色密碼，只在本機 `.env` 設定。不要以 PostgreSQL 管理員帳戶填入 `POSTGRES_USER`。

一般使用者不需要 `steam_classroom_test`。只有執行資料庫整合測試的開發者才需要獨立測試資料庫。

在本機文字編輯器開啟根目錄 `.env`，填入伺服器、port、應用角色、密碼及開發資料庫名稱。密碼只填在 `.env`，不要放進命令列、截圖、問題回報或日誌。範本中的 `CHANGE_ME_WITH_YOUR_LOCAL_PASSWORD` 必須換成實際密碼。不要把 `.env` 加入版本控制。

再次執行 `setup_classpilot.bat`：它會保留 `.env`，並以應用程式設定唯讀連線，回報實際資料庫名稱及 Alembic revision。所連資料庫名稱須符合 `.env` 的 `POSTGRES_DB`；目前 readiness 要求 revision `20261019_0004`。若資料庫尚無 schema 或 revision 不符，設定會明確停止並指出需要的 revision，不會執行 `create_all`、`stamp`、手動 DDL 或 migration。請先確認實際目標並取得明確 migration 授權，再由授權人員按更新／維護流程執行。

資料庫 schema migration 可能不可逆或影響既有資料。正式或重要資料在 migration 前應安排 PostgreSQL 備份並確認還原方式；程式碼回退不會回復資料庫 schema 或資料。

## 開始使用

當設定檢查及 readiness 成功後，雙擊根目錄 `start_classpilot.bat`。停止時雙擊 `stop_classpilot.bat`。完整日常行為見[日常啟動與停止](DAILY_START.md)。
