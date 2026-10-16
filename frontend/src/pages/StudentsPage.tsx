import { useEffect, useMemo, useState } from 'react'
import { api, type Student } from '../api'
import { Empty, FormActions, jsonBody, Notice, Pager, Panel } from '../components/Shared'

const PAGE_SIZE = 8
export function StudentsPage({ notify }: { notify: (text: string, kind?: 'error' | 'success') => void }) {
  const [rows, setRows] = useState<Student[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Student | null>(null)
  const [error, setError] = useState('')
  const [page, setPage] = useState(1)
  async function load() { setLoading(true); try { setRows(await api<Student[]>('/students')); setError('') } catch (e) { setError((e as Error).message) } finally { setLoading(false) } }
  useEffect(() => { void load() }, [])
  const shown = useMemo(() => rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE), [rows, page])
  function startEdit(student?: Student) { setEditing(student ?? null); setFormOpen(true); setError('') }
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const data = jsonBody(event) as { name: string; age: string }
      await api<Student>(editing ? `/students/${editing.id}` : '/students', { method: editing ? 'PUT' : 'POST', body: JSON.stringify({ name: data.name, age: Number(data.age) }) })
      setFormOpen(false); setEditing(null); await load(); notify(editing ? '學生資料已更新。' : '已新增學生。', 'success')
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  async function remove(student: Student) {
    if (!window.confirm(`確定刪除「${student.name}」？仍有班級註冊或出席紀錄時，系統會拒絕刪除。`)) return
    try { await api<void>(`/students/${student.id}`, { method: 'DELETE' }); await load(); notify('學生已刪除。', 'success') } catch (e) { setError((e as Error).message) }
  }
  return <div className="page-stack">
    <Panel title="學生名冊" description="管理學生基本資料；姓名可重複，年齡須為 4 至 18 歲。" action={<button className="button button-primary" onClick={() => startEdit()}>＋ 新增學生</button>}>
      {error && <Notice onClose={() => setError('')}>{error}</Notice>}
      {formOpen && <form className="form-card form-grid" onSubmit={submit}>
        <label>學生姓名<input name="name" required maxLength={100} defaultValue={editing?.name ?? ''} placeholder="例如：林小晴" /></label>
        <label>年齡<input name="age" required type="number" min={4} max={18} defaultValue={editing?.age ?? 8} /></label>
        <div className="form-span"><FormActions onCancel={() => { setFormOpen(false); setEditing(null); setError('') }} busy={busy} submitText={editing ? '儲存修改' : '新增學生'} /></div>
      </form>}
      {loading ? <div className="loading-state">正在載入學生資料…</div> : rows.length === 0 ? <Empty>尚未建立學生，新增第一位學生開始使用。</Empty> : <>
        <div className="table-scroll"><table><thead><tr><th>學生</th><th>年齡</th><th>識別編號</th><th className="align-right">操作</th></tr></thead><tbody>{shown.map((student) => <tr key={student.id}><td><strong>{student.name}</strong></td><td>{student.age} 歲</td><td><span className="id-chip">STU-{String(student.id).padStart(4, '0')}</span></td><td><div className="row-actions"><button className="button button-soft button-small" onClick={() => startEdit(student)}>編輯</button><button className="button button-danger button-small" onClick={() => void remove(student)}>刪除</button></div></td></tr>)}</tbody></table></div>
        <Pager page={page} total={rows.length} pageSize={PAGE_SIZE} onChange={setPage} />
      </>}
    </Panel>
  </div>
}
