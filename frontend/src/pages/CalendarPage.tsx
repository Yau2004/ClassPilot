import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ApiError,
  ApiNetworkError,
  ApiResponseFormatError,
  allPages,
  api,
  todayLocal,
  type LessonLifecycle,
  type SessionAttendanceStatus,
  type SessionDetail,
  type SessionParticipant,
  type SessionSummary,
  type StudentProfile,
  type StudentSession,
} from '../api'
import { Empty, FormActions, Notice, Panel } from '../components/Shared'
import { StudentIdentityLine, StudentTeachingInformation } from '../components/StudentProfileFields'
import { calendarWeekdays as weekdays, dateKey, monthDates } from '../calendarDates'

type SlotDraft = { key: number; session_date: string; start_time: string; end_time: string }
type SlotOutcome = { state: 'pending' | 'saved' | 'retryable' | 'uncertain' | 'confirmed_existing'; sessionId?: number; message?: string }
type RecordDraft = Record<number, { attendance_status: SessionAttendanceStatus; evaluation: string }>
const EMPTY_SESSION_SUMMARIES: SessionSummary[] = []

const attendanceOptions: { value: SessionAttendanceStatus; label: string }[] = [
  { value: 'unrecorded', label: '未登記' },
  { value: 'present', label: '出席' },
  { value: 'absent', label: '缺席' },
  { value: 'late', label: '遲到' },
  { value: 'excused', label: '請假' },
]
const lifecycleLabels: Record<LessonLifecycle, string> = {
  planned: '安排中', cancelled: '已取消', completed: '已完成',
}

function dateParts(value: string) {
  const [year, month, day] = value.split('-').map(Number)
  return { year, month, day }
}

function monthKey(value: string) {
  return value.slice(0, 7)
}

function shiftMonth(value: string, delta: number) {
  const [year, month] = value.split('-').map(Number)
  const shifted = new Date(Date.UTC(year, month - 1 + delta, 1))
  return `${shifted.getUTCFullYear()}-${String(shifted.getUTCMonth() + 1).padStart(2, '0')}`
}

function shiftDate(value: string, delta: number) {
  const { year, month, day } = dateParts(value)
  const shifted = new Date(Date.UTC(year, month - 1, day + delta))
  return dateKey(shifted.getUTCFullYear(), shifted.getUTCMonth() + 1, shifted.getUTCDate())
}

function dateLabel(value: string) {
  const { year, month, day } = dateParts(value)
  return `${year}年${month}月${day}日`
}

function timeLabel(value: string | null) {
  return value ? value.slice(0, 5) : '時間未定'
}

function sessionTimeLabel(item: Pick<SessionSummary, 'start_time' | 'end_time'>) {
  return item.start_time && item.end_time
    ? `${timeLabel(item.start_time)}–${timeLabel(item.end_time)}`
    : '時間未定'
}

function statusLabel(item: Pick<SessionSummary, 'confirmation_status' | 'lifecycle_status'>) {
  if (item.lifecycle_status === 'cancelled') return lifecycleLabels.cancelled
  if (item.lifecycle_status === 'completed') return lifecycleLabels.completed
  return item.confirmation_status === 'confirmed' ? '已確認' : '待確認'
}

function safeError(error: unknown) {
  return error instanceof Error ? error.message : '操作未完成，請重試。'
}

function initialRecordDraft(participants: SessionParticipant[]): RecordDraft {
  return Object.fromEntries(participants.map((participant) => [participant.student_id, {
    attendance_status: participant.attendance_status,
    evaluation: participant.evaluation ?? '',
  }]))
}

function isSameDraft(a: RecordDraft, b: RecordDraft) {
  return JSON.stringify(a) === JSON.stringify(b)
}

export function CalendarPage({ initialStudent, onClearStudent }: {
  initialStudent: StudentProfile | null
  onClearStudent: () => void
}) {
  const [today] = useState(todayLocal)
  const [selectedDate, setSelectedDate] = useState(todayLocal)
  const [calendarData, setCalendarData] = useState<{ month: string; sessions: SessionSummary[]; upcoming: SessionSummary[]; error: string } | null>(null)
  const [studentProfilesState, setStudentProfilesState] = useState<{ items: StudentProfile[]; error: string } | null>(null)
  const studentProfilesRequest = useRef<Promise<StudentProfile[]> | null>(null)
  const [unassignedSessions, setUnassignedSessions] = useState<SessionSummary[]>([])
  const [unassignedError, setUnassignedError] = useState('')
  const [selectedSessionId, setSelectedSessionId] = useState<number | null>(null)
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState('')
  const [draft, setDraft] = useState<RecordDraft>({})
  const [savingRecords, setSavingRecords] = useState(false)
  const [recordMessage, setRecordMessage] = useState('')
  const [editOpen, setEditOpen] = useState(false)
  const [editDirty, setEditDirty] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [createBusy, setCreateBusy] = useState(false)
  const [createError, setCreateError] = useState('')
  const [courseType, setCourseType] = useState('')
  const [topic, setTopic] = useState('')
  const [lessonNotes, setLessonNotes] = useState('')
  const [confirmationStatus, setConfirmationStatus] = useState<'pending' | 'confirmed'>('pending')
  const [participantIds, setParticipantIds] = useState<number[]>([])
  const [slots, setSlots] = useState<SlotDraft[]>([])
  const [slotOutcomes, setSlotOutcomes] = useState<Record<number, SlotOutcome>>({})
  const [suggestedLearningContent, setSuggestedLearningContent] = useState('')
  const [suggestionMessage, setSuggestionMessage] = useState('')
  const [studentScheduleState, setStudentScheduleState] = useState<{ studentId: number; items: StudentSession[] | null; error: string } | null>(null)
  const month = monthKey(selectedDate)
  const calendarCells = useMemo(() => monthDates(month), [month])
  const monthRequest = useRef(0)
  const initialStudentId = initialStudent?.id
  const studentPickerOpen = createOpen || editOpen
  const students = studentProfilesState?.items ?? []
  const studentsLoading = studentPickerOpen && studentProfilesState === null
  const studentsError = studentProfilesState?.error ?? ''
  const studentSchedule = initialStudentId !== undefined && studentScheduleState?.studentId === initialStudentId ? studentScheduleState.items : null
  const studentScheduleError = initialStudentId !== undefined && studentScheduleState?.studentId === initialStudentId ? studentScheduleState.error : ''
  const studentScheduleLoading = initialStudentId !== undefined && studentScheduleState?.studentId !== initialStudentId
  const summaries = calendarData?.month === month ? calendarData.sessions : EMPTY_SESSION_SUMMARIES
  const upcomingSessions = calendarData?.month === month ? calendarData.upcoming : EMPTY_SESSION_SUMMARIES
  const calendarLoading = calendarData?.month !== month
  const calendarError = calendarData?.month === month ? calendarData.error : ''

  const loadMonth = useCallback(async () => {
    const requestId = ++monthRequest.current
    try {
      const [sessions, upcoming] = await Promise.all([
        api<SessionSummary[]>(`/sessions/range?start_date=${calendarCells[0]}&end_date=${calendarCells.at(-1)}`),
        api<SessionSummary[]>(`/sessions/range?start_date=${today}&end_date=${shiftDate(today, 30)}`),
      ])
      if (requestId !== monthRequest.current) return
      setCalendarData({ month, sessions, upcoming, error: '' })
    } catch (error) {
      if (requestId !== monthRequest.current) return
      setCalendarData((current) => ({
        month,
        sessions: current?.month === month ? current.sessions : [],
        upcoming: current?.month === month ? current.upcoming : [],
        error: safeError(error),
      }))
    }
  }, [calendarCells, month, today])

  useEffect(() => {
    let active = true
    const requestId = ++monthRequest.current
    Promise.all([
      api<SessionSummary[]>(`/sessions/range?start_date=${calendarCells[0]}&end_date=${calendarCells.at(-1)}`),
      api<SessionSummary[]>(`/sessions/range?start_date=${today}&end_date=${shiftDate(today, 30)}`),
    ]).then(([sessions, upcoming]) => {
      if (active && requestId === monthRequest.current) setCalendarData({ month, sessions, upcoming, error: '' })
    }).catch((error: unknown) => {
      if (active && requestId === monthRequest.current) {
        setCalendarData((current) => ({
          month,
          sessions: current?.month === month ? current.sessions : [],
          upcoming: current?.month === month ? current.upcoming : [],
          error: safeError(error),
        }))
      }
    })
    return () => { active = false }
  }, [calendarCells, month, today])
  useEffect(() => {
    let active = true
    void api<SessionSummary[]>('/sessions/unassigned')
      .then((items) => { if (active) { setUnassignedSessions(items); setUnassignedError('') } })
      .catch((error) => { if (active) setUnassignedError(safeError(error)) })
    return () => { active = false }
  }, [])
  useEffect(() => {
    if (!studentPickerOpen || studentProfilesState !== null) return
    let active = true
    const request = studentProfilesRequest.current ?? allPages<StudentProfile>('/students/profiles?include_archived=true')
    studentProfilesRequest.current = request
    request.then((items) => {
      if (active) setStudentProfilesState({ items, error: '' })
    }).catch((error: unknown) => {
      if (active) setStudentProfilesState({ items: [], error: safeError(error) })
    }).finally(() => {
      if (studentProfilesRequest.current === request) studentProfilesRequest.current = null
    })
    return () => { active = false }
  }, [studentPickerOpen, studentProfilesState])

  useEffect(() => {
    if (initialStudentId === undefined) return
    let active = true
    api<StudentSession[]>(`/students/${initialStudentId}/sessions`)
      .then((items) => { if (active) setStudentScheduleState({ studentId: initialStudentId, items, error: '' }) })
      .catch((error) => { if (active) setStudentScheduleState({ studentId: initialStudentId, items: null, error: safeError(error) }) })
    return () => { active = false }
  }, [initialStudentId])

  const daySessions = useMemo(
    () => summaries.filter((item) => item.session_date === selectedDate)
      .sort((a, b) => (a.start_time ?? '99:99').localeCompare(b.start_time ?? '99:99') || a.id - b.id),
    [summaries, selectedDate],
  )
  const calendarCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const item of summaries) {
      if (item.session_date) counts.set(item.session_date, (counts.get(item.session_date) ?? 0) + 1)
    }
    return counts
  }, [summaries])
  const upcoming = useMemo(
    () => upcomingSessions.filter((item) => item.session_date && item.lifecycle_status === 'planned')
      .sort((a, b) => `${a.session_date} ${a.start_time ?? '99:99'}`.localeCompare(`${b.session_date} ${b.start_time ?? '99:99'}`))
      .slice(0, 5),
    [upcomingSessions],
  )
  const recordsDirty = detail !== null && !isSameDraft(draft, initialRecordDraft(detail.participants))
  const hasUnsavedSessionChanges = recordsDirty || editDirty

  function confirmLeavingSession(action: string) {
    return !hasUnsavedSessionChanges || window.confirm(`課節資料尚未儲存。${action}會放棄目前修改，確定繼續？`)
  }

  async function openSession(item: SessionSummary) {
    if (!confirmLeavingSession('離開')) return
    setSelectedDate(item.session_date ?? selectedDate)
    setSelectedSessionId(item.id)
    setDetail(null)
    setEditOpen(false)
    setEditDirty(false)
    setDetailError('')
    setRecordMessage('')
    setDetailLoading(true)
    try {
      const result = await api<SessionDetail>(`/sessions/${item.id}`)
      setDetail(result)
      setDraft(initialRecordDraft(result.participants))
    } catch (error) {
      setDetailError(safeError(error))
    } finally {
      setDetailLoading(false)
    }
  }

  function clearSession() {
    if (!confirmLeavingSession('離開')) return
    setSelectedSessionId(null)
    setDetail(null)
    setEditOpen(false)
    setEditDirty(false)
    setDetailError('')
    setRecordMessage('')
  }

  function selectDate(value: string) {
    if (!confirmLeavingSession('切換日期')) return
    setSelectedDate(value)
    setSelectedSessionId(null)
    setDetail(null)
    setEditOpen(false)
    setEditDirty(false)
    setRecordMessage('')
  }

  async function saveParticipantRecords() {
    if (!detail) return
    const updates = detail.participants.filter((participant) => participant.removed_at === null)
      .flatMap((participant) => {
        const next = draft[participant.student_id]
        if (!next) return []
        const update: { student_id: number; attendance_status?: SessionAttendanceStatus; evaluation?: string | null } = {
          student_id: participant.student_id,
        }
        if (next.attendance_status !== participant.attendance_status) update.attendance_status = next.attendance_status
        if (next.evaluation !== (participant.evaluation ?? '')) update.evaluation = next.evaluation || null
        return Object.keys(update).length > 1 ? [update] : []
      })
    if (updates.length === 0) return
    setSavingRecords(true)
    setRecordMessage('')
    try {
      const result = await api<SessionDetail>(`/sessions/${detail.id}/participant-records`, {
        method: 'PATCH', body: JSON.stringify({ participants: updates }),
      })
      setDetail(result)
      setDraft(initialRecordDraft(result.participants))
      setRecordMessage('出席與評價已儲存。')
    } catch (error) {
      setRecordMessage(safeError(error))
    } finally {
      setSavingRecords(false)
    }
  }

  function beginCreate(student?: StudentProfile) {
    if (!confirmLeavingSession('開啟新增課節')) return
    setSelectedSessionId(null)
    setDetail(null)
    setEditOpen(false)
    setEditDirty(false)
    setCourseType('')
    setTopic('')
    setLessonNotes('')
    setConfirmationStatus('pending')
    setParticipantIds(student ? [student.id] : initialStudent ? [initialStudent.id] : [])
    setSlots([{ key: Date.now(), session_date: selectedDate, start_time: '', end_time: '' }])
    setSlotOutcomes({})
    setCreateError('')
    setCreateOpen(true)
    setSuggestedLearningContent(student?.general_notes ?? initialStudent?.general_notes ?? '')
    setSuggestionMessage('')
  }

  function updateParticipantSelection(ids: number[]) {
    setParticipantIds(ids)
    const chosen = ids.length === 1 ? students.find((student) => student.id === ids[0]) : undefined
    setSuggestedLearningContent(chosen?.general_notes ?? '')
    setSuggestionMessage('')
  }

  function updateSlot(key: number, field: keyof Omit<SlotDraft, 'key'>, value: string) {
    const state = slotOutcomes[key]?.state
    if (state === 'saved' || state === 'uncertain' || state === 'confirmed_existing') return
    setSlots((items) => items.map((item) => item.key === key ? { ...item, [field]: value } : item))
    setSlotOutcomes((current) => ({ ...current, [key]: { state: 'pending' } }))
  }

  async function createSessions(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (participantIds.length === 0) {
      setCreateError('請至少選擇一位學生。')
      return
    }
    const retrySlots = slots.filter((slot) => !slotOutcomes[slot.key] || slotOutcomes[slot.key].state === 'pending' || slotOutcomes[slot.key].state === 'retryable')
    if (retrySlots.length === 0) return
    setCreateBusy(true)
    setCreateError('')
    let createdAny = false
    for (const slot of retrySlots) {
      try {
        const result = await api<SessionDetail>('/sessions', {
          method: 'POST',
          body: JSON.stringify({
            course_type: courseType.trim() || null,
            topic: topic.trim() || null,
            notes: lessonNotes.trim() || null,
            session_date: slot.session_date || null,
            start_time: slot.start_time || null,
            end_time: slot.end_time || null,
            confirmation_status: confirmationStatus,
            lifecycle_status: 'planned',
            participant_ids: participantIds,
          }),
        })
        setSlotOutcomes((current) => ({ ...current, [slot.key]: { state: 'saved', sessionId: result.id } }))
        createdAny = true
      } catch (error) {
        const uncertain = error instanceof ApiNetworkError
          || error instanceof ApiResponseFormatError
          || (error instanceof ApiError && error.status >= 500)
        setSlotOutcomes((current) => ({
          ...current,
          [slot.key]: {
            state: uncertain ? 'uncertain' : 'retryable',
            message: safeError(error),
          },
        }))
      }
    }
    if (createdAny) await loadMonth()
    if (createdAny) {
      try { setUnassignedSessions(await api<SessionSummary[]>('/sessions/unassigned')) } catch (error) { setUnassignedError(safeError(error)) }
      if (initialStudent) {
        try { setStudentScheduleState({ studentId: initialStudent.id, items: await api<StudentSession[]>(`/students/${initialStudent.id}/sessions`), error: '' }) }
        catch (error) { setStudentScheduleState({ studentId: initialStudent.id, items: null, error: safeError(error) }) }
      }
    }
    setCreateBusy(false)
  }

  async function checkUncertainSlot(slot: SlotDraft) {
    setCreateBusy(true)
    try {
      const candidates = slot.session_date
        ? await api<SessionSummary[]>(`/sessions/range?start_date=${slot.session_date}&end_date=${slot.session_date}`)
        : await api<SessionSummary[]>('/sessions/unassigned')
      const likely = candidates.filter((item) =>
        item.session_date === (slot.session_date || null)
        && item.start_time === (slot.start_time || null)
        && item.end_time === (slot.end_time || null)
        && item.course_type === (courseType.trim() || null)
        && item.topic === (topic.trim() || null)
        && item.participant_count === participantIds.length,
      )
      if (likely.length === 0) {
        setSlotOutcomes((current) => ({ ...current, [slot.key]: { state: 'retryable', message: '日程中找不到相同安排，可重試此堂。' } }))
      } else {
        setSlotOutcomes((current) => ({
          ...current,
          [slot.key]: { state: 'uncertain', message: `日程中有 ${likely.length} 堂相似安排；請在日程核對後明確確認。` },
        }))
      }
    } catch (error) {
      setCreateError(safeError(error))
    } finally {
      setCreateBusy(false)
    }
  }

  function addSlot() {
    setSlots((items) => [...items, { key: Date.now() + items.length, session_date: selectedDate, start_time: '', end_time: '' }])
  }

  function closeCreate() {
    if (createBusy) return
    if (slots.some((slot) => slotOutcomes[slot.key]?.state === 'uncertain')) {
      setCreateError('仍有保存結果待核對。請先檢查日程，避免重複建立課節。')
      return
    }
    const hasUnsubmitted = slots.some((slot) => !slotOutcomes[slot.key] || slotOutcomes[slot.key].state === 'retryable' || slotOutcomes[slot.key].state === 'pending')
    if (hasUnsubmitted && !window.confirm('尚有未保存的時段。關閉會放棄這些輸入，確定繼續？')) return
    setCreateOpen(false)
  }

  function confirmExistingSlot(slot: SlotDraft) {
    const outcome = slotOutcomes[slot.key]
    if (outcome?.state !== 'uncertain') return
    const confirmation = window.prompt('請先在日程確認這堂已存在，再輸入其課節編號；取消則保留待核對狀態。')
    if (confirmation === null) return
    const id = Number(confirmation.trim())
    if (!Number.isInteger(id) || id <= 0) {
      setCreateError('請輸入有效的課節編號；此時段仍不會重送。')
      return
    }
    setSlotOutcomes((current) => ({ ...current, [slot.key]: { state: 'confirmed_existing', sessionId: id } }))
    setCreateError('')
  }

  async function changeLifecycle(status: 'cancelled' | 'completed') {
    if (!detail) return
    const action = status === 'cancelled' ? '取消' : '完成'
    if (recordsDirty) {
      setRecordMessage('請先儲存或放棄尚未儲存的出席與評價，再更改課節狀態。')
      return
    }
    if (!window.confirm(`確定${action}這堂課？`)) return
    try {
      const result = await api<SessionDetail>(`/sessions/${detail.id}`, {
        method: 'PATCH', body: JSON.stringify({ lifecycle_status: status }),
      })
      setDetail(result)
      setDraft(initialRecordDraft(result.participants))
      await loadMonth()
      try { setUnassignedSessions(await api<SessionSummary[]>('/sessions/unassigned')) } catch (error) { setUnassignedError(safeError(error)) }
    } catch (error) {
      setDetailError(safeError(error))
    }
  }

  function changeMonth(delta: number) {
    const target = shiftMonth(month, delta)
    const [year, monthNumber] = target.split('-').map(Number)
    const currentDay = dateParts(selectedDate).day
    const lastDay = new Date(Date.UTC(year, monthNumber, 0)).getUTCDate()
    selectDate(dateKey(year, monthNumber, Math.min(currentDay, lastDay)))
  }

  const monthTitle = `${Number(month.slice(5, 7))}月 ${month.slice(0, 4)}`
  const savedCount = slots.filter((slot) => slotOutcomes[slot.key]?.state === 'saved').length
  const hasRetryable = slots.some((slot) =>
    !slotOutcomes[slot.key]
    || slotOutcomes[slot.key].state === 'pending'
    || slotOutcomes[slot.key].state === 'retryable',
  )
  const sharedFieldsLocked = slots.some((slot) => ['saved', 'uncertain', 'confirmed_existing'].includes(slotOutcomes[slot.key]?.state ?? ''))

  return <div className="page-stack">
    <div className="workbench-grid">
      <div className="workbench-main">
        {initialStudent && <Panel title={`學生上課安排：${initialStudent.name}`} description="顯示待安排、未來及過往課節；評價只在此學生的安排檢視中呈現。" action={<button className="button button-soft" onClick={onClearStudent}>返回全體日程</button>}>
          <div className="student-schedule-actions"><button className="button button-primary" onClick={() => beginCreate(initialStudent)}>＋ 替這位學生新增時段</button><span className="muted">每個日期／時間會建立成獨立課節。</span></div>
          {studentScheduleError && <Notice>{studentScheduleError}</Notice>}
          {studentScheduleLoading ? <div className="loading-state">正在載入上課安排…</div> : studentSchedule?.length === 0 ? <Empty>這位學生尚未有課節安排。</Empty> : studentSchedule && <div className="student-session-groups">
            {(['unassigned', 'upcoming', 'past'] as const).map((group) => {
              const items = studentSchedule.filter((item) => group === 'unassigned'
                ? item.session_date === null
                : group === 'upcoming'
                  ? item.session_date !== null && item.session_date >= today
                  : item.session_date !== null && item.session_date < today)
              const title = group === 'unassigned' ? '待安排' : group === 'upcoming' ? '未來課節' : '過往課節'
              return <section className="student-session-group" key={group}>
                <h3>{title}<span>{items.length}</span></h3>
                {items.length === 0 ? <p className="muted">目前沒有課節</p> : items.map((item) => <button className="student-session-row" key={item.id} onClick={() => void openSession(item)}>
                  <span><strong>{item.session_date ? dateLabel(item.session_date) : '日期未定'} · {sessionTimeLabel(item)}</strong><small>{item.course_type || item.topic || '教學內容未定'}{item.participant_removed ? ' · 已移除參與（保留歷史）' : ''}</small></span>
                  <span className={`lesson-status status-${item.lifecycle_status}`}>{item.lifecycle_status === 'planned' ? statusLabel(item) : lifecycleLabels[item.lifecycle_status]}</span>
                </button>)}
              </section>
            })}
          </div>}
        </Panel>}

        <Panel title={initialStudent ? '課節詳情' : dateLabel(selectedDate)} description={selectedDate === today ? '今天 · Asia/Hong_Kong' : `所選日期 · Asia/Hong_Kong`} action={<button className="button button-primary" onClick={() => beginCreate()}>＋ 新增課節</button>}>
          {calendarError && <Notice>{calendarError}</Notice>}
          {calendarLoading ? <div className="loading-state">正在載入課節…</div> : initialStudent ? null : daySessions.length === 0 ? <Empty>這天沒有課節。可新增課節，或查看右側待安排清單。</Empty> : <div className="lesson-list">
            {daySessions.map((item) => <button key={item.id} className={`lesson-card ${item.lifecycle_status === 'cancelled' ? 'lesson-card-cancelled' : ''} ${selectedSessionId === item.id ? 'lesson-card-selected' : ''}`} onClick={() => void openSession(item)}>
              <span className="lesson-time">{sessionTimeLabel(item)}</span>
              <span className="lesson-card-body"><strong>{item.course_type || item.topic || '教學內容未定'}</strong><small>{item.topic || '尚未填寫該堂教學內容'} · {item.participant_count} 位學生</small></span>
              <span className={`lesson-status status-${item.lifecycle_status}`}>{item.lifecycle_status === 'planned' ? statusLabel(item) : lifecycleLabels[item.lifecycle_status]}</span>
            </button>)}
          </div>}

          {selectedSessionId !== null && <section className="session-detail" aria-live="polite">
            <div className="session-detail-heading"><div><h3>課節詳情</h3>{detail && <p>{detail.session_date ? dateLabel(detail.session_date) : '日期未定'} · {sessionTimeLabel(detail)}</p>}</div><button className="icon-button" aria-label="關閉課節詳情" onClick={clearSession}>×</button></div>
            {detailLoading ? <div className="loading-state">正在載入課節詳情…</div> : detailError ? <Notice>{detailError}</Notice> : detail && <>
              <div className="session-summary-grid">
                <div><span>課程類型</span><strong>{detail.course_type || '未設定'}</strong></div>
                <div><span>該堂教學內容</span><strong>{detail.topic || '未設定'}</strong></div>
                <div><span>排課狀態</span><strong>{statusLabel(detail)}</strong></div>
                <div><span>課節狀態</span><strong>{lifecycleLabels[detail.lifecycle_status]}</strong></div>
              </div>
              {detail.notes && <p className="session-notes">課節備註：{detail.notes}</p>}
              {detail.overlap_warnings.length > 0 && <Notice kind="error">時段重疊提醒：這位學生在相近時段另有課節。你仍可保存安排，請核對時間。</Notice>}
              {editOpen ? <SessionEditForm detail={detail} students={students} studentsLoading={studentsLoading} studentsError={studentsError} onRetryStudents={() => { studentProfilesRequest.current = null; setStudentProfilesState(null) }} busy={savingRecords} onDirtyChange={setEditDirty} onCancel={() => { if (!confirmLeavingSession('關閉修改表單')) return; setEditOpen(false); setEditDirty(false) }} onSaved={(updated) => { setDetail(updated); setDraft(initialRecordDraft(updated.participants)); setEditOpen(false); setEditDirty(false); void loadMonth() }} /> : <>
                <div className="participant-table-wrap"><table className="participant-table"><thead><tr><th>學生</th><th>出席</th><th>課後評價</th></tr></thead><tbody>
                  {detail.participants.map((participant) => <tr key={participant.student_id} className={participant.removed_at ? 'participant-removed' : ''}>
                    <td className="participant-profile-cell"><StudentIdentityLine student={participant.student} />{participant.removed_at && <small>已移除參與 · 歷史保留</small>}</td>
                    <td><select aria-label={`${participant.student.name} 出席狀態`} disabled={participant.removed_at !== null || detail.lifecycle_status === 'cancelled'} value={draft[participant.student_id]?.attendance_status ?? 'unrecorded'} onChange={(event) => setDraft((current) => ({ ...current, [participant.student_id]: { ...current[participant.student_id], attendance_status: event.target.value as SessionAttendanceStatus } }))}>{attendanceOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></td>
                    <td><textarea aria-label={`${participant.student.name} 課後評價`} rows={2} disabled={participant.removed_at !== null || detail.lifecycle_status === 'cancelled'} value={draft[participant.student_id]?.evaluation ?? ''} onChange={(event) => setDraft((current) => ({ ...current, [participant.student_id]: { ...current[participant.student_id], evaluation: event.target.value } }))} placeholder="記錄這堂課的表現與下一步" /></td>
                  </tr>)}
                </tbody></table></div>
                {detail.participants.length === 0 && <Empty>這堂課尚未加入學生。</Empty>}
                {recordMessage && <Notice kind={recordMessage.includes('已儲存') ? 'success' : 'error'}>{recordMessage}</Notice>}
                <div className="session-actions">
                  <button className="button button-primary" disabled={!recordsDirty || savingRecords || detail.lifecycle_status === 'cancelled'} onClick={() => void saveParticipantRecords()}>{savingRecords ? '正在儲存…' : '儲存出席與評價'}</button>
                  {detail.lifecycle_status === 'planned' && <>
                    <button className="button button-soft" disabled={recordsDirty} onClick={() => setEditOpen(true)}>修改課節</button>
                    <button className="button button-soft" onClick={() => void changeLifecycle('completed')} disabled={detail.confirmation_status !== 'confirmed'}>標記完成</button>
                    <button className="button button-danger" onClick={() => void changeLifecycle('cancelled')}>取消課節</button>
                  </>}
                  {detail.lifecycle_status !== 'cancelled' && detail.participants.some((item) => item.removed_at === null) && <span className="muted">未登記不代表缺席。</span>}
                </div>
              </>}
            </>}
          </section>}
        </Panel>
      </div>

      <aside className="workbench-aside">
        <Panel title={`待安排${unassignedSessions.length ? ` · ${unassignedSessions.length}` : ''}`} description="日期未定的需求留在這裡，不使用虛構時間。">
          {unassignedError && <Notice>{unassignedError}</Notice>}
          {unassignedSessions.length === 0 ? <Empty>目前沒有待安排課節。</Empty> : <div className="upcoming-list">{unassignedSessions.map((item) => <button key={item.id} className="upcoming-row" onClick={() => void openSession(item)}><span className="upcoming-date">—</span><span className="upcoming-info"><strong>{item.course_type || item.topic || '教學內容未定'}</strong><small>{item.participant_count}位學生 · 日期待安排</small></span><span className="lesson-status status-pending">{statusLabel(item)}</span></button>)}</div>}
        </Panel>
        <Panel title="課程月曆" description="日期以香港時區顯示。日期格也會顯示課節數。" className="calendar-panel">
          <div className="calendar-controls"><button className="button button-soft button-small" onClick={() => selectDate(today)}>今天</button><div><button className="icon-button" aria-label="上一個月" onClick={() => changeMonth(-1)}>‹</button><strong>{monthTitle}</strong><button className="icon-button" aria-label="下一個月" onClick={() => changeMonth(1)}>›</button></div></div>
          <div className="calendar-grid" role="grid" aria-label={`${monthTitle}課程月曆`}>
            {weekdays.map((day) => <span className="calendar-weekday" role="columnheader" key={day}>{day}</span>)}
            {calendarCells.map((value) => {
              const count = calendarCounts.get(value) ?? 0
              const selected = value === selectedDate
              const outsideMonth = monthKey(value) !== month
              return <button key={value} role="gridcell" aria-pressed={selected} aria-label={`${dateLabel(value)}${outsideMonth ? '，相鄰月份' : ''}，${count === 0 ? '沒有課節' : `${count} 堂課`}`} className={`calendar-cell ${selected ? 'calendar-cell-selected' : ''} ${value === today ? 'calendar-cell-today' : ''} ${count ? 'calendar-cell-has-lessons' : ''} ${outsideMonth ? 'calendar-cell-outside-month' : ''}`} onClick={() => selectDate(value)}>
                <span>{dateParts(value).day}</span><small>{count ? `${count}堂` : '—'}</small>
              </button>
            })}
          </div>
          <div className="calendar-legend"><span><i className="legend-dot legend-today" />今天</span><span><i className="legend-dot legend-lessons" />有課節（顯示數量）</span></div>
        </Panel>
        <Panel title="近期課節" description="待確認會明確標示；取消課節不列入。" className="upcoming-panel">
          {calendarLoading ? <div className="loading-state">正在載入…</div> : upcoming.length === 0 ? <Empty>未來 30 日沒有即將上課的課節。</Empty> : <div className="upcoming-list">{upcoming.map((item) => <button key={item.id} className="upcoming-row" onClick={() => void openSession(item)}><span className="upcoming-date">{item.session_date && `${Number(item.session_date.slice(5, 7))}/${Number(item.session_date.slice(8, 10))}`}</span><span className="upcoming-info"><strong>{item.course_type || item.topic || '教學內容未定'}</strong><small>{sessionTimeLabel(item)} · {item.participant_count}位</small></span><span className={`lesson-status status-${item.confirmation_status}`}>{statusLabel(item)}</span></button>)}</div>}
          <button className="text-link pending-link" onClick={() => { setSelectedDate(today); setCreateOpen(false); setSelectedSessionId(null); setDetail(null) }}>回到今天</button>
        </Panel>
      </aside>
    </div>

    {createOpen && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeCreate() }}>
      <section className="schedule-modal" role="dialog" aria-modal="true" aria-labelledby="create-session-title">
        <div className="session-detail-heading"><div><h2 id="create-session-title">新增課節</h2><p>每一個時段會個別建立；部分成功時只重試明確失敗的課節。</p></div><button className="icon-button" aria-label="關閉新增課節" disabled={createBusy} onClick={closeCreate}>×</button></div>
        {createError && <Notice>{createError}</Notice>}
        <form className="form-grid create-session-form" onSubmit={createSessions}>
          <label>課程類型<input disabled={sharedFieldsLocked} list="course-types" value={courseType} onChange={(event) => setCourseType(event.target.value)} placeholder="例如 Roblox Studio" /><datalist id="course-types"><option>Roblox Studio</option><option>Programming</option><option>LEGO SPIKE</option><option>Minecraft Education</option></datalist></label>
          <label>排課狀態<select disabled={sharedFieldsLocked} value={confirmationStatus} onChange={(event) => setConfirmationStatus(event.target.value as 'pending' | 'confirmed')}><option value="pending">待確認</option><option value="confirmed">已確認</option></select></label>
          <label className="form-span">該堂教學內容<input disabled={sharedFieldsLocked} value={topic} onChange={(event) => setTopic(event.target.value)} placeholder="記錄這一堂實際安排教甚麼" /></label>
          <div className="form-span student-chooser-field"><strong>參與學生</strong>{studentsLoading ? <div className="loading-state">正在載入學生資料…</div> : studentsError ? <><Notice>{studentsError}</Notice><button type="button" className="button button-soft" onClick={() => { studentProfilesRequest.current = null; setStudentProfilesState(null) }}>重新載入學生</button></> : <StudentChooser students={students.filter((student) => !student.is_archived)} selectedIds={participantIds} disabled={sharedFieldsLocked} onChange={updateParticipantSelection} />}<small>勾選前可查看資料；同名學生可依年齡、性別及其他已提供資訊區分。</small></div>
          {suggestedLearningContent && <div className="learning-suggestion form-span"><span>學生資料「要教學的內容」建議：{suggestedLearningContent}</span><button type="button" className="button button-soft button-small" onClick={() => { setTopic(suggestedLearningContent); setSuggestionMessage('已帶入本堂教學內容；你仍可修改。') }}>帶入本堂</button>{suggestionMessage && <small>{suggestionMessage}</small>}</div>}
          <label className="form-span">課節備註<textarea disabled={sharedFieldsLocked} rows={2} value={lessonNotes} onChange={(event) => setLessonNotes(event.target.value)} /></label>
          <div className="form-span slots-editor"><div className="slots-editor-heading"><strong>上課時段</strong><button type="button" className="button button-soft button-small" onClick={addSlot}>＋ 再加一個時段</button></div>
            {slots.map((slot, index) => {
              const outcome = slotOutcomes[slot.key]
              return <div className="slot-row" key={slot.key}>
                <strong>時段 {index + 1}</strong>
                <label>日期<input disabled={outcome?.state === 'saved' || outcome?.state === 'uncertain' || outcome?.state === 'confirmed_existing'} type="date" value={slot.session_date} onChange={(event) => updateSlot(slot.key, 'session_date', event.target.value)} /></label>
                <label>開始<input disabled={outcome?.state === 'saved' || outcome?.state === 'uncertain' || outcome?.state === 'confirmed_existing'} type="time" value={slot.start_time} onChange={(event) => updateSlot(slot.key, 'start_time', event.target.value)} /></label>
                <label>結束<input disabled={outcome?.state === 'saved' || outcome?.state === 'uncertain' || outcome?.state === 'confirmed_existing'} type="time" value={slot.end_time} onChange={(event) => updateSlot(slot.key, 'end_time', event.target.value)} /></label>
                {slots.length > 1 && <button type="button" className="button button-soft button-small" disabled={outcome?.state === 'saved' || outcome?.state === 'uncertain' || outcome?.state === 'confirmed_existing'} onClick={() => setSlots((items) => items.filter((item) => item.key !== slot.key))}>移除</button>}
                {outcome && <div className={`slot-outcome slot-outcome-${outcome.state}`}>
                  {outcome.state === 'saved' ? '此時段已保存' : outcome.state === 'confirmed_existing' ? '已確認使用日程中的既有課節' : outcome.state === 'uncertain' ? `結果待核對：${outcome.message}` : outcome.state === 'retryable' ? `未保存：${outcome.message}` : ''}
                  {outcome.state === 'uncertain' && <><button type="button" className="button button-soft button-small" disabled={createBusy} onClick={() => void checkUncertainSlot(slot)}>檢查日程</button><button type="button" className="button button-soft button-small" onClick={() => confirmExistingSlot(slot)}>確認已存在</button></>}
                </div>}
              </div>
            })}
          </div>
          <div className="form-span create-session-footer"><p className="muted">日期與時間可留空作待安排；已確認的課節需填日期、完整時間、教學內容及至少一位學生。保存失敗會保留表單。</p><div className="form-actions">{slots.some((slot) => slotOutcomes[slot.key]?.state === 'uncertain') && <span className="muted">待核對的課節不會自動重送。</span>}<button className="button button-primary" type="submit" disabled={createBusy || !hasRetryable}>{createBusy ? '正在保存…' : savedCount ? '重試未保存的時段' : '保存課節'}</button><button className="button button-soft" type="button" disabled={createBusy} onClick={closeCreate}>關閉</button></div></div>
        </form>
      </section>
    </div>}
  </div>
}

function StudentChooser({ students, selectedIds, disabled, onChange }: {
  students: StudentProfile[]
  selectedIds: number[]
  disabled?: boolean
  onChange: (ids: number[]) => void
}) {
  const visibleStudents = students.filter((student) => !student.is_archived || selectedIds.includes(student.id))
  if (visibleStudents.length === 0) return <Empty>目前沒有可加入的學生。</Empty>
  return <div className="student-chooser-list">
    {visibleStudents.map((student) => {
      const checked = selectedIds.includes(student.id)
      return <article className={`student-choice-card ${checked ? 'student-choice-selected' : ''}`} key={student.id}>
        <div className="student-choice-heading">
          <label className="student-choice-checkbox" htmlFor={`student-choice-${student.id}`}><input id={`student-choice-${student.id}`} type="checkbox" checked={checked} disabled={disabled} aria-labelledby={`student-choice-name-${student.id}`} aria-describedby={`student-choice-info-${student.id}`} onChange={(event) => onChange(event.target.checked ? [...selectedIds, student.id] : selectedIds.filter((id) => id !== student.id))} /><span className="sr-only">選取 {student.name}</span></label>
          <StudentIdentityLine student={student} nameId={`student-choice-name-${student.id}`} />
        </div>
        <StudentTeachingInformation student={student} id={`student-choice-info-${student.id}`} />
      </article>
    })}
  </div>
}

function SessionEditForm({ detail, students, studentsLoading, studentsError, onRetryStudents, busy, onDirtyChange, onCancel, onSaved }: {
  detail: SessionDetail
  students: StudentProfile[]
  studentsLoading: boolean
  studentsError: string
  onRetryStudents: () => void
  busy: boolean
  onDirtyChange: (dirty: boolean) => void
  onCancel: () => void
  onSaved: (detail: SessionDetail) => void
}) {
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const activeIds = detail.participants.filter((item) => item.removed_at === null).map((item) => item.student_id)
  const [selectedIds, setSelectedIds] = useState<number[]>(activeIds)
  const [schedule, setSchedule] = useState({
    course_type: detail.course_type ?? '', topic: detail.topic ?? '', session_date: detail.session_date ?? '',
    start_time: detail.start_time?.slice(0, 5) ?? '', end_time: detail.end_time?.slice(0, 5) ?? '',
    confirmation_status: detail.confirmation_status, notes: detail.notes ?? '',
  })
  const originalSchedule = {
    course_type: detail.course_type ?? '', topic: detail.topic ?? '', session_date: detail.session_date ?? '',
    start_time: detail.start_time?.slice(0, 5) ?? '', end_time: detail.end_time?.slice(0, 5) ?? '',
    confirmation_status: detail.confirmation_status, notes: detail.notes ?? '',
  }
  const dirty = JSON.stringify(schedule) !== JSON.stringify(originalSchedule)
    || JSON.stringify([...selectedIds].sort()) !== JSON.stringify([...activeIds].sort())
  useEffect(() => { onDirtyChange(dirty) }, [dirty, onDirtyChange])
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSaving(true)
    setError('')
    try {
      const updated = await api<SessionDetail>(`/sessions/${detail.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          course_type: schedule.course_type.trim() || null,
          topic: schedule.topic.trim() || null,
          session_date: schedule.session_date || null,
          start_time: schedule.start_time || null,
          end_time: schedule.end_time || null,
          confirmation_status: schedule.confirmation_status,
          notes: schedule.notes.trim() || null,
          participant_ids: selectedIds,
        }),
      })
      onSaved(updated)
    } catch (e) {
      setError(safeError(e))
    } finally {
      setSaving(false)
    }
  }
  return <form className="form-grid session-edit-form" onSubmit={submit}>
    {error && <div className="form-span"><Notice>{error}</Notice></div>}
    <label>課程類型<input value={schedule.course_type} onChange={(event) => setSchedule({ ...schedule, course_type: event.target.value })} /></label>
    <label>排課狀態<select value={schedule.confirmation_status} onChange={(event) => setSchedule({ ...schedule, confirmation_status: event.target.value as 'pending' | 'confirmed' })}><option value="pending">待確認</option><option value="confirmed">已確認</option></select></label>
    <label className="form-span">該堂教學內容<input value={schedule.topic} onChange={(event) => setSchedule({ ...schedule, topic: event.target.value })} /></label>
    <label>日期<input type="date" value={schedule.session_date} onChange={(event) => setSchedule({ ...schedule, session_date: event.target.value })} /></label>
    <label>開始時間<input type="time" value={schedule.start_time} onChange={(event) => setSchedule({ ...schedule, start_time: event.target.value })} /></label>
    <label>結束時間<input type="time" value={schedule.end_time} onChange={(event) => setSchedule({ ...schedule, end_time: event.target.value })} /></label>
    <div className="form-span student-chooser-field"><strong>參與學生</strong>{studentsLoading ? <div className="loading-state">正在載入學生資料…</div> : studentsError ? <><Notice>{studentsError}</Notice><button type="button" className="button button-soft" onClick={onRetryStudents}>重新載入學生</button></> : <StudentChooser students={[...students, ...detail.participants.map((item) => item.student).filter((profile) => !students.some((student) => student.id === profile.id))]} selectedIds={selectedIds} onChange={setSelectedIds} />}<small>已封存的既有參與學生會保留；取消勾選其他學生會保留其歷史紀錄。</small></div>
    <label className="form-span">課節備註<textarea rows={3} value={schedule.notes} onChange={(event) => setSchedule({ ...schedule, notes: event.target.value })} /></label>
    <div className="form-span"><FormActions onCancel={onCancel} busy={saving || busy} submitText="儲存課節修改" /></div>
  </form>
}
