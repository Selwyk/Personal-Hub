import type { components } from './api-schema';
export type Entry = components['schemas']['EntryOut'];
export type EntryInput = components['schemas']['EntryInput'];
export type EntryPage = components['schemas']['EntryPage'];
export type Entity = components['schemas']['EntityOut'];
export type CandidateInput = components['schemas']['CandidateInput'];
export type Task = components['schemas']['TaskOut'];
export type TaskInput = components['schemas']['TaskInput'];
export type TaskPage = components['schemas']['TaskPage'];

let csrfToken = '';
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
export function setCsrf(token: string) { csrfToken = token; }
export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  if (method !== 'GET' && !csrfToken) {
    const state = await api<{ csrf_token: string }>('/auth/csrf');
    setCsrf(state.csrf_token);
  }
  const response = await fetch('/api/v1' + path, {
    method, credentials: 'same-origin',
    headers: { ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(method !== 'GET' ? { 'X-CSRF-Token': csrfToken } : {}) },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/auth/')) window.dispatchEvent(new Event('hub:session-expired'));
    throw new ApiError(response.status, typeof data.detail === 'string' ? data.detail : 'No se ha podido completar la operación.');
  }
  if (data.csrf_token) setCsrf(data.csrf_token);
  return data as T;
}

export async function download(path: string, filename: string, errorMessage = 'No se ha podido exportar el Diario.') {
  const response = await fetch('/api/v1' + path, { credentials: 'same-origin' });
  if (!response.ok) throw new ApiError(response.status, errorMessage);
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = filename; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function today() {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Madrid', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  const get = (name: string) => parts.find(p => p.type === name)!.value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}
export function dateLabel(value: string) {
  return new Intl.DateTimeFormat('es-ES', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'Europe/Madrid' }).format(new Date(value + 'T12:00:00Z'));
}
export const activities = { research: 'Investigación', proposal: 'Propuesta', implementation: 'Implementación', deployment: 'Despliegue', test: 'Prueba', documentation: 'Documentación', incident: 'Incidencia' };
export const evidence = { confirmed: 'Confirmado', inferred: 'Inferido', unverified: 'No verificado' };
