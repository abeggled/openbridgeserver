/**
 * Playwright E2E-Tests — Logikblöcke kopieren/einfügen über Logikseiten hinweg (Issue #1084)
 *
 * Prüft: Mehrfachauswahl kopieren, Einfügen auf einer anderen Logikseite,
 * Erhalt der Block-Settings nach dem Einfügen.
 */

import { test, expect } from '@playwright/test'
import { apiPost, apiDelete, apiGet, openLogicGraph } from '../helpers'

interface FlowNode { id: string; type: string; position: { x: number; y: number }; data: Record<string, unknown> }
interface FlowEdge { id: string; source: string; target: string; sourceHandle: string | null; targetHandle: string | null }
interface Graph { id: string; name: string; flow_data: { nodes: FlowNode[]; edges: FlowEdge[] } }

async function createGraphViaApi(name: string, nodes: FlowNode[] = [], edges: FlowEdge[] = []): Promise<string> {
  const data = await apiPost('/api/v1/logic/graphs', {
    name,
    description: '',
    enabled: true,
    flow_data: { nodes, edges },
  }) as { id: string }
  return data.id
}

async function deleteGraphViaApi(id: string): Promise<void> {
  await apiDelete(`/api/v1/logic/graphs/${id}`)
}

async function gotoLogicWithGraph(page: any, graphId: string) {
  await page.goto('/logic')
  await page.waitForLoadState('networkidle')
  await openLogicGraph(page, graphId)
  await expect(page.locator('[data-testid="btn-copy-nodes"]')).toBeVisible({ timeout: 5_000 })
}

// Vue Flow's own click-vs-drag threshold makes a synthetic Ctrl/Cmd-click an
// unreliable way to multi-select in Playwright (a stationary click with a
// multi-select key held is not guaranteed to register as a click at all —
// see the d3-drag "eventEnd" threshold logic in @vue-flow/core). A real
// Shift-drag box-select is the mechanism users actually rely on and involves
// genuine pointer movement, so it reliably crosses those thresholds.
async function selectAllNodesViaBoxSelect(page: any) {
  const canvasBox = await page.locator('.logic-canvas').boundingBox()
  const nodeBoxes = await page.locator('.vue-flow__node').all()
  const rects = await Promise.all(nodeBoxes.map((n: any) => n.boundingBox()))

  const minX = Math.max(canvasBox.x + 5, Math.min(...rects.map((r: any) => r.x)) - 40)
  const minY = Math.max(canvasBox.y + 5, Math.min(...rects.map((r: any) => r.y)) - 40)
  const maxX = Math.min(canvasBox.x + canvasBox.width - 5, Math.max(...rects.map((r: any) => r.x + r.width)) + 40)
  const maxY = Math.min(canvasBox.y + canvasBox.height - 5, Math.max(...rects.map((r: any) => r.y + r.height)) + 40)

  await page.keyboard.down('Shift')
  await page.mouse.move(minX, minY)
  await page.mouse.down()
  await page.mouse.move(maxX, maxY, { steps: 10 })
  await page.mouse.up()
  await page.keyboard.up('Shift')
}

test('Logic: markierte Blöcke kopieren und auf einer anderen Seite einfügen', async ({ page }) => {
  const suffix = Date.now()
  const sourceName = `E2E-CopySrc-${suffix}`
  const targetName = `E2E-CopyDst-${suffix}`

  const sourceId = await createGraphViaApi(sourceName, [
    { id: 'n1', type: 'and', position: { x: 0, y: 0 }, data: { input_count: 3 } },
    { id: 'n2', type: 'or', position: { x: 200, y: 0 }, data: { input_count: 2 } },
  ], [
    { id: 'e1', source: 'n1', target: 'n2', sourceHandle: 'out', targetHandle: 'in1' },
  ])
  const targetId = await createGraphViaApi(targetName)

  try {
    await gotoLogicWithGraph(page, sourceId)
    await expect(page.locator('.vue-flow__node')).toHaveCount(2, { timeout: 5_000 })

    // Beide Blöcke per Shift-Rahmen markieren (wie im echten Editor)
    await selectAllNodesViaBoxSelect(page)

    await page.click('[data-testid="btn-copy-nodes"]')
    await expect(page.locator('.bg-green-500\\/10')).toBeVisible({ timeout: 8_000 })

    // Zur Zielseite wechseln
    await openLogicGraph(page, targetId)
    await expect(page.locator('.vue-flow__node')).toHaveCount(0, { timeout: 5_000 })

    await page.click('[data-testid="btn-paste-nodes"]')
    await expect(page.locator('.vue-flow__node')).toHaveCount(2, { timeout: 5_000 })

    await page.click('[data-testid="btn-save"]')
    // A generic ".bg-green-500/10" check can pass on the still-visible
    // paste-success toast from the earlier step, before the save request
    // actually completes — wait for the save-specific status text instead,
    // which only appears once saveGraph()'s await resolves.
    await expect(page.locator('[data-testid="status-msg"]')).toHaveText('Graph gespeichert', { timeout: 8_000 })

    const savedTarget = await apiGet(`/api/v1/logic/graphs/${targetId}`) as Graph
    expect(savedTarget.flow_data.nodes).toHaveLength(2)
    expect(savedTarget.flow_data.edges).toHaveLength(1)

    const pastedAnd = savedTarget.flow_data.nodes.find(n => n.type === 'and')
    expect(pastedAnd?.data).toMatchObject({ input_count: 3 })
    expect(pastedAnd?.id).not.toBe('n1')

    const [edge] = savedTarget.flow_data.edges
    const pastedIds = savedTarget.flow_data.nodes.map(n => n.id)
    expect(pastedIds).toContain(edge.source)
    expect(pastedIds).toContain(edge.target)
  } finally {
    await deleteGraphViaApi(sourceId)
    await deleteGraphViaApi(targetId)
  }
})
