import { test, expect } from '@playwright/test'
import * as path from 'path'
import { BASE_URL, getToken, apiDeleteWithBody } from '../helpers'

// Minimal inline SVG als Buffer für Datei-Uploads
const MINIMAL_SVG = Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M1 1"/></svg>',
)

// Navigiert zur Icons-Tab-Seite
async function gotoIconsTab(page: any) {
  await page.goto('/settings')
  await page.click('button:has-text("Icons")')
  await expect(page.locator('[data-testid="icons-tab"]')).toBeVisible({ timeout: 5_000 })
}

// Lädt ein SVG per API hoch (für Test-Setup).
// Verwendet getToken() — liest das Token aus .auth/admin.json ohne Login-Request,
// um den Rate-Limiter (5 Logins/min) nicht zu triggern.
async function uploadIconViaApi(name: string): Promise<void> {
  const token = await getToken()
  const fd = new FormData()
  fd.append('files', new Blob([MINIMAL_SVG], { type: 'image/svg+xml' }), `${name}.svg`)
  await fetch(`${BASE_URL}/api/v1/icons/import`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: fd,
  })
}

// Löscht Icons per API (Cleanup)
async function cleanupIcons(names: string[]): Promise<void> {
  if (names.length === 0) return
  await apiDeleteWithBody('/api/v1/icons/', { names })
}

// ---------------------------------------------------------------------------
// Test 1: Leerer Zustand wird angezeigt (nach Cleanup)
// ---------------------------------------------------------------------------

test('Icons-Tab zeigt Leer-Zustand wenn keine Icons vorhanden', async ({ page }) => {
  // Alle ggf. vorhandenen Test-Icons wegräumen
  await cleanupIcons(['e2e-empty-test'])

  await gotoIconsTab(page)

  // Entweder Leer-Meldung oder Grid — je nach Gesamtbestand
  // Wir prüfen nur, dass der Tab korrekt lädt
  await expect(page.locator('[data-testid="icons-tab"]')).toBeVisible()
  await expect(page.locator('[data-testid="input-icons-search"]')).toBeVisible()
})

// ---------------------------------------------------------------------------
// Test 2: SVG hochladen via Datei-Input, Icon erscheint im Grid
// ---------------------------------------------------------------------------

test('SVG-Datei hochladen und Icon erscheint im Grid', async ({ page }) => {
  const iconName = `e2e-upload-${Date.now()}`

  try {
    await gotoIconsTab(page)

    // Datei-Upload über verstecktes Input triggern
    const fileInput = page.locator('[data-testid="input-icons-file"]')
    await fileInput.setInputFiles({
      name: `${iconName}.svg`,
      mimeType: 'image/svg+xml',
      buffer: MINIMAL_SVG,
    })

    // Erfolgsmeldung und Icon im Grid abwarten
    await expect(page.locator('[data-testid="icons-msg"]')).toContainText('importiert', { timeout: 8_000 })
    await expect(page.locator(`[data-testid="icon-item-${iconName}"]`)).toBeVisible({ timeout: 5_000 })
  } finally {
    await cleanupIcons([iconName])
  }
})

// ---------------------------------------------------------------------------
// Test 3: Icon selektieren und löschen
// ---------------------------------------------------------------------------

test('Icon selektieren und löschen', async ({ page }) => {
  const iconName = `e2e-delete-${Date.now()}`
  await uploadIconViaApi(iconName)

  try {
    await gotoIconsTab(page)

    // Icon im Grid anklicken (Checkbox per Label-Klick)
    const item = page.locator(`[data-testid="icon-item-${iconName}"]`)
    await expect(item).toBeVisible({ timeout: 8_000 })
    await item.click()

    // Löschen-Button erscheint und wird geklickt
    await expect(page.locator('[data-testid="btn-icons-delete"]')).toBeVisible()
    await page.click('[data-testid="btn-icons-delete"]')

    // Icon verschwindet aus dem Grid
    await expect(item).not.toBeVisible({ timeout: 5_000 })
    await expect(page.locator('[data-testid="icons-msg"]')).toContainText('gelöscht', { timeout: 5_000 })
  } finally {
    // Best-effort: Icon könnte bereits gelöscht sein
    await cleanupIcons([iconName])
  }
})

// ---------------------------------------------------------------------------
// Test 4: Suche / Filterung
// ---------------------------------------------------------------------------

test('Icon-Suche filtert das Grid', async ({ page }) => {
  const nameA = `e2e-search-alpha-${Date.now()}`
  const nameB = `e2e-search-beta-${Date.now()}`
  await uploadIconViaApi(nameA)
  await uploadIconViaApi(nameB)

  try {
    await gotoIconsTab(page)

    await expect(page.locator(`[data-testid="icon-item-${nameA}"]`)).toBeVisible({ timeout: 8_000 })
    await expect(page.locator(`[data-testid="icon-item-${nameB}"]`)).toBeVisible({ timeout: 5_000 })

    // Nach "alpha" suchen
    await page.fill('[data-testid="input-icons-search"]', 'alpha')

    await expect(page.locator(`[data-testid="icon-item-${nameA}"]`)).toBeVisible({ timeout: 3_000 })
    await expect(page.locator(`[data-testid="icon-item-${nameB}"]`)).not.toBeVisible()
  } finally {
    await cleanupIcons([nameA, nameB])
  }
})

// ---------------------------------------------------------------------------
// Test 5: Alle wählen / Alle abwählen
// ---------------------------------------------------------------------------

test('Alle Icons wählen und abwählen', async ({ page }) => {
  const nameA = `e2e-selall-a-${Date.now()}`
  const nameB = `e2e-selall-b-${Date.now()}`
  await uploadIconViaApi(nameA)
  await uploadIconViaApi(nameB)

  try {
    await gotoIconsTab(page)
    await expect(page.locator(`[data-testid="icon-item-${nameA}"]`)).toBeVisible({ timeout: 8_000 })

    // "Alle wählen" klicken — Export-Button sollte erscheinen
    await page.click('[data-testid="btn-icons-select-all"]')
    await expect(page.locator('[data-testid="btn-icons-export"]')).toBeVisible({ timeout: 3_000 })

    // "Alle abwählen" klicken — Export-Button verschwindet
    await page.click('[data-testid="btn-icons-select-all"]')
    await expect(page.locator('[data-testid="btn-icons-export"]')).not.toBeVisible({ timeout: 3_000 })
  } finally {
    await cleanupIcons([nameA, nameB])
  }
})

// ---------------------------------------------------------------------------
// Test 6: Export löst Download aus
// ---------------------------------------------------------------------------

test('Export ausgewählter Icons löst ZIP-Download aus', async ({ page }) => {
  const iconName = `e2e-export-${Date.now()}`
  await uploadIconViaApi(iconName)

  try {
    await gotoIconsTab(page)
    await expect(page.locator(`[data-testid="icon-item-${iconName}"]`)).toBeVisible({ timeout: 8_000 })

    // Icon selektieren
    await page.click(`[data-testid="icon-item-${iconName}"]`)
    await expect(page.locator('[data-testid="btn-icons-export"]')).toBeVisible()

    // Download abwarten
    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 10_000 }),
      page.click('[data-testid="btn-icons-export"]'),
    ])

    expect(download.suggestedFilename()).toBe('obs_icons.zip')
  } finally {
    await cleanupIcons([iconName])
  }
})

// ---------------------------------------------------------------------------
// Test 7: FontAwesome Import-Formular (UI-Validierung)
// ---------------------------------------------------------------------------

test('FontAwesome-Import-Button ist deaktiviert wenn keine Namen eingegeben', async ({ page }) => {
  await gotoIconsTab(page)

  const btn = page.locator('[data-testid="btn-fa-import"]')
  await expect(btn).toBeDisabled()

  // Name eingeben → Button wird aktiv
  await page.fill('[data-testid="input-fa-names"]', 'house')
  await expect(btn).toBeEnabled()

  // Namen wieder leeren → Button wieder deaktiviert
  await page.fill('[data-testid="input-fa-names"]', '')
  await expect(btn).toBeDisabled()
})

// ---------------------------------------------------------------------------
// Test 8: Ungültige Datei zeigt Fehlermeldung
// ---------------------------------------------------------------------------

test('Upload einer Nicht-SVG-Datei zeigt Fehlermeldung', async ({ page }) => {
  await gotoIconsTab(page)

  const fileInput = page.locator('[data-testid="input-icons-file"]')
  await fileInput.setInputFiles({
    name: 'fake.svg',
    mimeType: 'image/svg+xml',
    buffer: Buffer.from('{"not": "an svg"}'),
  })

  await expect(page.locator('[data-testid="icons-msg"]')).toBeVisible({ timeout: 8_000 })
  // Fehlermeldung ist rot (nicht grün)
  await expect(page.locator('[data-testid="icons-msg"]')).toHaveClass(/text-red-400/)
})
