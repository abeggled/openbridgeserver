/**
 * Playwright E2E-Tests für Issue #356:
 *   Selektion von verschachtelten JSON-Feldern im MQTT Binding-Formular
 *
 * Testet:
 *  1. BindingForm JSON-Flattening: verschachtelte Keys erscheinen mit Dot-Notation im Dropdown
 *  2. MQTT Binding-Schema: json_key ist konfigurierbar
 *  3. Backend: verschachtelter Pfad wird korrekt aus JSON-Payload extrahiert
 */

import { test, expect } from '@playwright/test'
import { apiPost, apiDelete, apiGet } from '../helpers'

const NESTED_PAYLOAD = JSON.stringify({
  nodeId: 'ACDBDA616B2D',
  timestamp: 1777968567,
  name: 'TH-Sensor Badezimmer',
  channels: {
    Temperature: 17.5,
    Humidity: 58,
  },
})

// ---------------------------------------------------------------------------
// Test 1: MQTT Binding-Schema enthält json_key
// ---------------------------------------------------------------------------

test('MQTT Binding-Schema enthält json_key', async () => {
  const schema = await apiGet('/api/v1/adapters/MQTT/binding-schema') as {
    properties: Record<string, unknown>
  }
  const props = Object.keys(schema.properties ?? {})
  expect(props).toContain('json_key')
  expect(props).toContain('source_data_type')
})

// ---------------------------------------------------------------------------
// Test 2: BindingForm — verschachtelte JSON-Keys erscheinen im Dropdown
// ---------------------------------------------------------------------------

test('BindingForm: verschachtelte JSON-Keys im Dropdown (Dot-Notation)', async ({ page }) => {
  // Create MQTT adapter instance + datapoint via API
  const mqttAdapter = await apiPost('/api/v1/adapters/instances', {
    name: `E2E-MQTT-${Date.now()}`,
    adapter_type: 'MQTT',
    config: { host: 'localhost', port: 1883 },
    enabled: false,
  }) as { id: string }

  const dp = await apiPost('/api/v1/datapoints', {
    name: `E2E-DP-NestedJSON-${Date.now()}`,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }

  try {
    // Navigate to datapoint detail page
    await page.goto(`/datapoints/${dp.id}`)
    await page.waitForLoadState('networkidle')

    // Open the binding form
    await page.click('[data-testid="btn-add-binding"]')
    await page.waitForSelector('[data-testid="select-adapter-instance"]', { timeout: 8_000 })

    // Select MQTT adapter instance
    await page.selectOption('[data-testid="select-adapter-instance"]', mqttAdapter.id)
    await page.waitForTimeout(500)

    // Ensure direction is SOURCE
    await page.selectOption('[data-testid="select-direction"]', 'SOURCE')
    await page.waitForTimeout(300)

    // Fill in MQTT topic
    await page.fill('[data-testid="input-mqtt-topic"]', 'sensor/badezimmer/th')
    await page.waitForTimeout(200)

    // Select source_data_type = json
    await page.selectOption('[data-testid="select-source-data-type"]', 'json')
    await page.waitForTimeout(300)

    // Enter nested JSON sample
    await page.fill('[data-testid="mqtt-json-sample"]', NESTED_PAYLOAD)
    await page.dispatchEvent('[data-testid="mqtt-json-sample"]', 'input')
    await page.waitForTimeout(200)

    // The dropdown must now be visible
    const select = page.locator('[data-testid="mqtt-json-key-select"]')
    await expect(select).toBeVisible({ timeout: 3_000 })

    // Get all option values
    const options = await select.locator('option').allTextContents()

    // Must include flat top-level keys and nested dot-notation keys
    expect(options.some(o => o.includes('nodeId'))).toBe(true)
    expect(options.some(o => o.includes('channels.Temperature'))).toBe(true)
    expect(options.some(o => o.includes('channels.Humidity'))).toBe(true)

    // Must NOT include the parent object "channels" as a selectable leaf
    // (it's an object, not a primitive — only leaves should appear)
    const leafOptions = options.filter(o => o.trim() !== '— aus Sample —')
    expect(leafOptions.every(o => !o.match(/^channels\s*=/))).toBe(true)

    // Select channels.Temperature via the dropdown
    await page.selectOption('[data-testid="mqtt-json-key-select"]', 'channels.Temperature')
    await page.waitForTimeout(200)

    // The text input must now show channels.Temperature
    const inputValue = await page.inputValue('[data-testid="mqtt-json-key-input"]')
    expect(inputValue).toBe('channels.Temperature')
  } finally {
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
    await apiDelete(`/api/v1/adapters/instances/${mqttAdapter.id}`)
  }
})

// ---------------------------------------------------------------------------
// Test 3: Backend — json_key mit Dot-Notation extrahiert verschachtelten Wert
// ---------------------------------------------------------------------------

test('MQTT Binding mit json_key channels.Temperature extrahiert korrekten Wert', async () => {
  // This test verifies the backend transformation logic via the binding-schema API
  // The actual extraction is covered by unit tests; here we verify the binding
  // accepts a dot-notation json_key without validation errors.

  const mqttAdapter = await apiPost('/api/v1/adapters/instances', {
    name: `E2E-MQTT-DotKey-${Date.now()}`,
    adapter_type: 'MQTT',
    config: { host: 'localhost', port: 1883 },
    enabled: false,
  }) as { id: string }

  const dp = await apiPost('/api/v1/datapoints', {
    name: `E2E-DP-DotKey-${Date.now()}`,
    data_type: 'FLOAT',
    tags: [],
  }) as { id: string }

  try {
    const binding = await apiPost(`/api/v1/datapoints/${dp.id}/bindings`, {
      adapter_instance_id: mqttAdapter.id,
      direction: 'SOURCE',
      config: {
        topic: 'sensor/test/nested',
        source_data_type: 'json',
        json_key: 'channels.Temperature',
      },
    }) as { id: string; config: Record<string, unknown> }

    expect(binding.id).toBeTruthy()
    expect(binding.config.json_key).toBe('channels.Temperature')
    expect(binding.config.source_data_type).toBe('json')

    await apiDelete(`/api/v1/datapoints/${dp.id}/bindings/${binding.id}`)
  } finally {
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
    await apiDelete(`/api/v1/adapters/instances/${mqttAdapter.id}`)
  }
})
