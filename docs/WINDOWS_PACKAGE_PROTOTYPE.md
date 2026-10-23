# Windows 封裝可行性原型

本文件描述第 1 階段封裝基礎及第 2 階段隔離 PostgreSQL 原型，不是普通用戶安裝包。第 2 階段核心整合已由使用者確認：手動 ZIP 匯入與驗證、隔離 cluster 初始化、精確 migration、封裝網站/readiness、合成資料保存、停止與重啟均通過。乾淨 Windows、一般使用者環境、桌面選檔互動仍未驗證。PyInstaller onedir 封裝 Python／FastAPI、Alembic 資源及前端正式 build；不把 PostgreSQL binaries 放進 ClassPilot 產物。普通用戶發行流程與目前狀態見 [Windows 發行流程原型](WINDOWS_RELEASE_GUIDE.md)。

## 建置

建置端需要 Windows x64、Python 3.12 的既有 `steam-classroom` 環境、Node.js，以及已安裝的前端 `package-lock.json` 依賴。Python 執行期依賴獨立列在 `backend/requirements-runtime-lock.txt`；只供封裝使用的工具獨立列在根目錄 `requirements-packaging-lock.txt`。不要在 Conda base 安裝，也不要建立 `.venv`。

首次準備時，從已啟用的 `steam-classroom` 環境，以封裝鎖定檔安裝建置工具；依賴安裝只在建置端進行：

```powershell
python -m pip install --no-user -r requirements-packaging-lock.txt
```

前端依 `frontend/package-lock.json` 準備 Node 依賴。之後由 repository 根目錄執行：

```powershell
python scripts\build_windows_release.py
```

建置入口會先執行 `npm run build`，再執行 PyInstaller。使用者提供 ZIP 的原型產物位於 `dist/windows-user-zip/ClassPilotPrototype/`，與先前的封裝原型輸出分開。日常開發啟動器不呼叫這個建置入口。

## 執行封裝自我檢查

在 repository 外執行：

```powershell
& 'D:\path with spaces\ClassPilotPrototype\ClassPilotPrototype.exe' --self-check --report-file "$env:TEMP\classpilot-self-check.json"
```

自我檢查載入前端資產、FastAPI application、Alembic revision graph 及 PostgreSQL 匯入規格，並建立 loopback Uvicorn 設定；不開啟 listener、不呼叫資料庫 readiness、不連資料庫、不讀取開發 `.env`、不讀取或寫入 LocalAppData 設定，也不執行 migration。報告檔只會寫到明確指定的位置。檢查會回報需要使用者提供 PostgreSQL ZIP；PostgreSQL binaries 未封裝是預期狀態。

## PostgreSQL ZIP 設定原型

支援規格目前固定為 PostgreSQL 18.6、EDB package revision 5、Windows x64，檔名 `postgresql-18.6-5-windows-x64-binaries.zip`。設定視窗顯示 [PostgreSQL 官方 Windows 下載頁](https://www.postgresql.org/download/windows/)，並讓使用者自行選取本機 ZIP；不會掃描其他資料夾或自動下載。實作以 vendor 公布的 SHA-256 固定比對，檢查所需執行檔、x64 PE 標記、版本、兩份授權 notice 及 Microsoft Visual C++ runtime 檔案。檢查失敗不會建立資料庫。

驗證後只擷取執行所需的 `bin`、`lib`、`share` 和授權 notices 到明確指定的原型暫存資料夾；不擷取 pgAdmin、StackBuilder 或原始 ZIP。接著顯示確認畫面，只有使用者確認後才建立隔離 cluster 和原型資料庫，並套用 manifest 明列的 revision `20261019_0004`。一般啟動不會執行 migration。此流程仍是原型，不會寫入正式 `%LOCALAPPDATA%\ClassPilot`。

## 模式界線

- 開發模式繼續使用根目錄 `.env`、`steam-classroom`、Node 前端開發伺服器，以及原有 start／stop 入口。
- 凍結發行原型不使用或回退至開發 `.env`。原型設定與憑證只允許寫入隔離暫存目錄，不寫入正式 LocalAppData。
- `%LOCALAPPDATA%\ClassPilot` 是後續單一 Windows 使用者發行模式的規劃位置；本原型不建立其中任何檔案或目錄。
- PostgreSQL ZIP 匯入與免資料庫驗證可獨立測試；在收到新的使用者提供 ZIP 前，不代表 PostgreSQL cluster、migration、HTTP、桌面啟停或資料持久化已通過。
- EDB 自動下載未獲許可，因此 ClassPilot 不含自動下載功能。先前自動取得的 archive 不作為本次手動選取測試檔。
