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

async function mountCron(cronExpr = '0 7 * * *') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  return mount(mod.default, {
    props: {
      node: { id: 'c1', type: 'timer_cron', data: { cron: cronExpr } },
      nodeTypes: [{ type: 'timer_cron', label: 'Timer/Cron', description: 'Zeitgesteuert' }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

// ─── mount & initial state ────────────────────────────────────────────────────

describe('NodeConfigPanel timer_cron — mount', () => {
  it('shows cron section with preset select', async () => {
    const w = await mountCron()
    await flushPromises()
    expect(w.text()).toContain('Vorgefertigte Zeitpläne')
    w.unmount()
  })

  it('populates cron fields from initial cron expression on mount', async () => {
    const w = await mountCron('30 8 * * 1-5')
    await flushPromises()

    // The 5 cron field inputs are inside .cron-grid
    const inputs = w.findAll('.cron-grid input')
    expect(inputs).toHaveLength(5)
    expect(inputs[0].element.value).toBe('30') // min
    expect(inputs[1].element.value).toBe('8')  // hour
    expect(inputs[4].element.value).toBe('1-5') // weekday
    w.unmount()
  })

  it('shows the raw cron expression in the expr input', async () => {
    const w = await mountCron('*/5 * * * *')
    await flushPromises()

    // Raw expr input has font-mono but NOT text-center (cron grid inputs have text-center)
    const exprInput = w.findAll('input').find(i =>
      i.classes().includes('font-mono') && !i.classes().includes('text-center')
    )
    expect(exprInput.element.value).toBe('*/5 * * * *')
    w.unmount()
  })
})

// ─── preset selection ─────────────────────────────────────────────────────────

describe('NodeConfigPanel timer_cron — preset selection', () => {
  it('selecting a preset updates cron and emits update', async () => {
    const w = await mountCron('0 7 * * *')
    await flushPromises()

    const presetSelect = w.find('select')
    await presetSelect.setValue('* * * * *')
    await presetSelect.trigger('change')
    await flushPromises()

    const updates = w.emitted('update')
    expect(updates).toBeTruthy()
    expect(updates.at(-1)[0].cron).toBe('* * * * *')
    w.unmount()
  })

  it('selecting a preset also updates the cron field inputs', async () => {
    const w = await mountCron('0 7 * * *')
    await flushPromises()

    const presetSelect = w.find('select')
    await presetSelect.setValue('*/15 * * * *')
    await presetSelect.trigger('change')
    await flushPromises()

    const inputs = w.findAll('.cron-grid input')
    expect(inputs[0].element.value).toBe('*/15')
    expect(inputs[1].element.value).toBe('*')
    w.unmount()
  })

  it('shows cronDescription when a known preset is selected', async () => {
    // '* * * * *' has label "Jede Minute"
    const w = await mountCron('* * * * *')
    await flushPromises()
    // The cronDescription computed should find the label for this preset
    expect(w.text()).toContain('Minute')
    w.unmount()
  })
})

// ─── field editing ───────────────────────────────────────────────────────────

describe('NodeConfigPanel timer_cron — field editing', () => {
  it('changing the minute field updates the cron expression and emits update', async () => {
    const w = await mountCron('0 7 * * *')
    await flushPromises()

    const inputs = w.findAll('.cron-grid input')
    const minInput = inputs[0]
    await minInput.setValue('30')
    await minInput.trigger('input')
    await flushPromises()

    const updates = w.emitted('update')
    expect(updates).toBeTruthy()
    const lastCron = updates.at(-1)[0].cron
    expect(lastCron.startsWith('30')).toBe(true)
    w.unmount()
  })

  it('changing the hour field updates the full cron expr', async () => {
    const w = await mountCron('0 7 * * *')
    await flushPromises()

    const inputs = w.findAll('.cron-grid input')
    await inputs[1].setValue('12')
    await inputs[1].trigger('input')
    await flushPromises()

    const updates = w.emitted('update')
    expect(updates.at(-1)[0].cron).toContain('12')
    w.unmount()
  })
})

// ─── raw expr input ───────────────────────────────────────────────────────────

describe('NodeConfigPanel timer_cron — raw expression input', () => {
  function findExprInput(w) {
    // Raw expr input: font-mono, but NOT text-center (cron grid inputs have text-center)
    return w.findAll('input').find(i =>
      i.classes().includes('font-mono') && !i.classes().includes('text-center')
    )
  }

  it('typing in raw expression input updates cron fields', async () => {
    const w = await mountCron('0 7 * * *')
    await flushPromises()

    await findExprInput(w).setValue('5 9 * * 2')
    await findExprInput(w).trigger('change')
    await flushPromises()

    const inputs = w.findAll('.cron-grid input')
    expect(inputs[0].element.value).toBe('5')
    expect(inputs[1].element.value).toBe('9')
    expect(inputs[4].element.value).toBe('2')
    w.unmount()
  })

  it('typing in raw expression emits update', async () => {
    const w = await mountCron('0 7 * * *')
    await flushPromises()

    await findExprInput(w).setValue('0 8 * * 1')
    await findExprInput(w).trigger('change')
    await flushPromises()

    expect(w.emitted('update')).toBeTruthy()
    expect(w.emitted('update').at(-1)[0].cron).toBe('0 8 * * 1')
    w.unmount()
  })
})
