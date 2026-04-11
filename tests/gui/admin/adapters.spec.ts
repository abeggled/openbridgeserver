/**
 * Playwright E2E-Tests für den Home Assistant Adapter (Issue #9)
 *
 * Testet:
 *  1. HOME_ASSISTANT Adapter-Typ ist registriert und erscheint in der API
 *  2. HA-Instanz anlegen via GUI und in der Adapter-Liste sehen
 *  3. HA-Instanz via API anlegen, dann über GUI löschen
 *  4. Binding-Config: entity_id-Feld im Formular sichtbar und speicherbar
 */

import { test, expect } from '@playwright/test'
import { apiPost, apiDelete, apiGet } from '../helpers'


// ---------------------------------------------------------------------------
// Test 1: HOME_ASSISTANT ist als Adapter-Typ registriert
// ---------------------------------------------------------------------------

test('HOME_ASSISTANT Adapter-Typ ist registriert', async () => {
  const types = await apiGet('/api/v1/adapters') as Array<{ adapter_type: string }>
  const typeNames = types.map((t) => t.adapter_type)
  expect(typeNames).toContain('HOME_ASSISTANT')
})


// ---------------------------------------------------------------------------
// Test 2: HOME_ASSISTANT Adapter-Schema enthält die erwarteten Felder
// ---------------------------------------------------------------------------

test('HOME_ASSISTANT Adapter-Schema enthält host, port, token, ssl', async () => {
  const schema = await apiGet('/api/v1/adapters/HOME_ASSISTANT/schema') as {
    properties: Record<string, unknown>
  }
  const props = Object.keys(schema.properties ?? {})
  expect(props).toContain('host')
  expect(props).toContain('port')
  expect(props).toContain('token')
  expect(props).toContain('ssl')
})


// ---------------------------------------------------------------------------
// Test 3: HOME_ASSISTANT Binding-Schema enthält entity_id und optionale Felder
// ---------------------------------------------------------------------------

test('HOME_ASSISTANT Binding-Schema enthält entity_id und attribute', async () => {
  const schema = await apiGet('/api/v1/adapters/HOME_ASSISTANT/binding-schema') as {
    required: string[]
    properties: Record<string, unknown>
  }
  const required = schema.required ?? []
  const props = Object.keys(schema.properties ?? {})

  expect(required).toContain('entity_id')
  expect(props).toContain('entity_id')
  expect(props).toContain('attribute')
  expect(props).toContain('service_domain')
  expect(props).toContain('service_name')
  expect(props).toContain('service_data_key')
})


// ---------------------------------------------------------------------------
// Test 4: HA-Instanz via API anlegen, dann in der GUI sehen
// ---------------------------------------------------------------------------

test('HA-Instanz anlegen und in Adapter-Liste sehen', async ({ page }) => {
  const name = `E2E-HA-${Date.now()}`

  const created = await apiPost('/api/v1/adapters/instances', {
    adapter_type: 'HOME_ASSISTANT',
    name,
    config: {
      host: '192.168.1.100',
      port: 8123,
      token: 'test-token',
      ssl: false,
    },
    enabled: false,  // disabled so it won't try to connect
  }) as { id: string }
  const instanceId = created.id

  try {
    await page.goto('/adapters')
    await page.waitForLoadState('networkidle')

    // Instance name must appear on the adapters page
    await expect(page.getByText(name)).toBeVisible({ timeout: 8_000 })
  } finally {
    await apiDelete(`/api/v1/adapters/instances/${instanceId}`)
  }
})


// ---------------------------------------------------------------------------
// Test 5: HA-Instanz via GUI anlegen (über den "Neue Instanz"-Button)
// ---------------------------------------------------------------------------

test('HA-Instanz via GUI anlegen', async ({ page }) => {
  const name = `E2E-HA-GUI-${Date.now()}`
  let instanceId: string | null = null

  try {
    await page.goto('/adapters')
    await page.waitForLoadState('networkidle')

    // Open "New Instance" form
    await page.click('[data-testid="btn-new-instance"]')
    await expect(page.locator('[data-testid="select-adapter-type"]')).toBeVisible({ timeout: 5_000 })

    // Select adapter type — triggers schema load
    await page.selectOption('[data-testid="select-adapter-type"]', 'HOME_ASSISTANT')

    // Wait for SchemaForm fields to appear (schema is loaded async)
    await expect(page.locator('[data-testid="config-field-host"]')).toBeVisible({ timeout: 5_000 })

    // Fill adapter name
    await page.fill('[data-testid="input-instance-name"]', name)

    // Fill connection config via SchemaForm fields
    await page.fill('[data-testid="config-field-host"]', '192.168.1.200')
    // port is integer → use .fill on the number input
    await page.locator('[data-testid="config-field-port"]').fill('8123')
    await page.fill('[data-testid="config-field-token"]', 'test-only-token')
    // ssl is a checkbox — leave unchecked (default false)

    // Save
    await page.click('[data-testid="btn-save-instance"]')

    // New instance card must appear in the list
    await expect(page.getByText(name)).toBeVisible({ timeout: 8_000 })

    // Find the created instance ID for cleanup
    const instances = await apiGet('/api/v1/adapters/instances') as Array<{ id: string; name: string }>
    const found = instances.find((i) => i.name === name)
    if (found) instanceId = found.id
  } finally {
    if (instanceId) {
      await apiDelete(`/api/v1/adapters/instances/${instanceId}`)
    }
  }
})


// ---------------------------------------------------------------------------
// Test 6: HA-Instanz via GUI löschen
// ---------------------------------------------------------------------------

test('HA-Instanz via GUI löschen', async ({ page }) => {
  const name = `E2E-HA-Del-${Date.now()}`

  const created = await apiPost('/api/v1/adapters/instances', {
    adapter_type: 'HOME_ASSISTANT',
    name,
    config: { host: '127.0.0.1', port: 8123, token: 'tok', ssl: false },
    enabled: false,
  }) as { id: string }
  const instanceId = created.id

  try {
    await page.goto('/adapters')
    await page.waitForLoadState('networkidle')

    // Locate the instance row (data-testid added to AdaptersView)
    const row = page.locator(`[data-testid="adapter-row-${instanceId}"]`)
    await expect(row).toBeVisible({ timeout: 8_000 })

    // The delete button is only visible when the card is expanded → expand first
    await row.locator(`[data-testid="btn-expand-${instanceId}"]`).click()

    // Wait for the delete button to appear
    const deleteBtn = row.locator('[data-testid="btn-delete-instance"]')
    await expect(deleteBtn).toBeVisible({ timeout: 3_000 })
    await deleteBtn.click()

    // ConfirmDialog (data-testid="btn-confirm" exists in ConfirmDialog.vue)
    await page.click('[data-testid="btn-confirm"]')

    // Row must disappear after deletion
    await expect(row).not.toBeVisible({ timeout: 5_000 })
  } finally {
    // Best-effort cleanup (no-op if already deleted via GUI)
    await apiDelete(`/api/v1/adapters/instances/${instanceId}`)
  }
})


// ---------------------------------------------------------------------------
// Test 7: HA Binding mit entity_id anlegen (via API + check Binding-Schema)
// ---------------------------------------------------------------------------

test('HA Binding mit entity_id anlegen', async () => {
  // Create a DataPoint
  const dp = await apiPost('/api/v1/datapoints', {
    name: `E2E-HA-DP-${Date.now()}`,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }

  // Create a HA adapter instance (disabled)
  const instance = await apiPost('/api/v1/adapters/instances', {
    adapter_type: 'HOME_ASSISTANT',
    name: `E2E-HA-Bind-${Date.now()}`,
    config: { host: '127.0.0.1', port: 8123, token: 'tok', ssl: false },
    enabled: false,
  }) as { id: string }

  try {
    // Create a binding via API
    const binding = await apiPost(`/api/v1/datapoints/${dp.id}/bindings`, {
      adapter_type: 'HOME_ASSISTANT',
      adapter_instance_id: instance.id,
      direction: 'SOURCE',
      config: {
        entity_id: 'sensor.temperature',
        attribute: null,
      },
      enabled: true,
    }) as { id: string; config: Record<string, unknown> }

    expect(binding.config.entity_id).toBe('sensor.temperature')
    expect(binding.config.attribute).toBeNull()

    // Cleanup binding
    await apiDelete(`/api/v1/datapoints/${dp.id}/bindings/${binding.id}`)
  } finally {
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
    await apiDelete(`/api/v1/adapters/instances/${instance.id}`)
  }
})
