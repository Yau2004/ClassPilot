export type Student = { id: number; name: string; age: number }
export type Classroom = { id: number; name: string; description: string | null }
export type AttendanceStatus = 'present' | 'absent' | 'late' | 'excused'
export type AttendanceRecord = {
  id: number
  class_id: number
  student_id: number
  attendance_date: string
  status: AttendanceStatus
  note: string | null
}

const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, detail: unknown) {
    super(messageFor(status, detail))
    this.status = status
    this.detail = detail
  }
}

function messageFor(status: number, detail: unknown) {
  const raw = typeof detail === 'string' ? detail.toLowerCase() : ''
  if (status === 404) return '找不到這筆資料，可能已被移除；請重新整理後再試。'
  if (status === 409) {
    if (raw.includes('already exists') || raw.includes('already enrolled')) return '這位學生已在此班級註冊，或當日已有出席紀錄。'
    if (raw.includes('not enrolled')) return '學生目前未註冊此班級，請先完成註冊。'
    if (raw.includes('still has')) return '仍有學生註冊或出席歷史紀錄，請先處理相關紀錄後再刪除。'
    return '資料與既有紀錄衝突，請檢查註冊及出席紀錄。'
  }
  if (status === 422) return '輸入資料不符合規則：請檢查必填欄位、年齡範圍、日期格式及出席狀態。'
  return `伺服器回應異常（${status}），請稍後再試。`
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...init?.headers },
    })
  } catch {
    throw new Error('無法連線到 API，請確認後端已啟動及前端 API 位址設定。')
  }
  if (!response.ok) {
    let detail: unknown = null
    try { detail = (await response.json()).detail } catch { /* keep a safe generic message */ }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function allPages<T>(path: string): Promise<T[]> {
  const pageSize = 100
  const result: T[] = []
  for (let offset = 0; ; offset += pageSize) {
    const joiner = path.includes('?') ? '&' : '?'
    const page = await api<T[]>(`${path}${joiner}offset=${offset}&limit=${pageSize}`)
    result.push(...page)
    if (page.length < pageSize) return result
  }
}

export function todayLocal() {
  const date = new Date()
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

export const statusLabels: Record<AttendanceStatus, string> = {
  present: '出席', absent: '缺席', late: '遲到', excused: '請假',
}
