import { createContext, useCallback, useContext, useEffect, useState, type ReactNode, type FormEvent } from 'react';
import { startAuthentication, startRegistration } from '@simplewebauthn/browser';
import { api, setCsrf, ApiError } from './api';
import { ErrorMessage, Loading } from './shared';

type AuthState = { level: string; username?: string; has_totp?: boolean; has_webauthn?: boolean };
type Result = { level?: string; csrf_token?: string; recovery_codes?: string[]; login_required?: boolean };
const AuthContext = createContext<{ state?: AuthState; reload: () => Promise<void> }>({ reload: async () => {} });
export function useAuth() { return useContext(AuthContext); }
export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>();
  const [error, setError] = useState('');
  const reload = useCallback(async () => {
    try {
      const csrf = await api<{ csrf_token: string }>('/auth/csrf'); setCsrf(csrf.csrf_token);
      setState(await api<AuthState>('/auth/me')); setError('');
    } catch (err) { setError((err as Error).message); }
  }, []);
  useEffect(() => { void reload(); const expired = () => { void reload(); }; window.addEventListener('hub:session-expired', expired); return () => window.removeEventListener('hub:session-expired', expired); }, [reload]);
  if (error) return <main className="auth-page"><ErrorMessage message={error}/><button onClick={() => void reload()}>Volver a conectar</button></main>;
  if (!state) return <main className="auth-page"><Loading/></main>;
  return <AuthContext.Provider value={{ state, reload }}>{state.level === 'full' ? children : <Authentication/>}</AuthContext.Provider>;
}

export async function registerKey(name: string) {
  const options = await api<{ challenge_id: string; options: Parameters<typeof startRegistration>[0]['optionsJSON'] }>('/auth/webauthn/register/options', 'POST', {});
  const credential = await startRegistration({ optionsJSON: options.options });
  return api<Result>('/auth/webauthn/register/verify', 'POST', { name, challenge_id: options.challenge_id, credential });
}
export async function authenticateKey(password?: string, trust_device = false) {
  const options = await api<{ challenge_id: string; options: Parameters<typeof startAuthentication>[0]['optionsJSON'] }>('/auth/webauthn/authenticate/options', 'POST', {});
  const credential = await startAuthentication({ optionsJSON: options.options });
  return api<Result>('/auth/webauthn/authenticate/verify', 'POST', { challenge_id: options.challenge_id, credential, ...(password !== undefined ? { password } : {}), trust_device });
}

function Authentication() {
  const { state, reload } = useAuth();
  const [username, setUsername] = useState(''); const [password, setPassword] = useState('');
  const [newPassword, setNewPassword] = useState(''); const [confirmation, setConfirmation] = useState('');
  const [code, setCode] = useState(''); const [trust, setTrust] = useState(false);
  const [recovery, setRecovery] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [setup, setSetup] = useState<{ secret: string; qr_svg: string; challenge_id: string }>();
  const [codes, setCodes] = useState<string[]>();
  const stage = state!.level;
  async function run(operation: () => Promise<Result | void>) {
    setBusy(true); setError('');
    try { const result = await operation(); if (result?.recovery_codes) setCodes(result.recovery_codes); else await reload(); }
    catch (err) { setError((err as Error).message); if (err instanceof ApiError && err.status === 401) await reload(); } finally { setBusy(false); }
  }
  function login(event: FormEvent) { event.preventDefault(); void run(() => api('/auth/login', 'POST', { username, password })); }
  function verify(event: FormEvent) { event.preventDefault(); void run(() => api(recovery ? '/auth/recover' : '/auth/totp/verify', 'POST', { code, trust_device: !recovery && trust })); }
  return <main className="auth-page"><div className="auth-brand"><span className="brand-symbol">H</span><span>PERSONAL HUB</span></div><section className="auth-card"><p className="eyebrow">Tu registro, tu espacio</p><h1>{codes ? 'Guarda tus códigos de recuperación' : stage === 'anonymous' ? 'Bienvenido a tu Hub' : stage === 'initial_password' ? 'Cambia la contraseña temporal' : stage === 'password' ? 'Confirma que eres tú' : 'Protege tu cuenta'}</h1><ErrorMessage message={error}/>
    {codes ? <><p>Estos códigos se muestran una sola vez. Guarda una copia en un lugar seguro.</p><div className="recovery-codes">{codes.map(c => <code key={c}>{c}</code>)}</div><button className="primary" onClick={() => { setCodes(undefined); void reload(); }}>He guardado los códigos</button></> : stage === 'anonymous' ? <><p className="muted">Accede para consultar tu Diario personal.</p><form onSubmit={login}><label>Usuario<input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required/></label><label>Contraseña<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required/></label><button className="primary" disabled={busy}>Entrar</button></form><p className="muted small">Primera instalación: usa las credenciales temporales de tu configuración o crea el usuario con el comando administrativo.</p></> : stage === 'initial_password' ? <><p className="muted">Elige una contraseña propia de al menos 15 caracteres. Después configurarás el segundo factor.</p><form onSubmit={e => { e.preventDefault(); if (newPassword !== confirmation) { setError('Las contraseñas no coinciden.'); return; } void run(async () => { const result = await api<Result>('/auth/initial-password', 'POST', { new_password: newPassword }); setPassword(''); setNewPassword(''); setConfirmation(''); return result; }); }}><label>Nueva contraseña<input type="password" autoComplete="new-password" minLength={15} maxLength={1024} value={newPassword} onChange={e => setNewPassword(e.target.value)} required/></label><label>Repite la nueva contraseña<input type="password" autoComplete="new-password" minLength={15} maxLength={1024} value={confirmation} onChange={e => setConfirmation(e.target.value)} required/></label><button className="primary" disabled={busy}>Cambiar contraseña y continuar</button></form></> : stage === 'password' ? <><p className="muted">Usa tu aplicación autenticadora o una llave de seguridad.</p>{!recovery && <label className="check"><input type="checkbox" checked={trust} onChange={e => setTrust(e.target.checked)}/>Confiar en este navegador durante 30 días</label>}{(state?.has_totp || recovery) && <form onSubmit={verify}><label>{recovery ? 'Código de recuperación' : 'Código de la aplicación'}<input autoComplete="one-time-code" inputMode={recovery ? 'text' : 'numeric'} value={code} onChange={e => setCode(e.target.value)} required/></label><button className="primary" disabled={busy}>{recovery ? 'Recuperar acceso' : 'Verificar código'}</button></form>}{state?.has_webauthn && <button disabled={busy} onClick={() => void run(() => authenticateKey(undefined, trust))}>Usar llave de seguridad</button>}<button className="text-button" onClick={() => { setRecovery(!recovery); setCode(''); }}>{recovery ? 'Volver al segundo factor' : 'He perdido mis factores'}</button></> : <><p className="muted">Activa un segundo factor para acceder. Tu llave debe admitir PIN o biometría.</p>{setup ? <form onSubmit={e => { e.preventDefault(); void run(() => api('/auth/totp/confirm', 'POST', { challenge_id: setup.challenge_id, code })); }}><img className="qr" alt="Código QR para configurar tu aplicación autenticadora" src={'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(setup.qr_svg)}/><p>Clave manual</p><code className="manual-key">{setup.secret}</code><label>Código de confirmación<input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={e => setCode(e.target.value)} required/></label><button className="primary" disabled={busy}>Confirmar y activar</button><button type="button" onClick={() => setSetup(undefined)}>Volver a configurar</button></form> : <div className="stack"><button className="primary" disabled={busy} onClick={() => void run(async () => { setSetup(await api('/auth/totp/setup', 'POST', {})); })}>Configurar aplicación autenticadora</button><button disabled={busy} onClick={() => void run(() => registerKey('Llave principal'))}>Registrar llave de seguridad</button></div>}</>}
  </section><p className="auth-footer">Un espacio privado para dar continuidad a tus actividades personales.</p></main>;
}
