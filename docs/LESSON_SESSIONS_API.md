# 課節與學生資料 API（B 階段）

此 API 對應 Alembic revision `20261019_0004`。舊班級、註冊和日期式出席表保留原狀；revision 不會推導課節，也不會轉換舊出席。

## 時區與時間欄位

教育中心時區固定為 `Asia/Hong_Kong`。課節日期以 PostgreSQL `DATE` 儲存，起訖時間以不帶時區的 PostgreSQL `TIME` 儲存。API 接受 `YYYY-MM-DD` 日期與 `HH:MM`（也接受 `HH:MM:SS`）香港本地時間；回應時間以 ISO 時間格式表示。日期與時間不轉成 UTC，也不由伺服器、瀏覽器或作業系統時區推算。未排日期和未知時間維持 `null`。

月份查詢按香港日曆月的首日至末日篩選 `session_date`；區間查詢的兩端日期皆包含。學生重疊檢查只比較同一香港日期、非取消的課節，使用半開區間 `new_start < existing_end AND new_end > existing_start`。重疊回傳警告，不阻止建立。

## 學生

`GET /students?include_archived=false` 回傳 ID、姓名、年齡及封存狀態摘要，不回傳支援需要或評價欄位。預設只列出未封存學生；設 `include_archived=true` 可一併查看封存學生。

`POST /students` 接受 `name`（必填）、`age`（可為 `null`）、`gender`、`support_need_status`、`support_need_tags`、`support_notes`、`general_notes`。支援需要狀態為 `not_provided`、`none_reported`、`needs_support`。性別為選填 enum：`female`、`male`、`other`、`prefer_not_to_say`。建立回應維持舊摘要形狀 `{id, name, age}`。

`GET /students/{student_id}` 維持摘要回應；`GET /students/{student_id}/profile` 提供完整資料。學生資料中的 `general_notes` 在介面文案為「要教學的內容」，表示通常要學的主題或學習需求；舊值原封不動保留，不自動重新解讀。敏感欄位不寫入 log。

`GET /students/{student_id}/sessions` 回傳該學生的待安排、未來與過往課節摘要，以及該學生自己的出席、課後評價和是否已移除參與。這個學生專屬檢視不回傳支援需要欄位；一般月曆摘要也不回傳個別評價。課節 `topic` 是該堂實際安排的教學內容，與學生的 `general_notes` 分開保存。建立課節時介面可讓老師明確選擇帶入建議，並可編輯。

Profile 回應範例：

```json
{
  "id": 12,
  "name": "Demo Student",
  "age": null,
  "gender": null,
  "support_need_status": "not_provided",
  "support_need_tags": [],
  "support_notes": null,
  "general_notes": null,
  "is_archived": false
}
```

`PUT /students/{student_id}` 和 `PATCH /students/{student_id}` 接受只含要修改欄位的 JSON。未提交欄位保持不變；明確送 `age: null`、`gender: null`、`support_notes: null` 或 `general_notes: null` 可清空該欄位。標籤以 `support_need_tags: []` 清空。狀態不能設為 null。

`POST /students/{student_id}/archive` 封存；`POST /students/{student_id}/restore` 解除封存。封存不刪除學生或歷史；封存學生仍可查詢，但不能加入新班級、新舊制出席或新課節。`DELETE /students/{student_id}` 在存在班級註冊、舊出席或課節參與紀錄時回 `409`；有歷史的學生應封存。

年齡沿用既有非空年齡值的 4–18 限制，新增資料可不提供年齡。既有年齡不會被改寫；目前沒有找到 4–18 的獨立產品政策文件。

## 課節

### 查詢

- `GET /sessions/month?month=YYYY-MM`：回 `{month, timezone, sessions}`，每堂只含日程摘要、狀態與參與人數，不含支援需要、備註或評價。
- `GET /sessions/range?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`：含首尾日期的日程摘要。日期順序錯誤或超過 367 個日曆日回 `422`。
- `GET /sessions/unassigned`：回傳尚未指定日期的課節摘要。
- `GET /sessions/{session_id}`：回傳課節詳情、參與學生、支援資料、出席、評價和重疊警告。已移除參與者會保留在詳情，並帶有 `removed_at`。

工作台以月份摘要載入日期標記，選取課節後才讀取詳情。從同一個學生的新增安排表可加入多個時段；每個時段各送一個 `POST /sessions`，各自建立獨立課節，不複製原課節出席或評價。每個時段會分別顯示保存結果。明確 HTTP 驗證錯誤可單獨重試；若網路中斷、回應格式錯誤或伺服器 5xx 令保存結果不明，介面先查詢日程並禁止盲目重送該時段，避免重複建立。這不是原子批次 API；其他時段可各自成功，成功項不會隨失敗項一起重送。

摘要欄位為 `id`、`course_type`、`topic`、`session_date`、`start_time`、`end_time`、`confirmation_status`、`lifecycle_status`、`participant_count`。詳情另含 `notes`、`participants`、`overlap_warnings`。

月份回應只包裝日程摘要，例如：

```json
{
  "month": "2026-11",
  "timezone": "Asia/Hong_Kong",
  "sessions": [
    {
      "id": 45,
      "course_type": "Roblox Studio",
      "topic": "角色控制器",
      "session_date": "2026-11-01",
      "start_time": "09:00:00",
      "end_time": "10:00:00",
      "confirmation_status": "pending",
      "lifecycle_status": "planned",
      "participant_count": 2
    }
  ]
}
```

### 建立與修改

`POST /sessions` 建立課節，回 `201` 與詳情：

```json
{
  "course_type": "Roblox Studio",
  "topic": "角色控制器",
  "session_date": "2026-11-01",
  "start_time": "09:00",
  "end_time": "10:00",
  "confirmation_status": "pending",
  "lifecycle_status": "planned",
  "participant_ids": [12, 18],
  "notes": null
}
```

日期可省略或為 null；沒有日期時起訖時間必須為 null。已知日期但未確定時間時，兩個時間皆為 null。開始和結束時間必須同時提供，且開始早於結束。

`PATCH /sessions/{session_id}` 只修改有提交的欄位。若提交 `participant_ids`，它代表新的有效名單；移除者以 `removed_at` 保留，不刪除參與記錄，重新加入同一學生會復用原記錄。新增參與者必須是未封存學生。重複 ID 回 `422`。

狀態值：

- `confirmation_status`: `pending` 或 `confirmed`。
- `lifecycle_status`: `planned`、`cancelled` 或 `completed`。

標為 `confirmed` 至少需要日期、完整起訖時間、課程類型或主題，以及一位參與學生。標為 `completed` 也需滿足同一完整條件且已確認。從 `planned` 可轉為 `cancelled` 或 `completed`；兩種終態不能再編輯課節內容或名單。取消不更改任何出席狀態。取消課節不能再批次寫出席；完成課節仍可補存出席／評價。狀態不足或時間無效回 `422`，終態修改回 `409`。

### 批次出席與評價

`PATCH /sessions/{session_id}/participant-records` 一次在單一資料庫交易中更新多位有效參與者。所有項目成功才 commit；任何資料庫錯誤會 rollback 整批並回 `409`。

```json
{
  "participants": [
    {"student_id": 12, "attendance_status": "present", "evaluation": "完成角色控制器"},
    {"student_id": 18, "attendance_status": "excused", "evaluation": "已安排補課"}
  ]
}
```

出席值：`unrecorded`、`present`、`absent`、`late`、`excused`。新參與者預設 `unrecorded`；它不等於缺席。每個項目只需提交要改的欄位；省略 `evaluation` 會保留原評價，明確送 `evaluation: null` 才清空。要清除已登記出席需明確送 `attendance_status: "unrecorded"`。批次中每位學生只能出現一次，且必須是該課節有效參與者；缺少欄位或非有效參與者回 `422`／`409`，取消課節回 `409`。

建立、修改和批次保存均回 `SessionDetail`。參與者詳情節錄：

```json
{
  "student_id": 12,
  "attendance_status": "unrecorded",
  "evaluation": null,
  "removed_at": null,
  "student": {
    "id": 12,
    "name": "Demo Student",
    "age": null,
    "gender": null,
    "support_need_status": "not_provided",
    "support_need_tags": [],
    "support_notes": null,
    "general_notes": null,
    "is_archived": false
  }
}
```

## 主要錯誤

- `404`: 學生或課節不存在。
- `409`: 學生封存、終態課節修改、非參與者寫入、取消課節寫出席、唯一性衝突或批次資料庫交易失敗。
- `422`: 欄位格式、狀態組合、日期時間完整性、重複參與者或日期區間錯誤。

前端將沒有 HTTP 回應的網路錯誤、非 2xx HTTP 錯誤，以及成功狀態但非 JSON／無法解析的回應分開呈現。診斷訊息只顯示不含 query string 的 request URL、method、HTTP status 和 Content-Type，不輸出回應個人資料或連線秘密。

## Migration 與舊資料

`20261019_0004` 只新增學生欄位及兩張課節表，舊班級、註冊、出席表不刪除、不轉換。舊日期式出席缺少時間及獨立課節資訊，因此不映射成課節。年齡欄位改為可空但保留原值；原 4–18 check constraint 保留。migration downgrade 會在發現新課節／學生資料或 null 年齡時拒絕執行，避免有資料時降版造成遺失。

此文件與 migration 的撰寫不等於執行授權。套用前需先確認開發庫及測試庫目標、備份、revision 與回復計畫，並取得明確批准。
