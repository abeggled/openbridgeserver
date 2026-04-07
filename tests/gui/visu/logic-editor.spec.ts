import { test, expect } from '@playwright/test'
import { apiPost, apiDelete } from '../helpers'

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

    // 4. Wait for the canvas to render the node
    await expect(page.locator('[data-testid="debug-band"]').first()).toBeHidden({ timeout: 5_000 })

    // 5. Enable debug mode
    await page.click('[data-testid="btn-debug"]')

    // 6. Run the graph
    await page.click('[data-testid="btn-run"]')

    // 7. The debug-band must appear and show a value (not "—")
    const debugBand = page.locator('[data-testid="debug-band"]').first()
    await expect(debugBand).toBeVisible({ timeout: 3_000 })
    const text = await debugBand.textContent()
    expect(text?.trim()).not.toBe('—')
    expect(text?.trim()).not.toBe('')
  } finally {
    await apiDelete(`/api/v1/logic/graphs/${graphId}`)
  }
})
