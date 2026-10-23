# ClassPilot 文件入口

- [Windows 首次安裝](INSTALL_WINDOWS.md)：新用戶安裝前提、設定入口、PostgreSQL 設定及首次 readiness。
- [日常啟動與停止](DAILY_START.md)：已設定電腦的日常操作。
- [Windows 更新](UPDATE_WINDOWS.md)：安全檢查、依賴同步、前端 build 和 migration 授權邊界。
- [常見錯誤與安全診斷](TROUBLESHOOTING.md)：Conda、Node、設定、資料庫、schema、port 及日誌問題。
- [開發與測試](DEVELOPMENT_TESTING.md)：開發模式、測試資料庫隔離及相關命令。
- [課節與學生 API](LESSON_SESSIONS_API.md)：目前 API、欄位與香港時區規則。
- [Windows 封裝原型](WINDOWS_PACKAGE_PROTOTYPE.md)：僅供開發者驗證的 PyInstaller onedir 原型，不是普通用戶安裝包。
- [Windows 發行流程原型](WINDOWS_RELEASE_GUIDE.md)：單一使用者 Setup、首次設定、路徑限制、更新／解除安裝狀態及驗收邊界。

首次設定、日常啟動、更新和開發測試是不同流程。一般使用者不需要建立測試資料庫，也不應在日常啟動時安裝依賴或執行 migration。
