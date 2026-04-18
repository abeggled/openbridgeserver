import { test, expect } from '@playwright/test'
import { apiPost, apiDelete } from '../helpers'

/**
 * E2E tests for issue #208:
 *   1. Logic-Editor: api_client node — Response Content-Typ label + new option values
 *   2. Logic-Editor: datapoint_read node — N-value custom Wertzuordnung (value_map)
 *   3. Binding-Form: custom Wertzuordnung with N values + JSON parse error feedback
 */

// ---------------------------------------------------------------------------
// Helper: create a minimal graph with one node and open it in the editor
// ---------------------------------------------------------------------------
async function createAndOpenGraph(
  page: import('@playwright/test').Page,
  nodeType: string,
  nodeData: object,
): Promise<string> {
  const graph = await apiPost('/api/v1/logic/graphs', {
    name:        `E2E-Issue208-${Date.now()}`,
    description: 'Playwright issue #208 test',
    enabled:     true,
    flow_data: {
      nodes: [{
        id:       'n1',
        type:     nodeType,
        position: { x: 200, y: 200 },
        data:     nodeData,
      }],
      edges: [],
    },
  }) as { id: string }

  await page.goto('/logic')
  await page.waitForLoadState('networkidle')
  await page.selectOption('[data-testid="select-graph"]', graph.id)
  await page.waitForTimeout(1_000)

  return graph.id
}


// ===========================================================================
// 1. api_client: Response Content-Typ label + option values
// ===========================================================================

test('api_client: Response Content-Typ Label und application/json Option sichtbar', async ({ page }) => {
  const graphId = await createAndOpenGraph(page, 'api_client', {
    url:           'http://example.com/api',
    method:        'GET',
    response_type: 'application/json',
  })

  try {
    // Open the node config panel by clicking the node on the canvas
    await page.locator('.vue-flow__node').first().click()
    await page.waitForTimeout(500)

    // The label must be "Response Content-Typ" (without hyphen before "Content")
    await expect(page.getByText('Response Content-Typ')).toBeVisible({ timeout: 5_000 })

    // The dropdown must contain "application/json"
    const select = page.locator('select').filter({ hasText: 'application/json' })
    await expect(select).toBeVisible({ timeout: 5_000 })

    // "text/plain" must also be present
    await expect(select.locator('option[value="text/plain"]')).toHaveCount(1)

    // The old values "json" and "text" must NOT appear as option values
    await expect(select.locator('option[value="json"]')).toHaveCount(0)
    await expect(select.locator('option[value="text"]')).toHaveCount(0)
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})


// ===========================================================================
// 2. datapoint_read: N-value custom Wertzuordnung in NodeConfigPanel
// ===========================================================================

test('datapoint_read: N-value Wertzuordnung wird korrekt geladen und angezeigt', async ({ page }) => {
  const nValueMap = {
    '0':  'Aus',
    '1':  'Initialisierung',
    '2':  'Isolationsmessung',
    '3':  'Netzprüfung',
    '10': 'Standby',
  }

  // Create a datapoint to link to
  const dp = await apiPost('/api/v1/datapoints', {
    name:      `E2E-DP-ValueMap-${Date.now()}`,
    data_type: 'INTEGER',
    tags:      [],
  }) as { id: string }

  const graphId = await createAndOpenGraph(page, 'datapoint_read', {
    datapoint_id:   dp.id,
    datapoint_name: 'TestDP',
    value_map:      nValueMap,
  })

  try {
    await page.locator('.vue-flow__node').first().click()
    await page.waitForTimeout(500)

    // Switch to the Transformation tab
    await page.getByRole('button', { name: 'Transformation' }).click()
    await page.waitForTimeout(300)

    // The custom textarea must show the N-value map
    const textarea = page.locator('textarea[placeholder*="Aus"]')
    await expect(textarea).toBeVisible({ timeout: 5_000 })

    const content = await textarea.inputValue()
    const parsed = JSON.parse(content)
    expect(parsed['0']).toBe('Aus')
    expect(parsed['10']).toBe('Standby')
    expect(Object.keys(parsed)).toHaveLength(5)
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})


// ===========================================================================
// 3. datapoint_read NodeConfigPanel: Ungültiges JSON zeigt Fehlermeldung
// ===========================================================================

test('NodeConfigPanel: Ungültiges JSON in Wertzuordnung zeigt Fehlermeldung', async ({ page }) => {
  const dp = await apiPost('/api/v1/datapoints', {
    name:      `E2E-DP-JSONErr-${Date.now()}`,
    data_type: 'INTEGER',
    tags:      [],
  }) as { id: string }

  const graphId = await createAndOpenGraph(page, 'datapoint_read', {
    datapoint_id:   dp.id,
    datapoint_name: 'TestDP',
  })

  try {
    await page.locator('.vue-flow__node').first().click()
    await page.waitForTimeout(500)

    await page.getByRole('button', { name: 'Transformation' }).click()
    await page.waitForTimeout(300)

    // Choose "Benutzerdefiniert" preset
    await page.locator('select').filter({ hasText: 'Wertzuordnung' }).selectOption('custom')
    await page.waitForTimeout(200)

    // Enter invalid JSON
    const textarea = page.locator('textarea[placeholder*="Aus"]')
    await textarea.fill('{not valid json')
    await textarea.dispatchEvent('change')
    await page.waitForTimeout(200)

    // Error message must appear
    await expect(page.getByText(/Ungültiges JSON/i)).toBeVisible({ timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})
