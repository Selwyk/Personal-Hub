import { useEffect, useState, useCallback, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import Markdown from 'react-markdown';
import { api, dateLabel, activities, evidence, type Entry } from './api';

export function useResource<T>(path: string) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision(r => r + 1), []);
  useEffect(() => {
    let active = true;
    setLoading(true); setError('');
    api<T>(path).then(value => { if (active) setData(value); })
      .catch(err => { if (active) setError(err.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [path, revision]);
  return { data, error, loading, refresh };
}
export function ErrorMessage({ message }: { message: string }) {
  return message ? <div className="notice error" role="alert">{message}</div> : null;
}
export function Loading() { return <p className="muted" role="status">Cargando…</p>; }
export function Empty({ title, children }: { title: string, children?: ReactNode }) {
  return <div className="empty"><span className="empty-mark" aria-hidden="true">＋</span><h3>{title}</h3>{children}</div>;
}
export function PageTitle({ eyebrow, title, children }: { eyebrow: string, title: string, children?: ReactNode }) {
  return <header className="page-title"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1></div><div className="actions">{children}</div></header>;
}
export function SafeMarkdown({ content }: { content: string }) {
  return <div className="markdown"><Markdown skipHtml components={{ img: () => <span className="muted">[Imagen omitida]</span>, a: ({ children, href }) => <a href={href} rel="noopener noreferrer">{children}</a> }}>{content}</Markdown></div>;
}
export function EntryCard({ entry }: { entry: Entry }) {
  return <article className="entry-card"><div className="entry-date"><span>{entry.occurred_on.slice(8)}</span><small>{new Intl.DateTimeFormat('es', { month: 'short' }).format(new Date(entry.occurred_on + 'T12:00:00'))}</small></div><div className="entry-main"><div className="badges"><span className={'badge ' + entry.status}>{entry.status === 'draft' ? 'Borrador' : 'Publicado'}</span><span className="badge">{activities[entry.activity_type]}</span><span className="evidence">{evidence[entry.evidence_level]}</span></div><h3><Link to={'/diario/entrada/' + entry.id}>{entry.title}</Link></h3>{entry.summary && <p className="muted">{entry.summary}</p>}<div className="tags">{entry.projects.map(p => <span key={p.id}>{p.name}</span>)}{entry.tags.map(t => <span key={t.id}>#{t.name}</span>)}</div><time dateTime={entry.occurred_on} className="sr-only">{dateLabel(entry.occurred_on)}</time></div></article>;
}
export function Modal({ title, children, onClose }: { title: string, children: ReactNode, onClose: () => void }) {
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    const container = document.querySelector('.modal') as HTMLElement;
    const focusable = () => Array.from(container.querySelectorAll<HTMLElement>('button,input,select,textarea,a[href]')).filter(e => !e.hasAttribute('disabled'));
    focusable()[0]?.focus();
    const handle = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if (event.key === 'Tab') {
        const items = focusable(); const first = items[0]; const last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener('keydown', handle);
    return () => { document.removeEventListener('keydown', handle); previous?.focus(); };
  }, [onClose]);
  return <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div className="section-heading"><h2 id="modal-title">{title}</h2><button onClick={onClose} aria-label="Cerrar diálogo">×</button></div>{children}</section></div>;
}
