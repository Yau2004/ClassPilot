import { useState } from 'react'
import { Notice } from './components/Shared'
import { AttendancePage } from './pages/AttendancePage'
import { ClassesPage } from './pages/ClassesPage'
import { StudentsPage } from './pages/StudentsPage'

type Page = 'students' | 'classes' | 'attendance'
const navigation: { id: Page; label: string; glyph: string; subtitle: string }[] = [
  { id: 'students', label: '學生管理', glyph: 'S', subtitle: '學生名冊' },
  { id: 'classes', label: '班級管理', glyph: 'C', subtitle: '班級與註冊' },
  { id: 'attendance', label: '出席管理', glyph: '✓', subtitle: '每日出席' },
]

function App() {
  const [page, setPage] = useState<Page>('students')
  const [notice, setNotice] = useState<{ text: string; kind: 'error' | 'success' } | null>(null)
  const current = navigation.find((item) => item.id === page)!
  function notify(text: string, kind: 'error' | 'success' = 'error') {
    setNotice({ text, kind })
    window.setTimeout(() => setNotice(null), 4200)
  }
  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#main" onClick={(event) => event.preventDefault()} aria-label="ClassPilot 首頁">
        <span className="brand-mark">CP</span><span><strong>ClassPilot</strong><small>STEAM CLASSROOM</small></span>
      </a>
      <div className="nav-caption">課堂工作區</div>
      <nav aria-label="主要導覽">{navigation.map((item) => <button key={item.id} className={`nav-item ${page === item.id ? 'active' : ''}`} aria-current={page === item.id ? 'page' : undefined} onClick={() => { setPage(item.id); setNotice(null) }}><span className={`nav-glyph glyph-${item.id}`}>{item.glyph}</span><span>{item.label}</span>{page === item.id && <span className="nav-dot" />}</button>)}</nav>
      <div className="sidebar-bottom"><div className="local-card"><span className="local-dot" /><div><strong>本機工作區</strong><small>資料儲存於 PostgreSQL</small></div></div><div className="sidebar-version">ClassPilot · MVP</div></div>
    </aside>
    <main id="main" className="main-area">
      <header className="topbar"><div className="breadcrumb">工作區 <span>/</span> <strong>{current.label}</strong></div><div className="profile-chip"><span className="avatar avatar-blue">CP</span><span><strong>課堂管理員</strong><small>本機展示</small></span></div></header>
      <div className="content-wrap"><div className="page-heading"><div><div className="eyebrow">CLASSROOM MANAGEMENT</div><h1>{current.subtitle}</h1><p>讓每堂課的安排與紀錄，都清楚又從容。</p></div><div className="heading-stamp"><span>✦</span><span>今天也有好點子</span></div></div>
        {notice && <Notice kind={notice.kind} onClose={() => setNotice(null)}>{notice.text}</Notice>}
        {page === 'students' && <StudentsPage notify={notify} />}
        {page === 'classes' && <ClassesPage notify={notify} />}
        {page === 'attendance' && <AttendancePage notify={notify} />}
        <footer className="page-footer"><span>ClassPilot 本機展示 MVP</span><span>一點好奇，成就大想法 <span className="footer-star">✦</span></span></footer>
      </div>
    </main>
  </div>
}

export default App
