import { test, expect } from '@playwright/test'
import { apiPost, apiDelete } from '../helpers'

// ---------------------------------------------------------------------------
// Test 1: DataPoint anlegen via GUI-Flow und in der Liste sehen
// ---------------------------------------------------------------------------

test('DataPoint anlegen und in Liste sehen', async ({ page }) => {
  const name = `E2E-Temp-${Date.now()}`

  await page.goto('/gui/')
  await page.click('[data-testid="nav-datapoints"]')
  await expect(page).toHaveURL(/\/datapoints/)

  await page.click('[data-testid="btn-new-datapoint"]')

  // Fill the form
  await page.fill('[data-testid="input-name"]', name)
  await page.selectOption('[data-testid="select-datatype"]', 'FLOAT')

  await page.click('[data-testid="btn-save"]')

  // The new row must appear in the table
  await expect(page.locator('[data-testid="datapoint-list"]')).toContainText(name, { timeout: 5_000 })

  // Cleanup: find the row and delete via API
  const rows = await page.locator('[data-testid^="dp-row-"]').all()
  for (const row of rows) {
    const text = await row.textContent()
    if (text?.includes(name)) {
      const testid = await row.getAttribute('data-testid') ?? ''
      const id = testid.replace('dp-row-', '')
      await apiDelete(`/api/v1/datapoints/${id}`)
      break
    }
  }
})

// ---------------------------------------------------------------------------
// Test 2: DataPoint via API anlegen, dann über GUI löschen
// ---------------------------------------------------------------------------

test('DataPoint löschen über ConfirmDialog', async ({ page }) => {
  const name = `E2E-Delete-${Date.now()}`

  // Create via API so we control the fixture
  const created = await apiPost('/api/v1/datapoints', {
    name,
    data_type: 'BOOLEAN',
    tags: [],
  }) as { id: string }
  const dpId = created.id

  try {
    await page.goto('/datapoints')
    // Search by name so the row appears regardless of total DP count
    await page.waitForSelector('[data-testid="input-search"]', { timeout: 10_000 })
    await page.fill('[data-testid="input-search"]', name)
    await page.waitForTimeout(500) // debounce is ~350 ms
    await expect(page.locator(`[data-testid="dp-row-${dpId}"]`)).toBeVisible({ timeout: 5_000 })

    // Click delete button inside the row
    const row = page.locator(`[data-testid="dp-row-${dpId}"]`)
    await row.locator('button.btn-icon.text-red-400').click()

    // Confirm dialog appears → click confirm
    await page.click('[data-testid="btn-confirm"]')

    // Row must disappear
    await expect(page.locator(`[data-testid="dp-row-${dpId}"]`)).not.toBeVisible({ timeout: 5_000 })
  } finally {
    // Best-effort cleanup in case delete via GUI failed
    await apiDelete(`/api/v1/datapoints/${dpId}`)
  }
})

// ---------------------------------------------------------------------------
// Test 3: Suche nach UUID findet DataPoint
// ---------------------------------------------------------------------------

test('Suche nach UUID findet DataPoint', async ({ page }) => {
  const name = `E2E-UUID-${Date.now()}`
  const created = await apiPost('/api/v1/datapoints', {
    name,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }
  const dpId = created.id

  try {
    await page.goto('/datapoints')
    await page.waitForSelector('[data-testid="input-search"]', { timeout: 10_000 })

    // Search by full UUID
    await page.fill('[data-testid="input-search"]', dpId)
    await page.waitForTimeout(500)
    await expect(page.locator(`[data-testid="dp-row-${dpId}"]`)).toBeVisible({ timeout: 5_000 })

    // Also works with the first 8 characters
    await page.fill('[data-testid="input-search"]', dpId.slice(0, 8))
    await page.waitForTimeout(500)
    await expect(page.locator(`[data-testid="dp-row-${dpId}"]`)).toBeVisible({ timeout: 5_000 })
  } finally {
    await apiDelete(`/api/v1/datapoints/${dpId}`)
  }
})

// ---------------------------------------------------------------------------
// Test 4: Quality-Filter zeigt nur passende Einträge
// ---------------------------------------------------------------------------

test('Quality-Filter good zeigt nur DataPoints mit Wert', async ({ page }) => {
  const name = `E2E-Qual-${Date.now()}`
  const created = await apiPost('/api/v1/datapoints', {
    name,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }
  const dpId = created.id

  try {
    // Write a value so the DP gets quality=good
    await apiPost(`/api/v1/datapoints/${dpId}/value`, { value: 21.5 })

    await page.goto('/datapoints')
    await page.waitForSelector('[data-testid="input-search"]', { timeout: 10_000 })

    // Narrow results to this DP by name so it's always on page 1, regardless
    // of how many other DPs exist in the environment.
    await page.fill('[data-testid="input-search"]', name)
    await page.waitForTimeout(500)
    await expect(page.locator(`[data-testid="dp-row-${dpId}"]`)).toBeVisible({ timeout: 5_000 })

    // Filter to good-quality only — our DP must stay visible
    await page.selectOption('[data-testid="select-quality"]', 'good')
    await page.waitForTimeout(500)
    await expect(page.locator(`[data-testid="dp-row-${dpId}"]`)).toBeVisible({ timeout: 5_000 })

    // Switch to bad — the DP must disappear
    await page.selectOption('[data-testid="select-quality"]', 'bad')
    await page.waitForTimeout(500)
    await expect(page.locator(`[data-testid="dp-row-${dpId}"]`)).not.toBeVisible({ timeout: 5_000 })
  } finally {
    await apiDelete(`/api/v1/datapoints/${dpId}`)
  }
})

// ---------------------------------------------------------------------------
// Test 5: Tag-Filter-Dropdown enthält Tags aus Ergebnismenge
// ---------------------------------------------------------------------------

test('Tag-Filter-Dropdown enthält Tags und filtert korrekt', async ({ page }) => {
  const uniqueTag = `zone-e2e-${Date.now()}`
  const nameTagged   = `E2E-Tag-Y-${Date.now()}`
  const nameUntagged = `E2E-Tag-N-${Date.now()}`

  const dpTagged = await apiPost('/api/v1/datapoints', {
    name: nameTagged,
    data_type: 'FLOAT',
    tags: [uniqueTag],
  }) as { id: string }
  const dpUntagged = await apiPost('/api/v1/datapoints', {
    name: nameUntagged,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }

  try {
    await page.goto('/datapoints')
    // Load both DPs into view by searching for the common prefix
    await page.waitForSelector('[data-testid="input-search"]', { timeout: 10_000 })
    await page.fill('[data-testid="input-search"]', 'E2E-Tag-')
    await page.waitForTimeout(500)

    // Verify the tag option appears in the dropdown
    const tagSelect = page.locator('[data-testid="select-tag"]')
    await expect(tagSelect.locator(`option[value="${uniqueTag}"]`)).toHaveCount(1, { timeout: 5_000 })

    // Select the tag
    await page.selectOption('[data-testid="select-tag"]', uniqueTag)
    await page.waitForTimeout(500)

    // Tagged DP visible, untagged DP hidden
    await expect(page.locator(`[data-testid="dp-row-${dpTagged.id}"]`)).toBeVisible({ timeout: 5_000 })
    await expect(page.locator(`[data-testid="dp-row-${dpUntagged.id}"]`)).not.toBeVisible({ timeout: 5_000 })
  } finally {
    await apiDelete(`/api/v1/datapoints/${dpTagged.id}`)
    await apiDelete(`/api/v1/datapoints/${dpUntagged.id}`)
  }
})

// ---------------------------------------------------------------------------
// Test 6: Infinite Scroll lädt weitere Einträge nach
// ---------------------------------------------------------------------------

test('Infinite Scroll lädt weitere Einträge nach', async ({ page }) => {
  const prefix = `E2E-Scroll-${Date.now()}`
  const created: string[] = []

  // Create 55 DPs (> default page size of 50) via API
  for (let i = 0; i < 55; i++) {
    const dp = await apiPost('/api/v1/datapoints', {
      name: `${prefix}-${String(i).padStart(3, '0')}`,
      data_type: 'FLOAT',
      tags: [],
    }) as { id: string }
    created.push(dp.id)
  }

  try {
    await page.goto('/datapoints')
    await page.waitForSelector('[data-testid="input-search"]', { timeout: 15_000 })

    // Filter to just our fixtures
    await page.fill('[data-testid="input-search"]', prefix)
    await page.waitForTimeout(600)

    // Initial load: at most 50 rows
    const initialCount = await page.locator('[data-testid^="dp-row-"]').count()
    expect(initialCount).toBeLessThanOrEqual(50)
    expect(initialCount).toBeGreaterThan(0)

    // The app layout scrolls <main>, not window. Scroll it to the bottom.
    await page.evaluate(() => {
      const main = document.querySelector('main')
      if (main) main.scrollTop = main.scrollHeight
    })
    await page.waitForTimeout(1_500)   // wait for IntersectionObserver + network

    // Now more rows should be visible
    const afterScrollCount = await page.locator('[data-testid^="dp-row-"]').count()
    expect(afterScrollCount).toBeGreaterThan(initialCount)
  } finally {
    for (const id of created) {
      await apiDelete(`/api/v1/datapoints/${id}`)
    }
  }
})

// ---------------------------------------------------------------------------
// Test 7: Zurück vom Detail stellt Scroll-Position und Filter wieder her
// ---------------------------------------------------------------------------

test('Zurück-Navigation stellt Filter wieder her', async ({ page }) => {
  const name = `E2E-Back-${Date.now()}`
  const created = await apiPost('/api/v1/datapoints', {
    name,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }
  const dpId = created.id

  try {
    await page.goto('/datapoints')
    await page.waitForSelector('[data-testid="input-search"]', { timeout: 10_000 })

    // Apply a filter
    await page.fill('[data-testid="input-search"]', name)
    await page.waitForTimeout(500)
    await expect(page.locator(`[data-testid="dp-row-${dpId}"]`)).toBeVisible({ timeout: 5_000 })

    // Navigate to the detail view by clicking the row name link
    await page.locator(`[data-testid="dp-row-${dpId}"] a`).first().click()
    await expect(page).toHaveURL(/\/datapoints\/.+/, { timeout: 5_000 })

    // Navigate back
    await page.goBack()
    await expect(page).toHaveURL(/\/datapoints$/, { timeout: 5_000 })

    // The search filter must be restored
    await expect(page.locator('[data-testid="input-search"]')).toHaveValue(name, { timeout: 5_000 })

    // The row must still be visible without the user having to re-type
    await expect(page.locator(`[data-testid="dp-row-${dpId}"]`)).toBeVisible({ timeout: 5_000 })
  } finally {
    await apiDelete(`/api/v1/datapoints/${dpId}`)
  }
})
