import { test, expect } from '@playwright/test'
import { apiPost, apiDelete } from '../helpers'

test('RingBuffer Live-Eintrag ohne Reload', async ({ page }) => {
  // Fixture: create a DataPoint
  const created = await apiPost('/api/v1/datapoints', {
    name: `E2E-RB-${Date.now()}`,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }
  const dpId = created.id

  try {
    await page.goto('/ringbuffer')

    // Status badge must say "Live"
    await expect(page.locator('[data-testid="status-badge"]')).toContainText('Live', { timeout: 5_000 })

    // Count current entries
    const before = await page.locator('[data-testid="ringbuffer-entry"]').count()

    // Push a value via API
    await apiPost(`/api/v1/datapoints/${dpId}/value`, { value: 42.0, quality: 'good' })

    // Within 3 s a new entry must appear (count goes up by at least 1)
    await expect(async () => {
      const after = await page.locator('[data-testid="ringbuffer-entry"]').count()
      expect(after).toBeGreaterThan(before)
    }).toPass({ timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/datapoints/${dpId}`)
  }
})
