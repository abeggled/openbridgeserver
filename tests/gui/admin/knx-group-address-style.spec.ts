/**
 * #1296: a two-level KNX project shows and takes group addresses in its own
 * notation (`1/257`), while OBS stores the internal three-level text (`1/1/1`).
 *
 * The project is derived at test time from the demo project by
 * tests/knxproj_style_variants.py (the same derivation the backend tests use),
 * imported through the Settings page, and then checked in the GA catalog
 * (binding form suggestions), the binding form itself, the datapoint's KNX
 * context and the KNX device view.
 */
import { execFileSync } from 'child_process'
import * as path from 'path'
import { test, expect } from '@playwright/test'
import { apiDelete, apiGet, apiPost } from '../helpers'

const REPO = path.resolve(__dirname, '..', '..', '..')

function twoLevelProject(): Buffer {
  return execFileSync(
    path.join(REPO, 'tools', 'with-venv'),
    ['python', '-c', "import sys; from tests.knxproj_style_variants import knxproj_in_style; sys.stdout.buffer.write(knxproj_in_style('TwoLevel'))"],
    { cwd: REPO, maxBuffer: 64 * 1024 * 1024 },
  )
}

test('zweistufiges KNX-Projekt: Adressen erscheinen und werden angenommen in der Projektschreibweise', async ({ page }) => {
  test.setTimeout(120_000)
  const knx = await apiPost('/api/v1/adapters/instances', {
    name: `E2E-KNX-TwoLevel-${Date.now()}`,
    adapter_type: 'KNX',
    config: {},
    enabled: false,
  }) as { id: string }
  const dp = await apiPost('/api/v1/datapoints', { name: `E2E-DP-TwoLevel-${Date.now()}`, data_type: 'BOOLEAN', tags: [] }) as { id: string }

  try {
    // Import through the GUI
    await page.goto('/settings')
    await page.getByRole('button', { name: 'Datenmanagement' }).click()
    await page.locator('input[accept=".knxproj"]').setInputFiles({
      name: 'zweistufig.knxproj',
      mimeType: 'application/octet-stream',
      buffer: twoLevelProject(),
    })
    await page.locator('#knx-project-import').getByRole('button', { name: 'Importieren' }).click()
    await expect(page.getByText('500 Gruppenadressen importiert')).toBeVisible({ timeout: 60_000 })

    // GA catalog in the binding form: suggestions in two-level notation
    await page.goto(`/datapoints/${dp.id}`)
    await page.click('[data-testid="btn-add-binding"]')
    await page.selectOption('[data-testid="select-adapter-instance"]', knx.id)
    const gaInput = page.getByPlaceholder('z.B. 1/515 oder Name suchen …')
    await gaInput.fill('Licht EG Schalten')
    const suggestion = page.locator('li', { hasText: 'Licht EG Schalten' }).first()
    await expect(suggestion).toContainText('1/257')
    await expect(suggestion).not.toContainText('1/1/1')

    // An impossible address is explained in the project's notation, not as a validator dump
    await gaInput.fill('1/5000')
    await page.locator('form button[type="submit"]').click()
    await expect(page.getByText('„1/5000“ ist keine gültige Gruppenadresse. So sieht eine Adresse in diesem Projekt aus: 1/515')).toBeVisible()
    await expect(page.getByText('pydantic')).toHaveCount(0)

    // A two-level address typed by hand is taken and stored internally
    await gaInput.fill('1/258')
    await page.locator('form button[type="submit"]').click()
    await expect(page.locator('[data-testid="datapoint-knx-ga"]').first()).toHaveText('1/258')
    await expect(page.locator('[data-testid="datapoint-knx-context"]')).toContainText('Licht OG Schalten')
    const bindings = await apiGet(`/api/v1/datapoints/${dp.id}/bindings`) as Array<{ config: { group_address: string } }>
    expect(bindings.map(b => b.config.group_address)).toEqual(['1/1/2'])

    // Editing shows the stored address in the project notation again
    await page.locator('div.bg-surface-700', { has: page.locator('[data-testid="datapoint-knx-context"]') }).getByTitle('Bearbeiten').click()
    await expect(page.getByPlaceholder('z.B. 1/515 oder Name suchen …')).toHaveValue('1/258')

    // KNX device view: the device's linked addresses
    await page.goto('/knx-devices')
    await page.locator('[data-testid="knx-device-row-1.1.5"]').click()
    await expect(page.locator('[data-testid="knx-device-ga"]')).toHaveText(['1/257', '1/258'])
  } finally {
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
    await apiDelete(`/api/v1/adapters/instances/${knx.id}`)
  }
})
