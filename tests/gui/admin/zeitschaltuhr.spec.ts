/**
 * Playwright E2E-Tests für den Zeitschaltuhr-Adapter (Issue #302)
 *
 * Testet:
 *  1. ZEITSCHALTUHR ist als Adapter-Typ registriert
 *  2. Binding-Schema enthält alle neuen Felder (Feiertagsschaltuhr + Datum-Fenster)
 *  3. Tagesschaltuhr-Binding anlegen und Config prüfen
 *  4. Feiertagsschaltuhr-Binding (timer_type='holiday') anlegen und Config prüfen
 *  5. Datum-Fenster-Binding (date_window_enabled) anlegen und Ausdrücke prüfen
 *  6. /holidays Endpoint liefert korrekte Liste für eine ZSU-Instanz
 *  7. ZSU-Instanz via GUI anlegen
 */

import { test, expect } from '@playwright/test'
import { apiPost, apiDelete, apiGet } from '../helpers'

// ---------------------------------------------------------------------------
// Hilfsfunktionen
// ---------------------------------------------------------------------------

async function createZsuInstance(overrideConfig?: Record<string, unknown>) {
  return await apiPost('/api/v1/adapters/instances', {
    adapter_type: 'ZEITSCHALTUHR',
    name: `E2E-ZSU-${Date.now()}`,
    config: {
      latitude: 47.5,
      longitude: 8.0,
      holiday_country: 'CH',
      holiday_subdivision: '',
      custom_holidays: ['01-01:Neujahr', '08-01:Nationalfeiertag', '12-25:Weihnachten'],
      ...overrideConfig,
    },
    enabled: false,
  }) as { id: string }
}

async function createDp(suffix: string) {
  return await apiPost('/api/v1/datapoints', {
    name: `E2E-ZSU-DP-${suffix}-${Date.now()}`,
    data_type: 'BOOLEAN',
    tags: [],
  }) as { id: string }
}

const BASE_DAILY_CONFIG = {
  timer_type: 'daily',
  weekdays: [0, 1, 2, 3, 4, 5, 6],
  time_ref: 'absolute',
  hour: 8,
  minute: 0,
  offset_minutes: 0,
  every_hour: false,
  every_minute: false,
  holiday_mode: 'ignore',
  vacation_mode: 'ignore',
  value: '1',
}

// ---------------------------------------------------------------------------
// Test 1: Adapter-Typ registriert
// ---------------------------------------------------------------------------

test('ZEITSCHALTUHR Adapter-Typ ist registriert', async () => {
  const types = await apiGet('/api/v1/adapters') as Array<{ adapter_type: string }>
  expect(types.map((t) => t.adapter_type)).toContain('ZEITSCHALTUHR')
})

// ---------------------------------------------------------------------------
// Test 2: Adapter-Schema enthält expected Felder
// ---------------------------------------------------------------------------

test('ZEITSCHALTUHR Adapter-Schema enthält holiday_country und custom_holidays', async () => {
  const schema = await apiGet('/api/v1/adapters/ZEITSCHALTUHR/schema') as {
    properties: Record<string, unknown>
  }
  const props = Object.keys(schema.properties ?? {})
  expect(props).toContain('holiday_country')
  expect(props).toContain('holiday_subdivision')
  expect(props).toContain('custom_holidays')
  expect(props).toContain('latitude')
  expect(props).toContain('longitude')
})

// ---------------------------------------------------------------------------
// Test 3: Binding-Schema enthält alle neuen Felder
// ---------------------------------------------------------------------------

test('ZEITSCHALTUHR Binding-Schema enthält timer_type, selected_holidays und Datum-Fenster-Felder', async () => {
  const schema = await apiGet('/api/v1/adapters/ZEITSCHALTUHR/binding-schema') as {
    properties: Record<string, unknown>
  }
  const props = Object.keys(schema.properties ?? {})

  // Basis-Felder
  expect(props).toContain('timer_type')
  expect(props).toContain('weekdays')
  expect(props).toContain('time_ref')
  expect(props).toContain('holiday_mode')
  expect(props).toContain('vacation_mode')
  expect(props).toContain('value')

  // Feiertagsschaltuhr
  expect(props).toContain('selected_holidays')

  // Datum-Fenster
  expect(props).toContain('date_window_enabled')
  expect(props).toContain('date_window_from')
  expect(props).toContain('date_window_to')
})

// ---------------------------------------------------------------------------
// Test 4: Tagesschaltuhr-Binding anlegen und Config prüfen
// ---------------------------------------------------------------------------

test('Tagesschaltuhr-Binding per API anlegen und Config auslesen', async () => {
  const dp       = await createDp('daily')
  const instance = await createZsuInstance()

  try {
    const binding = await apiPost(`/api/v1/datapoints/${dp.id}/bindings`, {
      adapter_instance_id: instance.id,
      direction: 'SOURCE',
      config: BASE_DAILY_CONFIG,
      enabled: true,
    }) as { id: string; config: Record<string, unknown> }

    expect(binding.config.timer_type).toBe('daily')
    expect(binding.config.hour).toBe(8)
    expect(binding.config.minute).toBe(0)
    expect(binding.config.value).toBe('1')
    expect(binding.config.date_window_enabled).toBeFalsy()

    await apiDelete(`/api/v1/datapoints/${dp.id}/bindings/${binding.id}`)
  } finally {
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
    await apiDelete(`/api/v1/adapters/instances/${instance.id}`)
  }
})

// ---------------------------------------------------------------------------
// Test 5: Feiertagsschaltuhr-Binding (timer_type='holiday')
// ---------------------------------------------------------------------------

test('Feiertagsschaltuhr-Binding anlegen und selected_holidays prüfen', async () => {
  const dp       = await createDp('holiday')
  const instance = await createZsuInstance()

  try {
    const binding = await apiPost(`/api/v1/datapoints/${dp.id}/bindings`, {
      adapter_instance_id: instance.id,
      direction: 'SOURCE',
      config: {
        ...BASE_DAILY_CONFIG,
        timer_type: 'holiday',
        selected_holidays: ['Neujahr', 'Weihnachten'],
        vacation_mode: 'skip',
      },
      enabled: true,
    }) as { id: string; config: Record<string, unknown> }

    expect(binding.config.timer_type).toBe('holiday')
    expect(binding.config.selected_holidays).toEqual(['Neujahr', 'Weihnachten'])
    expect(binding.config.vacation_mode).toBe('skip')

    await apiDelete(`/api/v1/datapoints/${dp.id}/bindings/${binding.id}`)
  } finally {
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
    await apiDelete(`/api/v1/adapters/instances/${instance.id}`)
  }
})

test('Feiertagsschaltuhr mit leerem selected_holidays (alle Feiertage) anlegen', async () => {
  const dp       = await createDp('holiday-all')
  const instance = await createZsuInstance()

  try {
    const binding = await apiPost(`/api/v1/datapoints/${dp.id}/bindings`, {
      adapter_instance_id: instance.id,
      direction: 'SOURCE',
      config: {
        ...BASE_DAILY_CONFIG,
        timer_type: 'holiday',
        selected_holidays: [],
      },
      enabled: true,
    }) as { id: string; config: Record<string, unknown> }

    expect(binding.config.timer_type).toBe('holiday')
    expect(binding.config.selected_holidays).toEqual([])

    await apiDelete(`/api/v1/datapoints/${dp.id}/bindings/${binding.id}`)
  } finally {
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
    await apiDelete(`/api/v1/adapters/instances/${instance.id}`)
  }
})

// ---------------------------------------------------------------------------
// Test 6: Datum-Fenster-Binding (date_window_enabled)
// ---------------------------------------------------------------------------

test('Datum-Fenster easter-7 bis easter+7 anlegen und Ausdrücke prüfen', async () => {
  const dp       = await createDp('datwin-easter')
  const instance = await createZsuInstance()

  try {
    const binding = await apiPost(`/api/v1/datapoints/${dp.id}/bindings`, {
      adapter_instance_id: instance.id,
      direction: 'SOURCE',
      config: {
        ...BASE_DAILY_CONFIG,
        date_window_enabled: true,
        date_window_from: 'easter-7',
        date_window_to: 'easter+7',
      },
      enabled: true,
    }) as { id: string; config: Record<string, unknown> }

    expect(binding.config.date_window_enabled).toBe(true)
    expect(binding.config.date_window_from).toBe('easter-7')
    expect(binding.config.date_window_to).toBe('easter+7')

    await apiDelete(`/api/v1/datapoints/${dp.id}/bindings/${binding.id}`)
  } finally {
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
    await apiDelete(`/api/v1/adapters/instances/${instance.id}`)
  }
})

test('Datum-Fenster advent+0 bis 01-06 (Advent→Epiphanias) anlegen', async () => {
  const dp       = await createDp('datwin-advent')
  const instance = await createZsuInstance()

  try {
    const binding = await apiPost(`/api/v1/datapoints/${dp.id}/bindings`, {
      adapter_instance_id: instance.id,
      direction: 'SOURCE',
      config: {
        ...BASE_DAILY_CONFIG,
        date_window_enabled: true,
        date_window_from: 'advent+0',
        date_window_to: '01-06',
      },
      enabled: true,
    }) as { id: string; config: Record<string, unknown> }

    expect(binding.config.date_window_enabled).toBe(true)
    expect(binding.config.date_window_from).toBe('advent+0')
    expect(binding.config.date_window_to).toBe('01-06')

    await apiDelete(`/api/v1/datapoints/${dp.id}/bindings/${binding.id}`)
  } finally {
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
    await apiDelete(`/api/v1/adapters/instances/${instance.id}`)
  }
})

test('Datum-Fenster mit fixem Datum 05-01 bis 08-31 anlegen', async () => {
  const dp       = await createDp('datwin-fixed')
  const instance = await createZsuInstance()

  try {
    const binding = await apiPost(`/api/v1/datapoints/${dp.id}/bindings`, {
      adapter_instance_id: instance.id,
      direction: 'SOURCE',
      config: {
        ...BASE_DAILY_CONFIG,
        date_window_enabled: true,
        date_window_from: '05-01',
        date_window_to: '08-31',
      },
      enabled: true,
    }) as { id: string; config: Record<string, unknown> }

    expect(binding.config.date_window_enabled).toBe(true)
    expect(binding.config.date_window_from).toBe('05-01')
    expect(binding.config.date_window_to).toBe('08-31')

    await apiDelete(`/api/v1/datapoints/${dp.id}/bindings/${binding.id}`)
  } finally {
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
    await apiDelete(`/api/v1/adapters/instances/${instance.id}`)
  }
})

// ---------------------------------------------------------------------------
// Test 7: /holidays Endpoint
// ---------------------------------------------------------------------------

test('/holidays Endpoint liefert Neujahr und Nationalfeiertag für CH-Instanz', async () => {
  const instance = await createZsuInstance({
    holiday_country: 'CH',
    holiday_subdivision: '',
    custom_holidays: ['01-01:Neujahr', '08-01:Nationalfeiertag'],
  })

  try {
    const holidays = await apiGet(
      `/api/v1/adapters/instances/${instance.id}/holidays`,
    ) as Array<{ date: string; name: string }>

    expect(Array.isArray(holidays)).toBe(true)

    const names = holidays.map((h) => h.name)
    expect(names).toContain('Neujahr')
    expect(names).toContain('Nationalfeiertag')

    // Dates must be ISO format YYYY-MM-DD
    for (const h of holidays) {
      expect(h.date).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    }

    // Result must be sorted by date
    const dates = holidays.map((h) => h.date)
    expect(dates).toEqual([...dates].sort())
  } finally {
    await apiDelete(`/api/v1/adapters/instances/${instance.id}`)
  }
})

test('/holidays Endpoint mit year-Parameter liefert nur das angegebene Jahr', async () => {
  const instance = await createZsuInstance({
    custom_holidays: ['01-01:Neujahr', '12-25:Weihnachten'],
  })

  try {
    const holidays = await apiGet(
      `/api/v1/adapters/instances/${instance.id}/holidays?year=2026`,
    ) as Array<{ date: string; name: string }>

    expect(Array.isArray(holidays)).toBe(true)
    for (const h of holidays) {
      expect(h.date.startsWith('2026-')).toBe(true)
    }
  } finally {
    await apiDelete(`/api/v1/adapters/instances/${instance.id}`)
  }
})

// ---------------------------------------------------------------------------
// Test 8: ZSU-Instanz via GUI anlegen
// ---------------------------------------------------------------------------

test('ZSU-Instanz via GUI anlegen', async ({ page }) => {
  const name = `E2E-ZSU-GUI-${Date.now()}`
  let instanceId: string | null = null

  try {
    await page.goto('/adapters')
    await page.waitForLoadState('networkidle')

    await page.click('[data-testid="btn-new-instance"]')
    await expect(page.locator('[data-testid="select-adapter-type"]')).toBeVisible({ timeout: 5_000 })

    await page.selectOption('[data-testid="select-adapter-type"]', 'ZEITSCHALTUHR')

    // Wait for schema fields to appear
    await expect(page.locator('[data-testid="config-field-holiday_country"]')).toBeVisible({ timeout: 5_000 })

    await page.fill('[data-testid="input-instance-name"]', name)

    // Set holiday country to DE
    await page.fill('[data-testid="config-field-holiday_country"]', 'DE')

    await page.click('[data-testid="btn-save-instance"]')
    await expect(page.getByText(name)).toBeVisible({ timeout: 8_000 })

    const instances = await apiGet('/api/v1/adapters/instances') as Array<{ id: string; name: string }>
    const found = instances.find((i) => i.name === name)
    if (found) instanceId = found.id
  } finally {
    if (instanceId) await apiDelete(`/api/v1/adapters/instances/${instanceId}`)
  }
})
