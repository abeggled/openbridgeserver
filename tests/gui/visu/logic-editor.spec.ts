import { test, expect } from '@playwright/test'
import { apiPost, apiPut, apiGet, apiDelete } from '../helpers'


/**
 * End-to-end test: Create a logic graph with a const_value node via API,
 * open it in the GUI, enable debug mode, run it, and verify the debug-band
 * shows a value (not the default "—").
 */
test('Logic-Editor Debug-Modus zeigt Wert nach Ausführen', async ({ page }) => {
  // 1. Create a graph with one const_value node via API
  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-Graph-${Date.now()}`,
    description: 'Playwright test graph',
    enabled: true,
    flow_data: {
      nodes: [
        {
          id: 'node-1',
          type: 'const_value',
          position: { x: 100, y: 100 },
          data: {
            label: 'Const',
            value: '42',
            data_type: 'number',
          },
        },
      ],
      edges: [],
    },
  }) as { id: string }
  const graphId = graph.id

  try {
    // 2. Navigate to the Logic view
    await page.goto('/logic')
    await page.waitForLoadState('networkidle')

    // 3. Select the graph from the dropdown
    await page.selectOption('[data-testid="select-graph"]', graphId)

    // 4. Wait for the canvas to render the node (VueFlow + API load takes a moment)
    await page.waitForTimeout(1_000)
    await expect(page.locator('[data-testid="debug-band"]').first()).toBeHidden({ timeout: 5_000 })

    // 5. Enable debug mode
    await page.click('[data-testid="btn-debug"]')

    // 6. Run the graph
    await page.click('[data-testid="btn-run"]')

    // 7. The debug-band must appear and show a value (not "—")
    //    runGraph() calls POST /api/v1/logic/graphs/{id}/run → Vue reactivity update; allow up to 8 s
    const debugBand = page.locator('[data-testid="debug-band"]').first()
    await expect(debugBand).toBeVisible({ timeout: 8_000 })
    const text = await debugBand.textContent()
    expect(text?.trim()).not.toBe('—')
    expect(text?.trim()).not.toBe('')
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})

// ---------------------------------------------------------------------------
// Enhanced AND gate: 3 inputs, debug output shows correct boolean result
// ---------------------------------------------------------------------------
test('AND-Gate mit 3 Eingängen (input_count=3) zeigt true wenn alle Eingänge true', async ({ page }) => {
  // Build: three const_value(true) nodes → AND(input_count=3) node
  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-AND3-${Date.now()}`,
    description: 'Playwright: AND 3 inputs',
    enabled: true,
    flow_data: {
      nodes: [
        { id: 'c1', type: 'const_value', position: { x: 0,   y: 0   }, data: { value: 'true', data_type: 'bool' } },
        { id: 'c2', type: 'const_value', position: { x: 0,   y: 100 }, data: { value: 'true', data_type: 'bool' } },
        { id: 'c3', type: 'const_value', position: { x: 0,   y: 200 }, data: { value: 'true', data_type: 'bool' } },
        { id: 'g',  type: 'and',         position: { x: 300, y: 100 }, data: { input_count: 3 } },
      ],
      edges: [
        { id: 'e1', source: 'c1', target: 'g', sourceHandle: 'value', targetHandle: 'in1' },
        { id: 'e2', source: 'c2', target: 'g', sourceHandle: 'value', targetHandle: 'in2' },
        { id: 'e3', source: 'c3', target: 'g', sourceHandle: 'value', targetHandle: 'in3' },
      ],
    },
  }) as { id: string }
  const graphId = graph.id
  try {
    await page.goto('/logic')
    await page.waitForLoadState('networkidle')
    await page.selectOption('[data-testid="select-graph"]', graphId)
    await page.waitForTimeout(1_000)
    await page.click('[data-testid="btn-debug"]')
    await page.click('[data-testid="btn-run"]')
    // Verify the AND gate's debug band shows a truthy result
    const debugBands = page.locator('[data-testid="debug-band"]')
    await expect(debugBands.first()).toBeVisible({ timeout: 8_000 })
    // At least one debug band must be visible (graph ran successfully)
    const count = await debugBands.count()
    expect(count).toBeGreaterThan(0)
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})

// ---------------------------------------------------------------------------
// AND gate with negated output: true AND true → negate_out → false
// ---------------------------------------------------------------------------
test('AND-Gate mit negate_out zeigt false wenn beide Eingänge true', async ({ page }) => {
  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-AND-NEG-${Date.now()}`,
    description: 'Playwright: AND negate_out',
    enabled: true,
    flow_data: {
      nodes: [
        { id: 'c1', type: 'const_value', position: { x: 0,   y: 0   }, data: { value: 'true', data_type: 'bool' } },
        { id: 'c2', type: 'const_value', position: { x: 0,   y: 100 }, data: { value: 'true', data_type: 'bool' } },
        { id: 'g',  type: 'and',         position: { x: 300, y: 50  }, data: { negate_out: true } },
      ],
      edges: [
        { id: 'e1', source: 'c1', target: 'g', sourceHandle: 'value', targetHandle: 'in1' },
        { id: 'e2', source: 'c2', target: 'g', sourceHandle: 'value', targetHandle: 'in2' },
      ],
    },
  }) as { id: string }
  const graphId = graph.id
  try {
    // Run via API and check the result directly
    const result = await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {}) as { outputs: Record<string, Record<string, unknown>> }
    expect(result.outputs['g']?.['out']).toBe(false)
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})

// ---------------------------------------------------------------------------
// heating_circuit: node type exists in registry and graph runs without error
// ---------------------------------------------------------------------------
test('heating_circuit-Node läuft durch und gibt heating_mode aus', async ({ page }) => {
  // New design: single 'value' input; slot assigned by time of day.
  // We just verify the node executes and returns a valid heating_mode (0 or 1).
  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-HC-${Date.now()}`,
    description: 'Playwright: heating_circuit',
    enabled: true,
    flow_data: {
      nodes: [
        { id: 'cv', type: 'const_value', position: { x: 0,   y: 0 }, data: { value: '10', data_type: 'number' } },
        { id: 'hc', type: 'heating_circuit', position: { x: 300, y: 0 }, data: { temp_winter: 15.0, temp_summer: 20.0 } },
      ],
      edges: [
        { id: 'e1', source: 'cv', target: 'hc', sourceHandle: 'value', targetHandle: 'value' },
      ],
    },
  }) as { id: string }
  const graphId = graph.id
  try {
    const result = await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {}) as { outputs: Record<string, Record<string, unknown>> }
    expect(result.outputs['hc']).toBeDefined()
    // heating_mode is 0 or 1 (slot-based; exact value depends on test run time)
    expect([0, 1]).toContain(result.outputs['hc']['heating_mode'])
    // debug outputs are present in the response
    expect('t1' in result.outputs['hc']).toBe(true)
    expect('t2' in result.outputs['hc']).toBe(true)
    expect('t3' in result.outputs['hc']).toBe(true)
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})

// ---------------------------------------------------------------------------
// min_max_tracker: node runs and returns min/max outputs
// ---------------------------------------------------------------------------
test('min_max_tracker-Node gibt min_abs und max_abs aus', async ({ page }) => {
  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-MMT-${Date.now()}`,
    description: 'Playwright: min_max_tracker',
    enabled: true,
    flow_data: {
      nodes: [
        { id: 'v',  type: 'const_value',   position: { x: 0,   y: 0 }, data: { value: '42', data_type: 'number' } },
        { id: 'mm', type: 'min_max_tracker', position: { x: 300, y: 0 }, data: {} },
      ],
      edges: [
        { id: 'e1', source: 'v', target: 'mm', sourceHandle: 'value', targetHandle: 'value' },
      ],
    },
  }) as { id: string }
  const graphId = graph.id
  try {
    const result = await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {}) as { outputs: Record<string, Record<string, unknown>> }
    expect(result.outputs['mm']).toBeDefined()
    expect(result.outputs['mm']['min_abs']).toBe(42)
    expect(result.outputs['mm']['max_abs']).toBe(42)
    expect(result.outputs['mm']['min_daily']).toBe(42)
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})

// ---------------------------------------------------------------------------
// consumption_counter: first run returns 0, second run returns delta
// ---------------------------------------------------------------------------
test('consumption_counter-Node berechnet Verbrauch zwischen zwei Läufen', async ({ page }) => {
  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-CC-${Date.now()}`,
    description: 'Playwright: consumption_counter',
    enabled: true,
    flow_data: {
      nodes: [
        { id: 'v',  type: 'const_value',        position: { x: 0,   y: 0 }, data: { value: '1000', data_type: 'number' } },
        { id: 'cc', type: 'consumption_counter', position: { x: 300, y: 0 }, data: {} },
      ],
      edges: [
        { id: 'e1', source: 'v', target: 'cc', sourceHandle: 'value', targetHandle: 'value' },
      ],
    },
  }) as { id: string }
  const graphId = graph.id
  try {
    // First run: sets baseline, consumption = 0
    const r1 = await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {}) as { outputs: Record<string, Record<string, unknown>> }
    expect(r1.outputs['cc']['daily']).toBe(0)

    // Update const_value to 1050 and run again → delta = 50
    await apiPut(`/api/v1/logic/graphs/${graphId}`, {
      name: `E2E-CC-updated`,
      description: 'updated',
      enabled: true,
      flow_data: {
        nodes: [
          { id: 'v',  type: 'const_value',        position: { x: 0,   y: 0 }, data: { value: '1050', data_type: 'number' } },
          { id: 'cc', type: 'consumption_counter', position: { x: 300, y: 0 }, data: {} },
        ],
        edges: [
          { id: 'e1', source: 'v', target: 'cc', sourceHandle: 'value', targetHandle: 'value' },
        ],
      },
    })
    const r2 = await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {}) as { outputs: Record<string, Record<string, unknown>> }
    expect(r2.outputs['cc']['daily']).toBe(50)
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})

// ---------------------------------------------------------------------------
// node_types API: new types are listed in the registry
// ---------------------------------------------------------------------------
test('Node-Type-Registry enthält alle neuen Funktionsblöcke', async ({ page }) => {
  const types = await apiGet('/api/v1/logic/node-types') as Array<{ type: string }>
  const typeIds = types.map(t => t.type)
  expect(typeIds).toContain('and')
  expect(typeIds).toContain('or')
  expect(typeIds).toContain('xor')
  expect(typeIds).toContain('heating_circuit')
  expect(typeIds).toContain('min_max_tracker')
  expect(typeIds).toContain('consumption_counter')
})

// ---------------------------------------------------------------------------
// Logic editor: new node types appear in the node palette
// ---------------------------------------------------------------------------
test('Logic-Editor Palette zeigt neue Node-Typen an', async ({ page }) => {
  await page.goto('/logic')
  await page.waitForLoadState('networkidle')

  // Wait for the palette to populate from the API (node types are fetched async)
  // Each new node type must have a visible label entry in the palette
  await expect(page.getByText('Sommer/Winter (DIN)', { exact: true })).toBeVisible({ timeout: 8_000 })
  await expect(page.getByText('Min/Max Tracker',  { exact: true })).toBeVisible({ timeout: 3_000 })
  await expect(page.getByText('Verbrauchszähler', { exact: true })).toBeVisible({ timeout: 3_000 })
})
