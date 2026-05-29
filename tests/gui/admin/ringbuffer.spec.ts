import { test, expect } from '@playwright/test'
import {
  apiPost,
  apiDelete,
  deleteAllFiltersets,
  gotoMonitor,
  gotoMonitorLive,
  waitForMonitorReady,
} from '../helpers'

// Filtersets are global state. Start every test from an empty, unfiltered
// feed so a leftover topbar-active set cannot gate out live pushes.
test.beforeEach(async () => {
  // Monitor tests wait for a real WebSocket ("Live" badge) plus live pushes —
  // the default 30s per-test budget is too tight under CI load.
  test.setTimeout(60_000)
  await deleteAllFiltersets()
})

test('RingBuffer Live-Eintrag ohne Reload', async ({ page }) => {
  // Fixture: create a DataPoint
  const created = await apiPost('/api/v1/datapoints', {
    name: `E2E-RB-${Date.now()}`,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }
  const dpId = created.id

  try {
    await gotoMonitorLive(page)

    // Before the push, no entries for this brand-new DP should exist
    const before = await page.locator(`[data-testid="ringbuffer-entry"][data-dp="${dpId}"]`).count()

    // Push a value via API — server broadcasts ringbuffer_entry via WS
    await apiPost(`/api/v1/datapoints/${dpId}/value`, { value: 42.0, quality: 'good' })

    // The WS push must add the new row within 15 s (CI environments can be slow)
    await expect(page.locator(`[data-testid="ringbuffer-entry"][data-dp="${dpId}"]`))
      .toHaveCount(before + 1, { timeout: 15_000 })
  } finally {
    await apiDelete(`/api/v1/datapoints/${dpId}`)
  }
})

test('RingBuffer Pause/Resume stoppt Live-Append und holt Queue nach', async ({ page }) => {
  const created = await apiPost('/api/v1/datapoints', {
    name: `E2E-RB-Pause-${Date.now()}`,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }
  const dpId = created.id

  try {
    await gotoMonitorLive(page)

    const rows = page.locator(`[data-testid="ringbuffer-entry"][data-dp="${dpId}"]`)
    const before = await rows.count()

    await page.click('[data-testid="btn-live-pause"]')
    await expect(page.locator('[data-testid="status-badge"]')).toContainText('Pausiert')

    await apiPost(`/api/v1/datapoints/${dpId}/value`, { value: 1.0, quality: 'good' })
    await apiPost(`/api/v1/datapoints/${dpId}/value`, { value: 2.0, quality: 'good' })
    await page.waitForTimeout(800)

    await expect(rows).toHaveCount(before)

    await page.click('[data-testid="btn-live-resume"]')
    await expect(page.locator('[data-testid="status-badge"]')).toContainText('Live')
    await expect(rows).toHaveCount(before + 2, { timeout: 12_000 })
  } finally {
    await apiDelete(`/api/v1/datapoints/${dpId}`)
  }
})

test('RingBuffer Auto-Scroll folgt Live, bleibt stabil bei Pause', async ({ page }) => {
  const created = await apiPost('/api/v1/datapoints', {
    name: `E2E-RB-Scroll-${Date.now()}`,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }
  const dpId = created.id

  try {
    await gotoMonitorLive(page)
    const rows = page.locator(`[data-testid="ringbuffer-entry"][data-dp="${dpId}"]`)
    const before = await rows.count()

    for (let i = 0; i < 45; i += 1) {
      await apiPost(`/api/v1/datapoints/${dpId}/value`, { value: i, quality: 'good' })
    }
    await expect(rows).toHaveCount(before + 45, { timeout: 15_000 })

    const wrap = page.locator('[data-testid="ringbuffer-table-wrap"]')
    await wrap.evaluate((el) => { (el as HTMLElement).scrollTop = 500 })
    const beforeLivePush = await wrap.evaluate((el) => (el as HTMLElement).scrollTop)
    expect(beforeLivePush).toBeGreaterThan(0)

    await apiPost(`/api/v1/datapoints/${dpId}/value`, { value: 999, quality: 'good' })
    await expect.poll(async () => wrap.evaluate((el) => (el as HTMLElement).scrollTop), { timeout: 8_000 }).toBe(0)

    await page.click('[data-testid="btn-live-pause"]')
    await wrap.evaluate((el) => { (el as HTMLElement).scrollTop = 500 })
    const beforePausedPush = await wrap.evaluate((el) => (el as HTMLElement).scrollTop)
    expect(beforePausedPush).toBeGreaterThan(0)

    await apiPost(`/api/v1/datapoints/${dpId}/value`, { value: 1000, quality: 'good' })
    await page.waitForTimeout(800)
    const afterPausedPush = await wrap.evaluate((el) => (el as HTMLElement).scrollTop)
    expect(afterPausedPush).toBeGreaterThan(0)
  } finally {
    await apiDelete(`/api/v1/datapoints/${dpId}`)
  }
})

test('RingBuffer zeigt Live-Einträge auch ohne manuellen Refresh', async ({ page }) => {
  const created = await apiPost('/api/v1/datapoints', {
    name: `E2E-RB-LiveOnly-${Date.now()}`,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }
  const dpId = created.id

  try {
    await gotoMonitorLive(page)
    const rows = page.locator(`[data-testid="ringbuffer-entry"][data-dp="${dpId}"]`)
    const before = await rows.count()

    await apiPost(`/api/v1/datapoints/${dpId}/value`, { value: 10.0, quality: 'good' })
    await apiPost(`/api/v1/datapoints/${dpId}/value`, { value: 11.0, quality: 'good' })
    await apiPost(`/api/v1/datapoints/${dpId}/value`, { value: 12.0, quality: 'good' })

    await expect(rows).toHaveCount(before + 3, { timeout: 12_000 })
  } finally {
    await apiDelete(`/api/v1/datapoints/${dpId}`)
  }
})

test('RingBuffer Monitor-Modal öffnet stabil ohne separates Speicher-PopUp', async ({ page }) => {
  await page.route('**/api/v1/ringbuffer/query', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })
  await page.route('**/api/v1/ringbuffer/filtersets', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })
  await page.route('**/api/v1/ringbuffer/stats', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        total: 0,
        max_entries: 10000,
        storage: 'file',
        effective_retention_seconds: null,
        max_file_size_bytes: null,
        max_age: null,
        file_size_bytes: 0,
      }),
    })
  })

  await gotoMonitor(page)
  await page.click('[data-testid="btn-open-monitor-config"]')

  await expect(page.locator('[data-testid="rb-config-max-size-value"]')).toBeVisible()
  await expect(page.locator('[data-testid="rb-config-retention-value"]')).toBeVisible()
  await expect(page.locator('[data-testid="rb-config-stats-total"]')).toContainText('0')
  await expect(page.getByRole('button', { name: /speicher.*popup/i })).toHaveCount(0)
})

test('RingBuffer Monitor-Modal hält Speicher-/Retention-State und sendet Limits korrekt', async ({ page }) => {
  await page.route('**/api/v1/ringbuffer/query', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })
  await page.route('**/api/v1/ringbuffer/filtersets', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })
  await page.route('**/api/v1/ringbuffer/stats', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        total: 12,
        max_entries: 10000,
        storage: 'file',
        effective_retention_seconds: 86400,
        max_file_size_bytes: 10485760,
        max_age: 86400,
        file_size_bytes: 4096,
      }),
    })
  })

  let postedBody: Record<string, unknown> | null = null
  await page.route('**/api/v1/ringbuffer/config', async (route) => {
    postedBody = route.request().postDataJSON() as Record<string, unknown>
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        total: 12,
        max_entries: 50000,
        storage: 'file',
        effective_retention_seconds: 172800,
        max_file_size_bytes: 2147483648,
        max_age: 63072000,
        file_size_bytes: 8192,
      }),
    })
  })

  await gotoMonitor(page)
  await page.click('[data-testid="btn-open-monitor-config"]')

  await expect(page.locator('[data-testid="rb-config-max-entries-enabled"]')).toBeChecked()
  await expect(page.locator('[data-testid="rb-config-max-size-enabled"]')).toBeChecked()
  await expect(page.locator('[data-testid="rb-config-retention-enabled"]')).toBeChecked()
  await page.fill('[data-testid="rb-config-max-size-value"]', '2')
  await page.selectOption('[data-testid="rb-config-max-size-unit"]', 'gb')
  await page.fill('[data-testid="rb-config-retention-value"]', '2')
  await page.selectOption('[data-testid="rb-config-retention-unit"]', 'years')
  await page.fill('[data-testid="rb-config-max-entries"]', '50000')

  await expect(page.locator('[data-testid="rb-config-max-size-value"]')).toHaveValue('2')
  await expect(page.locator('[data-testid="rb-config-retention-value"]')).toHaveValue('2')
  await expect(page.locator('[data-testid="rb-config-max-entries"]')).toHaveValue('50000')

  await page.click('[data-testid="rb-config-save"]')

  await expect.poll(() => postedBody).not.toBeNull()
  expect(postedBody).toEqual({
    storage: 'file',
    max_entries: 50000,
    max_file_size_bytes: 2147483648,
    max_age: 63072000,
  })
})

test('RingBuffer Monitor-Modal Statistik rendert stabil bei leerem und gefülltem Buffer', async ({ page }) => {
  let showFilledStats = false
  await page.route('**/api/v1/ringbuffer/query', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })
  await page.route('**/api/v1/ringbuffer/filtersets', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })
  await page.route('**/api/v1/ringbuffer/stats', async (route) => {
    if (!showFilledStats) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total: 0,
          max_entries: 10000,
          storage: 'file',
          effective_retention_seconds: null,
          max_file_size_bytes: null,
          max_age: null,
          file_size_bytes: 0,
        }),
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        total: 25,
        max_entries: 10000,
        storage: 'file',
        effective_retention_seconds: 172800,
        max_file_size_bytes: 52428800,
        max_age: 172800,
        file_size_bytes: 1048576,
      }),
    })
  })

  await gotoMonitor(page)
  await page.click('[data-testid="btn-open-monitor-config"]')
  const statsBox = await page.locator('[data-testid="rb-config-stats"]').boundingBox()
  const firstInputBox = await page.locator('[data-testid="rb-config-max-entries"]').boundingBox()
  expect(statsBox).not.toBeNull()
  expect(firstInputBox).not.toBeNull()
  if (statsBox && firstInputBox) {
    expect(statsBox.y).toBeLessThan(firstInputBox.y)
  }
  await expect(page.locator('[data-testid="rb-config-stats-total"]')).toContainText('0')
  await expect(page.locator('[data-testid="rb-config-stats-file-size"]')).toContainText('0 B')
  await expect(page.locator('[data-testid="rb-config-stats-retention"]')).toContainText('—')

  showFilledStats = true
  await page.reload()
  await waitForMonitorReady(page)
  await page.click('[data-testid="btn-open-monitor-config"]')
  await expect(page.locator('[data-testid="rb-config-stats-total"]')).toContainText('25')
  await expect(page.locator('[data-testid="rb-config-stats-file-size"]')).toContainText('1.00 MB')
  await expect(page.locator('[data-testid="rb-config-stats-retention"]')).toContainText('2d')
})

test('RingBuffer Monitor-Modal unterstützt Max.-Einträge ohne Limit und schliesst nach Erfolg', async ({ page }) => {
  await page.route('**/api/v1/ringbuffer/query', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })
  await page.route('**/api/v1/ringbuffer/filtersets', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    })
  })

  let maxEntriesUnlimited = false
  await page.route('**/api/v1/ringbuffer/stats', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        total: 7,
        max_entries: maxEntriesUnlimited ? null : 10000,
        storage: 'file',
        effective_retention_seconds: 3665,
        max_file_size_bytes: 10485760,
        max_age: 86400,
        file_size_bytes: 2048,
      }),
    })
  })

  let postedBody: Record<string, unknown> | null = null
  await page.route('**/api/v1/ringbuffer/config', async (route) => {
    postedBody = route.request().postDataJSON() as Record<string, unknown>
    maxEntriesUnlimited = true
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        total: 7,
        max_entries: null,
        storage: 'file',
        effective_retention_seconds: 3665,
        max_file_size_bytes: 10485760,
        max_age: 86400,
        file_size_bytes: 2048,
      }),
    })
  })

  await gotoMonitor(page)
  await page.click('[data-testid="btn-open-monitor-config"]')
  await expect(page.locator('[data-testid="rb-config-max-entries-enabled"]')).toBeChecked()
  await page.click('[data-testid="rb-config-max-entries-enabled"]')
  await expect(page.locator('[data-testid="rb-config-max-entries-enabled"]')).not.toBeChecked()
  await page.click('[data-testid="rb-config-save"]')

  await expect.poll(() => postedBody).not.toBeNull()
  expect(postedBody).toEqual({
    storage: 'file',
    max_entries: null,
    max_file_size_bytes: 10485760,
    max_age: 86400,
  })

  await expect(page.locator('text=Monitor-Konfiguration gespeichert')).toBeVisible()
  await expect(page.locator('[data-testid="rb-config-save"]')).toBeHidden({ timeout: 3000 })

  await page.click('[data-testid="btn-open-monitor-config"]')
  await expect(page.locator('[data-testid="rb-config-max-entries-enabled"]')).not.toBeChecked()
})
