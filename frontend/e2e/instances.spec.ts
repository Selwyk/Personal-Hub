import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

test.skip(!process.env.HUB_ISOLATION_ROOT, 'Requires two new synthetic product profiles and smoke states');
test('Rechaza sesiones renombradas y orígenes del otro producto', async ({ browser }) => {
  const root = path.resolve(process.env.HUB_ISOLATION_ROOT!);
  const profiles = await Promise.all(['personal', 'work'].map(async domain => {
    const folder = path.join(root, domain);
    const spec = JSON.parse(await readFile(path.join(folder, 'instance.json'), 'utf8'));
    expect(spec.synthetic).toBe(true);
    const state = JSON.parse(await readFile(path.join(folder, 'browser-state.json'), 'utf8'));
    return { spec, state };
  }));
  for (const [index, own] of profiles.entries()) {
    const other = profiles[1 - index];
    const name = 'hub_' + own.spec.instance_id.replaceAll('-', '') + '_session';
    const foreignName = 'hub_' + other.spec.instance_id.replaceAll('-', '') + '_session';
    const session = own.state.cookies.find((c: { name: string }) => c.name === name);
    expect(session).toBeTruthy();
    const foreign = await browser.newContext();
    const context = await browser.newContext({ storageState: own.state });
    try {
      await foreign.addCookies([{ ...session, name: foreignName, domain: new URL(other.spec.origin).hostname }]);
      expect((await foreign.request.get(other.spec.origin + '/api/v1/journal/entries')).status()).toBe(401);
      const csrf = await (await context.request.get(own.spec.origin + '/api/v1/auth/csrf')).json();
      const response = await context.request.post(own.spec.origin + '/api/v1/tasks', {
        headers: { Origin: other.spec.origin, 'X-CSRF-Token': csrf.csrf_token }, data: { title: 'Synthetic forbidden cross-origin' },
      });
      expect(response.status()).toBe(403);
      expect((await context.request.get(own.spec.origin + '/api/v1/tasks')).status()).toBe(200);
    } finally { await foreign.close(); await context.close(); }
  }
});
