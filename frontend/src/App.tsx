import { useState } from 'react'
import { type StudentProfile } from './api'
import { Notice } from './components/Shared'
import { CalendarPage } from './pages/CalendarPage'
import { StudentsPage } from './pages/StudentsPage'

type Page = 'schedule' | 'students'
const navigation: { id: Page; label: string; glyph: string; subtitle: string }[] = [
  { id: 'schedule', label: '課程日程', glyph: '日', subtitle: '課堂工作台' },
  { id: 'students', label: '學生管理', glyph: 'S', subtitle: '學生名冊' },
]

function App() {
  const [page, setPage] = useState<Page>('schedule')
  const [scheduleStudent, setScheduleStudent] = useState<StudentProfile | null>(null)
  const [notice, setNotice] = useState<{ text: string; kind: 'error' | 'success' } | null>(null)
  const current = navigation.find((item) => item.id === page)!
  function notify(text: string, kind: 'error' | 'success' = 'error') {
    setNotice({ text, kind })
    window.setTimeout(() => setNotice(null), 4200)
  }
  function navigate(next: Page) {
    setPage(next)
    if (next !== 'schedule') setScheduleStudent(null)
    setNotice(null)
  }
  function scheduleStudentLessons(student: StudentProfile) {
    setScheduleStudent(student)
    setPage('schedule')
    setNotice(null)
  }
  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#main" onClick={(event) => event.preventDefault()} aria-label="ClassPilot 首頁">
        <span className="brand-mark">CP</span><span><strong>ClassPilot</strong><small>STEAM CLASSROOM</small></span>
      </a>
      <div className="nav-caption">課堂工作區</div>
      <nav aria-label="主要導覽">{navigation.map((item) => <button key={item.id} className={`nav-item ${page === item.id ? 'active' : ''}`} aria-current={page === item.id ? 'page' : undefined} onClick={() => navigate(item.id)}><span className={`nav-glyph glyph-${item.id}`}>{item.glyph}</span><span>{item.label}</span>{page === item.id && <span className="nav-dot" />}</button>)}</nav>
      <div className="sidebar-bottom"><div className="local-card"><span className="local-dot" /><div><strong>本機工作區</strong><small>資料儲存於 PostgreSQL</small></div></div><div className="sidebar-version">ClassPilot · MVP</div></div>
    </aside>
    <main id="main" className="main-area">
      <header className="topbar"><div className="breadcrumb">工作區 <span>/</span> <strong>{current.label}</strong></div><div className="profile-chip"><span className="avatar avatar-blue">CP</span><span><strong>課堂管理員</strong><small>本機工作區</small></span></div></header>
      <div className="content-wrap"><div className="page-heading"><div><div className="eyebrow">CLASSROOM MANAGEMENT</div><h1>{current.subtitle}</h1><p>{page === 'schedule' ? '從日程安排課節，並在同一處完成出席與課後評價。' : '讓每堂課的安排與紀錄，都清楚又從容。'}</p></div></div>
        {notice && <Notice kind={notice.kind} onClose={() => setNotice(null)}>{notice.text}</Notice>}
        {page === 'schedule' && <CalendarPage initialStudent={scheduleStudent} onClearStudent={() => setScheduleStudent(null)} />}
        {page === 'students' && <StudentsPage notify={notify} onScheduleStudent={scheduleStudentLessons} />}
        <footer className="page-footer"><span>ClassPilot 本機工作區</span></footer>
      </div>
    </main>
  </div>
}

export default App
