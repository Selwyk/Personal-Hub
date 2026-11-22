import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TaskList, Tasks } from './tasks';
import { api, download, type Task } from './api';

vi.mock('./api', async importOriginal => ({ ...await importOriginal<typeof import('./api')>(), api: vi.fn(), download: vi.fn() }));
const fixture: Task = { id: 'synthetic-task', title: 'Tarea sintética', description: 'Descripción sintética',
  status: 'todo', priority: 'normal', due_on: null, parent_id: null, completed_at: null,
  projects: [{ id: 'project-fixture', name: 'Proyecto sintético' }], tags: [], version: 1,
  created_at: '2026-11-21T10:00:00Z', updated_at: '2026-11-21T10:00:00Z' };

function mockApi(items: Task[] = [fixture]) {
  vi.mocked(api).mockImplementation(async (path, method = 'GET') => {
    if (path === '/modules') return { tasks: true } as never;
    if (path === '/projects' || path === '/tags') return [] as never;
    if (method === 'GET' && path.startsWith('/tasks?')) return { items, total: items.length, limit: 20, offset: 0 } as never;
    return fixture as never;
  });
}
afterEach(() => { cleanup(); vi.resetAllMocks(); });
describe('Tareas V0.1', () => {
  it('no consulta tareas ni catálogos cuando el módulo está desactivado', async () => {
    vi.mocked(api).mockResolvedValue({ tasks: false }); render(<Tasks/>);
    expect(await screen.findByText('El módulo Tareas no está habilitado en esta instancia.')).toBeVisible();
    expect(api).toHaveBeenCalledTimes(1); expect(api).toHaveBeenCalledWith('/modules');
  });
  it('muestra el fallo de capacidades y permite reintentar', async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error('Red sintética')).mockResolvedValue({ tasks: false });
    render(<Tasks/>); expect(await screen.findByRole('alert')).toHaveTextContent('Red sintética');
    fireEvent.click(screen.getByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByText('El módulo Tareas no está habilitado en esta instancia.')).toBeVisible();
  });
  it('lee sin escribir, crea con vencimiento nulo y relaciones, y cancela sin guardar', async () => {
    mockApi([]); render(<TaskList/>); await screen.findByText('No hay tareas con estos filtros');
    expect(vi.mocked(api).mock.calls.every(([, method]) => !method || method === 'GET')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Nueva tarea' }));
    fireEvent.change(screen.getByLabelText('Título', { exact: true }), { target: { value: 'Descartar' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar edición' }));
    expect(vi.mocked(api).mock.calls.some(([, method]) => method === 'POST')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Nueva tarea' }));
    expect(screen.getByLabelText('Título', { exact: true })).toHaveValue('');
    fireEvent.change(screen.getByLabelText('Título', { exact: true }), { target: { value: 'Nueva sintética' } });
    fireEvent.change(screen.getByLabelText('Proyectos (separados por comas)'), { target: { value: ' Proyecto A, Proyecto B ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Guardar tarea' }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/tasks', 'POST', expect.objectContaining({ title: 'Nueva sintética', due_on: null, projects: ['Proyecto A', 'Proyecto B'] })));
    await screen.findByText('No hay tareas con estos filtros');
  });
  it('edita con versión, conserva cambios tras conflicto y recarga explícitamente', async () => {
    mockApi(); render(<TaskList/>); await screen.findByRole('heading', { name: fixture.title });
    fireEvent.click(screen.getByRole('button', { name: 'Editar ' + fixture.title }));
    fireEvent.change(screen.getByLabelText('Título', { exact: true }), { target: { value: 'Cambio pendiente' } });
    vi.mocked(api).mockRejectedValueOnce(new Error('La tarea ha cambiado. Recarga antes de guardar.'));
    fireEvent.click(screen.getByRole('button', { name: 'Guardar tarea' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('La tarea ha cambiado');
    expect(screen.getByLabelText('Título', { exact: true })).toHaveValue('Cambio pendiente');
    expect(api).toHaveBeenCalledWith('/tasks/' + fixture.id, 'PUT', expect.objectContaining({ title: 'Cambio pendiente', version: 1 }));
    vi.mocked(api).mockResolvedValueOnce({ ...fixture, version: 2, title: 'Servidor sintético' });
    fireEvent.click(screen.getByRole('button', { name: 'Descartar cambios y recargar tarea' }));
    await waitFor(() => expect(screen.getByLabelText('Título', { exact: true })).toHaveValue('Servidor sintético'));
    fireEvent.change(screen.getByLabelText('Vencimiento opcional'), { target: { value: '2026-12-31' } });
    fireEvent.click(screen.getByRole('button', { name: 'Guardar tarea' }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/tasks/' + fixture.id, 'PUT', expect.objectContaining({ version: 2, due_on: '2026-12-31' })));
    await screen.findByRole('heading', { name: fixture.title });
  });
  it('completa por versión, filtra y exporta todas las tareas', async () => {
    mockApi(); render(<TaskList/>); await screen.findByRole('heading', { name: fixture.title });
    fireEvent.click(screen.getByRole('button', { name: 'Completar ' + fixture.title }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/tasks/' + fixture.id + '/complete', 'POST', { version: 1 }));
    fireEvent.change(screen.getByLabelText('Filtrar por estado'), { target: { value: 'completed' } });
    await waitFor(() => expect(api).toHaveBeenCalledWith('/tasks?limit=20&offset=0&status=completed'));
    fireEvent.click(screen.getByRole('button', { name: 'Exportar tareas · JSON' }));
    expect(download).toHaveBeenCalledWith('/tasks/export', 'tasks.json', 'No se han podido exportar las tareas.');
  });
  it('muestra errores de listado sin afirmar lista vacía', async () => {
    vi.mocked(api).mockRejectedValue(new Error('Fallo de listado sintético')); render(<TaskList/>);
    expect(await screen.findByRole('alert')).toHaveTextContent('Fallo de listado sintético');
    expect(screen.queryByText('No hay tareas con estos filtros')).not.toBeInTheDocument();
  });
  it('muestra errores de completar y exportar sin quitar la tarea', async () => {
    mockApi(); render(<TaskList/>); await screen.findByRole('heading', { name: fixture.title });
    vi.mocked(api).mockRejectedValueOnce(new Error('Conflicto sintético'));
    fireEvent.click(screen.getByRole('button', { name: 'Completar ' + fixture.title }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Conflicto sintético');
    expect(screen.getByRole('heading', { name: fixture.title })).toBeVisible();
    vi.mocked(download).mockRejectedValueOnce(new Error('Fallo de exportación sintético'));
    fireEvent.click(screen.getByRole('button', { name: 'Exportar tareas · JSON' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Fallo de exportación sintético'));
  });
  it('renderiza el texto privado como texto y no ofrece completar tareas terminadas', async () => {
    mockApi([{ ...fixture, status: 'completed', title: '<img src=x onerror=alert(1)>', description: '<script>fixture</script>' }]);
    render(<TaskList/>); const heading = await screen.findByRole('heading', { name: '<img src=x onerror=alert(1)>' });
    expect(heading.querySelector('img')).toBeNull();
    expect(within(screen.getByRole('region', { name: 'Lista de tareas' })).queryByRole('button', { name: /Completar/ })).toBeNull();
  });
});
