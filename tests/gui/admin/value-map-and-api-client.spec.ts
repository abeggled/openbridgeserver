import { test, expect } from '@playwright/test'
import { apiGet, apiPost, apiDelete } from '../helpers'

/**
 * E2E tests for issue #208:
 *   1. API-check: api_client node schema has "Response Content-Typ" + application/json enum
 *   2. Logic-Editor: datapoint_read N-value Wertzuordnung is loaded and shown correctly
 *   3. Logic-Editor: invalid JSON in custom Wertzuordnung shows error message
 */

// ---------------------------------------------------------------------------
// Helper: create a graph with one node and navigate to the Logic editor
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

// ---------------------------------------------------------------------------
// Helper: click a VueFlow node to open the config panel
// ---------------------------------------------------------------------------
async function openNodePanel(page: import('@playwright/test').Page) {
  // VueFlow nodes can contain multiple child elements; click the inner node wrapper
  const node = page.locator('.vue-flow__node').first()
  await node.click({ force: true })
  await page.waitForTimeout(600)
}


// ===========================================================================
// 1. api_client: node-types API returns correct response_type schema
//    (no UI navigation — directly tests the backend change from issue #208)
// ===========================================================================

test('api_client: response_type Schema enthält application/json und kein legacy json (API)', async () => {
  const nodeTypes = await apiGet('/api/v1/logic/node-types') as any[]
  const apiClient = nodeTypes.find((nt: any) => nt.type === 'api_client')

  expect(apiClient, 'api_client node type muss registriert sein').toBeDefined()

  const schema = apiClient.config_schema?.response_type
  expect(schema, 'config_schema.response_type muss existieren').toBeDefined()

  expect(schema.label).toBe('Response Content-Typ')
  expect(schema.default).toBe('application/json')
  expect(schema.enum).toContain('application/json')
  expect(schema.enum).toContain('text/plain')
  // Legacy values must not appear as valid enum entries
  expect(schema.enum).not.toContain('json')
  expect(schema.enum).not.toContain('text')
})


// ===========================================================================
// 2. datapoint_read: N-value Wertzuordnung wird in NodeConfigPanel angezeigt
// ===========================================================================

test('datapoint_read: N-value Wertzuordnung wird korrekt geladen und angezeigt', async ({ page }) => {
  const nValueMap = {
    '0':  'Aus',
    '1':  'Initialisierung',
    '2':  'Isolationsmessung',
    '3':  'Netzprüfung',
    '10': 'Standby',
  }

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
    await openNodePanel(page)

    // Switch to the Transformation tab
    await page.getByRole('button', { name: 'Transformation' }).click()
    await page.waitForTimeout(400)

    // The custom textarea must be visible (because value_map is set → preset = 'custom')
    const textarea = page.locator('[data-testid="value-map-custom"]')
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
// 3. NodeConfigPanel: ungültiges JSON in Wertzuordnung zeigt Fehlermeldung
// ===========================================================================

test('NodeConfigPanel: Ungültiges JSON in Wertzuordnung zeigt Fehlermeldung', async ({ page }) => {
  const dp = await apiPost('/api/v1/datapoints', {
    name:      `E2E-DP-JSONErr-${Date.now()}`,
    data_type: 'INTEGER',
    tags:      [],
  }) as { id: string }

  // Start with a valid value_map so the textarea is already visible (preset = 'custom')
  // — avoids a selectOption interaction that may not trigger Vue's v-model reliably
  const graphId = await createAndOpenGraph(page, 'datapoint_read', {
    datapoint_id:   dp.id,
    datapoint_name: 'TestDP',
    value_map:      { '0': 'Aus', '1': 'An' },
  })

  try {
    await openNodePanel(page)

    // Switch to the Transformation tab
    await page.getByRole('button', { name: 'Transformation' }).click()
    await page.waitForTimeout(400)

    // Textarea must be visible because value_map is set → preset resolved to 'custom'
    const textarea = page.locator('[data-testid="value-map-custom"]')
    await expect(textarea).toBeVisible({ timeout: 5_000 })

    // Replace with invalid JSON — fill() fires the input event → live validation shows error
    await textarea.fill('{not valid json')
    await page.waitForTimeout(200)

    // Error message must appear (driven by @input handler, no Tab needed)
    await expect(page.getByText(/Ungültiges JSON/i)).toBeVisible({ timeout: 3_000 })
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})
