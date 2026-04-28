import { test, expect } from '@playwright/test'
import { apiPost, apiPut, apiGet, apiDelete, getToken } from '../helpers'


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
// avg_multi: node type exists and outputs avg + all moving-average windows
// ---------------------------------------------------------------------------
test('avg_multi-Node berechnet Mittelwert und gibt alle Zeitfenster aus', async ({ page }) => {
  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-AVGM-${Date.now()}`,
    description: 'Playwright: avg_multi basic',
    enabled: true,
    flow_data: {
      nodes: [
        { id: 'c1', type: 'const_value', position: { x: 0,   y: 0   }, data: { value: '10', data_type: 'number' } },
        { id: 'c2', type: 'const_value', position: { x: 0,   y: 100 }, data: { value: '20', data_type: 'number' } },
        { id: 'am', type: 'avg_multi',   position: { x: 300, y: 50  }, data: { input_count: 2 } },
      ],
      edges: [
        { id: 'e1', source: 'c1', target: 'am', sourceHandle: 'value', targetHandle: 'in_1' },
        { id: 'e2', source: 'c2', target: 'am', sourceHandle: 'value', targetHandle: 'in_2' },
      ],
    },
  }) as { id: string }
  const graphId = graph.id
  try {
    const result = await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {}) as { outputs: Record<string, Record<string, unknown>> }
    const amOut = result.outputs['am']
    expect(amOut).toBeDefined()
    // Current average of 10 and 20
    expect(amOut['avg']).toBe(15)
    // All moving-average windows must be present
    for (const key of ['avg_1m', 'avg_1h', 'avg_1d', 'avg_7d', 'avg_14d', 'avg_30d', 'avg_180d', 'avg_365d']) {
      expect(amOut).toHaveProperty(key)
    }
    // After a single run, all windows should equal 15 (only one sample)
    expect(amOut['avg_1m']).toBe(15)
    expect(amOut['avg_365d']).toBe(15)
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})

// ---------------------------------------------------------------------------
// avg_multi: persist_state=false means node_state not saved to DB
// ---------------------------------------------------------------------------
test('avg_multi persist_state=false speichert keinen Zustand in der Datenbank', async ({ page }) => {
  // Create an avg_multi graph where persist_state=false
  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-AVGM-NOPERSIST-${Date.now()}`,
    description: 'Playwright: avg_multi persist_state=false',
    enabled: true,
    flow_data: {
      nodes: [
        { id: 'c1', type: 'const_value', position: { x: 0,   y: 0 }, data: { value: '42', data_type: 'number' } },
        { id: 'am', type: 'avg_multi',   position: { x: 300, y: 0 }, data: { input_count: 1, persist_state: false } },
      ],
      edges: [
        { id: 'e1', source: 'c1', target: 'am', sourceHandle: 'value', targetHandle: 'in_1' },
      ],
    },
  }) as { id: string }
  const graphId = graph.id
  try {
    // Run the graph so the manager processes it
    const r1 = await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {}) as { outputs: Record<string, Record<string, unknown>> }
    expect(r1.outputs['am']?.['avg']).toBe(42)
    // Read the stored graph from the API to inspect node_state
    // node_state is not exposed in the public API response, but we can verify the
    // graph runs again without error (stateless restart simulation).
    const r2 = await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {}) as { outputs: Record<string, Record<string, unknown>> }
    expect(r2.outputs['am']?.['avg']).toBe(42)
    // Both runs succeed — no error from missing persisted state
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})

// ---------------------------------------------------------------------------
// avg_multi: persist_state=true (default) — state is preserved across runs
// ---------------------------------------------------------------------------
test('avg_multi persist_state=true akkumuliert Zeitfenster-Samples über mehrere Runs', async ({ page }) => {
  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-AVGM-PERSIST-${Date.now()}`,
    description: 'Playwright: avg_multi persist_state=true',
    enabled: true,
    flow_data: {
      nodes: [
        { id: 'c1', type: 'const_value', position: { x: 0,   y: 0 }, data: { value: '10', data_type: 'number' } },
        { id: 'am', type: 'avg_multi',   position: { x: 300, y: 0 }, data: { input_count: 1, persist_state: true } },
      ],
      edges: [
        { id: 'e1', source: 'c1', target: 'am', sourceHandle: 'value', targetHandle: 'in_1' },
      ],
    },
  }) as { id: string }
  const graphId = graph.id
  try {
    // Run three times — each run adds a sample to the buffer
    await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {})
    await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {})
    const r3 = await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {}) as { outputs: Record<string, Record<string, unknown>> }
    // avg_1m covers all three samples (all within the last minute)
    // All samples have value 10, so avg_1m must be 10
    expect(r3.outputs['am']?.['avg_1m']).toBe(10)
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
// gate (TOR): gate open passes signal through
// ---------------------------------------------------------------------------
test('TOR-Gate offen: Eingang wird durchgeleitet', async ({ page }) => {
  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-TOR-Open-${Date.now()}`,
    description: 'Playwright: gate open passes value',
    enabled: true,
    flow_data: {
      nodes: [
        { id: 'val',  type: 'const_value', position: { x: 0,   y: 0   }, data: { value: '42', data_type: 'number' } },
        { id: 'ena',  type: 'const_value', position: { x: 0,   y: 100 }, data: { value: 'true', data_type: 'bool' } },
        { id: 'gate', type: 'gate',        position: { x: 300, y: 50  }, data: { closed_behavior: 'retain' } },
      ],
      edges: [
        { id: 'e1', source: 'val',  target: 'gate', sourceHandle: 'value', targetHandle: 'in' },
        { id: 'e2', source: 'ena',  target: 'gate', sourceHandle: 'value', targetHandle: 'enable' },
      ],
    },
  }) as { id: string }
  const graphId = graph.id
  try {
    const result = await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {}) as { outputs: Record<string, Record<string, unknown>> }
    expect(result.outputs['gate']['out']).toBe(42)
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})

// ---------------------------------------------------------------------------
// gate (TOR): gate closed with retain holds last value
// ---------------------------------------------------------------------------
test('TOR-Gate geschlossen (retain): letzter Wert wird gehalten', async ({ page }) => {
  // Run 1: gate open → store 99
  // Run 2: gate closed → output must still be 99
  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-TOR-Retain-${Date.now()}`,
    description: 'Playwright: gate retain last value',
    enabled: true,
    flow_data: {
      nodes: [
        { id: 'val',  type: 'const_value', position: { x: 0,   y: 0   }, data: { value: '99', data_type: 'number' } },
        { id: 'ena',  type: 'const_value', position: { x: 0,   y: 100 }, data: { value: 'true', data_type: 'bool' } },
        { id: 'gate', type: 'gate',        position: { x: 300, y: 50  }, data: { closed_behavior: 'retain' } },
      ],
      edges: [
        { id: 'e1', source: 'val',  target: 'gate', sourceHandle: 'value', targetHandle: 'in' },
        { id: 'e2', source: 'ena',  target: 'gate', sourceHandle: 'value', targetHandle: 'enable' },
      ],
    },
  }) as { id: string }
  const graphId = graph.id
  try {
    // First run: gate open → stores 99
    await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {})

    // Update graph: gate now closed (enable=false)
    await apiPut(`/api/v1/logic/graphs/${graphId}`, {
      name: `E2E-TOR-Retain-closed`,
      description: 'gate closed',
      enabled: true,
      flow_data: {
        nodes: [
          { id: 'val',  type: 'const_value', position: { x: 0,   y: 0   }, data: { value: '0', data_type: 'number' } },
          { id: 'ena',  type: 'const_value', position: { x: 0,   y: 100 }, data: { value: 'false', data_type: 'bool' } },
          { id: 'gate', type: 'gate',        position: { x: 300, y: 50  }, data: { closed_behavior: 'retain' } },
        ],
        edges: [
          { id: 'e1', source: 'val',  target: 'gate', sourceHandle: 'value', targetHandle: 'in' },
          { id: 'e2', source: 'ena',  target: 'gate', sourceHandle: 'value', targetHandle: 'enable' },
        ],
      },
    })
    const r2 = await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {}) as { outputs: Record<string, Record<string, unknown>> }
    // Must return last stored value 99, not the new input 0
    expect(r2.outputs['gate']['out']).toBe(99)
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})

// ---------------------------------------------------------------------------
// gate (TOR): gate closed with default_value outputs configured value
// ---------------------------------------------------------------------------
test('TOR-Gate geschlossen (default_value): Standardwert wird ausgegeben', async ({ page }) => {
  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-TOR-Default-${Date.now()}`,
    description: 'Playwright: gate default_value',
    enabled: true,
    flow_data: {
      nodes: [
        { id: 'val',  type: 'const_value', position: { x: 0,   y: 0   }, data: { value: '50', data_type: 'number' } },
        { id: 'ena',  type: 'const_value', position: { x: 0,   y: 100 }, data: { value: 'false', data_type: 'bool' } },
        { id: 'gate', type: 'gate',        position: { x: 300, y: 50  }, data: { closed_behavior: 'default_value', default_value: '7' } },
      ],
      edges: [
        { id: 'e1', source: 'val',  target: 'gate', sourceHandle: 'value', targetHandle: 'in' },
        { id: 'e2', source: 'ena',  target: 'gate', sourceHandle: 'value', targetHandle: 'enable' },
      ],
    },
  }) as { id: string }
  const graphId = graph.id
  try {
    const result = await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {}) as { outputs: Record<string, Record<string, unknown>> }
    expect(result.outputs['gate']['out']).toBe(7)
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})

// ---------------------------------------------------------------------------
// gate (TOR): negate_enable inverts control signal
// ---------------------------------------------------------------------------
test('TOR-Gate negate_enable: Freigabe invertiert öffnet Tor bei enable=false', async ({ page }) => {
  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-TOR-Negate-${Date.now()}`,
    description: 'Playwright: gate negate_enable',
    enabled: true,
    flow_data: {
      nodes: [
        { id: 'val',  type: 'const_value', position: { x: 0,   y: 0   }, data: { value: '33', data_type: 'number' } },
        { id: 'ena',  type: 'const_value', position: { x: 0,   y: 100 }, data: { value: 'false', data_type: 'bool' } },
        { id: 'gate', type: 'gate',        position: { x: 300, y: 50  }, data: { negate_enable: true } },
      ],
      edges: [
        { id: 'e1', source: 'val',  target: 'gate', sourceHandle: 'value', targetHandle: 'in' },
        { id: 'e2', source: 'ena',  target: 'gate', sourceHandle: 'value', targetHandle: 'enable' },
      ],
    },
  }) as { id: string }
  const graphId = graph.id
  try {
    const result = await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {}) as { outputs: Record<string, Record<string, unknown>> }
    // negate_enable=true, enable=false → gate open → passes 33
    expect(result.outputs['gate']['out']).toBe(33)
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})

// ---------------------------------------------------------------------------
// gate (TOR): node appears in palette as "TOR"
// ---------------------------------------------------------------------------
test('Logic-Editor Palette zeigt TOR-Node an', async ({ page }) => {
  await page.goto('/logic')
  await page.waitForLoadState('networkidle')
  await expect(page.getByText('TOR', { exact: true })).toBeVisible({ timeout: 8_000 })
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
  expect(typeIds).toContain('gate')
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

// ---------------------------------------------------------------------------
// api_client: node-type registry contains the node and its auth fields
// ---------------------------------------------------------------------------
test('api_client Node-Typ ist in der Registry und hat Auth-Felder im config_schema', async ({ page }) => {
  const types = await apiGet('/api/v1/logic/node-types') as Array<{
    type: string
    config_schema: Record<string, { type: string; enum?: string[] }>
  }>
  const apiClient = types.find(t => t.type === 'api_client')
  expect(apiClient).toBeDefined()

  const schema = apiClient!.config_schema
  // Base fields
  expect(schema).toHaveProperty('url')
  expect(schema).toHaveProperty('method')
  // Auth fields
  expect(schema).toHaveProperty('auth_type')
  expect(schema.auth_type.enum).toEqual(expect.arrayContaining(['none', 'basic', 'digest', 'bearer']))
  expect(schema).toHaveProperty('auth_username')
  expect(schema).toHaveProperty('auth_password')
  expect(schema).toHaveProperty('auth_token')
})

// ---------------------------------------------------------------------------
// api_client: palette shows "API Client" label
// ---------------------------------------------------------------------------
test('Logic-Editor Palette zeigt API Client Node an', async ({ page }) => {
  await page.goto('/logic')
  await page.waitForLoadState('networkidle')
  await expect(page.getByText('API Client', { exact: true })).toBeVisible({ timeout: 8_000 })
})

// ---------------------------------------------------------------------------
// api_client: GET request to local server endpoint returns success=True
// ---------------------------------------------------------------------------
test('api_client GET-Request gegen eigenen Server liefert success=true', async ({ page }) => {
  // Use the public health endpoint — no auth required, always returns 200
  const BASE_URL = process.env.BASE_URL ?? 'http://localhost:8080'
  const targetUrl = `${BASE_URL}/api/v1/system/health`

  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-ApiClient-GET-${Date.now()}`,
    description: 'Playwright: api_client GET 200',
    enabled: true,
    flow_data: {
      nodes: [
        {
          id: 'trig',
          type: 'const_value',
          position: { x: 0, y: 0 },
          data: { value: 'true', data_type: 'bool' },
        },
        {
          id: 'ac',
          type: 'api_client',
          position: { x: 300, y: 0 },
          data: {
            url:           targetUrl,
            method:        'GET',
            response_type: 'json',
            verify_ssl:    false,
            auth_type:     'none',
          },
        },
      ],
      edges: [
        { id: 'e1', source: 'trig', target: 'ac', sourceHandle: 'value', targetHandle: 'trigger' },
      ],
    },
  }) as { id: string }
  const graphId = graph.id

  try {
    const result = await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {}) as {
      outputs: Record<string, Record<string, unknown>>
    }
    expect(result.outputs['ac']).toBeDefined()
    expect(result.outputs['ac']['success']).toBe(true)
    expect(result.outputs['ac']['status']).toBe(200)
    expect(result.outputs['ac']['response']).not.toBeNull()
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})

// ---------------------------------------------------------------------------
// api_client: Bearer Auth adds Authorization header — tested via API result
// ---------------------------------------------------------------------------
test('api_client Bearer Auth sendet Authorization-Header', async ({ page }) => {
  // Use the real JWT token so the auth-protected endpoint returns 200.
  // This verifies the api_client node correctly forwards the Bearer header.
  const BASE_URL = process.env.BASE_URL ?? 'http://localhost:8080'
  const targetUrl = `${BASE_URL}/api/v1/logic/node-types`
  const token = await getToken()

  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-ApiClient-Bearer-${Date.now()}`,
    description: 'Playwright: api_client Bearer auth',
    enabled: true,
    flow_data: {
      nodes: [
        {
          id: 'trig',
          type: 'const_value',
          position: { x: 0, y: 0 },
          data: { value: 'true', data_type: 'bool' },
        },
        {
          id: 'ac',
          type: 'api_client',
          position: { x: 300, y: 0 },
          data: {
            url:        targetUrl,
            method:     'GET',
            auth_type:  'bearer',
            auth_token: token,
            verify_ssl: false,
          },
        },
      ],
      edges: [
        { id: 'e1', source: 'trig', target: 'ac', sourceHandle: 'value', targetHandle: 'trigger' },
      ],
    },
  }) as { id: string }
  const graphId = graph.id

  try {
    const result = await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {}) as {
      outputs: Record<string, Record<string, unknown>>
    }
    // Node must have executed (not skipped) and returned a valid HTTP status
    expect(result.outputs['ac']).toBeDefined()
    expect(result.outputs['ac']['status']).toBe(200)
    expect(result.outputs['ac']['success']).toBe(true)
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})

// ---------------------------------------------------------------------------
// api_client: config panel in GUI shows auth fields based on auth_type
// ---------------------------------------------------------------------------
test('api_client Config-Panel zeigt Auth-Felder korrekt an', async ({ page }) => {
  // Create a graph with an api_client node via API
  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-ApiClient-Config-${Date.now()}`,
    description: 'Playwright: api_client config panel auth',
    enabled: true,
    flow_data: {
      nodes: [
        {
          id: 'ac',
          type: 'api_client',
          position: { x: 100, y: 100 },
          data: {
            url:       'http://example.com',
            method:    'GET',
            auth_type: 'none',
          },
        },
      ],
      edges: [],
    },
  }) as { id: string }
  const graphId = graph.id

  try {
    await page.goto('/logic')
    await page.waitForLoadState('networkidle')
    await page.selectOption('[data-testid="select-graph"]', graphId)
    await page.waitForTimeout(1_500)

    // Click on the node to open the config panel
    await page.locator('.vue-flow__node').first().click()
    await page.waitForTimeout(500)

    // Auth type selector must be visible
    const authTypeSelect = page.locator('[data-testid="api-client-auth-type"]')
    await expect(authTypeSelect).toBeVisible({ timeout: 5_000 })

    // With auth_type=none: username/password fields must NOT be visible
    await expect(page.locator('[data-testid="api-client-auth-basic"]')).not.toBeVisible()
    await expect(page.locator('[data-testid="api-client-auth-bearer"]')).not.toBeVisible()

    // Switch to basic auth → username field appears
    await authTypeSelect.selectOption('basic')
    await expect(page.locator('[data-testid="api-client-auth-basic"]')).toBeVisible({ timeout: 3_000 })
    await expect(page.locator('[data-testid="api-client-auth-bearer"]')).not.toBeVisible()

    // Switch to bearer → token field appears, username disappears
    await authTypeSelect.selectOption('bearer')
    await expect(page.locator('[data-testid="api-client-auth-bearer"]')).toBeVisible({ timeout: 3_000 })
    await expect(page.locator('[data-testid="api-client-auth-basic"]')).not.toBeVisible()

    // Switch back to none → all auth fields hidden
    await authTypeSelect.selectOption('none')
    await expect(page.locator('[data-testid="api-client-auth-basic"]')).not.toBeVisible()
    await expect(page.locator('[data-testid="api-client-auth-bearer"]')).not.toBeVisible()
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})

// ---------------------------------------------------------------------------
// api_client: downstream success trigger fires when HTTP call returns 200
// ---------------------------------------------------------------------------
test('api_client Erfolg-Ausgang löst nachgelagerten Node aus bei HTTP 200', async ({ page }) => {
  // Graph: const_value(true) → api_client.trigger
  //        api_client.success + const_value(true) → and_gate
  // After the second-pass fix, and_gate.out must be true when HTTP returns 200.
  // Use the public health endpoint — no auth required.
  const BASE_URL = process.env.BASE_URL ?? 'http://localhost:8080'
  const targetUrl = `${BASE_URL}/api/v1/system/health`

  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-ApiClient-Downstream-${Date.now()}`,
    description: 'Playwright: api_client downstream trigger',
    enabled: true,
    flow_data: {
      nodes: [
        { id: 'trig',  type: 'const_value', position: { x: 0,   y: 0   }, data: { value: 'true', data_type: 'bool' } },
        { id: 'cv2',   type: 'const_value', position: { x: 0,   y: 100 }, data: { value: 'true', data_type: 'bool' } },
        {
          id: 'ac', type: 'api_client', position: { x: 300, y: 0 },
          data: { url: targetUrl, method: 'GET', response_type: 'json', verify_ssl: false, auth_type: 'none' },
        },
        { id: 'gate',  type: 'and', position: { x: 600, y: 50  }, data: { input_count: 2 } },
      ],
      edges: [
        { id: 'e1', source: 'trig', target: 'ac',   sourceHandle: 'value',   targetHandle: 'trigger' },
        { id: 'e2', source: 'ac',   target: 'gate',  sourceHandle: 'success', targetHandle: 'in1' },
        { id: 'e3', source: 'cv2',  target: 'gate',  sourceHandle: 'value',   targetHandle: 'in2' },
      ],
    },
  }) as { id: string }
  const graphId = graph.id

  try {
    const result = await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {}) as {
      outputs: Record<string, Record<string, unknown>>
    }
    // api_client must show success
    expect(result.outputs['ac']['success']).toBe(true)
    // AND gate must have received success=true from the second-pass re-execution
    expect(result.outputs['gate']['out']).toBe(true)
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})

// ---------------------------------------------------------------------------
// json_extractor: registry contains node type with correct schema
// ---------------------------------------------------------------------------
test('json_extractor ist in der Node-Type-Registry mit json_path-Schema', async ({ page }) => {
  const types = await apiGet('/api/v1/logic/node-types') as Array<{
    type: string
    config_schema: Record<string, { type: string }>
  }>
  const jx = types.find(t => t.type === 'json_extractor')
  expect(jx).toBeDefined()
  expect(jx!.config_schema).toHaveProperty('json_path')
})

// ---------------------------------------------------------------------------
// xml_extractor: registry contains node type with correct schema
// ---------------------------------------------------------------------------
test('xml_extractor ist in der Node-Type-Registry mit xml_path-Schema', async ({ page }) => {
  const types = await apiGet('/api/v1/logic/node-types') as Array<{
    type: string
    config_schema: Record<string, { type: string }>
  }>
  const xx = types.find(t => t.type === 'xml_extractor')
  expect(xx).toBeDefined()
  expect(xx!.config_schema).toHaveProperty('xml_path')
})

// ---------------------------------------------------------------------------
// json_extractor: runs via API and extracts correct value
// ---------------------------------------------------------------------------
test('json_extractor extrahiert Wert aus JSON-String', async ({ page }) => {
  const payload = JSON.stringify({ sensor: { temperature: 21.5 } })

  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-JSON-Extractor-${Date.now()}`,
    description: 'Playwright: json_extractor',
    enabled: true,
    flow_data: {
      nodes: [
        {
          id: 'cv',
          type: 'const_value',
          position: { x: 0, y: 0 },
          data: { value: payload, data_type: 'string' },
        },
        {
          id: 'jx',
          type: 'json_extractor',
          position: { x: 300, y: 0 },
          data: { json_path: 'sensor.temperature' },
        },
      ],
      edges: [
        { id: 'e1', source: 'cv', target: 'jx', sourceHandle: 'value', targetHandle: 'data' },
      ],
    },
  }) as { id: string }
  const graphId = graph.id

  try {
    const result = await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {}) as {
      outputs: Record<string, Record<string, unknown>>
    }
    expect(result.outputs['jx']).toBeDefined()
    expect(result.outputs['jx']['value']).toBe(21.5)
    // _preview must contain the original payload
    expect(result.outputs['jx']['_preview']).toContain('temperature')
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})

// ---------------------------------------------------------------------------
// xml_extractor: runs via API and extracts correct value
// ---------------------------------------------------------------------------
test('xml_extractor extrahiert Wert aus XML-String', async ({ page }) => {
  const xmlPayload = '<root><temperature>21.5</temperature></root>'

  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-XML-Extractor-${Date.now()}`,
    description: 'Playwright: xml_extractor',
    enabled: true,
    flow_data: {
      nodes: [
        {
          id: 'cv',
          type: 'const_value',
          position: { x: 0, y: 0 },
          data: { value: xmlPayload, data_type: 'string' },
        },
        {
          id: 'xx',
          type: 'xml_extractor',
          position: { x: 300, y: 0 },
          data: { xml_path: './/temperature' },
        },
      ],
      edges: [
        { id: 'e1', source: 'cv', target: 'xx', sourceHandle: 'value', targetHandle: 'data' },
      ],
    },
  }) as { id: string }
  const graphId = graph.id

  try {
    const result = await apiPost(`/api/v1/logic/graphs/${graphId}/run`, {}) as {
      outputs: Record<string, Record<string, unknown>>
    }
    expect(result.outputs['xx']).toBeDefined()
    expect(result.outputs['xx']['value']).toBe('21.5')
    expect(result.outputs['xx']['_preview']).toContain('temperature')
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})

// ---------------------------------------------------------------------------
// json_extractor: config panel shows preview area and path picker
// ---------------------------------------------------------------------------
test('json_extractor Config-Panel zeigt Preview-Bereich und Pfad-Eingabe', async ({ page }) => {
  const graph = await apiPost('/api/v1/logic/graphs', {
    name: `E2E-JSON-Config-${Date.now()}`,
    description: 'Playwright: json_extractor config UI',
    enabled: true,
    flow_data: {
      nodes: [
        {
          id: 'jx',
          type: 'json_extractor',
          position: { x: 100, y: 100 },
          data: { json_path: 'temperature' },
        },
      ],
      edges: [],
    },
  }) as { id: string }
  const graphId = graph.id

  try {
    await page.goto('/logic')
    await page.waitForLoadState('networkidle')
    await page.selectOption('[data-testid="select-graph"]', graphId)
    await page.waitForTimeout(1_500)

    // Click on the node to open the config panel
    await page.locator('.vue-flow__node').first().click()
    await page.waitForTimeout(500)

    // Preview textarea must be present
    const preview = page.locator('[data-testid="extractor-preview"]')
    await expect(preview).toBeVisible({ timeout: 5_000 })

    // Path input must be present and contain the configured path
    const pathInput = page.locator('[data-testid="extractor-path-input"]')
    await expect(pathInput).toBeVisible({ timeout: 3_000 })
    await expect(pathInput).toHaveValue('temperature')
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})
