import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { allPages, api, ApiError, type StudentProfile, type StudentGender, type SupportNeedStatus } from '../api'
import { Empty, FormActions, Notice, Pager, Panel } from '../components/Shared'
import { StudentIdentityLine, StudentTeachingInformation } from '../components/StudentProfileFields'

const PAGE_SIZE = 8
type StudentDraft = {
  name: string
  age: string
  gender: StudentGender | ''
  support_need_status: SupportNeedStatus | ''
  support_need_tags: string
  support_notes: string
  general_notes: string
}

function draftFor(student?: StudentProfile | null): StudentDraft {
  return {
    name: student?.name ?? '',
    age: student?.age == null ? '' : String(student.age),
    gender: student?.gender ?? '',
    support_need_status: student?.support_need_status === 'not_provided' ? '' : student?.support_need_status ?? '',
    support_need_tags: student?.support_need_tags.join(', ') ?? '',
    support_notes: student?.support_notes ?? '',
    general_notes: student?.general_notes ?? '',
  }
}

function sameDraft(left: StudentDraft, right: StudentDraft) {
  return JSON.stringify(left) === JSON.stringify(right)
}

export function StudentsPage({ notify, onScheduleStudent }: {
  notify: (text: string, kind?: 'error' | 'success') => void
  onScheduleStudent: (student: StudentProfile) => void
}) {
  const [rows, setRows] = useState<StudentProfile[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<StudentProfile | null>(null)
  const [draft, setDraft] = useState<StudentDraft>(() => draftFor())
  const [error, setError] = useState('')
  const [historyBlockStudentId, setHistoryBlockStudentId] = useState<number | null>(null)
  const [page, setPage] = useState(1)
  const triggerRef = useRef<HTMLElement | null>(null)
  const dialogRef = useRef<HTMLElement>(null)
  const nameInputRef = useRef<HTMLInputElement>(null)
  const closeRequestRef = useRef<() => void>(() => undefined)
  const originalDraft = useMemo(() => draftFor(editing), [editing])
  const dirty = formOpen && !sameDraft(draft, originalDraft)

  useEffect(() => {
    let active = true
    allPages<StudentProfile>('/students/profiles?include_archived=true')
      .then((items) => { if (active) { setRows(items); setError('') } })
      .catch((e) => { if (active) setError((e as Error).message) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!formOpen) return
    const previousFocus = triggerRef.current
    const focusTimer = window.requestAnimationFrame(() => nameInputRef.current?.focus())
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        closeRequestRef.current()
        return
      }
      if (event.key !== 'Tab' || !dialogRef.current) return
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [href], [tabindex]:not([tabindex="-1"])',
      )).filter((element) => element.getClientRects().length > 0)
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      window.cancelAnimationFrame(focusTimer)
      document.removeEventListener('keydown', onKeyDown)
      previousFocus?.focus()
    }
  }, [formOpen])

  const shown = useMemo(() => rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE), [rows, page])

  function openForm(student?: StudentProfile) {
    triggerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setError('')
    setHistoryBlockStudentId(null)
    setEditing(student ?? null)
    setDraft(draftFor(student))
    setFormOpen(true)
  }

  const requestClose = useCallback(() => {
    if (busy) return
    if (dirty && !window.confirm('有尚未儲存的修改。確定要關閉並放棄這些輸入嗎？')) return
    setFormOpen(false)
    setEditing(null)
    setDraft(draftFor())
    setError('')
  }, [busy, dirty])
  useEffect(() => {
    closeRequestRef.current = requestClose
  }, [requestClose])

  function openStudentSchedule(student: StudentProfile) {
    setError('')
    setHistoryBlockStudentId(null)
    onScheduleStudent(student)
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    if (!draft.gender || !draft.support_need_status) {
      setError('請選擇性別及教學支援需要狀態。')
      return
    }
    setBusy(true)
    setError('')
    try {
      const profile = {
        name: draft.name.trim(),
        age: draft.age === '' ? null : Number(draft.age),
        gender: draft.gender || null,
        support_need_status: draft.support_need_status,
        support_need_tags: draft.support_need_tags.split(',').map((tag) => tag.trim()).filter(Boolean),
        support_notes: draft.support_notes.trim() || null,
        general_notes: draft.general_notes.trim() || null,
      }
      const saved = await api<{ id: number }>(editing ? `/students/${editing.id}` : '/students', {
        method: editing ? 'PUT' : 'POST', body: JSON.stringify(profile),
      })
      const completeProfile: StudentProfile = {
        id: saved.id,
        name: profile.name,
        age: profile.age,
        gender: profile.gender,
        support_need_status: profile.support_need_status,
        support_need_tags: profile.support_need_tags,
        support_notes: profile.support_notes,
        general_notes: profile.general_notes,
        is_archived: editing?.is_archived ?? false,
      }
      setRows((current) => editing
        ? current.map((row) => row.id === completeProfile.id ? completeProfile : row)
        : [...current, completeProfile].sort((left, right) => left.id - right.id))
      if (!editing && Math.ceil((rows.length + 1) / PAGE_SIZE) > page) setPage(Math.ceil((rows.length + 1) / PAGE_SIZE))
      setFormOpen(false)
      setEditing(null)
      setDraft(draftFor())
      setError('')
      notify(editing ? '學生資料已更新。' : '已新增學生。', 'success')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function remove(student: StudentProfile) {
    if (!window.confirm(`確定刪除學生「${student.name}」？有歷史資料時系統會拒絕刪除並保留紀錄。`)) return
    setHistoryBlockStudentId(null)
    try {
      await api<void>(`/students/${student.id}`, { method: 'DELETE' })
      setRows((current) => current.filter((row) => row.id !== student.id))
      setHistoryBlockStudentId(null)
      notify('學生已刪除。', 'success')
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setHistoryBlockStudentId(student.id)
        setError('這位學生已有課節或保留的歷史紀錄，不能刪除。可封存學生以保留歷史。')
      } else {
        setHistoryBlockStudentId(null)
        setError((e as Error).message)
      }
    }
  }

  async function archiveStudent(student: StudentProfile) {
    if (!window.confirm(`封存「${student.name}」？學生會從新課節選取清單移除，既有歷史會保留。`)) return
    try {
      const archived = await api<StudentProfile>(`/students/${student.id}/archive`, { method: 'POST' })
      setRows((current) => current.map((row) => row.id === archived.id ? archived : row))
      setHistoryBlockStudentId(null)
      setError('')
      notify('學生已封存，歷史紀錄已保留。', 'success')
    } catch (e) { setError((e as Error).message) }
  }

  async function restoreStudent(student: StudentProfile) {
    try {
      const restored = await api<StudentProfile>(`/students/${student.id}/restore`, { method: 'POST' })
      setRows((current) => current.map((row) => row.id === restored.id ? restored : row))
      setError('')
      notify('學生已還原。', 'success')
    } catch (e) { setError((e as Error).message) }
  }

  return <div className="page-stack">
    <Panel title="學生名冊" description="姓名可重複，年齡可留空，填寫時須為 4 至 18 歲。" action={<button className="button button-primary" onClick={() => openForm()}>＋ 新增學生</button>}>
      {error && !formOpen && <Notice onClose={() => { setError(''); setHistoryBlockStudentId(null) }}>{error}{historyBlockStudentId !== null && <button className="button button-soft button-small history-archive-action" onClick={() => { const student = rows.find((row) => row.id === historyBlockStudentId); if (student) void archiveStudent(student) }}>封存並保留歷史</button>}</Notice>}
      {loading ? <div className="loading-state">正在載入學生資料…</div> : rows.length === 0 ? <Empty>尚未建立學生，新增第一位學生開始使用。</Empty> : <>
        <div className="student-roster-grid">{shown.map((student) => <article className="student-roster-card" key={student.id}>
          <div className="student-roster-heading"><StudentIdentityLine student={student} /><div className="row-actions"><button className="button button-soft button-small" onClick={() => openForm(student)}>編輯</button>{student.is_archived ? <button className="button button-soft button-small" onClick={() => void restoreStudent(student)}>還原</button> : <button className="button button-soft button-small" onClick={() => openStudentSchedule(student)}>上課安排</button>}<button className="button button-danger button-small" onClick={() => void remove(student)}>刪除</button></div></div>
          <StudentTeachingInformation student={student} />
        </article>)}</div>
        <Pager page={page} total={rows.length} pageSize={PAGE_SIZE} onChange={setPage} />
      </>}
    </Panel>
    {formOpen && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) requestClose() }}>
      <section className="schedule-modal student-dialog" role="dialog" aria-modal="true" aria-labelledby="student-dialog-title" ref={dialogRef}>
        <div className="session-detail-heading"><div><h2 id="student-dialog-title">{editing ? '編輯學生' : '新增學生'}</h2><p>{editing ? `更新「${editing.name}」的學生資料。` : '填寫學生基本資料與日常教學需要。'}</p></div><button type="button" className="icon-button" aria-label="關閉學生資料視窗" disabled={busy} onClick={requestClose}>×</button></div>
        {error && <Notice>{error}</Notice>}
        <form className="form-grid student-form" onSubmit={submit}>
          <label>學生姓名<input ref={nameInputRef} name="name" required maxLength={100} value={draft.name} onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))} placeholder="例如：林小晴" /></label>
          <label>年齡<input name="age" type="number" min={4} max={18} value={draft.age} onChange={(event) => setDraft((current) => ({ ...current, age: event.target.value }))} /></label>
          <label>性別<select name="gender" required value={draft.gender} onChange={(event) => setDraft((current) => ({ ...current, gender: event.target.value as StudentGender | '' }))}><option value="" disabled>請選擇性別</option><option value="female">女</option><option value="male">男</option><option value="other">其他</option><option value="prefer_not_to_say">不願透露</option></select></label>
          <label>教學支援需要<select name="support_need_status" required value={draft.support_need_status} onChange={(event) => setDraft((current) => ({ ...current, support_need_status: event.target.value as SupportNeedStatus | '' }))}><option value="" disabled>請選擇</option><option value="none_reported">明確表示沒有相關需要</option><option value="needs_support">有相關教學支援需要</option></select></label>
          <label className="form-span" hidden={draft.support_need_status !== 'needs_support'}>支援標籤（以逗號分隔）<input name="support_need_tags" maxLength={2000} value={draft.support_need_tags} onChange={(event) => setDraft((current) => ({ ...current, support_need_tags: event.target.value }))} placeholder="例如：需要短指令、安排活動轉換提醒" /></label>
          <label className="form-span">教學注意事項<textarea name="support_notes" rows={3} maxLength={4000} value={draft.support_notes} onChange={(event) => setDraft((current) => ({ ...current, support_notes: event.target.value }))} placeholder="只記錄授課所需的支援方式；不要推斷診斷。" /></label>
          <label className="form-span">要教學的內容<textarea name="general_notes" rows={3} maxLength={4000} value={draft.general_notes} onChange={(event) => setDraft((current) => ({ ...current, general_notes: event.target.value }))} placeholder="記錄通常要學的主題或學習需求；每堂實際教學內容請填在課節。" /></label>
          <div className="form-span"><FormActions onCancel={requestClose} busy={busy} submitText={editing ? '儲存修改' : '新增學生'} /></div>
        </form>
      </section>
    </div>}
  </div>
}
