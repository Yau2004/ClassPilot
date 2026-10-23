export type Student = { id: number; name: string; age: number | null }
export type StudentGender = 'female' | 'male' | 'other' | 'prefer_not_to_say'
export type SupportNeedStatus = 'not_provided' | 'none_reported' | 'needs_support'
export type StudentProfile = Student & {
  gender: StudentGender | null
  support_need_status: SupportNeedStatus
  support_need_tags: string[]
  support_notes: string | null
  general_notes: string | null
  is_archived: boolean
}

export type AttendanceStatus = 'present' | 'absent' | 'late' | 'excused'

export type ConfirmationStatus = 'pending' | 'confirmed'
export type LessonLifecycle = 'planned' | 'cancelled' | 'completed'
export type SessionAttendanceStatus = 'unrecorded' | AttendanceStatus
export type SessionSummary = {
  id: number
  course_type: string | null
  topic: string | null
  session_date: string | null
  start_time: string | null
  end_time: string | null
  confirmation_status: ConfirmationStatus
  lifecycle_status: LessonLifecycle
  participant_count: number
}
export type OverlapWarning = {
  session_id: number
  session_date: string
  start_time: string
  end_time: string
}
export type SessionParticipant = {
  student_id: number
  attendance_status: SessionAttendanceStatus
  evaluation: string | null
  removed_at: string | null
  student: StudentProfile
}
export type SessionDetail = SessionSummary & {
  notes: string | null
  participants: SessionParticipant[]
  overlap_warnings: OverlapWarning[]
}
export type StudentSession = SessionSummary & {
  attendance_status: SessionAttendanceStatus
  evaluation: string | null
  participant_removed: boolean
}

// Production is served by FastAPI, so API calls stay on the current origin.
// The optional override remains available for standalone frontend development.
const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

export class ApiError extends Error {
  status: number
  detail: unknown
  contentType: string
  url: string
  method: string

  constructor(status: number, detail: unknown, contentType: string, method: string, url: string) {
    super(`API 回應錯誤（${method} ${url}；HTTP ${status}；Content-Type: ${contentType || '未提供'}）。${messageFor(status)}`)
    this.status = status
    this.detail = detail
    this.contentType = contentType
    this.method = method
    this.url = url
  }
}

export class ApiNetworkError extends Error {
  method: string
  url: string

  constructor(method: string, url: string) {
    super(`網路請求未取得 HTTP 回應（${method} ${url}）。請檢查服務是否正在執行、API 位址、CORS 或本機網路設定。`)
    this.method = method
    this.url = url
  }
}

export class ApiResponseFormatError extends Error {
  status: number
  contentType: string
  method: string
  url: string

  constructor(status: number, contentType: string, method: string, url: string) {
    super(`API 回傳格式不符（${method} ${url}；HTTP ${status}；Content-Type: ${contentType || '未提供'}），預期 application/json。請檢查 API 路由、同源設定或 SPA fallback。`)
    this.status = status
    this.contentType = contentType
    this.method = method
    this.url = url
  }
}

function safeRequestUrl(path: string) {
  const url = new URL(`${API_BASE}${path}`, window.location.origin)
  return `${url.origin}${url.pathname}`
}

function messageFor(status: number) {
  if (status === 404) return '找不到這筆資料，可能已被移除；請重新整理後再試。'
  if (status === 409) return '資料與既有課節或保留的歷史紀錄衝突，請檢查相關紀錄。'
  if (status === 422) return '輸入資料不符合規則：請檢查必填欄位、年齡範圍、日期格式及課節狀態。'
  return `伺服器回應異常（${status}），請稍後再試。`
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  const method = (init?.method ?? 'GET').toUpperCase()
  const url = safeRequestUrl(path)
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...init?.headers },
    })
  } catch {
    throw new ApiNetworkError(method, url)
  }
  const contentType = response.headers.get('content-type') ?? ''
  if (!response.ok) {
    let detail: unknown = null
    if (contentType.toLowerCase().includes('application/json')) {
      try { detail = (await response.json()).detail } catch { /* retain the safe status and content type */ }
    }
    throw new ApiError(response.status, detail, contentType, method, url)
  }
  if (response.status === 204) return undefined as T
  if (!contentType.toLowerCase().includes('application/json')) {
    throw new ApiResponseFormatError(response.status, contentType, method, url)
  }
  try {
    return await response.json() as T
  } catch {
    throw new ApiResponseFormatError(response.status, contentType, method, url)
  }
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
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Hong_Kong', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date())
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${values.year}-${values.month}-${values.day}`
}

