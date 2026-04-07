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
