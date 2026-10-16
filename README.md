# ClassPilot

ClassPilot 是一個以繁體中文呈現的本機 STEAM 課堂管理作品集 MVP。它把學生、班級、註冊及每日出席流程整理成可操作的管理介面，方便在本機展示完整資料流程。

## MVP 範圍

- 學生管理：列表、新增、修改、刪除；學生資料按前端頁面分頁顯示。
- 班級管理：列表、新增、修改、刪除；檢視學生名單、加入學生及取消註冊。
- 出席管理：選擇班級與本機日期、新增及檢視紀錄、修改狀態／備註及刪除。
- 後端提供 FastAPI／SQLModel API，PostgreSQL 儲存資料，Alembic 管理 schema。
- 前端使用 React、TypeScript、Vite 和一般 CSS；桌面側欄在窄螢幕縮成精簡導覽。

這是本機展示 MVP，尚無身分驗證或權限保護，不可當作具有安全存取控制的公開服務。系統目前以「同一班級、同一學生、同一日期最多一筆」管理出席，不支援同日多節課或課堂場次。未登記的學生不會自動視為缺席。取消註冊會保留出席歷史；有註冊或出席歷史時，學生與班級的刪除會被拒絕。

## 技術架構

- Backend：Python 3.12、FastAPI、SQLModel、Pydantic Settings、SQLAlchemy、psycopg、Alembic。
- Database：PostgreSQL，開發庫 `steam_classroom_dev`、測試庫 `steam_classroom_test`。
- Frontend：React、TypeScript、Vite、CSS variables。
- Tests：pytest、FastAPI TestClient、httpx；前端提供 TypeScript 型別檢查及 Vite production build。

## Windows PowerShell 設定

以下命令從專案根目錄 `D:\ProgramData\Codex Project\ClassPilot` 執行。後端使用既有 Conda 環境，不使用 base 或 `.venv`：

```powershell
$steamPython = 'C:\Users\User\.conda\envs\steam-classroom\python.exe'
& $steamPython --version
```

專案根目錄 `.env` 由後端按絕對路徑載入。請在本機自行填寫資料庫密碼；不要把 `.env` 加入版本控制。測試設定必須指定 `POSTGRES_TEST_DB=steam_classroom_test`，不可改用開發庫。環境範本位於 [.env.example](.env.example)，前端範本位於 [frontend/.env.example](frontend/.env.example)，前端檔案只設定 API 位址，不放資料庫憑證。

首次使用時，在專案根目錄安裝前端本機依賴：

```powershell
cd frontend
npm install
npm run typecheck
npm run build
```

前端預設 API 位址為 `http://127.0.0.1:8000`。如需改用其他後端位址，複製範本為 `frontend/.env.local` 並修改 `VITE_API_BASE_URL`：

```powershell
Copy-Item .env.example .env.local
```

例如將 `VITE_API_BASE_URL` 設為 `http://127.0.0.1:8100`。後端 CORS 目前只允許 `http://127.0.0.1:5173`，不要把允許來源設為萬用字元。

## Database migrations

從專案根目錄執行；先確認目標和 revision，migration 不會在應用程式啟動時自動執行。`20261013_0001` 建立學生表；`20261015_0002` 建立班級及學生註冊表；`20261015_0003` 建立出席紀錄表。

目前 `steam_classroom_dev` 和 `steam_classroom_test` 均已確認在 `20261015_0003`。開發庫在本機資料筆數為 0 時套用 0002、0003；`alembic current` 及 `alembic check` 均已通過。

```powershell
$env:PYTHONPATH = 'backend'
& $steamPython -m alembic -c backend/alembic.ini -x database=dev current
& $steamPython -m alembic -c backend/alembic.ini -x database=dev upgrade head
& $steamPython -m alembic -c backend/alembic.ini -x database=dev current
& $steamPython -m alembic -c backend/alembic.ini -x database=dev check
```

測試庫需要獨立升級：

```powershell
& $steamPython -m alembic -c backend/alembic.ini -x database=test upgrade head
& $steamPython -m alembic -c backend/alembic.ini -x database=test current
```

不要對資料庫執行 downgrade、清空或重建來解決一般設定問題。若 revision 與預期不符，先調查資料庫狀態。

## 啟動服務

開兩個 PowerShell 視窗，工作目錄均為專案根目錄。

視窗一，啟動 FastAPI：

```powershell
$steamPython = 'C:\Users\User\.conda\envs\steam-classroom\python.exe'
& $steamPython -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```

API 文件位於 <http://127.0.0.1:8000/docs>；`GET /health` 只表示應用程式正在運作，不代表資料庫連線正常。

視窗二，啟動 React 前端：

```powershell
cd frontend
npm run dev -- --host 127.0.0.1
```

開啟 <http://127.0.0.1:5173>。Vite 前端只能連到設定的 API 位址；本機 CORS 僅允許 `127.0.0.1:5173`。

## 測試與 build

完整後端測試只會使用 `steam_classroom_test`。Fixture 會檢查實際資料庫名稱與 revision，並以 rollback 隔離每個測試。

```powershell
# 從專案根目錄
$steamPython = 'C:\Users\User\.conda\envs\steam-classroom\python.exe'
& $steamPython -m pytest backend/tests

# 從 frontend 目錄
npm run typecheck
npm run build
```

## 3 分鐘展示流程

1. 在「學生管理」建立兩位虛構學生，展示姓名與年齡驗證，再修改其中一位資料。
2. 在「班級管理」建立一個 STEAM 班級，點開班級詳情，將兩位學生加入名單。
3. 在「出席管理」選擇班級及今天日期，為一位學生新增「出席」，並為另一位新增「遲到」與備註。
4. 修改其中一筆出席狀態，切回班級頁取消一位學生註冊，再回到出席頁確認歷史資料仍可見。
5. 可嘗試刪除仍有註冊或出席歷史的學生／班級，展示後端 409 保護。

## 視覺參考與限制

視覺採柔和藍白背景、半透明圓角卡片、細邊框與淺陰影，參考 [Soft UI Evolution](https://github.com/joshhu/uitest/blob/main/styles/19-soft-ui-evolution.html) 的視覺語言；本專案依需求自行實作 CSS，沒有直接複製參考頁程式碼。

目前尚未提供登入、角色權限、課表／多節課出席、功課、學習進度、統計、CSV、雲端部署或 React 路由。學生列表 API 目前回傳完整名冊，前端在頁面上分頁顯示；班級、班級學生及出席列表會逐頁讀取既有 offset／limit API。介面未宣稱符合 WCAG，且本機展示版本沒有公開服務所需的存取控制。
