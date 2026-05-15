import { test, expect, type Page, type Locator } from '@playwright/test'
import { apiPost, apiPatch, apiDelete, gotoMonitor } from '../helpers'

// API-First helper: create a filterset and flip topbar_active=true. UI tests
// that don't validate the FilterEditor itself use this so they don't depend
// on Combobox interaction timing (Test 1 exercises the full editor flow).
async function createActiveFilterset(name: string, filter: Record<string, unknown>): Promise<string> {
  const set = (await apiPost('/api/v1/ringbuffer/filtersets', { name, filter })) as { id: string }
  await apiPatch(`/api/v1/ringbuffer/filtersets/${set.id}/topbar`, { topbar_active: true })
  return set.id
}

// Phase-2 FilterEditor + TopbarFilterChips coverage (#431, #435, #436).
// The old nested-group FilterBuilder UI from #392 is gone; this file now
// targets the flat FilterCriteria model and the sticky topbar.

function uniqueName(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 1000)}`
}

// Open the "+ Filter" menu and click "Neu" — leaves us in an empty FilterEditor.
async function openNewFilterEditor(page: Page): Promise<void> {
  await page.click('[data-testid="topbar-add-filter-btn"]')
  await page.click('[data-testid="topbar-add-filter-new"]')
  await expect(page.locator('[data-testid="filter-editor-name"]')).toBeVisible({ timeout: 5_000 })
}

// Pick the first matching item from a Combobox scoped by the wrapper test-id.
// Works for DpCombobox, TagCombobox, HierarchyCombobox and AdapterCombobox —
// they all render <Combobox> internally and share the same input/item slots.
async function pickInCombobox(page: Page, scopeTestId: string, query: string): Promise<void> {
  const root = page.locator(`[data-testid="${scopeTestId}"]`)
  const input = root.locator('[data-testid="combobox-input"]').first()
  await input.click()
  if (query) await input.fill(query)
  await root.locator('[data-testid="combobox-item-0"]').first().click({ timeout: 5_000 })
}

// Click "Speichern & in Topleiste" and wait until the full chain has settled:
// 1) POST/PUT /filtersets   creates or updates the set
// 2) PATCH /filtersets/:id/topbar  flips topbar_active=true (fired by onSave(true))
// 3) POST /filtersets/query (or /query/v2 if no active sets exist) refreshes the table
// Without 2) the chip is not yet in the OR-union; without 3) the table still
// shows the previous result. Wait promises are set up before the click to
// avoid races against fast responses.
async function saveAndCaptureId(page: Page): Promise<string> {
  const saveRespP = page.waitForResponse(
    (r) =>
      /\/api\/v1\/ringbuffer\/filtersets(?:\/[^/]+)?$/.test(new URL(r.url()).pathname) &&
      ['POST', 'PUT'].includes(r.request().method()) &&
      r.ok(),
    { timeout: 10_000 },
  )
  const topbarRespP = page.waitForResponse(
    (r) =>
      /\/api\/v1\/ringbuffer\/filtersets\/[^/]+\/topbar$/.test(new URL(r.url()).pathname) &&
      r.request().method() === 'PATCH' &&
      r.ok(),
    { timeout: 10_000 },
  )
  const tableRefreshP = waitForTableRefreshPromise(page)
  await page.click('[data-testid="filter-editor-save-topbar"]')
  const saveResp = await saveRespP
  const body = await saveResp.json()
  await topbarRespP
  await tableRefreshP
  return body.id as string
}

// Promise that resolves on the next multi-set OR-union query OR the
// single-set query/v2 fallback (used when no topbar set is active).
function waitForTableRefreshPromise(page: Page) {
  return page.waitForResponse(
    (r) => {
      const path = new URL(r.url()).pathname
      return (
        (path.endsWith('/ringbuffer/filtersets/query') ||
          path.endsWith('/ringbuffer/query/v2')) &&
        r.request().method() === 'POST' &&
        r.ok()
      )
    },
    { timeout: 10_000 },
  )
}

// Wait until the chip for the given set appears in the topbar.
function topbarChip(page: Page, setId: string): Locator {
  return page.locator(`[data-testid="topbar-chip-${setId}"]`)
}

test('FilterCriteria: Tags-Liste OR-matcht, Datapoints AND-engt ein', async ({ page }) => {
  const tag = uniqueName('fe02-and-or')
  const dpA = (await apiPost('/api/v1/datapoints', {
    name: uniqueName('E2E-RB-FE02-A'),
    data_type: 'FLOAT',
    tags: [tag],
  })) as { id: string }
  const dpB = (await apiPost('/api/v1/datapoints', {
    name: uniqueName('E2E-RB-FE02-B'),
    data_type: 'FLOAT',
    tags: [tag],
  })) as { id: string }
  let setId: string | null = null

  try {
    await apiPost(`/api/v1/datapoints/${dpA.id}/value`, { value: 11.0, quality: 'good' })
    await apiPost(`/api/v1/datapoints/${dpB.id}/value`, { value: 22.0, quality: 'good' })

    await gotoMonitor(page)

    await openNewFilterEditor(page)
    await page.fill('[data-testid="filter-editor-name"]', uniqueName('FS-AND-OR'))
    await pickInCombobox(page, 'filter-editor-tags', tag)
    setId = await saveAndCaptureId(page)

    await expect(topbarChip(page, setId)).toBeVisible({ timeout: 5_000 })
    await expect(
      page.locator(`[data-testid="ringbuffer-entry"][data-dp="${dpA.id}"]`),
    ).toHaveCount(1, { timeout: 10_000 })
    await expect(
      page.locator(`[data-testid="ringbuffer-entry"][data-dp="${dpB.id}"]`),
    ).toHaveCount(1)

    // Re-open the editor, add a datapoint filter — AND between tags-section and
    // datapoints-section now excludes dpB even though dpB still carries the tag.
    await page.click(`[data-testid="topbar-chip-body-${setId}"]`)
    await pickInCombobox(page, 'filter-editor-dps', dpA.id)
    await saveAndCaptureId(page)

    await expect(
      page.locator(`[data-testid="ringbuffer-entry"][data-dp="${dpA.id}"]`),
    ).toHaveCount(1, { timeout: 10_000 })
    await expect(
      page.locator(`[data-testid="ringbuffer-entry"][data-dp="${dpB.id}"]`),
    ).toHaveCount(0)
  } finally {
    if (setId) await apiDelete(`/api/v1/ringbuffer/filtersets/${setId}`)
    await apiDelete(`/api/v1/datapoints/${dpA.id}`)
    await apiDelete(`/api/v1/datapoints/${dpB.id}`)
  }
})

test('Topbar-Chip-Toggle schaltet das Set ein und aus', async ({ page }) => {
  const dpIn = (await apiPost('/api/v1/datapoints', {
    name: uniqueName('E2E-RB-FE02-IN'),
    data_type: 'FLOAT',
    tags: ['fe02-toggle'],
  })) as { id: string }
  const dpOut = (await apiPost('/api/v1/datapoints', {
    name: uniqueName('E2E-RB-FE02-OUT'),
    data_type: 'FLOAT',
    tags: ['fe02-toggle'],
  })) as { id: string }
  let setId: string | null = null

  try {
    await apiPost(`/api/v1/datapoints/${dpIn.id}/value`, { value: 10.0, quality: 'good' })
    await apiPost(`/api/v1/datapoints/${dpOut.id}/value`, { value: 20.0, quality: 'good' })

    // API-first set creation: this test is about toggle UX, not FilterEditor.
    setId = await createActiveFilterset(uniqueName('FS-TOGGLE'), { datapoints: [dpIn.id] })

    await gotoMonitor(page)

    const dpInRows = page.locator(`[data-testid="ringbuffer-entry"][data-dp="${dpIn.id}"]`)
    const dpOutRows = page.locator(`[data-testid="ringbuffer-entry"][data-dp="${dpOut.id}"]`)

    await expect(topbarChip(page, setId)).toBeVisible({ timeout: 5_000 })
    await expect(dpInRows).toHaveCount(1, { timeout: 10_000 })
    await expect(dpOutRows).toHaveCount(0)

    // Toggle (●→○) flips is_active=false. The set stays pinned to the topbar
    // (topbar_active=true) but the backend multi-query skips inactive sets,
    // so the OR-union is empty and the table renders nothing.
    await page.click(`[data-testid="topbar-chip-toggle-${setId}"]`)
    await expect(page.locator('[data-testid="ringbuffer-empty"]')).toBeVisible({ timeout: 10_000 })
    await expect(dpInRows).toHaveCount(0)
    await expect(dpOutRows).toHaveCount(0)

    // Toggle back on → multi-query returns only dpIn.
    await page.click(`[data-testid="topbar-chip-toggle-${setId}"]`)
    await expect(dpInRows).toHaveCount(1, { timeout: 10_000 })
    await expect(dpOutRows).toHaveCount(0)

    // Remove (×) drops topbar_active=false → no active sets at all → the
    // single-set query/v2 fallback returns every entry, both DPs visible.
    await page.click(`[data-testid="topbar-chip-remove-${setId}"]`)
    await expect(dpInRows).toHaveCount(1, { timeout: 10_000 })
    await expect(dpOutRows).toHaveCount(1)
  } finally {
    if (setId) await apiDelete(`/api/v1/ringbuffer/filtersets/${setId}`)
    await apiDelete(`/api/v1/datapoints/${dpIn.id}`)
    await apiDelete(`/api/v1/datapoints/${dpOut.id}`)
  }
})

test('Hierarchy-Knoten löst descendant-inclusive auf (Recursive-CTE)', async ({ page }) => {
  const tree = (await apiPost('/api/v1/hierarchy/trees', {
    name: uniqueName('FE02-Tree'),
    description: 'E2E',
  })) as { id: string }
  const node = (await apiPost('/api/v1/hierarchy/nodes', {
    tree_id: tree.id,
    parent_id: null,
    name: uniqueName('FE02-Node'),
    description: 'E2E',
    order: 0,
  })) as { id: string }
  const dp = (await apiPost('/api/v1/datapoints', {
    name: uniqueName('E2E-RB-FE02-HIER'),
    data_type: 'FLOAT',
    tags: ['fe02-hier'],
  })) as { id: string }
  await apiPost('/api/v1/hierarchy/links', { node_id: node.id, datapoint_id: dp.id })
  let setId: string | null = null

  try {
    await apiPost(`/api/v1/datapoints/${dp.id}/value`, { value: 42.0, quality: 'good' })

    await gotoMonitor(page)

    await openNewFilterEditor(page)
    await page.fill('[data-testid="filter-editor-name"]', uniqueName('FS-HIER'))
    await pickInCombobox(page, 'filter-editor-hierarchy', node.id)
    setId = await saveAndCaptureId(page)

    // The DP is linked to the node but not picked explicitly. Recursive-CTE
    // resolution on the server expands the node into its descendant DPs.
    await expect(topbarChip(page, setId)).toBeVisible({ timeout: 5_000 })
    await expect(
      page.locator(`[data-testid="ringbuffer-entry"][data-dp="${dp.id}"]`),
    ).toHaveCount(1, { timeout: 10_000 })
  } finally {
    if (setId) await apiDelete(`/api/v1/ringbuffer/filtersets/${setId}`)
    await apiDelete(
      `/api/v1/hierarchy/links?node_id=${encodeURIComponent(node.id)}&datapoint_id=${encodeURIComponent(dp.id)}`,
    )
    await apiDelete(`/api/v1/hierarchy/trees/${tree.id}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})

test('Live-Event eines nicht passenden DPs wird durch aktiven Filter gegated', async ({ page }) => {
  const dpIn = (await apiPost('/api/v1/datapoints', {
    name: uniqueName('Waschküche E2E'),
    data_type: 'FLOAT',
    tags: ['fe02-baseq'],
  })) as { id: string }
  const dpOut = (await apiPost('/api/v1/datapoints', {
    name: uniqueName('Keller E2E'),
    data_type: 'FLOAT',
    tags: ['fe02-baseq'],
  })) as { id: string }
  let setId: string | null = null

  try {
    await apiPost(`/api/v1/datapoints/${dpIn.id}/value`, { value: 10.0, quality: 'good' })
    await apiPost(`/api/v1/datapoints/${dpOut.id}/value`, { value: 20.0, quality: 'good' })

    setId = await createActiveFilterset(uniqueName('FS-LIVE-GATE'), { datapoints: [dpIn.id] })

    await gotoMonitor(page)

    const dpInRows = page.locator(`[data-testid="ringbuffer-entry"][data-dp="${dpIn.id}"]`)
    const dpOutRows = page.locator(`[data-testid="ringbuffer-entry"][data-dp="${dpOut.id}"]`)

    await expect(topbarChip(page, setId)).toBeVisible({ timeout: 5_000 })
    await expect(dpInRows).toHaveCount(1, { timeout: 10_000 })
    await expect(dpOutRows).toHaveCount(0)

    // Live-Push for unrelated dp must not bypass the active filterset.
    // useClientSideMatch + entryInTimeWindow gate WS entries against the
    // active filter (Phase-2 follow-up bugfix consolidated in this branch).
    await apiPost(`/api/v1/datapoints/${dpOut.id}/value`, { value: 21.0, quality: 'good' })
    await page.waitForTimeout(1500)
    await expect(dpOutRows).toHaveCount(0)

    // Live-Push for the matching dp must increase the row count.
    await apiPost(`/api/v1/datapoints/${dpIn.id}/value`, { value: 11.0, quality: 'good' })
    await expect(dpInRows).toHaveCount(2, { timeout: 5_000 })
  } finally {
    if (setId) await apiDelete(`/api/v1/ringbuffer/filtersets/${setId}`)
    await apiDelete(`/api/v1/datapoints/${dpIn.id}`)
    await apiDelete(`/api/v1/datapoints/${dpOut.id}`)
  }
})
