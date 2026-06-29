import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  vi.doMock('@/api/client', () => ({
    dpApi:       { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    searchApi:   { search: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    securityApi: { checkUrlTarget: vi.fn(), addUrlTarget: vi.fn() },
    authApi:     { login: vi.fn(), me: vi.fn() },
  }))
})

afterEach(() => { vi.doUnmock('@/api/client') })

async function mountPanel(type, data, nodeOutputs = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  return mount(mod.default, {
    props: {
      node: { id: 'n1', type, data },
      nodeTypes: [{ type, label: type, description: '' }],
      nodeOutputs,
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

// ─── json_extractor — empty state ────────────────────────────────────────────

describe('NodeConfigPanel json_extractor — empty state', () => {
  it('shows the + button to add output paths', async () => {
    const w = await mountPanel('json_extractor', { json_paths: '[]' })
    await flushPromises()
    // The + button has title text "Ausgang hinzufügen"
    const addBtn = w.findAll('button').find(b => b.text() === '+')
    expect(addBtn).toBeTruthy()
    w.unmount()
  })

  it('shows empty-state hint text when no paths exist', async () => {
    const w = await mountPanel('json_extractor', { json_paths: '[]' })
    await flushPromises()
    expect(w.text()).toContain('Klicke')
    w.unmount()
  })
})

// ─── json_extractor — add / remove / update paths ─────────────────────────────

describe('NodeConfigPanel json_extractor — add path', () => {
  it('clicking + adds a new output row', async () => {
    const w = await mountPanel('json_extractor', { json_paths: '[]' })
    await flushPromises()

    const addBtn = w.findAll('button').find(b => b.text() === '+')
    await addBtn.trigger('click')
    await flushPromises()

    expect(w.find('[data-testid="extractor-path-input"]').exists()).toBe(true)
    w.unmount()
  })

  it('clicking + emits update with one path entry', async () => {
    const w = await mountPanel('json_extractor', { json_paths: '[]' })
    await flushPromises()

    await w.findAll('button').find(b => b.text() === '+').trigger('click')
    await flushPromises()

    const updates = w.emitted('update')
    expect(updates).toBeTruthy()
    const paths = JSON.parse(updates.at(-1)[0].json_paths)
    expect(paths).toHaveLength(1)
    expect(paths[0]).toHaveProperty('label')
    expect(paths[0]).toHaveProperty('path')
    w.unmount()
  })

  it('clicking + twice adds two output rows', async () => {
    const w = await mountPanel('json_extractor', { json_paths: '[]' })
    await flushPromises()

    const getAddBtn = () => w.findAll('button').find(b => b.text() === '+')
    await getAddBtn().trigger('click')
    await flushPromises()
    await getAddBtn().trigger('click')
    await flushPromises()

    expect(w.findAll('[data-testid="extractor-path-input"]')).toHaveLength(2)
    w.unmount()
  })
})

describe('NodeConfigPanel json_extractor — remove path', () => {
  it('clicking − removes the path row', async () => {
    const paths = JSON.stringify([
      { label: 'Wert 1', path: 'temperature' },
    ])
    const w = await mountPanel('json_extractor', { json_paths: paths })
    await flushPromises()

    expect(w.find('[data-testid="extractor-path-input"]').exists()).toBe(true)

    const removeBtn = w.findAll('button').find(b => b.text() === '−')
    await removeBtn.trigger('click')
    await flushPromises()

    const updates = w.emitted('update')
    const remaining = JSON.parse(updates.at(-1)[0].json_paths)
    expect(remaining).toHaveLength(0)
    w.unmount()
  })

  it('removing one of two paths keeps the other', async () => {
    const paths = JSON.stringify([
      { label: 'Temp', path: 'temp' },
      { label: 'Hum',  path: 'hum'  },
    ])
    const w = await mountPanel('json_extractor', { json_paths: paths })
    await flushPromises()

    const removeBtns = w.findAll('button').filter(b => b.text() === '−')
    await removeBtns[0].trigger('click')
    await flushPromises()

    const remaining = JSON.parse(w.emitted('update').at(-1)[0].json_paths)
    expect(remaining).toHaveLength(1)
    expect(remaining[0].label).toBe('Hum')
    w.unmount()
  })
})

describe('NodeConfigPanel json_extractor — update path label', () => {
  it('changing path label input emits update with new label', async () => {
    const paths = JSON.stringify([{ label: 'Wert 1', path: '' }])
    const w = await mountPanel('json_extractor', { json_paths: paths })
    await flushPromises()

    // Label input is the first flex-1 input inside extractor-output-row
    const row = w.find('.extractor-output-row')
    const labelInput = row.find('input.input.text-xs.flex-1')
    await labelInput.setValue('Temperatur')
    await labelInput.trigger('input')
    await flushPromises()

    const updated = JSON.parse(w.emitted('update').at(-1)[0].json_paths)
    expect(updated[0].label).toBe('Temperatur')
    w.unmount()
  })

  it('changing path input emits update with new path', async () => {
    const paths = JSON.stringify([{ label: 'Wert 1', path: '' }])
    const w = await mountPanel('json_extractor', { json_paths: paths })
    await flushPromises()

    const pathInput = w.find('[data-testid="extractor-path-input"]')
    await pathInput.setValue('sensors.temp')
    await pathInput.trigger('input')
    await flushPromises()

    const updated = JSON.parse(w.emitted('update').at(-1)[0].json_paths)
    expect(updated[0].path).toBe('sensors.temp')
    w.unmount()
  })
})

// ─── json_extractor — legacy migration ────────────────────────────────────────

describe('NodeConfigPanel json_extractor — legacy migration', () => {
  it('shows upgrade banner when json_path exists but json_paths is empty', async () => {
    const w = await mountPanel('json_extractor', { json_path: 'data.value', json_paths: '[]' })
    await flushPromises()
    expect(w.text()).toContain('Legacy-Konfiguration')
    expect(w.text()).toContain('data.value')
    w.unmount()
  })

  it('clicking upgrade button migrates to multi-path and emits update', async () => {
    const w = await mountPanel('json_extractor', { json_path: 'data.value', json_paths: '[]' })
    await flushPromises()

    const migrateBtn = w.findAll('button').find(b => b.text().includes('upgraden'))
    expect(migrateBtn).toBeTruthy()
    await migrateBtn.trigger('click')
    await flushPromises()

    const updates = w.emitted('update')
    const last = updates.at(-1)[0]
    expect(last.json_path).toBe('')
    const paths = JSON.parse(last.json_paths)
    expect(paths).toHaveLength(1)
    expect(paths[0].path).toBe('data.value')
    w.unmount()
  })
})

// ─── json_extractor — path picker from preview ────────────────────────────────

describe('NodeConfigPanel json_extractor — path picker from preview', () => {
  it('shows path picker dropdown when nodeOutputs contains preview JSON', async () => {
    const nodeOutputs = { n1: { _preview: '{"temperature": 22.5, "humidity": 60}' } }
    const paths = JSON.stringify([{ label: 'Wert 1', path: '' }])
    const w = await mountPanel('json_extractor', { json_paths: paths }, nodeOutputs)
    await flushPromises()

    const pathSelect = w.find('[data-testid="extractor-path-select"]')
    expect(pathSelect.exists()).toBe(true)
    const options = pathSelect.findAll('option')
    expect(options.some(o => o.element.value === 'temperature')).toBe(true)
    w.unmount()
  })
})

// ─── xml_extractor — basic add / remove ──────────────────────────────────────

describe('NodeConfigPanel xml_extractor — add path', () => {
  it('clicking + adds an output row and emits update', async () => {
    const w = await mountPanel('xml_extractor', { xml_paths: '[]' })
    await flushPromises()

    await w.findAll('button').find(b => b.text() === '+').trigger('click')
    await flushPromises()

    expect(w.find('[data-testid="extractor-path-input"]').exists()).toBe(true)
    const updates = w.emitted('update')
    const paths = JSON.parse(updates.at(-1)[0].xml_paths)
    expect(paths).toHaveLength(1)
    w.unmount()
  })
})

describe('NodeConfigPanel xml_extractor — remove path', () => {
  it('clicking − removes the path row', async () => {
    const paths = JSON.stringify([{ label: 'Wert 1', path: './/value' }])
    const w = await mountPanel('xml_extractor', { xml_paths: paths })
    await flushPromises()

    await w.findAll('button').find(b => b.text() === '−').trigger('click')
    await flushPromises()

    const remaining = JSON.parse(w.emitted('update').at(-1)[0].xml_paths)
    expect(remaining).toHaveLength(0)
    w.unmount()
  })
})

describe('NodeConfigPanel xml_extractor — legacy migration', () => {
  it('shows legacy banner when xml_path exists but xml_paths is empty', async () => {
    const w = await mountPanel('xml_extractor', { xml_path: './/temperature', xml_paths: '[]' })
    await flushPromises()
    expect(w.text()).toContain('.//temperature')
    w.unmount()
  })

  it('clicking upgrade migrates xml_path to xml_paths', async () => {
    const w = await mountPanel('xml_extractor', { xml_path: './/temperature', xml_paths: '[]' })
    await flushPromises()

    const migrateBtn = w.findAll('button').find(b => b.text().toLowerCase().includes('upgraden') || b.text().toLowerCase().includes('ausgänge'))
    expect(migrateBtn).toBeTruthy()
    await migrateBtn.trigger('click')
    await flushPromises()

    const last = w.emitted('update').at(-1)[0]
    expect(last.xml_path).toBe('')
    const paths = JSON.parse(last.xml_paths)
    expect(paths[0].path).toBe('.//temperature')
    w.unmount()
  })
})

// ─── json_extractor — path picker fills active row ────────────────────────────

describe('NodeConfigPanel json_extractor — path picker fills row', () => {
  it('selecting a path from the picker fills the active row path', async () => {
    const nodeOutputs = { n1: { _preview: '{"temperature": 22.5}' } }
    const paths = JSON.stringify([{ label: 'Wert 1', path: '' }])
    const w = await mountPanel('json_extractor', { json_paths: paths }, nodeOutputs)
    await flushPromises()

    // Focus path input to set activeExtractorRow = 0
    await w.find('[data-testid="extractor-path-input"]').trigger('focus')

    const pathSelect = w.find('[data-testid="extractor-path-select"]')
    await pathSelect.setValue('temperature')
    await pathSelect.trigger('change')
    await flushPromises()

    const updated = JSON.parse(w.emitted('update').at(-1)[0].json_paths)
    expect(updated[0].path).toBe('temperature')
    w.unmount()
  })

  it('selecting a path when no rows exist creates a new row', async () => {
    const nodeOutputs = { n1: { _preview: '{"humidity": 60}' } }
    const w = await mountPanel('json_extractor', { json_paths: '[]' }, nodeOutputs)
    await flushPromises()

    const pathSelect = w.find('[data-testid="extractor-path-select"]')
    await pathSelect.setValue('humidity')
    await pathSelect.trigger('change')
    await flushPromises()

    const updated = JSON.parse(w.emitted('update').at(-1)[0].json_paths)
    expect(updated).toHaveLength(1)
    expect(updated[0].path).toBe('humidity')
    w.unmount()
  })
})

// ─── math_formula node ────────────────────────────────────────────────────────

describe('NodeConfigPanel math_formula', () => {
  it('shows the formula input field', async () => {
    const w = await mountPanel('math_formula', { formula: 'a + b' })
    await flushPromises()
    const input = w.find('input[placeholder="a + b"]')
    expect(input.exists()).toBe(true)
    expect(input.element.value).toBe('a + b')
    w.unmount()
  })

  it('changing formula emits update', async () => {
    const w = await mountPanel('math_formula', { formula: '' })
    await flushPromises()

    const input = w.find('input[placeholder="a + b"]')
    await input.setValue('a * b + 1')
    await input.trigger('change')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0].formula).toBe('a * b + 1')
    w.unmount()
  })

  it('selecting an output formula preset emits update with output_formula', async () => {
    const w = await mountPanel('math_formula', {})
    await flushPromises()

    // Output preset select is the one with options like 'x * 1000' etc.
    const presetSelect = w.findAll('select').find(s =>
      s.findAll('option').some(o => o.element.value === 'x * 1000')
    )
    expect(presetSelect).toBeTruthy()
    await presetSelect.setValue('x * 1000')
    await presetSelect.trigger('change')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0].output_formula).toBe('x * 1000')
    w.unmount()
  })
})
