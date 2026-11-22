import { useState, type FormEvent } from 'react';
import { api, download, dateLabel, type Task, type TaskInput, type TaskPage, type Entity } from './api';
import { Empty, ErrorMessage, Loading, PageTitle, useResource } from './shared';

export const taskStates = { todo: 'Pendiente', in_progress: 'En curso', completed: 'Completada' };
export const taskPriorities = { low: 'Baja', normal: 'Normal', high: 'Alta', urgent: 'Urgente' };
const splitNames = (text: string) => text.split(',').map(s => s.trim()).filter(Boolean);

function TaskEditor({ task, onSaved, onCancel }: { task?: Task; onSaved: () => void; onCancel: () => void }) {
  const [form, setForm] = useState<TaskInput>({ title: task?.title || '', description: task?.description || '',
    status: task?.status || 'todo', priority: task?.priority || 'normal', due_on: task?.due_on || null });
  const [version, setVersion] = useState(task?.version);
  const [projects, setProjects] = useState(task?.projects.map(p => p.name).join(', ') || '');
  const [tags, setTags] = useState(task?.tags.map(t => t.name).join(', ') || '');
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const catalogProjects = useResource<Entity[]>('/projects'); const catalogTags = useResource<Entity[]>('/tags');
  function set<K extends keyof TaskInput>(key: K, value: TaskInput[K]) { setForm(f => ({ ...f, [key]: value })); }
  async function save(event: FormEvent) {
    event.preventDefault(); if (busy) return; setBusy(true); setError('');
    try {
      await api<Task>(task ? '/tasks/' + task.id : '/tasks', task ? 'PUT' : 'POST',
        { ...form, projects: splitNames(projects), tags: splitNames(tags), ...(task ? { version } : {}) });
      onSaved();
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  async function reload() {
    if (!task || busy) return; setBusy(true); setError('');
    try {
      const current = await api<Task>('/tasks/' + task.id);
      setForm({ title: current.title, description: current.description, status: current.status,
        priority: current.priority, due_on: current.due_on });
      setProjects(current.projects.map(p => p.name).join(', ')); setTags(current.tags.map(t => t.name).join(', '));
      setVersion(current.version);
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  return <section className="panel" aria-labelledby="task-editor-title"><h2 id="task-editor-title">{task ? 'Editar tarea' : 'Nueva tarea'}</h2>
    <ErrorMessage message={error || catalogProjects.error || catalogTags.error}/>
    <form onSubmit={e => void save(e)}><fieldset disabled={busy} className="task-fieldset">
      <label>Título<input required maxLength={240} value={form.title} onChange={e => set('title', e.target.value)}/></label>
      <label>Descripción<textarea maxLength={65536} rows={4} value={form.description || ''} onChange={e => set('description', e.target.value)}/></label>
      <div className="form-grid"><label>Estado<select value={form.status} onChange={e => set('status', e.target.value as Task['status'])}>{Object.entries(taskStates).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
      <label>Prioridad<select value={form.priority} onChange={e => set('priority', e.target.value as Task['priority'])}>{Object.entries(taskPriorities).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
      <label>Vencimiento opcional<input type="date" value={form.due_on || ''} onChange={e => set('due_on', e.target.value || null)}/></label>
      <p className="muted small">El vencimiento es una fecha, sin hora ni aviso automático.</p>
      <label>Proyectos (separados por comas)<input maxLength={3600} list="task-projects" value={projects} onChange={e => setProjects(e.target.value)}/></label>
      <label>Etiquetas (separadas por comas)<input maxLength={3600} list="task-tags" value={tags} onChange={e => setTags(e.target.value)}/></label></div>
      <datalist id="task-projects">{catalogProjects.data?.map(p => <option key={p.id} value={p.name}/>)}</datalist>
      <datalist id="task-tags">{catalogTags.data?.map(t => <option key={t.id} value={t.name}/>)}</datalist>
      <div className="actions"><button className="primary" type="submit">{busy ? 'Guardando…' : 'Guardar tarea'}</button><button type="button" onClick={onCancel}>Cancelar edición</button>
      {task && error && <button type="button" onClick={() => void reload()}>Descartar cambios y recargar tarea</button>}</div>
    </fieldset></form></section>;
}

export function TaskList() {
  const [status, setStatus] = useState(''); const [offset, setOffset] = useState(0);
  const [editing, setEditing] = useState(false); const [selected, setSelected] = useState<Task>();
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const { data, error: loadError, loading, refresh } = useResource<TaskPage>(`/tasks?limit=20&offset=${offset}${status ? '&status=' + status : ''}`);
  async function complete(task: Task) {
    if (busy) return; setBusy(true); setError('');
    try { await api<Task>(`/tasks/${task.id}/complete`, 'POST', { version: task.version }); refresh(); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  async function exportTasks() {
    setError(''); try { await download('/tasks/export', 'tasks.json', 'No se han podido exportar las tareas.'); }
    catch (err) { setError((err as Error).message); }
  }
  return <><PageTitle eyebrow="Organización" title="Tareas"><button className="primary" disabled={editing || busy} onClick={() => { setSelected(undefined); setEditing(true); }}>Nueva tarea</button><button onClick={() => void exportTasks()}>Exportar tareas · JSON</button></PageTitle>
    <ErrorMessage message={error || loadError}/>
    {editing ? <TaskEditor key={selected?.id || 'new'} task={selected} onCancel={() => setEditing(false)} onSaved={() => { setEditing(false); refresh(); }}/>
      : <><section className="panel filters"><div className="task-toolbar"><label>Filtrar por estado<select value={status} onChange={e => { setStatus(e.target.value); setOffset(0); }}><option value="">Todos</option>{Object.entries(taskStates).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label><button disabled={loading || busy} onClick={refresh}>Actualizar lista</button></div></section>
        {loading ? <Loading/> : loadError ? null : data?.items.length ? <section className="panel" aria-label="Lista de tareas">{data.items.map(task => <article key={task.id} className="task-card"><div className="task-main"><div className="badges"><span className="badge">{taskStates[task.status]}</span><span className="badge">Prioridad {taskPriorities[task.priority].toLowerCase()}</span></div><h2>{task.title}</h2>{task.description && <p className="task-description">{task.description}</p>}<p className="muted small">{task.due_on ? <>Vence el <time dateTime={task.due_on}>{dateLabel(task.due_on)}</time></> : 'Sin vencimiento'}</p><div className="tags">{task.projects.map(p => <span key={p.id}>{p.name}</span>)}{task.tags.map(t => <span key={t.id}>#{t.name}</span>)}</div></div><div className="actions"><button disabled={busy} onClick={() => { setSelected(task); setEditing(true); }} aria-label={'Editar ' + task.title}>Editar</button>{task.status !== 'completed' && <button disabled={busy} onClick={() => void complete(task)} aria-label={'Completar ' + task.title}>Completar</button>}</div></article>)}</section>
          : <Empty title="No hay tareas con estos filtros"/>}
        {data && <div className="actions pagination"><button disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Anterior</button><span>{data.total} tareas · Página {Math.floor(offset / 20) + 1}</span><button disabled={loading || offset + data.limit >= data.total} onClick={() => setOffset(offset + 20)}>Siguiente</button></div>}</>}
  </>;
}

export function Tasks() {
  const modules = useResource<{ tasks: boolean }>('/modules');
  if (modules.loading) return <Loading/>;
  if (modules.error) return <><ErrorMessage message={modules.error}/><button onClick={modules.refresh}>Reintentar</button></>;
  if (!modules.data?.tasks) return <><PageTitle eyebrow="Organización" title="Tareas"/><p>El módulo Tareas no está habilitado en esta instancia.</p></>;
  return <TaskList/>;
}
