# Windows 更新

更新流程由使用者控制版本變更；ClassPilot 不會自動 `pull`、reset、clean 或切換分支。

1. 先雙擊 `stop_classpilot.bat`，確認服務停止。
2. 在專案目錄檢查 `git status` 及需要保留的差異。先保存或提交自己的工作；不要用 reset、clean 或覆蓋方式丟棄修改。
3. 由使用者依團隊流程取得並檢閱更新程式碼。更新前保留根目錄 `.env`；它包含本機連線設定，不應被版本更新覆蓋。
4. 閱讀更新說明、Alembic migration 及相容性。沒有 migration 時，雙擊 `setup_classpilot.bat`，確認顯示的 Conda 環境／鎖定 Python 依賴操作、`npm ci` 和正式 build 後輸入 `Y`。此流程可重跑，但會按鎖定檔同步套件並重建前端。
5. 若更新包含 migration，先核對目標資料庫、目前 revision、migration 影響及備份／還原計畫。停止在明確授權邊界；取得該資料庫及目標 revision 的批准後，才由授權人員執行指定 migration。安裝器、啟動器均不會代跑。
6. 設定檢查成功後雙擊 `start_classpilot.bat`，確認 readiness 和瀏覽器頁面，再使用 `stop_classpilot.bat` 停止。

若更新中途失敗，記下最後完成的步驟及安全錯誤訊息，修正後重新執行設定入口。依賴安裝和 build 可以重跑；不要為恢復程式而清除 `.env` 或資料庫。若 migration 已執行，回退程式碼不會反向還原資料庫；依已核准的備份／還原計畫處理。

建議在更新任何重要資料庫前，使用 PostgreSQL 原生備份工具建立可還原備份，並驗證備份檔及還原程序。migration、資料刪除及 schema 轉換都可能不可逆；不要把略過備份視為其他使用者的預設方式。
