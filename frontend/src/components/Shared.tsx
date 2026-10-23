import type { FormEvent, ReactNode } from 'react'

export function Panel({ title, description, action, children, className = '' }: {
  title: string; description?: string; action?: ReactNode; children: ReactNode; className?: string
}) {
  return <section className={`panel ${className}`}>
    <div className="panel-heading"><div><h2>{title}</h2>{description && <p className="muted">{description}</p>}</div>{action}</div>
    {children}
  </section>
}

export function Notice({ kind = 'error', children, onClose }: { kind?: 'error' | 'success'; children: ReactNode; onClose?: () => void }) {
  return <div className={`notice notice-${kind}`} role="status"><span>{children}</span>{onClose && <button className="icon-button" onClick={onClose} aria-label="關閉提示">×</button>}</div>
}

export function Pager({ page, total, pageSize, onChange }: { page: number; total: number; pageSize: number; onChange: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  return <div className="pager"><span>共 {total} 筆・第 {Math.min(page, pages)} / {pages} 頁</span><div>
    <button className="button button-soft" disabled={page <= 1} onClick={() => onChange(page - 1)}>上一頁</button>
    <button className="button button-soft" disabled={page >= pages} onClick={() => onChange(page + 1)}>下一頁</button>
  </div></div>
}

export function Empty({ children = '目前沒有資料。' }: { children?: string }) {
  return <div className="empty-state"><span className="empty-mark" aria-hidden="true">✦</span><p>{children}</p></div>
}

export function FormActions({ onCancel, busy, submitText }: { onCancel: () => void; busy: boolean; submitText: string }) {
  return <div className="form-actions"><button className="button button-primary" type="submit" disabled={busy}>{busy ? '儲存中…' : submitText}</button><button className="button button-soft" type="button" disabled={busy} onClick={onCancel}>取消</button></div>
}

export function jsonBody(event: FormEvent<HTMLFormElement>) {
  return Object.fromEntries(new FormData(event.currentTarget).entries())
}
