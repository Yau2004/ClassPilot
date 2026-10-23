# ClassPilot

ClassPilot 是配合教育中心小班 STEAM 授課工作的本機課堂管理工具，協助整理學生資料、安排課節與時間表，並記錄每堂課的出席和課後評價。使用者透過 Windows 控制程式啟動，再於瀏覽器操作；目前不是公開託管的線上服務。

## 畫面預覽

截圖待補。

<!-- 預留：Windows 控制程式及首次設定畫面 -->
<!-- 預留：瀏覽器課程日程及學生管理畫面 -->

## 主要功能

- 管理學生資料、教學支援需要及教學注意事項。
- 以月曆查看課節，為同一學生安排多個上課時段。
- 分別記錄每堂課的出席狀態和課後評價。
- 透過 Windows 控制程式啟動或停止本機服務。
- 選擇本機資料位置；卸載程式時預設保留使用者資料。

## 使用技術

- **前端：** React、TypeScript、Vite。
- **後端：** Python 3.12、FastAPI、SQLModel。
- **資料庫：** PostgreSQL；使用 Alembic 管理 schema。
- **Windows 發行：** PyInstaller onedir、Inno Setup。

## 安裝與使用

發行目標為 Windows x64；專案目前未明確列出最低 Windows 版本。請前往 [GitHub Releases](https://github.com/Yau2004/ClassPilot/releases) 下載 `ClassPilotSetup.exe`。

### 首次設定

1. 從 GitHub Releases 下載並執行 `ClassPilotSetup.exe`。目前安裝器採目前 Windows 使用者安裝，不要求管理員權限；可選擇程式安裝位置，預設為 `%LOCALAPPDATA%\Programs\ClassPilot`。
2. 從 [PostgreSQL 官方 Windows 下載頁](https://www.postgresql.org/download/windows/)進入下載頁，手動取得支援的 PostgreSQL 18.6、package revision 5、Windows x64 binaries ZIP：`postgresql-18.6-5-windows-x64-binaries.zip`。ZIP 不包含在安裝檔內。
3. 開啟 ClassPilot，選取 ZIP 及資料位置。預設資料位置為 `%LOCALAPPDATA%\ClassPilot`，與程式安裝位置分開；確認後，程式會檢查檔案並建立專用資料環境及所需 schema。
4. 設定完成後按「啟動」，待服務就緒便會開啟瀏覽器。

### 之後使用

- 開啟桌面或開始功能表中的 ClassPilot。
- 按「啟動」使用課程日程及學生管理。
- 完成後按「停止並關閉」。日常啟動不會安裝依賴、重新建置或執行 migration。

### 重要限制與更新

- 目前只支援 Windows x64。安裝、暫存、PostgreSQL 執行檔及資料等相關路徑不可含中文、日文或韓文；設定時會檢查並在初始化前停止。路徑含空格可用。
- 資料位置須是本機固定磁碟，並由目前使用者以一般權限寫入。可選擇自訂位置；若程式安裝在 `Program Files`，該位置通常不能寫入。資料可放在安裝目錄內的 `Data` 子目錄，但仍須通過一般使用者權限檢查。
- PostgreSQL ZIP 需要使用者手動下載及選取。若缺少必要的 Microsoft runtime，設定會停止並提供處理指引；ClassPilot 不會自行安裝系統 runtime。
- 這是本機使用工具，不提供雲端同步或跨電腦資料共享。DPAPI 保護的憑證亦不能只靠複製資料夾移到另一個 Windows 帳戶或電腦使用。
- 卸載預設保留資料。搬移現有資料及自動更新目前未提供；安裝器會阻止直接覆寫既有安裝。重新安裝或版本更新前，請按[Windows 發行與更新指南](docs/WINDOWS_RELEASE_GUIDE.md)操作，並保留資料目錄及位置記錄。

完整操作及排錯：

- [Windows 發行、首次設定、更新與卸載](docs/WINDOWS_RELEASE_GUIDE.md)
- [開發環境常見錯誤與安全診斷](docs/TROUBLESHOOTING.md)（開發者）
- [開發與測試環境](docs/DEVELOPMENT_TESTING.md)（開發者）

## 開發動機

我在教育中心授課時，需要整理學生資料和上課時間表。每堂課可能有不同學生、教學內容與上課時間，因此我希望有一個更貼近實際工作的工具，把排課、學生資料、出席和課後評價集中管理，減少來回查找資料的時間。
