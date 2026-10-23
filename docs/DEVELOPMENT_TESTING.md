# 開發與測試

一般日常使用不需要 Vite dev server 或測試資料庫。本頁只供開發者使用；專案 Python 套件固定裝在 Python 3.12 的 `steam-classroom` Conda 環境，不裝在 `base`，也不建立 `.venv`。

## 開發環境

在 Conda Prompt 或已設定 Conda 的 PowerShell：

```powershell
conda env create -f environment.yml
conda activate steam-classroom
python -m pip install -r backend/requirements-lock.txt
```

環境已存在時不要重建；確認 Python 3.12 後依鎖定檔安裝。前端依賴使用 `frontend/package-lock.json`：

```powershell
Set-Location frontend
npm ci
npm run typecheck
npm run lint
npm test
npm run build
```

開發時可在兩個終端分別於專案根目錄執行：

```powershell
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```

```powershell
Set-Location frontend
npm run dev -- --host 127.0.0.1
```

後端測試由 `backend/tests/conftest.py` 保護。資料庫整合測試必須明確設定 `POSTGRES_TEST_DB=steam_classroom_test`，先驗證 `current_database()` 及預期 Alembic revision，並在交易中回滾。測試設定缺漏或 revision 不符會 fail closed，絕不改用開發資料庫。

一般安裝不需要測試庫。只有被授權執行整合測試的開發者，才需由資料庫管理員另行提供隔離的 `steam_classroom_test`；不要將真實學生資料複製進測試庫。測試清理不得對測試交易外執行。

## 資料庫 schema

本專案只支援 PostgreSQL，schema 由 Alembic 管理。migration 要求明確 `dev` 或 `test` 目標，並驗證連線資料庫名稱。撰寫或檢閱 migration 不等於獲准執行；執行前須明確核准資料庫與目標 revision。不可用 `create_all`、`stamp` 或手動 DDL 替代 migration。
