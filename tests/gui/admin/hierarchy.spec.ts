import { test, expect } from '@playwright/test'
import { apiPost, apiDelete } from '../helpers'

// ---------------------------------------------------------------------------
// Issue #467 — HierarchyManager Edit-Modal: konkrete Beispiele beim Umbenennen
// ---------------------------------------------------------------------------
//
// Setup: legt einen Tree mit zwei Ebenen an, öffnet das Edit-Modal und prüft,
// dass die Optionen der Anzeigestart-Ebene den echten Baum-/Knotennamen zeigen.

test('Edit-Modal zeigt konkrete Beispiele aus dem aktuellen Baum', async ({ page }) => {
  const treeName = `e2e-467-${Date.now()}`
  const rootName = 'EG Test'
  const childName = 'Wohnzimmer Test'

  // 1) Tree + zweistufige Hierarchie via API anlegen
  const tree = (await apiPost('/api/v1/hierarchy/trees', { name: treeName })) as { id: number }
  const root = (await apiPost('/api/v1/hierarchy/nodes', {
    tree_id: tree.id, parent_id: null, name: rootName,
  })) as { id: number }
  await apiPost('/api/v1/hierarchy/nodes', {
    tree_id: tree.id, parent_id: root.id, name: childName,
  })

  try {
    // 2) Settings → Hierarchie-Tab
    await page.goto('/settings')
    await page.click('button:has-text("Hierarchie")')
    await expect(page.locator('[data-testid="hierarchy-tab"]')).toBeVisible({ timeout: 5_000 })

    // 3) Edit-Pencil des neuen Baums klicken
    const treeCard = page.locator(`[data-testid="tree-${tree.id}"]`)
    await expect(treeCard).toBeVisible()
    await treeCard.locator(`[data-testid="btn-edit-tree-${tree.id}"]`).click()

    // 4) Select-Optionen enthalten echte Namen
    const select = page.locator('[data-testid="select-display-depth"]')
    await expect(select).toBeVisible()
    const optionTexts = await select.locator('option').allTextContents()

    expect(optionTexts[0]).toContain(treeName)            // "0 — <treeName> (Hierarchiename)"
    expect(optionTexts[0]).toContain('Hierarchiename')
    expect(optionTexts[1]).toContain(rootName)             // "1 — Erste Ebene (nur \"EG Test\")"
    expect(optionTexts[1]).toContain('nur')                // genau ein Wurzelknoten → distinct=1
    expect(optionTexts[2]).toContain(childName)            // "2 — Zweite Ebene (nur \"Wohnzimmer Test\")"
    // Ebene 3 + 4 sind disabled (Hierarchie hat nur 2 Ebenen)
    const disabled3 = await select.locator('option').nth(3).getAttribute('disabled')
    expect(disabled3).not.toBeNull()

    // 5) Name-Input hat im Edit-Modus keinen generischen Placeholder
    const nameInput = page.locator('input.input').first()
    await expect(nameInput).toHaveValue(treeName)
    const placeholder = await nameInput.getAttribute('placeholder')
    expect(placeholder ?? '').toBe('')
  } finally {
    // 6) Cleanup
    await apiDelete(`/api/v1/hierarchy/trees/${tree.id}`)
  }
})
