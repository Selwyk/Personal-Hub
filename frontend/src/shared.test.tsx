import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SafeMarkdown, Modal, ErrorMessage } from './shared';
import { api, setCsrf } from './api';

describe('contenido privado y seguridad del navegador', () => {
  it('renderiza Markdown sin ejecutar HTML ni descargar imágenes', () => {
    const { container } = render(<SafeMarkdown content={'# Fixture sintético\n<script>alert(1)</script>\n![remota](https://example.invalid/tracker.png)\n[malicioso](javascript:alert%281%29)'}/>);
    expect(screen.getByRole('heading')).toHaveTextContent('Fixture sintético');
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('a')?.getAttribute('href')).not.toMatch(/^javascript:/);
  });
  it('anuncia los errores', () => { render(<ErrorMessage message="Error sintético"/>); expect(screen.getByRole('alert')).toHaveTextContent('Error sintético'); });
  it('enfoca y cierra el diálogo con teclado', () => {
    const close = vi.fn(); render(<Modal title="Confirmación" onClose={close}><button>Aceptar</button></Modal>);
    expect(screen.getByRole('button', { name: 'Cerrar diálogo' })).toHaveFocus();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); expect(close).toHaveBeenCalledOnce();
  });
  it('envía CSRF en operaciones con estado sin almacenar credenciales', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 })); vi.stubGlobal('fetch', fetchMock);
    setCsrf('synthetic-csrf'); await api('/fixture', 'POST', { value: 1 });
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/fixture', expect.objectContaining({ credentials: 'same-origin', headers: expect.objectContaining({ 'X-CSRF-Token': 'synthetic-csrf' }) })); vi.unstubAllGlobals();
  });
  it('propaga conflictos de concurrencia como errores', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'version_conflict' }), { status: 409 })));
    await expect(api('/fixture', 'PUT', {})).rejects.toThrow(); vi.unstubAllGlobals();
  });
});
