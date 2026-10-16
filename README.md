# ClassPilot

ClassPilot 是一套 STEAM 課堂管理系統，協助管理學生、班級、學生註冊，以及每日出席紀錄。介面以繁體中文呈現，並可在桌面和窄螢幕上使用。

## 功能

- **學生管理：** 新增、查看、修改及刪除學生資料。
- **班級管理：** 建立與管理班級，查看班級內已註冊的學生。
- **學生註冊：** 將學生加入班級，或取消註冊。
- **出席管理：** 依班級和日期記錄出席、缺席、遲到或請假，並修改狀態和備註；取消註冊後仍保留出席歷史。
- **響應式介面：** 繁體中文介面會依螢幕大小調整版面。

每位學生在同一班級、同一天最多有一筆出席紀錄。仍有註冊或出席紀錄的學生或班級不能刪除；先移除註冊或相關出席紀錄後才能刪除。

## 技術

| 分層 | 使用技術 |
| --- | --- |
| 前端 | React、TypeScript、Vite、CSS |
| 後端 | Python 3.12、FastAPI、SQLModel |
| 資料庫 | PostgreSQL、Alembic |
| 測試與開發 | pytest、FastAPI TestClient、HTTPX、Conda、npm |

## 安裝與啟動

系統包含前端、後端與 PostgreSQL；請先準備環境及資料庫，再分別啟動前端與後端。

以下步驟以 Windows PowerShell 為例。除非另有說明，標示「專案根目錄」的命令都在剛 clone 下來的 `ClassPilot` 資料夾中執行。

### 1. 安裝所需軟體

請先安裝：

- [Git for Windows](https://git-scm.com/install/windows)：下載專案及管理版本。專案未指定最低 Git 版本，使用官方目前提供的版本即可。
- [Miniconda](https://www.anaconda.com/download)：建立專案專用的 Python 環境。無須另外安裝 Python；`environment.yml` 會在環境中安裝 Python 3.12。
- [Node.js](https://nodejs.org/en/download)：執行前端開發及建置工具。Vite 8 要求 Node.js 20.19+ 或 22.12+；建議安裝官方提供的 Node.js 24 LTS。
- [PostgreSQL for Windows](https://www.postgresql.org/download/windows/)：提供應用程式使用的資料庫。專案沒有指定 PostgreSQL 主版本，請使用 PostgreSQL 官方下載頁提供的正式版本。

pgAdmin 是管理 PostgreSQL 的圖形介面，並非執行 ClassPilot 的必要元件。本指南用 pgAdmin 設定資料庫；若安裝 PostgreSQL 時未選裝 pgAdmin，請從 PostgreSQL for Windows 下載頁取得安裝程式並選裝它。

安裝完成後開啟新的 PowerShell 視窗，確認工具可用：

**執行位置：任意資料夾**

```powershell
git --version
conda --version
node --version
npm --version
```

應看到各工具的版本資訊。Node.js 版本需符合上方 Vite 要求。

### 2. 下載專案

**執行位置：準備存放專案的資料夾**

```powershell
git clone https://github.com/Yau2004/ClassPilot.git
cd ClassPilot
```

完成後，PowerShell 的目前路徑應以 `ClassPilot` 結尾，且能看到 `README.md`、`backend` 和 `frontend`。

### 3. 建立並啟用 Python 環境

`environment.yml` 建立 Python 3.12 環境，但不包含後端套件；接著使用 repository 的鎖定清單安裝精確版本。Conda 環境名稱為 `steam-classroom`，不會安裝到 `base`。

**執行位置：專案根目錄**

```powershell
conda env create -f environment.yml
conda activate steam-classroom
python --version
python -m pip install -r backend/requirements-lock.txt
```

若環境已建立，跳過第一行；啟用後 `python --version` 應顯示 Python 3.12。套件安裝完成後，執行 `python -m pip --version` 時顯示的路徑應位於 `steam-classroom` 環境中。

### 4. 建立 PostgreSQL 帳戶及資料庫

請使用 PostgreSQL 安裝時設定的管理員帳戶在 pgAdmin 建立專用應用程式帳戶。不要讓應用程式以 PostgreSQL 管理員身分連線。

1. 在 pgAdmin 連線至 PostgreSQL 伺服器，展開 **Login/Group Roles**，選擇建立登入角色。
2. 將角色名稱設為 `steam_app`，在密碼欄輸入自己設定的密碼，並允許登入。不要給予 Superuser、建立角色或建立資料庫權限。
3. 在 pgAdmin 連線到伺服器的 `postgres` 維護資料庫，開啟 Query Tool，執行以下 SQL。執行者需是有建立資料庫權限的管理員；兩個資料庫的 Owner 都設為應用程式帳戶。

```sql
CREATE DATABASE steam_classroom_dev OWNER steam_app;
CREATE DATABASE steam_classroom_test OWNER steam_app;
```

完成後，pgAdmin 的 Databases 清單中應有 `steam_classroom_dev` 和 `steam_classroom_test`。測試資料庫與開發資料庫分開，測試程式只會連接測試資料庫。

### 5. 設定環境變數

**執行位置：專案根目錄**

```powershell
Copy-Item .env.example .env
notepad .env
```

在 `.env` 填入剛設定的 `steam_app` 密碼，並依你的 PostgreSQL 設定確認下列欄位。請保留開發庫和測試庫的名稱，不要將兩者設成同一個資料庫。

| 欄位 | 用途 |
| --- | --- |
| `POSTGRES_HOST` | PostgreSQL 主機；本機通常為 `localhost` |
| `POSTGRES_PORT` | PostgreSQL 連接埠；依安裝時設定填寫 |
| `POSTGRES_USER` | 應用程式帳戶，預設 `steam_app` |
| `POSTGRES_PASSWORD` | 你為 `steam_app` 設定的密碼 |
| `POSTGRES_DB` | 開發資料庫，使用 `steam_classroom_dev` |
| `POSTGRES_TEST_DB` | 測試資料庫，使用 `steam_classroom_test` |

前端範本 `frontend/.env.example` 已使用本機 API 預設位址 `http://127.0.0.1:8000`，初次使用不需另建前端環境檔。

### 6. 初始化開發資料庫

Alembic 是資料表結構遷移工具。首次啟動前，使用它在開發資料庫建立應用程式所需資料表。指令會明確指定目標為 `dev`。

**執行位置：專案根目錄；先確認已啟用 `steam-classroom` 環境**

```powershell
$env:PYTHONPATH = "backend"
python -m alembic -c backend/alembic.ini -x database=dev upgrade head
```

看到 migration 完成訊息後，開發資料庫即可供後端使用。測試資料庫會在「執行測試」步驟獨立初始化。

### 7. 啟動後端與前端

開啟兩個 PowerShell 視窗，並保持兩個視窗開啟。

**視窗一：後端。執行位置：專案根目錄；先啟用 `steam-classroom` 環境。**

```powershell
conda activate steam-classroom
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```

看到 Uvicorn 啟動訊息後，API 文件可在 <http://127.0.0.1:8000/docs> 開啟。

**視窗二：前端。執行位置：專案根目錄。**

```powershell
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

第一次啟動時，`npm ci` 依 `package-lock.json` 安裝前端依賴。Vite 顯示本機網址後，開啟 <http://127.0.0.1:5173>。

## 執行測試

後端測試使用獨立的 `steam_classroom_test` 資料庫。先在根目錄啟用 Conda 環境，並將測試資料庫升級至最新資料表版本，再執行測試。

**執行位置：專案根目錄；先啟用 `steam-classroom` 環境**

```powershell
conda activate steam-classroom
$env:PYTHONPATH = "backend"
python -m alembic -c backend/alembic.ini -x database=test upgrade head
python -m pytest backend/tests
```

測試設定必須在 `.env` 指定 `POSTGRES_TEST_DB=steam_classroom_test`；若缺少或名稱不符，測試會中止，不會改用開發資料庫。資料庫需先建立並由 `steam_app` 擁有。

前端型別檢查與正式建置使用相同的 `build` 指令；`build` 會先執行 TypeScript 型別檢查。

**執行位置：`frontend` 資料夾**

```powershell
npm run typecheck
npm run build
```

## 使用注意

- 目前沒有登入與權限管理，請限於本機使用，不要直接將服務暴露到公網。
- `.env` 含本機資料庫密碼，不應提交到 Git；repository 已提供不含真實密碼的 `.env.example`。
- 每位學生在同一班級、同一天最多一筆出席紀錄，尚未登記不代表缺席。
- 學生或班級仍有註冊或出席紀錄時，系統會阻止刪除，以保留相關紀錄。
