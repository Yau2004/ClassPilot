import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { allPages, api, type Classroom, type Student } from '../api'
import { Empty, FormActions, jsonBody, Notice, Pager, Panel } from '../components/Shared'

const PAGE_SIZE = 8
export function ClassesPage({ notify }: { notify: (text: string, kind?: 'error' | 'success') => void }) {
  const [classes, setClasses] = useState<Classroom[]>([])
  const [students, setStudents] = useState<Student[]>([])
  const [roster, setRoster] = useState<Student[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [studentToAdd, setStudentToAdd] = useState('')
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Classroom | null>(null)
  const [error, setError] = useState('')
  const [page, setPage] = useState(1)
  const selected = classes.find((item) => item.id === selectedId) ?? null
  async function loadClasses() { setLoading(true); try { const next = await allPages<Classroom>('/classes'); setClasses(next); if (selectedId && !next.some((item) => item.id === selectedId)) setSelectedId(null); setError('') } catch (e) { setError((e as Error).message) } finally { setLoading(false) } }
  async function loadDetail(id: number) { setDetailLoading(true); try { const [people, registered] = await Promise.all([api<Student[]>('/students'), allPages<Student>(`/classes/${id}/students`)]); setStudents(people); setRoster(registered) } catch (e) { setError((e as Error).message) } finally { setDetailLoading(false) } }
  useEffect(() => { void loadClasses() }, [])
  useEffect(() => { if (selectedId !== null) void loadDetail(selectedId); else setRoster([]) }, [selectedId])
  const shown = useMemo(() => classes.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE), [classes, page])
  async function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setBusy(true); setError(''); try { const data = jsonBody(event) as { name: string; description: string }; const saved = await api<Classroom>(editing ? `/classes/${editing.id}` : '/classes', { method: editing ? 'PUT' : 'POST', body: JSON.stringify({ name: data.name, description: data.description || null }) }); setFormOpen(false); setEditing(null); await loadClasses(); setSelectedId(saved.id); notify(editing ? '班級資料已更新。' : '已新增班級。', 'success') } catch (e) { setError((e as Error).message) } finally { setBusy(false) } }
  async function removeClass(room: Classroom) { if (!window.confirm(`確定刪除「${room.name}」？仍有註冊或出席紀錄時，系統會拒絕刪除。`)) return; try { await api<void>(`/classes/${room.id}`, { method: 'DELETE' }); setSelectedId(null); await loadClasses(); notify('班級已刪除。', 'success') } catch (e) { setError((e as Error).message) } }
  async function enroll(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (!selectedId || !studentToAdd) return; setBusy(true); setError(''); try { await api(`/classes/${selectedId}/students/${studentToAdd}`, { method: 'POST' }); await loadDetail(selectedId); setStudentToAdd(''); notify('學生已加入班級。', 'success') } catch (e) { setError((e as Error).message) } finally { setBusy(false) } }
  async function unenroll(student: Student) { if (!selectedId || !window.confirm(`確定取消 ${student.name} 的班級註冊？既有出席紀錄會保留。`)) return; try { await api<void>(`/classes/${selectedId}/students/${student.id}`, { method: 'DELETE' }); await loadDetail(selectedId); notify('已取消註冊，歷史出席紀錄會保留。', 'success') } catch (e) { setError((e as Error).message) } }
  return <div className="page-stack"><Panel title="班級與註冊" description="建立課程班級，管理每個班級的學生名單。" action={<button className="button button-primary" onClick={() => { setEditing(null); setFormOpen(true) }}>＋ 新增班級</button>}>
    {error && <Notice onClose={() => setError('')}>{error}</Notice>}
    {formOpen && <form className="form-card form-grid" onSubmit={submit}><label>班級名稱<input name="name" required defaultValue={editing?.name ?? ''} placeholder="例如：小小機械人工作坊" /></label><label>課程介紹<input name="description" defaultValue={editing?.description ?? ''} placeholder="選填，簡短介紹課程內容" /></label><div className="form-span"><FormActions onCancel={() => { setFormOpen(false); setEditing(null) }} busy={busy} submitText={editing ? '儲存修改' : '新增班級'} /></div></form>}
    {loading ? <div className="loading-state">正在載入班級…</div> : classes.length === 0 ? <Empty>尚未建立班級，新增課程後即可加入學生。</Empty> : <><div className="table-scroll"><table><thead><tr><th>班級</th><th>課程介紹</th><th>註冊學生</th><th className="align-right">操作</th></tr></thead><tbody>{shown.map((room) => <tr key={room.id} className={selectedId === room.id ? 'selected-row' : ''}><td><button className="text-link" onClick={() => setSelectedId(room.id)}>{room.name}</button></td><td>{room.description || <span className="muted">未填寫</span>}</td><td>{selectedId === room.id && !detailLoading ? roster.length : '—'}</td><td><div className="row-actions"><button className="button button-soft button-small" onClick={() => { setEditing(room); setFormOpen(true) }}>編輯</button><button className="button button-danger button-small" onClick={() => void removeClass(room)}>刪除</button></div></td></tr>)}</tbody></table></div><Pager page={page} total={classes.length} pageSize={PAGE_SIZE} onChange={setPage} /></>}
  </Panel>
  {selected && <Panel title={`${selected.name}・學生名單`} description="名單可跨頁取得；取消註冊不會刪除歷史出席。" action={<span className="count-pill">{roster.length} 位學生</span>}>
    {detailLoading ? <div className="loading-state">正在載入註冊名單…</div> : <>
      <form className="inline-form" onSubmit={enroll}><label className="grow">加入既有學生<select name="student_id" aria-label="選擇要加入的學生" value={studentToAdd} onChange={(e) => setStudentToAdd(e.target.value)}><option value="">選擇學生</option>{students.filter((student) => !roster.some((person) => person.id === student.id)).map((person) => <option key={person.id} value={person.id}>{person.name}・{person.age} 歲</option>)}</select></label><button className="button button-primary" disabled={busy || !studentToAdd}>加入班級</button></form>
      {roster.length === 0 ? <Empty>目前沒有註冊學生。</Empty> : <div className="member-list">{roster.map((student) => <div className="member-row" key={student.id}><span className="avatar avatar-lilac">{student.name.slice(0, 1)}</span><div className="grow"><strong>{student.name}</strong><small>{student.age} 歲・STU-{String(student.id).padStart(4, '0')}</small></div><button className="button button-danger button-small" onClick={() => void unenroll(student)}>取消註冊</button></div>)}</div>}
    </>}
  </Panel>}
  </div>
}
