import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// Run only against the dedicated empty bootstrap stack, never the user's installation.
test.skip(process.env.HUB_BOOTSTRAP_E2E !== '1', 'Requires the isolated first-start fixture stack');
test('Alta inicial: contraseña temporal, cambio obligatorio y WebAuthn virtual', async ({ page, context }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const cdp = await context.newCDPSession(page);
  await cdp.send('WebAuthn.enable');
  await cdp.send('WebAuthn.addVirtualAuthenticator', { options: {
    protocol: 'ctap2', transport: 'internal', hasResidentKey: true,
    hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true,
  } });
  await page.goto('/');
  await page.getByLabel('Usuario', { exact: true }).fill('admin');
  await page.getByLabel('Contraseña', { exact: true }).fill('Synthetic temporary credential 2026!');
  await page.getByRole('button', { name: 'Entrar', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Cambia la contraseña temporal' })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Cambia la contraseña temporal' })).toBeVisible();
  await page.getByLabel('Nueva contraseña', { exact: true }).fill('Synthetic permanent credential 2026!');
  await page.getByLabel('Repite la nueva contraseña', { exact: true }).fill('Synthetic mismatching credential 2026!');
  await page.getByRole('button', { name: 'Cambiar contraseña y continuar' }).click();
  await expect(page.getByRole('alert')).toContainText('no coinciden');
  await page.getByLabel('Repite la nueva contraseña', { exact: true }).fill('Synthetic permanent credential 2026!');
  await page.getByRole('button', { name: 'Cambiar contraseña y continuar' }).click();
  await expect(page.getByRole('heading', { name: 'Protege tu cuenta' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Registrar llave de seguridad', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Guarda tus códigos de recuperación' })).toBeVisible();
  await expect(page.locator('.recovery-codes code')).toHaveCount(10);
  await page.getByRole('button', { name: 'He guardado los códigos' }).click();
  await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Cerrar sesión', exact: true }).click();
  await page.getByLabel('Usuario', { exact: true }).fill('admin');
  await page.getByLabel('Contraseña', { exact: true }).fill('Synthetic temporary credential 2026!');
  await page.getByRole('button', { name: 'Entrar', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Bienvenido a tu Hub' })).toBeVisible();
  await page.getByLabel('Contraseña', { exact: true }).fill('Synthetic permanent credential 2026!');
  await page.getByRole('button', { name: 'Entrar', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Confirma que eres tú' })).toBeVisible();
  await page.getByRole('button', { name: 'Usar llave de seguridad', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Dashboard', exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});
