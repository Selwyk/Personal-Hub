import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { EntryEditor } from './journal';
import { api, type Entry } from './api';

vi.mock('./api', async importOriginal => ({ ...await importOriginal<typeof import('./api')>(), api: vi.fn() }));
const fixture: Entry = {
  id: 'synthetic-entry', occurred_on: '2026-09-01', title: 'Propuesta sintética revisada',
  summary: 'Resumen sintético', content_markdown: '## Detalle sintético\nSolo una propuesta.',
  status: 'draft', source: 'synthetic', activity_type: 'proposal', evidence_level: 'unverified',
  technologies: [], projects: [], tags: [], related_entry_ids: [], references: [], version: 1,
  created_at: '2026-09-01T12:00:00Z', updated_at: '2026-09-01T12:00:00Z', published_at: null,
};
function open(entry = fixture, path = '/diario/entrada/synthetic-entry') {
  vi.mocked(api).mockImplementation(async (resource, method = 'GET', body) => {
    if (resource === '/projects' || resource === '/tags') return [] as never;
    if (method === 'PUT') return { ...entry, ...(body as object), version: 2 } as never;
    return entry as never;
  });
  return render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/diario/entrada/:id" element={<EntryEditor/>}/>
    <Route path="/diario/nueva" element={<EntryEditor/>}/>
  </Routes></MemoryRouter>);
}
afterEach(() => vi.clearAllMocks());
describe('lectura y edición del Diario', () => {
  it.each(['draft', 'published'] as const)('abre una entrada %s en lectura sin modificarla', async status => {
    open({ ...fixture, status });
    expect(await screen.findByRole('heading', { name: fixture.title })).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Detalle sintético' })).toBeVisible();
    expect(screen.queryByLabelText('Título', { exact: true })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Editar entrada' })).toBeVisible();
    expect(vi.mocked(api).mock.calls.every(([, method]) => !method || method === 'GET')).toBe(true);
  });
  it('edita explícitamente, cancela sin guardar y vuelve a lectura tras guardar', async () => {
    open(); await screen.findByRole('heading', { name: fixture.title });
    fireEvent.click(screen.getByRole('button', { name: 'Editar entrada' }));
    fireEvent.change(screen.getByLabelText('Título', { exact: true }), { target: { value: 'Cambio descartado' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar edición' }));
    expect(screen.getByRole('heading', { name: fixture.title })).toBeVisible();
    expect(vi.mocked(api).mock.calls.some(([, method]) => method === 'PUT')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Editar entrada' }));
    expect(screen.getByLabelText('Título', { exact: true })).toHaveValue(fixture.title);
    fireEvent.change(screen.getByLabelText('Título', { exact: true }), { target: { value: 'Cambio guardado' } });
    fireEvent.click(screen.getByRole('button', { name: 'Guardar borrador' }));
    expect(await screen.findByRole('heading', { name: 'Cambio guardado' })).toBeVisible();
    expect(screen.queryByLabelText('Título', { exact: true })).not.toBeInTheDocument();
    expect(api).toHaveBeenCalledWith('/journal/entries/synthetic-entry', 'PUT', expect.objectContaining({ title: 'Cambio guardado', version: 1 }));
  });
  it('mantiene los cambios y el editor si falla el guardado', async () => {
    open(); await screen.findByRole('heading', { name: fixture.title });
    fireEvent.click(screen.getByRole('button', { name: 'Editar entrada' }));
    fireEvent.change(screen.getByLabelText('Título', { exact: true }), { target: { value: 'Cambio pendiente' } });
    vi.mocked(api).mockRejectedValueOnce(new Error('Conflicto sintético'));
    fireEvent.click(screen.getByRole('button', { name: 'Guardar borrador' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Conflicto sintético');
    expect(screen.getByLabelText('Título', { exact: true })).toHaveValue('Cambio pendiente');
  });
  it('abre nuevas entradas directamente en edición', async () => {
    open(fixture, '/diario/nueva');
    await waitFor(() => expect(screen.getByLabelText('Título', { exact: true })).toBeVisible());
    expect(screen.queryByRole('button', { name: 'Editar entrada' })).not.toBeInTheDocument();
  });
});
