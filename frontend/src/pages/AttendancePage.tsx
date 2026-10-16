import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { allPages, api, statusLabels, todayLocal, type AttendanceRecord, type AttendanceStatus, type Classroom, type Student } from '../api'
import { Empty, Notice, Panel } from '../components/Shared'

const statuses: AttendanceStatus[] = ['present', 'absent', 'late', 'excused']
export function AttendancePage({ notify }: { notify: (text: string, kind?: 'error' | 'success') => void }) {
  const [classes, setClasses] = useState<Classroom[]>([])
  const [students, setStudents] = useState<Student[]>([])
  const [roster, setRoster] = useState<Student[]>([])
  const [records, setRecords] = useState<AttendanceRecord[]>([])
  const [classId, setClassId] = useState('')
  const [date, setDate] = useState(todayLocal)
  const [studentId, setStudentId] = useState('')
  const [status, setStatus] = useState<AttendanceStatus>('present')
  const [note, setNote] = useState('')
  const [editing, setEditing] = useState<number | null>(null)
  const [editStatus, setEditStatus] = useState<AttendanceStatus>('present')
  const [editNote, setEditNote] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function loadClasses() { try { const all = await allPages<Classroom>('/classes'); setClasses(all); if (!classId && all.length) setClassId(String(all[0].id)) } catch (e) { setError((e as Error).message) } }
  async function loadDay(id: string, day: string) { if (!id || !day) { setRecords([]); return } setLoading(true); try { const [attendance, people, registered] = await Promise.all([allPages<AttendanceRecord>(`/classes/${id}/attendance?attendance_date=${encodeURIComponent(day)}`), api<Student[]>('/students'), allPages<Student>(`/classes/${id}/students`)]); setRecords(attendance); setStudents(people); setRoster(registered); setError('') } catch (e) { setError((e as Error).message) } finally { setLoading(false) } }
  useEffect(() => { void loadClasses() }, [])
  useEffect(() => { if (classId) void loadDay(classId, date); else { setRecords([]); setLoading(false) } }, [classId, date])
  const available = useMemo(() => roster.filter((person) => !records.some((record) => record.student_id === person.id)), [roster, records])
  const byStudent = useMemo(() => new Map(students.map((person) => [person.id, person])), [students])
  async function create(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (!classId || !studentId) return; setBusy(true); setError(''); try { await api(`/classes/${classId}/attendance`, { method: 'POST', body: JSON.stringify({ student_id: Number(studentId), attendance_date: date, status, note: note || null }) }); setStudentId(''); setNote(''); await loadDay(classId, date); notify('出席紀錄已新增。', 'success') } catch (e) { setError((e as Error).message) } finally { setBusy(false) } }
  function beginEdit(record: AttendanceRecord) { setEditing(record.id); setEditStatus(record.status); setEditNote(record.note ?? '') }
  async function saveEdit(event: FormEvent<HTMLFormElement>, record: AttendanceRecord) { event.preventDefault(); if (!classId) return; setBusy(true); setError(''); try { await api(`/classes/${classId}/attendance/${record.id}`, { method: 'PUT', body: JSON.stringify({ status: editStatus, note: editNote || null }) }); setEditing(null); await loadDay(classId, date); notify('出席紀錄已更新。', 'success') } catch (e) { setError((e as Error).message) } finally { setBusy(false) } }
  async function remove(record: AttendanceRecord) { if (!classId || !window.confirm('確定刪除這筆出席紀錄？')) return; try { await api<void>(`/classes/${classId}/attendance/${record.id}`, { method: 'DELETE' }); await loadDay(classId, date); notify('出席紀錄已刪除。', 'success') } catch (e) { setError((e as Error).message) } }
  return <div className="page-stack"><Panel title="每日出席" description="依班級與日期檢視出席狀態；未登記的學生不會自動視為缺席。">
    {error && <Notice onClose={() => setError('')}>{error}</Notice>}
    <div className="filter-row"><label>班級<select name="class_id" value={classId} onChange={(e) => setClassId(e.target.value)}><option value="">選擇班級</option>{classes.map((room) => <option key={room.id} value={room.id}>{room.name}</option>)}</select></label><label>日期<input name="attendance_date" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label><span className="date-hint">{records.length} 筆已登記</span></div>
  </Panel>
  {classId && <Panel title="新增紀錄" description="僅可為目前已註冊的學生新增紀錄；歷史紀錄仍會保留在下方列表。">
    <form className="form-grid attendance-form" onSubmit={create}><label>學生<select name="student_id" required value={studentId} onChange={(e) => setStudentId(e.target.value)}><option value="">選擇學生</option>{available.map((person) => <option key={person.id} value={person.id}>{person.name}・{person.age} 歲</option>)}</select></label><label>出席狀態<select name="status" value={status} onChange={(e) => setStatus(e.target.value as AttendanceStatus)}>{statuses.map((value) => <option key={value} value={value}>{statusLabels[value]}</option>)}</select></label><label className="form-span">備註<input name="note" value={note} onChange={(e) => setNote(e.target.value)} placeholder="選填" /></label><div className="form-span"><button className="button button-primary" type="submit" disabled={busy || !available.length}>{busy ? '儲存中…' : available.length ? '新增出席紀錄' : '目前沒有可登記的學生'}</button></div></form>
  </Panel>}
  <Panel title="出席紀錄" description="已退班學生的歷史紀錄仍可查詢及修正。" action={<span className="count-pill">{records.length} 筆</span>}>
    {loading ? <div className="loading-state">正在載入出席紀錄…</div> : !classId ? <Empty>請先建立或選擇班級。</Empty> : records.length === 0 ? <Empty children={`${date} 尚無出席紀錄。這不代表學生缺席。`} /> : <div className="table-scroll"><table><thead><tr><th>學生</th><th>狀態</th><th>備註</th><th className="align-right">操作</th></tr></thead><tbody>{records.map((record) => { const person = byStudent.get(record.student_id); return <tr key={record.id}><td><strong>{person?.name ?? `學生 #${record.student_id}`}</strong>{!roster.some((item) => item.id === record.student_id) && <span className="history-tag">歷史名單</span>}</td>{editing === record.id ? <><td><select aria-label="修改出席狀態" value={editStatus} onChange={(e) => setEditStatus(e.target.value as AttendanceStatus)}>{statuses.map((value) => <option key={value} value={value}>{statusLabels[value]}</option>)}</select></td><td><input aria-label="修改備註" value={editNote} onChange={(e) => setEditNote(e.target.value)} /></td><td><form className="row-actions" onSubmit={(event) => void saveEdit(event, record)}><button className="button button-primary button-small" disabled={busy}>儲存</button><button className="button button-soft button-small" type="button" onClick={() => setEditing(null)}>取消</button></form></td></> : <><td><span className={`status-badge status-${record.status}`}><span aria-hidden="true">●</span>{statusLabels[record.status]}</span></td><td>{record.note || <span className="muted">—</span>}</td><td><div className="row-actions"><button className="button button-soft button-small" onClick={() => beginEdit(record)}>修改</button><button className="button button-danger button-small" onClick={() => void remove(record)}>刪除</button></div></td></>}</tr> })}</tbody></table></div>}
  </Panel>
  </div>
}
