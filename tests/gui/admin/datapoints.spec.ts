import { test, expect } from '@playwright/test'
import { apiGet, apiPost, apiDelete } from '../helpers'

// ---------------------------------------------------------------------------
// Test 1: DataPoint anlegen via GUI-Flow und in der Liste sehen
// ---------------------------------------------------------------------------

test('DataPoint anlegen und in Liste sehen', async ({ page }) => {
  const name = `E2E-Temp-${Date.now()}`
  let dpId: string | null = null

  try {
    await page.goto('/gui/')
    await page.click('[data-testid="nav-datapoints"]')
    await expect(page).toHaveURL(/\/datapoints/)

    await page.click('[data-testid="btn-new-datapoint"]')

    // Fill the form
    await page.fill('[data-testid="input-name"]', name)
    await page.selectOption('[data-testid="select-datatype"]', 'FLOAT')

    // Synchronize on the mutation itself. Under concurrent E2E load the API can
    // legitimately take longer than the old fixed 5 s modal timeout, while the
    // disabled save button correctly indicates that the request is still pending.
    const createResponsePromise = page.waitForResponse(response =>
      response.request().method() === 'POST'
      && new URL(response.url()).pathname === '/api/v1/datapoints/',
    )
    await page.click('[data-testid="btn-save"]')
    const createResponse = await createResponsePromise
    expect(createResponse.ok(), `POST /api/v1/datapoints/ returned ${createResponse.status()}`).toBeTruthy()
    dpId = ((await createResponse.json()) as { id: string }).id

    // The response has completed; only Vue's local state update remains.
    await expect(page.locator('[data-testid="btn-save"]')).not.toBeVisible()

    // Search for the new DP — the list is alphabetically paginated so it won't
    // appear on page 1 without filtering
    await page.fill('[data-testid="input-search"]', name)
    await page.waitForTimeout(500)

    // The new row must appear in the filtered table
    await expect(page.locator('[data-testid="datapoint-list"]')).toContainText(name, { timeout: 5_000 })
  } finally {
    // Response-derived cleanup also covers failures after the mutation completed.
    if (dpId) await apiDelete(`/api/v1/datapoints/${dpId}`)
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
    // Quality filter is a row of toggle buttons, not a <select>
    await page.click('[data-testid="btn-quality-good"]')
    await page.waitForTimeout(500)
    await expect(page.locator(`[data-testid="dp-row-${dpId}"]`)).toBeVisible({ timeout: 5_000 })

    // Switch to bad — the DP must disappear
    await page.click('[data-testid="btn-quality-good"]') // deactivate good first
    await page.click('[data-testid="btn-quality-bad"]')
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

    // Tag filter is a custom multi-select dropdown — open it and verify the tag
    // appears, then click it to select
    await page.click('[data-testid="tag-filter"]')
    await expect(
      page.locator('[data-testid="tag-filter"]').getByText(uniqueTag, { exact: true })
    ).toBeVisible({ timeout: 5_000 })

    await page.locator('[data-testid="tag-filter"]').getByText(uniqueTag, { exact: true }).click()
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
    const initialSearchPromise = page.waitForResponse(response => {
      const url = new URL(response.url())
      return url.pathname === '/api/v1/search/'
        && url.searchParams.get('q') === prefix
        && url.searchParams.get('page') === '0'
        && response.ok()
    })
    await page.fill('[data-testid="input-search"]', prefix)
    await initialSearchPromise

    // Initial load: at most 50 rows
    const initialCount = await page.locator('[data-testid^="dp-row-"]').count()
    expect(initialCount).toBeLessThanOrEqual(50)
    expect(initialCount).toBeGreaterThan(0)

    // Bring the observed sentinel into view and synchronize on page 1 instead
    // of assuming a fixed delay is enough for IntersectionObserver + network.
    const nextPagePromise = page.waitForResponse(response => {
      const url = new URL(response.url())
      return url.pathname === '/api/v1/search/'
        && url.searchParams.get('q') === prefix
        && url.searchParams.get('page') === '1'
        && response.ok()
    })
    await page.locator('[data-testid="datapoint-list"] + div').scrollIntoViewIfNeeded()
    await nextPagePromise

    // Now more rows should be visible
    await expect.poll(() => page.locator('[data-testid^="dp-row-"]').count()).toBeGreaterThan(initialCount)
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

// ---------------------------------------------------------------------------
// Test 8: Neue Einheiten (°, mm/h, nSv/h, mSv/h) sind in der Auswahl
// ---------------------------------------------------------------------------

test('Neue Einheiten sind im Einheiten-Dropdown vorhanden', async ({ page }) => {
  await page.goto('/gui/')
  await page.click('[data-testid="nav-datapoints"]')
  await expect(page).toHaveURL(/\/datapoints/)
  await page.click('[data-testid="btn-new-datapoint"]')

  const select = page.locator('[data-testid="select-unit"]')
  await expect(select).toBeVisible({ timeout: 5_000 })

  for (const unit of ['°', 'mm/h', 'nSv/h', 'mSv/h']) {
    await expect(select.locator(`option[value="${unit}"]`)).toHaveCount(1, { timeout: 3_000 })
  }
})

// ---------------------------------------------------------------------------
// Test 9: DataPoint mit Einheit ° anlegen und in der Liste sehen
// ---------------------------------------------------------------------------

test('DataPoint mit Einheit ° anlegen', async ({ page }) => {
  const name = `E2E-Winkel-${Date.now()}`
  let dpId: string | null = null

  try {
    await page.goto('/gui/')
    await page.click('[data-testid="nav-datapoints"]')
    await expect(page).toHaveURL(/\/datapoints/)

    await page.click('[data-testid="btn-new-datapoint"]')
    await page.fill('[data-testid="input-name"]', name)
    await page.selectOption('[data-testid="select-unit"]', '°')

    const createResponsePromise = page.waitForResponse(response =>
      response.request().method() === 'POST'
      && new URL(response.url()).pathname === '/api/v1/datapoints/',
    )
    await page.click('[data-testid="btn-save"]')
    const createResponse = await createResponsePromise
    expect(createResponse.ok(), `POST /api/v1/datapoints/ returned ${createResponse.status()}`).toBeTruthy()
    dpId = ((await createResponse.json()) as { id: string }).id

    await expect(page.locator('[data-testid="btn-save"]')).not.toBeVisible()

    const searchResponsePromise = page.waitForResponse(response => {
      const url = new URL(response.url())
      return url.pathname === '/api/v1/search/'
        && url.searchParams.get('q') === name
        && response.ok()
    })
    await page.fill('[data-testid="input-search"]', name)
    await searchResponsePromise

    await expect(page.locator('[data-testid="datapoint-list"]')).toContainText(name, { timeout: 5_000 })
  } finally {
    if (dpId) await apiDelete(`/api/v1/datapoints/${dpId}`)
  }
})

// ---------------------------------------------------------------------------
// Test 10: Einheit im Bearbeiten-Dialog auf "keine" zurücksetzen
// ---------------------------------------------------------------------------

test('DataPoint-Einheit kann über den Bearbeiten-Dialog geleert werden', async ({ page }) => {
  const name = `E2E-UnitReset-${Date.now()}`
  const created = await apiPost('/api/v1/datapoints', {
    name,
    data_type: 'FLOAT',
    unit: '°C',
    tags: [],
  }) as { id: string }
  const dpId = created.id

  try {
    await page.goto('/datapoints')
    await page.waitForSelector('[data-testid="input-search"]', { timeout: 10_000 })
    await page.fill('[data-testid="input-search"]', name)
    await page.waitForTimeout(500)
    await expect(page.locator(`[data-testid="dp-row-${dpId}"]`)).toBeVisible({ timeout: 5_000 })

    const row = page.locator(`[data-testid="dp-row-${dpId}"]`)
    await row.locator('[title="Bearbeiten"]').click()
    await expect(page.locator('[data-testid="select-unit"]')).toHaveValue('°C', { timeout: 5_000 })
    await page.selectOption('[data-testid="select-unit"]', '')
    await page.click('[data-testid="btn-save"]')
    await expect(page.locator('[data-testid="btn-save"]')).not.toBeVisible({ timeout: 5_000 })

    const updated = await apiGet(`/api/v1/datapoints/${dpId}`) as { unit: string | null }
    expect(updated.unit).toBeNull()
  } finally {
    await apiDelete(`/api/v1/datapoints/${dpId}`)
  }
})
