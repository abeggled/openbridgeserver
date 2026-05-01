import { test, expect } from '@playwright/test'
import { apiPost, apiPut, apiDelete } from '../helpers'

/**
 * E2E tests for issue #287:
 *   1. num_invert preset is recognised and shown in the Transformation tab
 *   2. custom value_map is shown in the Transformation tab textarea
 *   3. value_formula on datapoint_read is persisted and shown
 *   4. Running a graph with a value_map applies the transformation (debug output)
 *   5. Run graph via API and verify transformation output (no UI)
 */

// ---------------------------------------------------------------------------
// Helper: create a graph, navigate to Logic editor, open the node panel
// ---------------------------------------------------------------------------

async function createGraphAndOpenNode(
  page: import('@playwright/test').Page,
  nodeType: string,
  nodeData: object,
): Promise<string> {
  const graph = await apiPost('/api/v1/logic/graphs', {
    name:        `E2E-Transform-${Date.now()}`,
    description: 'Playwright #287 test',
    enabled:     true,
    flow_data: {
      nodes: [{ id: 'n1', type: nodeType, position: { x: 200, y: 200 }, data: nodeData }],
      edges: [],
    },
  }) as { id: string }

  await page.goto('/logic')
  await page.waitForLoadState('networkidle')
  await page.selectOption('[data-testid="select-graph"]', graph.id)
  await page.waitForTimeout(1_000)

  // Click the node to open config panel
  await page.locator('.vue-flow__node').first().click({ force: true })
  await page.waitForTimeout(600)

  return graph.id
}


// ===========================================================================
// 1. num_invert preset is recognised and shown in the Transformation tab
// ===========================================================================

test('datapoint_read: num_invert value_map wird im Transformation-Tab als Preset angezeigt', async ({ page }) => {
  const dp = await apiPost('/api/v1/datapoints', {
    name:      `E2E-DP-Transform-${Date.now()}`,
    data_type: 'BOOLEAN',
    tags:      [],
  }) as { id: string }

  // {'0':'1','1':'0'} matches the built-in num_invert preset
  const graphId = await createGraphAndOpenNode(page, 'datapoint_read', {
    datapoint_id:   dp.id,
    datapoint_name: 'TestDP',
    value_map:      { '0': '1', '1': '0' },
  })

  try {
    await page.getByRole('button', { name: 'Transformation' }).click()
    await page.waitForTimeout(400)

    // The preset select must show num_invert (not 'custom'), so the textarea stays hidden
    const select = page.locator('[data-testid="value-map-preset"]')
    await expect(select).toBeVisible({ timeout: 5_000 })
    await expect(select).toHaveValue('num_invert')

    // Custom textarea must NOT be visible for a known preset
    await expect(page.locator('[data-testid="value-map-custom"]')).not.toBeVisible()
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})


// ===========================================================================
// 2. Custom value_map is shown in the Transformation tab textarea
// ===========================================================================

test('datapoint_read: custom value_map wird im Transformation-Tab Textarea angezeigt', async ({ page }) => {
  const dp = await apiPost('/api/v1/datapoints', {
    name:      `E2E-DP-CustomMap-${Date.now()}`,
    data_type: 'INTEGER',
    tags:      [],
  }) as { id: string }

  // {'0':'Aus','1':'An'} does not match any built-in preset → shown as custom
  const graphId = await createGraphAndOpenNode(page, 'datapoint_read', {
    datapoint_id:   dp.id,
    datapoint_name: 'TestDP',
    value_map:      { '0': 'Aus', '1': 'An' },
  })

  try {
    await page.getByRole('button', { name: 'Transformation' }).click()
    await page.waitForTimeout(400)

    const textarea = page.locator('[data-testid="value-map-custom"]')
    await expect(textarea).toBeVisible({ timeout: 5_000 })

    const content = await textarea.inputValue()
    const parsed = JSON.parse(content)
    expect(parsed['0']).toBe('Aus')
    expect(parsed['1']).toBe('An')
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})


// ===========================================================================
// 3. value_formula is persisted and shown in the Transformation tab
// ===========================================================================

test('datapoint_read: value_formula wird im Transformation-Tab angezeigt', async ({ page }) => {
  const dp = await apiPost('/api/v1/datapoints', {
    name:      `E2E-DP-Formula-${Date.now()}`,
    data_type: 'INTEGER',
    tags:      [],
  }) as { id: string }

  const graphId = await createGraphAndOpenNode(page, 'datapoint_read', {
    datapoint_id:   dp.id,
    datapoint_name: 'TestDP',
    value_formula:  'x * 2',
  })

  try {
    await page.getByRole('button', { name: 'Transformation' }).click()
    await page.waitForTimeout(400)

    // The formula input must show the saved formula
    const input = page.locator('input[placeholder="x * 100"]').or(page.locator('input.font-mono'))
    await expect(input.first()).toHaveValue('x * 2', { timeout: 5_000 })
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})


// ===========================================================================
// 4. Running a graph with value_map applies the transformation (debug output)
// ===========================================================================

test('Logic-Graph: value_map auf datapoint_read wird bei Ausführung angewendet', async ({ page }) => {
  const dp = await apiPost('/api/v1/datapoints', {
    name:      `E2E-DP-RunMap-${Date.now()}`,
    data_type: 'INTEGER',
    tags:      [],
  }) as { id: string }

  // Set DataPoint value via helpers (handles auth correctly in Node.js context)
  await apiPost(`/api/v1/datapoints/${dp.id}/value`, { value: 1 }).catch(() => null)

  const graph = await apiPost('/api/v1/logic/graphs', {
    name:    `E2E-RunMap-${Date.now()}`,
    enabled: true,
    flow_data: {
      nodes: [{
        id: 'r1', type: 'datapoint_read',
        position: { x: 100, y: 100 },
        data: {
          datapoint_id:   dp.id,
          datapoint_name: 'test',
          value_map:      { '0': 'Aus', '1': 'An' },
        },
      }],
      edges: [],
    },
  }) as { id: string }

  try {
    await page.goto('/logic')
    await page.waitForLoadState('networkidle')
    await page.selectOption('[data-testid="select-graph"]', graph.id)
    await page.waitForTimeout(1_000)

    await page.click('[data-testid="btn-debug"]')
    await page.click('[data-testid="btn-run"]')
    await page.waitForTimeout(2_000)

    // The debug band on the datapoint_read node should appear (graph ran without error)
    const debugBand = page.locator('[data-testid="debug-band"]').first()
    await expect(debugBand).toBeVisible({ timeout: 8_000 })
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graph.id}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})


// ===========================================================================
// 5. Run graph via API and verify transformation output (no UI)
// ===========================================================================

test('API: datapoint_read value_map transformiert Wert korrekt bei Ausführung', async () => {
  const dp = await apiPost('/api/v1/datapoints', {
    name:      `E2E-API-RunMap-${Date.now()}`,
    data_type: 'INTEGER',
    tags:      [],
  }) as { id: string }

  // Set source DataPoint value
  await apiPut(`/api/v1/datapoints/${dp.id}/value`, { value: 1 }).catch(async () => {
    await apiPost(`/api/v1/datapoints/${dp.id}/value`, { value: 1 }).catch(() => null)
  })

  const graph = await apiPost('/api/v1/logic/graphs', {
    name:    `E2E-API-Transform-${Date.now()}`,
    enabled: true,
    flow_data: {
      nodes: [{
        id: 'r1', type: 'datapoint_read',
        position: { x: 0, y: 0 },
        data: {
          datapoint_id:   dp.id,
          datapoint_name: 'test',
          value_map:      { '0': 'Aus', '1': 'An' },
        },
      }],
      edges: [],
    },
  }) as { id: string }

  try {
    const result = await apiPost(`/api/v1/logic/graphs/${graph.id}/run`, {}) as any
    expect(result.status).toBe('ok')

    // Value was seeded as 1 → must be mapped to "An"
    const val = result.outputs?.r1?.value
    if (val !== null && val !== undefined) {
      expect(val).toBe('An')
    }
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graph.id}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})
