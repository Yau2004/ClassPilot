# 常見錯誤與安全診斷

診斷前不要貼出 `.env`、完整資料庫連線字串、密碼、學生資料或包含這些內容的 traceback。提供錯誤類型、非秘密資料庫名稱及 revision 即可。

## 設定入口找不到 Conda

確認已安裝 Miniconda／Anaconda，且安裝目錄中有 `condabin\conda.bat`。若自動探索不到，可在 Windows 使用者環境變數 `CONDA_BAT` 指向該檔案，重新開啟檔案總管／終端後再雙擊設定入口。無須執行 `conda init` 或修改系統 PATH。

## Python 環境不相容

設定入口只接受 `steam-classroom` 中的 Python 3.12。它不會刪除或重建舊環境；先檢查該 Conda 環境及其他使用者，再決定如何修復。不要在 `base` 安裝專案依賴，也不要建立 `.venv`。

## 找不到 Node.js 或版本太舊

安裝 Node.js 20.19+ 或 22.12+ 並確認 npm 可用。設定入口不會自行下載或安裝系統軟體。

## `.env` 缺漏或資料庫連線失敗

設定入口只會在 `.env` 不存在時複製範本。確認 `.env` 位於專案根目錄、`POSTGRES_PASSWORD` 已在本機填入、PostgreSQL 服務可達，以及 `POSTGRES_DB`、host、port 和應用角色正確。不要將密碼貼入終端命令或回報內容。

## Schema revision 不符

唯讀檢查會顯示目標資料庫名稱、目前 revision 和所需 revision `20261019_0004`。確認實際目標及 migration 後，取得該資料庫的明確授權，再由授權人員執行精確 revision。不要使用 `stamp`、`create_all`、手動 DDL 或 `upgrade head` 繞過審核；日常啟動永不自動 migration。

## 8000 埠衝突或服務未就緒

啟動器不會終止其他程式。先確認 ClassPilot 是否已啟動；若不是，請由使用者辨識並關閉佔用程式，或交由管理者調查。資料庫未就緒、設定錯誤或 revision 不符時查看啟動畫面及 `%LOCALAPPDATA%\ClassPilot\<專案識別碼>\logs\service.log`。回報前遮蔽本機路徑以外的個人資料，不要貼完整 `.env`。

## 日誌與求助資料

ClassPilot 日誌應遮蔽連線憑證，並避免記錄學生支援需要、教學備註或評價。若日誌似乎含秘密，不要轉貼；先限制檔案存取並聯絡維護者。程式的遮蔽修復不代表曾共用的資料庫密碼已輪替；若需輪替，應安排帳戶與 `.env` 同步更新及服務重啟。
