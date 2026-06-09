import { test, expect } from '@playwright/test'

test('KNX-Projektimport sendet Hierarchieoptionen und zeigt Ergebnisdetails', async ({ page }) => {
  let importUrl: URL | null = null

  await page.route('**/api/v1/adapters/instances', async route => {
    await route.fulfill({
      json: [
        { id: 'adapter-1', adapter_type: 'KNX', name: 'KNX Main', enabled: true },
      ],
    })
  })

  await page.route('**/api/v1/knxproj/group-addresses**', async route => {
    await route.fulfill({ json: { total: 0, items: [] } })
  })

  await page.route('**/api/v1/knxproj/import**', async route => {
    importUrl = new URL(route.request().url())
    await route.fulfill({
      json: {
        imported: 12,
        created: 2,
        updated: 1,
        locations: 3,
        trades: 1,
        message: 'ok',
        hierarchies: [
          {
            mode: 'groups',
            status: 'created',
            tree_id: 'tree-groups',
            tree_name: 'ETS Gruppenadressen',
            nodes_created: 8,
            links_created: 2,
            message: 'created',
          },
          {
            mode: 'buildings',
            status: 'failed',
            tree_name: 'ETS Gebäude und Räume',
            nodes_created: 0,
            links_created: 0,
            message: 'Keine Gebäude-Daten aus dieser .knxproj importiert.',
          },
        ],
      },
    })
  })

  await page.goto('/settings')
  await page.getByRole('button', { name: 'Datenmanagement' }).click()
  await expect(page.getByRole('heading', { name: 'KNX Projekt importieren' })).toBeVisible()

  const topology = page.getByLabel('Topologie')
  const buildings = page.getByLabel('Gebäude / Räume')
  const trades = page.getByLabel('Gewerke')
  await expect(topology).toBeChecked()
  await expect(buildings).toBeChecked()
  await expect(trades).toBeChecked()

  await expect(page.getByLabel('Angelegte Objekte automatisch mit Hierarchieknoten verknüpfen')).toBeDisabled()
  await page.getByLabel('Objekte anlegen / aktualisieren').check()
  await expect(page.getByLabel('Angelegte Objekte automatisch mit Hierarchieknoten verknüpfen')).toBeEnabled()

  await trades.uncheck()
  await page.locator('input[accept=".knxproj"]').setInputFiles({
    name: 'demo.knxproj',
    mimeType: 'application/octet-stream',
    buffer: Buffer.from('fake knx project'),
  })

  await page.getByRole('button', { name: 'Importieren' }).click()

  await expect(page.getByText('12 Gruppenadressen importiert')).toBeVisible()
  await expect(page.getByText('Topologie: angelegt')).toBeVisible()
  await expect(page.getByText('8 Knoten, 2 Verknüpfungen')).toBeVisible()
  await expect(page.getByText('Gebäude / Räume: nicht angelegt')).toBeVisible()
  await expect(page.getByText('Keine Gebäude-Daten aus dieser .knxproj importiert.')).toBeVisible()

  expect(importUrl).not.toBeNull()
  expect(importUrl?.searchParams.get('adapter_name')).toBe('KNX Main')
  expect(importUrl?.searchParams.get('direction')).toBe('SOURCE')
  expect(importUrl?.searchParams.get('hierarchy_modes')).toBe('groups,buildings')
  expect(importUrl?.searchParams.get('hierarchy_auto_link')).toBe('true')
})
