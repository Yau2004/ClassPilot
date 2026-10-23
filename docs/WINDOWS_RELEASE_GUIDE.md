# ClassPilot Windows 單一使用者發行流程（第 3 階段原型）

本文件描述單一 Windows 使用者版原型。安裝器首次設定會讓使用者選擇資料目錄，並區分新資料、有效既有資料及無法安全辨識的內容。安裝／重用流程仍須在乾淨 Windows 電腦與一般使用者帳戶完成驗收；本文件不代表已通過該驗收。

## 安裝工具與建置界線

發行包選用 [Inno Setup 7.1.0 x64](https://jrsoftware.org/isdl.php) 作為單一 Setup.exe 編譯器。它能建立目前使用者的程式目錄、開始功能表／桌面捷徑，並以 `PrivilegesRequired=lowest` 避免要求系統管理員權限。本產品只需複製程式檔與建立捷徑，PostgreSQL ZIP 由首次設定視窗匯入，不需要 WiX Burn 的多套件鏈結，也不需要 .NET SDK。此版本編譯器已依使用者批准安裝於目前使用者範圍；正式公開發行前仍須確認適用授權。

Inno Setup 官方[商業授權說明](https://jrsoftware.org/isorder.php)請商業使用者購買授權，並說明尚未準備投入 production 的早期開發可待產品準備好後再購買。官方訂購頁未公開固定價格，須在結帳流程查看；本階段沒有購買、接受付款條款或編譯安裝器。若 ClassPilot 的使用符合官方「commercial user」定義，正式製作／CI 使用前應先確認授權並安排費用。

安裝程式來源是 `packaging/installer/ClassPilotSetup.iss`。目前安裝器仍是每使用者模式（`PrivilegesRequired=lowest`），建立目前使用者捷徑及卸載紀錄；本輪沒有切換管理員安裝模式，因該模式會改成全機捷徑／卸載登錄，且以提供 UAC 認證的管理員帳戶處理 per-user 路徑。安裝完成後不從 Setup 啟動 ClassPilot，請關閉安裝器後手動點擊捷徑，確保首次資料設定由原使用者以一般權限執行。若偵測到同一使用者已有安裝，安裝器會停止；安全更新流程尚未完成，因此不會覆寫現有程式。若預設 LocalAppData 資料路徑含中文，安裝器會警告但允許安裝，使用者可在首次設定選擇受支援的本機固定磁碟位置。PostgreSQL ZIP 不包含在 Setup.exe。

## 使用者首次設定

控制程式的一般操作流程如下：

1. 雙擊 `ClassPilotSetup.exe`，確認每使用者安裝位置及路徑支援提示。此原型安裝器目前不要求管理員權限，不建立 Windows 服務。程式可安裝在 `D:\ClassPilot\App`；安裝器只管理這個程式目錄。
2. 首次設定顯示資料目錄欄位與「瀏覽」按鈕。預設 `%LOCALAPPDATA%\ClassPilot`；這只決定資料位置，與程式安裝位置分開。「程式旁的 Data」依實際 `ClassPilot.exe` 所在目錄計算為 `<安裝目錄>\Data`，例如 `D:\Program Files\ClassPilot\Data`。這是唯一允許的程式目錄內資料例外；程式目錄本身和其他重疊路徑仍會拒絕。資料位置會以一般使用者權限實際檢查可寫性；`Program Files` 通常不可供一般使用者寫入，失敗時請改選可寫入的位置，不會放寬 ACL 或要求日常以管理員執行。資料目錄僅支援本機固定磁碟；網路位置及同步資料夾不支援／未驗證，已知 OneDrive、Dropbox、Google Drive 路徑會被拒絕，其他同步軟體請勿選用。
3. 按「檢查資料夾」後，ClassPilot 會檢查路徑、磁碟空間、寫入能力、安裝位置重疊、已知受保護／同步位置及中文路徑限制。視窗明確分開「使用另一份既有 ClassPilot 資料」和「在空資料夾建立新的資料環境」；新建不會帶入目前資料，原資料仍保留。非空且不是有效 ClassPilot 實例的資料夾會被拒絕，不會覆寫內容。
4. 有效既有資料可選「使用現有資料」。ClassPilot 檢查狀態格式、實例識別、PostgreSQL payload／cluster 版本、目前 Windows 使用者 DPAPI 憑證，並啟動自有 cluster 唯讀核對 schema；不重新 initdb、不覆寫憑證、不清空、不執行 migration。憑證解密失敗或 schema 不相容時停止。
5. 新資料環境只有在使用者確認後才檢查 ZIP、初始化 cluster、建立專用資料庫並套用精確 Alembic revision `20261019_0004`。日常啟動不會執行 migration。
6. 設定中斷後，不會把定位檔切到未完成的新位置。若原先已有有效資料位置，定位檔繼續指向原位置；首次設定中斷時可再次手動選取未完成資料夾，只有已識別的安全階段可恢復。不明資料只提供診斷及改選空目錄，不刪除 cluster、PID、設定或資料。

資料位置：

- 程式唯讀檔案：`%LOCALAPPDATA%\Programs\ClassPilot`
- 預設資料目錄：`%LOCALAPPDATA%\ClassPilot`；使用者可選其他本機固定磁碟路徑。
- `ClassPilot.exe` 的安裝目錄下只有直接子目錄 `Data` 可作為資料位置例外。安裝器排除此目錄的 payload 複製，並標記卸載時永不刪除；沒有涵蓋整個 `{app}` 的遞迴刪除規則。安裝器建立的權限不代表日常使用者可寫，應用會用目前使用者權限做實際寫入檢查。程式目錄與資料目錄由安裝器分開管理；更新或卸載不刪除 Data。DPAPI 憑證仍綁定原 Windows 使用者／電腦，不代表可複製到其他帳戶或電腦使用。
- 定位檔：`%LOCALAPPDATA%\ClassPilotLauncher\location.json`。只記錄資料路徑及設定完成狀態，不含密碼；不放在可選資料目錄內。解除安裝保留此定位檔，重新安裝後可重新找到原資料。
- 設定與 DPAPI 憑證：`<所選資料目錄>\config`
- PostgreSQL ZIP 驗證後的 binaries：`<所選資料目錄>\runtime\postgresql`
- PostgreSQL 資料：`<所選資料目錄>\data\postgresql`
- 日誌：`<所選資料目錄>\logs`
- 建議備份：`<所選資料目錄>\backups`

### 中文路徑限制

已確認 PostgreSQL 18.6 EDB binaries 的 `initdb.exe` 在包含中文路徑時無法可靠初始化；第一版不修復該相容性。安裝精靈與首次設定會檢查程式安裝、LocalAppData 資料、ZIP 解壓／PostgreSQL executable、資料及系統暫存位置，並在建立資料庫前顯示完整不支援路徑後停止。路徑含空格仍支援。

Windows 使用者名稱含中文字時，預設 `%LOCALAPPDATA%` 及其中的預設資料目錄會觸發限制。可選擇不含中文、日文或韓文的本機固定磁碟資料目錄（例如 `D:\ClassPilotData`），但程式安裝位置、系統暫存位置、PostgreSQL executable 及資料位置仍會逐一檢查；若仍有受限路徑，設定會在初始化前停止。此版不改用全機共用資料夾、不放寬 ACL、不要求管理員權限或變更 Windows 帳戶。空格可使用。

### 缺少 Microsoft runtime

目前支援規格要求 Windows x64 Microsoft Visual C++ runtime。若必要元件缺少，ClassPilot 在 payload 匯入及資料庫初始化前停止，顯示缺少的元件名稱與 [Microsoft 官方 Visual C++ Redistributable x64 指引](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist?view=msvc-170)。這個原型不會自行下載、安裝或更新系統 runtime；需要由使用者／IT 另行處理後再重試。乾淨 Windows runtime 驗收仍未完成。

若 PostgreSQL executable 回報 `0xC0000135`，這表示 Windows 回報 `STATUS_DLL_NOT_FOUND`，即 executable 或其相依 DLL 無法載入；這個代碼本身不指出是哪一個 DLL，也不能單憑它認定是 Visual C++ runtime。新版本在建立 cluster 前會分別執行 `initdb.exe`、`postgres.exe`、`pg_ctl.exe` 的唯讀 `--version` 探測，顯示失敗階段、元件及十進位／十六進位代碼。封裝控制程式啟動外部 PostgreSQL 程序時，會暫時恢復 Windows 預設 DLL 搜尋方式，避免凍結程式的 DLL 路徑影響子程序。診斷不能完成時不代表資料損壞；不要刪除或重建資料目錄。

更新至含此診斷功能的發行版後，維護者可對明確指定的資料位置執行唯讀探測：

```powershell
ClassPilot.exe --probe-postgres --state-root "<資料目錄>" --report-file "<暫存診斷報告.json>"
```

此模式只讀取封裝規格／payload manifest，並對三個 executable 執行 `--version`；不讀 DPAPI 設定、不連資料庫、不啟動 cluster、不寫入資料目錄、不執行 migration。報告僅含檢查狀態、元件名稱、版本及安全錯誤訊息。

## 日常啟動、狀態與停止

完成首次設定後，點擊桌面或開始功能表的 ClassPilot 圖示會開啟控制視窗。「啟動」按鈕在未啟動時啟動專屬 PostgreSQL 和 FastAPI，通過唯讀 readiness 後開啟瀏覽器；同一實例已就緒時只開啟該網站。啟動中按鈕會停用，失敗後可重試並查看安全診斷。其他程式佔用的 port 不會被當成 ClassPilot 網站。控制視窗可選擇「停止並關閉」，正常關閉 API 後再停止自有 PostgreSQL。日常啟動不安裝依賴、不 build、不執行 migration。

控制視窗始終提供「資料位置…」。切換至另一份既有資料或建立新資料前，會先要求安全停止目前網站與自有 PostgreSQL，並確認程序結束；全使用者單一實例鎖避免兩份資料同時運行。新建資料會在成功後才更新定位檔；失敗時保留原定位，未完成位置需由使用者再次選取。搬移目前資料尚未提供，介面會明確標示，沒有可執行的搬移按鈕。

發行實例只監聽 loopback，資料／憑證與原型資料庫、角色名稱及 ownership marker 不共用。診斷不會輸出密碼或連線字串。

## 更新與解除安裝

更新來源、相容性檢查、備份介面、migration 確認／復原流程尚未完成，因此控制視窗不提供更新按鈕。畫面會明確說明更新尚未提供；更新安裝器也會在發現既有安裝時停止。更新必須先辨識現有資料與 schema，提供備份及回復步驟；需要 migration 時另行取得明確批准。程式檔回退不會還原資料庫 schema 或資料。

更新及解除安裝只管理程式安裝檔、捷徑及自身安裝紀錄；`{app}\Data`、自訂資料目錄和 `%LOCALAPPDATA%\ClassPilotLauncher\location.json` 均保留，不會遞迴刪除 `{app}` 或共同父目錄。卸載前請先用 ClassPilot 的「停止並關閉」結束自有服務；卸載器不按程序名稱終止 PostgreSQL，也不會強制重開機。卸載完成後建議重開機再安裝新版；若 Windows 明確提示需要重啟，請自行選擇是否重啟。本階段不提供刪除全部資料或搬移操作。重新安裝後控制程式會讀取定位檔；若定位檔遺失，必須重新選擇並驗證既有資料，不會自動在預設路徑建立另一個資料庫。

目前安裝器會阻止覆寫既有安裝。安全更新的人工步驟：先在 ClassPilot 控制視窗按「停止並關閉」；使用 Windows「已安裝的應用程式」解除安裝舊版（不要刪除資料目錄或定位檔）；再安裝新版，於程式安裝頁選擇程式位置。新版首次啟動時選取原本資料目錄並按「使用現有資料」。若舊版 UI 尚未提供停止選項，先正常關閉瀏覽器，再由控制視窗停止服務。若 schema revision 不相容，停止並等待明確批准的更新／migration 流程。程式回退不會還原資料庫 schema 或資料。

## 建置與目前驗證狀態

應用程式建置仍由已存在的 Python 3.12 `steam-classroom`、鎖定 PyInstaller 及前端 lockfile 執行：

```powershell
python scripts\build_windows_release.py
```

安裝器來源檢查：

```powershell
python scripts\build_windows_installer.py --check-only
```

編譯安裝檔（只有在 Inno Setup 7.1.0 x64 已獲批准安裝後）：

```powershell
python scripts\build_windows_installer.py
```

本輪只建置應用與編譯安裝器，沒有執行 Setup／uninstaller、建立捷徑／安裝記錄或修改正式 LocalAppData。管理員 UAC 安裝模式仍待決定安裝範圍；乾淨 Windows、一般使用者帳戶、桌面選檔、中文使用者路徑及缺少系統 runtime 情況仍未驗證。隔離 PostgreSQL／封裝 HTTP 測試已通過，但不代表乾淨 Windows 安裝驗收。若安裝後必須重開機才能使用，舊程序殘留或檔案鎖定仍是待驗證可能性，本輪沒有執行卸載或讀取使用者 Windows 事件紀錄，故未定位。

維護者可在已批准的隔離 Windows 環境執行 `python scripts/verify_release_temp_integration.py`。此工具只在系統暫存目錄新建專用實例，驗證 ZIP、首次設定、指定 revision、封裝 HTTP 與合成資料重啟持久性；成功後才移除自己建立且已確認停止的暫存資料。失敗時保留暫存根目錄供診斷，不讀取 `.env`、不連接 dev/test 資料庫，也不寫入正式 LocalAppData。
