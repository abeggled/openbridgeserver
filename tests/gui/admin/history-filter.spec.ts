import { test, expect } from '@playwright/test'
import { apiGet, apiPost, apiDelete } from '../helpers'

// ---------------------------------------------------------------------------
// Helper: PATCH DataPoint via API
// ---------------------------------------------------------------------------

async function patchDp(id: string, data: object): Promise<void> {
  const { getToken } = await import('../helpers')
  const BASE_URL = process.env.BASE_URL ?? 'http://localhost:8080'
  const token = await getToken()
  const res = await fetch(`${BASE_URL}/api/v1/datapoints/${id}`, {
    method: 'PATCH',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error(`PATCH /datapoints/${id} failed: ${res.status}`)
}

// ---------------------------------------------------------------------------
// Test 1: DataPoint mit record_history=false anlegen, in Formular prüfen
// ---------------------------------------------------------------------------

test('DataPoint anlegen mit deaktivierter Historisierung', async ({ page }) => {
  const name = `E2E-NoHist-${Date.now()}`

  await page.goto('/gui/')
  await page.click('[data-testid="nav-datapoints"]')
  await expect(page).toHaveURL(/\/datapoints/)

  await page.click('[data-testid="btn-new-datapoint"]')
  await page.fill('[data-testid="input-name"]', name)
  await page.selectOption('[data-testid="select-datatype"]', 'FLOAT')

  // Historisierung deaktivieren
  const histCheckbox = page.locator('[data-testid="checkbox-record-history"]')
  await expect(histCheckbox).toBeChecked() // Standard: aktiv
  await histCheckbox.uncheck()
  await expect(histCheckbox).not.toBeChecked()

  await page.click('[data-testid="btn-save"]')
  await expect(page.locator('[data-testid="datapoint-list"]')).toContainText(name, { timeout: 5_000 })

  // DataPoint suchen und ID ermitteln
  await page.fill('[data-testid="input-search"]', name)
  await page.waitForTimeout(500)
  const rows = await page.locator('[data-testid^="dp-row-"]').all()
  let dpId = ''
  for (const row of rows) {
    const text = await row.textContent()
    if (text?.includes(name)) {
      dpId = (await row.getAttribute('data-testid') ?? '').replace('dp-row-', '')
      break
    }
  }

  try {
    // Detail-Ansicht öffnen und Badge prüfen
    if (dpId) {
      await page.goto(`/datapoints/${dpId}`)
      await expect(page.locator('[data-testid="badge-record-history"]')).toContainText('Deaktiviert', { timeout: 5_000 })
    }
  } finally {
    if (dpId) await apiDelete(`/api/v1/datapoints/${dpId}`)
  }
})

// ---------------------------------------------------------------------------
// Test 2: record_history per Bearbeiten-Dialog umschalten
// ---------------------------------------------------------------------------

test('Historisierung per Bearbeiten-Dialog aktivieren/deaktivieren', async ({ page }) => {
  const name = `E2E-ToggleHist-${Date.now()}`
  const created = await apiPost('/api/v1/datapoints', {
    name,
    data_type: 'FLOAT',
    tags: [],
    record_history: true,
  }) as { id: string }
  const dpId = created.id

  try {
    await page.goto(`/datapoints/${dpId}`)
    await expect(page.locator('[data-testid="badge-record-history"]')).toContainText('Aktiv', { timeout: 5_000 })

    // Bearbeiten öffnen und Historisierung deaktivieren
    await page.click('button:has-text("Bearbeiten")')
    const histCheckbox = page.locator('[data-testid="checkbox-record-history"]')
    await expect(histCheckbox).toBeChecked()
    await histCheckbox.uncheck()
    await page.click('[data-testid="btn-save"]')

    // Badge muss auf "Deaktiviert" wechseln
    await expect(page.locator('[data-testid="badge-record-history"]')).toContainText('Deaktiviert', { timeout: 5_000 })

    // Wieder aktivieren
    await page.click('button:has-text("Bearbeiten")')
    await page.locator('[data-testid="checkbox-record-history"]').check()
    await page.click('[data-testid="btn-save"]')
    await expect(page.locator('[data-testid="badge-record-history"]')).toContainText('Aktiv', { timeout: 5_000 })
  } finally {
    await apiDelete(`/api/v1/datapoints/${dpId}`)
  }
})

// ---------------------------------------------------------------------------
// Helper: History-Tab öffnen und warten bis DataPoints geladen sind
// ---------------------------------------------------------------------------

async function openHistoryFilterTab(page: import('@playwright/test').Page) {
  await page.goto('/settings')
  await page.click('button:has-text("Historie DB")')
  await expect(page.locator('[data-testid="history-filter-card"]')).toBeVisible({ timeout: 8_000 })
  // Warten bis Lade-Spinner verschwunden und Liste sichtbar ist
  await expect(page.locator('[data-testid="history-filter-loading"]')).not.toBeVisible({ timeout: 10_000 })
  await expect(page.locator('[data-testid="history-filter-list"]')).toBeVisible({ timeout: 5_000 })
}

// ---------------------------------------------------------------------------
// Test 3: Objekt-Filter in den Einstellungen — Toggle-Schalter
// ---------------------------------------------------------------------------

test('Objekt-Filter in Einstellungen — Toggle ändert Historisierung', async ({ page }) => {
  const name = `E2E-FilterToggle-${Date.now()}`
  const created = await apiPost('/api/v1/datapoints', {
    name,
    data_type: 'FLOAT',
    tags: [],
    record_history: true,
  }) as { id: string }
  const dpId = created.id

  try {
    await openHistoryFilterTab(page)

    // Objekt suchen
    await page.fill('[data-testid="input-history-filter-search"]', name)

    const toggle = page.locator(`[data-testid="toggle-history-${dpId}"]`)
    await expect(toggle).toBeVisible({ timeout: 8_000 })

    // Ausgangszustand: aktiv (grün)
    await expect(toggle).toHaveClass(/bg-green-500/)

    // Deaktivieren
    await toggle.click()
    await expect(toggle).not.toHaveClass(/bg-green-500/, { timeout: 5_000 })

    // Wieder aktivieren
    await toggle.click()
    await expect(toggle).toHaveClass(/bg-green-500/, { timeout: 5_000 })
  } finally {
    await apiDelete(`/api/v1/datapoints/${dpId}`)
  }
})

// ---------------------------------------------------------------------------
// Test 4: Objekt-Filter — Suche filtert DataPoints
// ---------------------------------------------------------------------------

test('Objekt-Filter Suche filtert Objekte korrekt', async ({ page }) => {
  const nameA = `E2E-FilterA-${Date.now()}`
  const nameB = `E2E-FilterB-${Date.now()}`
  const [dpA, dpB] = await Promise.all([
    apiPost('/api/v1/datapoints', { name: nameA, data_type: 'FLOAT', tags: [] }) as Promise<{ id: string }>,
    apiPost('/api/v1/datapoints', { name: nameB, data_type: 'FLOAT', tags: [] }) as Promise<{ id: string }>,
  ])

  try {
    await openHistoryFilterTab(page)

    // Suche nach nameA — nur A sichtbar
    await page.fill('[data-testid="input-history-filter-search"]', nameA)
    await expect(page.locator(`[data-testid="toggle-history-${dpA.id}"]`)).toBeVisible({ timeout: 8_000 })
    await expect(page.locator(`[data-testid="toggle-history-${dpB.id}"]`)).not.toBeVisible()

    // Suche leeren — beide sichtbar
    await page.fill('[data-testid="input-history-filter-search"]', '')
    await expect(page.locator(`[data-testid="toggle-history-${dpA.id}"]`)).toBeVisible({ timeout: 5_000 })
    await expect(page.locator(`[data-testid="toggle-history-${dpB.id}"]`)).toBeVisible({ timeout: 5_000 })
  } finally {
    await Promise.all([
      apiDelete(`/api/v1/datapoints/${dpA.id}`),
      apiDelete(`/api/v1/datapoints/${dpB.id}`),
    ])
  }
})

// ---------------------------------------------------------------------------
// Test 5: Objekt-Filter — Zähler stimmt mit tatsächlicher Gesamtzahl überein
// (Regression für Bug #212: nur 500 Objekte wurden angezeigt)
// ---------------------------------------------------------------------------

test('Objekt-Filter zeigt alle Objekte — Zähler entspricht API-Gesamtzahl', async ({ page }) => {
  const BASE_URL = process.env.BASE_URL ?? 'http://localhost:8080'

  // Gesamtzahl vor dem Test ermitteln
  const before = await apiGet('/api/v1/datapoints/?page=0&size=1') as { total: number }
  const totalBefore = before.total

  // 3 neue Objekte anlegen, damit mindestens diese im Filter erscheinen müssen
  const names = Array.from({ length: 3 }, (_, i) => `E2E-CountCheck-${Date.now()}-${i}`)
  const created = await Promise.all(
    names.map(name => apiPost('/api/v1/datapoints', { name, data_type: 'FLOAT', tags: [] }) as Promise<{ id: string }>)
  )
  const expectedTotal = totalBefore + 3

  try {
    // Brief pause so all 3 API writes are fully committed before page load
    await page.waitForTimeout(500)

    await openHistoryFilterTab(page)

    // Reload to guarantee a fresh fetch — the page might have loaded before the new DPs were indexed
    await page.reload()
    await page.click('button:has-text("Historie DB")')
    await expect(page.locator('[data-testid="history-filter-card"]')).toBeVisible({ timeout: 8_000 })
    await expect(page.locator('[data-testid="history-filter-loading"]')).not.toBeVisible({ timeout: 10_000 })

    // Der Zähler muss Y >= expectedTotal zeigen — parallel laufende Tests können weitere DPs anlegen,
    // daher ist nur eine Untergrenze korrekt (nicht exakter Vergleich).
    const counterText = page.locator('[data-testid="history-filter-card"] .card-header span')
    await expect.poll(async () => {
      const text = await counterText.textContent() ?? ''
      const m = text.match(/von (\d+) Objekt/)
      return m ? parseInt(m[1]) : 0
    }, { timeout: 8_000 }).toBeGreaterThanOrEqual(expectedTotal)

    // Alle 3 neu erstellten Objekte müssen einzeln auffindbar sein
    for (const dp of created) {
      await page.fill('[data-testid="input-history-filter-search"]', dp.id)
      await expect(page.locator(`[data-testid="toggle-history-${dp.id}"]`)).toBeVisible({ timeout: 8_000 })
    }
  } finally {
    await Promise.all(created.map(dp => apiDelete(`/api/v1/datapoints/${dp.id}`)))
  }
})
