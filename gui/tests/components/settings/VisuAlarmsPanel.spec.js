import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

let visuAlarmsApi
let visuDevicesApi
let dpApi

const DP = '11111111-1111-1111-1111-111111111111'
const DP2 = '22222222-2222-2222-2222-222222222222'

const PAGES = [
  { id: 'home', parent_id: null, name: 'Haus', type: 'LOCATION' },
  { id: 'post', parent_id: 'home', name: 'Post', type: 'PAGE' },
  { id: 'eg', parent_id: 'home', name: 'EG', type: 'PAGE' },
]

function alarm(overrides = {}) {
  return {
    id: 'post-alarm', name: 'Post', enabled: true, datapoint_id: DP, operator: '==', compare_value: true,
    display: 'message', title: 'Post ist gekommen', text: '', icon: '📬', severity: 'info', page_id: null,
    ack_button: true, end_on_clear: false, sound: false, devices: [], ...overrides,
  }
}

beforeEach(() => {
  vi.resetModules()
  visuAlarmsApi = {
    list: vi.fn().mockResolvedValue({ data: [alarm(), alarm({ id: 'phone', name: 'Telefon', datapoint_id: DP2, compare_value: null, display: 'page', page_id: 'post', title: '', icon: '' })] }),
    update: vi.fn().mockImplementation(async alarms => ({ data: alarms })),
  }
  visuDevicesApi = {
    pages: vi.fn().mockResolvedValue({ data: PAGES }),
    list: vi.fn().mockResolvedValue({ data: [{ slug: 'panel', name: 'Wandpanel' }, { slug: 'handy', name: 'Handy' }] }),
  }
  dpApi = { get: vi.fn().mockImplementation(async id => (id === DP ? { data: { name: 'Briefkasten' } } : Promise.reject(new Error('gone')))) }
  vi.doMock('@/api/client', () => ({ visuAlarmsApi, visuDevicesApi, dpApi }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

const DpComboboxStub = {
  props: ['modelValue', 'displayName'],
  emits: ['select'],
  template: '<button type="button" :data-name="displayName" @click="$emit(\'select\', { id: \'33333333-3333-3333-3333-333333333333\', name: \'Telefon klingelt\' })">{{ displayName }}</button>',
}

async function mountPanel() {
  const { default: VisuAlarmsPanel } = await import('@/components/settings/VisuAlarmsPanel.vue')
  const wrapper = mount(VisuAlarmsPanel, {
    global: {
      stubs: {
        DpCombobox: DpComboboxStub,
        IconPicker: { props: ['modelValue'], emits: ['update:modelValue'], template: '<input data-testid="icon-stub" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />' },
        VisuIcon: { props: ['icon'], template: '<span data-testid="visu-icon">{{ icon }}</span>' },
        HelpButton: { props: ['helpId'], template: '<span data-testid="help" :data-help-id="helpId" />' },
        Spinner: { template: '<span data-testid="spinner" />' },
      },
    },
  })
  await flushPromises()
  return wrapper
}

const card = (w, i) => w.get(`[data-testid="visu-alarm-${i}"]`)
async function open(w, i) {
  await card(w, i).get('[data-testid="btn-toggle-visu-alarm"]').trigger('click')
}

describe('VisuAlarmsPanel', () => {
  it('lists the alarms collapsed with icon, name and datapoint names', async () => {
    const w = await mountPanel()

    expect(w.get('[data-testid="help"]').attributes('data-help-id')).toBe('settings-visu-alarms')
    expect(card(w, 0).text()).toContain('Post')
    expect(card(w, 0).get('[data-testid="visu-icon"]').text()).toBe('📬')
    expect(card(w, 1).get('[data-testid="visu-icon"]').text()).toBe('📄') // page alarm without icon
    expect(w.find('[data-testid="visu-alarm-form"]').exists()).toBe(false)

    await open(w, 0)
    expect(card(w, 0).get('[data-testid="input-visu-alarm-datapoint"]').attributes('data-name')).toBe('Briefkasten')
    expect(card(w, 0).get('[data-testid="input-visu-alarm-compare"]').element.value).toBe('true')
    await open(w, 1)
    expect(card(w, 1).get('[data-testid="input-visu-alarm-datapoint"]').attributes('data-name')).toBe(DP2) // name lookup failed → id
    expect(card(w, 1).get('[data-testid="input-visu-alarm-compare"]').element.value).toBe('')
    expect(card(w, 1).findAll('[data-testid="select-visu-alarm-page"] option').map(o => o.text())).toEqual(['— Seite wählen —', 'Haus', 'Haus › Post', 'Haus › EG'])
    await open(w, 1)
    expect(card(w, 1).find('[data-testid="visu-alarm-form"]').exists()).toBe(false)
  })

  it('shows the empty state and a load failure', async () => {
    visuAlarmsApi.list.mockResolvedValueOnce({ data: [] })
    const empty = await mountPanel()
    expect(empty.get('[data-testid="visu-alarms-empty"]').text()).toContain('Noch keine Alarme')

    visuDevicesApi.pages.mockRejectedValueOnce(new Error('offline'))
    const failed = await mountPanel()
    expect(failed.get('[data-testid="visu-alarms-msg"]').text()).toBe('Alarme konnten nicht geladen werden.')
    // The list is incomplete: saving it would delete every stored alarm.
    expect(failed.find('[data-testid="btn-save-visu-alarms"]').exists()).toBe(false)
    expect(failed.get('[data-testid="btn-add-visu-alarm"]').attributes('disabled')).toBeDefined()
    expect(empty.find('[data-testid="btn-save-visu-alarms"]').exists()).toBe(true)
    expect(empty.get('[data-testid="btn-add-visu-alarm"]').attributes('disabled')).toBeUndefined()
  })

  it('creates a complete message alarm for selected devices', async () => {
    visuAlarmsApi.list.mockResolvedValueOnce({ data: [] })
    const w = await mountPanel()
    await w.get('[data-testid="btn-add-visu-alarm"]').trigger('click')
    const c = card(w, 0)

    expect(c.text()).toContain('(ohne Namen)')
    expect(c.get('[data-testid="visu-alarm-error"]').text()).toBe('Bitte einen Namen angeben.')
    await c.get('[data-testid="input-visu-alarm-name"]').setValue('Post ist da')
    expect(c.get('[data-testid="visu-alarm-error"]').text()).toBe('Bitte einen Datenpunkt wählen.')
    await c.get('[data-testid="input-visu-alarm-datapoint"]').trigger('click')
    expect(c.get('[data-testid="input-visu-alarm-datapoint"]').attributes('data-name')).toBe('Telefon klingelt')
    expect(c.get('[data-testid="visu-alarm-error"]').text()).toBe('Bitte einen Titel oder Text angeben.')
    await c.get('[data-testid="input-visu-alarm-text"]').setValue('Der Briefkasten wurde geöffnet')
    expect(c.find('[data-testid="visu-alarm-error"]').exists()).toBe(false)

    await c.get('[data-testid="select-visu-alarm-operator"]').setValue('>')
    await c.get('[data-testid="input-visu-alarm-compare"]').setValue('0')
    await c.get('[data-testid="icon-stub"]').setValue('📬')
    await c.get('[data-testid="select-visu-alarm-severity"]').setValue('warning')
    await c.get('[data-testid="check-visu-alarm-clear"]').setValue(true)
    await c.get('[data-testid="check-visu-alarm-sound"]').setValue(true)
    await c.get('[data-testid="check-visu-alarm-device-panel"]').trigger('change')
    await c.get('[data-testid="check-visu-alarm-device-handy"]').trigger('change')
    await c.get('[data-testid="check-visu-alarm-device-handy"]').trigger('change') // and off again
    await c.get('[data-testid="check-visu-alarm-enabled"]').setValue(false)

    await w.get('[data-testid="btn-save-visu-alarms"]').trigger('click')
    await flushPromises()

    const [payload] = visuAlarmsApi.update.mock.calls[0]
    expect(payload).toHaveLength(1)
    expect(payload[0]).toMatchObject({
      name: 'Post ist da', enabled: false, datapoint_id: '33333333-3333-3333-3333-333333333333', operator: '>', compare_value: 0,
      display: 'message', title: '', text: 'Der Briefkasten wurde geöffnet', icon: '📬', severity: 'warning', page_id: null,
      ack_button: true, end_on_clear: true, sound: true, devices: ['panel'],
    })
    expect(payload[0].id).toMatch(/^alarm-[a-z0-9]+-[a-z0-9]+$/)
    expect(payload[0]).not.toHaveProperty('_open')
    expect(w.get('[data-testid="visu-alarms-msg"]').text()).toBe('Alarme gespeichert.')
    expect(card(w, 0).find('[data-testid="visu-alarm-form"]').exists()).toBe(true) // stays open after saving
  })

  it('validates page alarms and the end condition', async () => {
    const w = await mountPanel()
    await open(w, 0)
    const c = card(w, 0)

    await c.get('[data-testid="radio-visu-alarm-page"]').setValue(true)
    expect(c.get('[data-testid="visu-alarm-error"]').text()).toBe('Bitte eine Seite wählen.')
    expect(w.get('[data-testid="btn-save-visu-alarms"]').attributes('disabled')).toBeDefined()
    await c.get('[data-testid="select-visu-alarm-page"]').setValue('post')
    expect(c.find('[data-testid="visu-alarm-error"]').exists()).toBe(false)

    await c.get('[data-testid="check-visu-alarm-ack"]').setValue(false)
    expect(c.get('[data-testid="visu-alarm-error"]').text()).toContain('braucht ein Ende')
    await c.get('[data-testid="check-visu-alarm-clear"]').setValue(true)
    expect(c.find('[data-testid="visu-alarm-error"]').exists()).toBe(false)

    await c.get('[data-testid="radio-visu-alarm-message"]').setValue(true)
    await c.get('[data-testid="input-visu-alarm-title"]').setValue('Post!')
    await w.get('[data-testid="btn-save-visu-alarms"]').trigger('click')
    await flushPromises()
    expect(visuAlarmsApi.update.mock.calls[0][0][0]).toMatchObject({ title: 'Post!', display: 'message', page_id: 'post', ack_button: false, end_on_clear: true })
  })

  it('keeps a deleted page visible and flags it', async () => {
    visuAlarmsApi.list.mockResolvedValueOnce({ data: [alarm({ display: 'page', page_id: 'gone' })] })
    const w = await mountPanel()
    await open(w, 0)

    const select = card(w, 0).get('[data-testid="select-visu-alarm-page"]')
    expect(select.element.value).toBe('gone')
    expect(select.findAll('option')[1].text()).toBe('(gelöschte Seite)')
    expect(card(w, 0).get('[data-testid="visu-alarm-error"]').text()).toBe('Die gewählte Seite existiert nicht mehr.')
  })

  it('shows a renamed or deleted device profile ticked in red and blocks saving until it is unticked', async () => {
    visuAlarmsApi.list.mockResolvedValueOnce({ data: [alarm({ devices: ['kitchen', 'panel'] })] })
    const w = await mountPanel()
    await open(w, 0)

    const missing = card(w, 0).get('[data-testid="check-visu-alarm-device-missing-kitchen"]')
    expect(missing.element.checked).toBe(true)
    expect(card(w, 0).text()).toContain('Gerät „kitchen“ (umbenannt oder gelöscht)')
    expect(card(w, 0).get('[data-testid="visu-alarm-error"]').text()).toContain('existiert nicht mehr')
    expect(w.get('[data-testid="btn-save-visu-alarms"]').attributes('disabled')).toBeDefined()

    await missing.trigger('change')
    await flushPromises()
    expect(card(w, 0).find('[data-testid="check-visu-alarm-device-missing-kitchen"]').exists()).toBe(false)
    expect(card(w, 0).find('[data-testid="visu-alarm-error"]').exists()).toBe(false)
    await w.get('[data-testid="btn-save-visu-alarms"]').trigger('click')
    await flushPromises()
    expect(visuAlarmsApi.update.mock.calls.at(-1)[0][0].devices).toEqual(['panel'])
  })

  it('still shows a stale device profile when no profiles exist at all', async () => {
    visuDevicesApi.list.mockResolvedValueOnce({ data: [] })
    visuAlarmsApi.list.mockResolvedValueOnce({ data: [alarm({ devices: ['kitchen'] })] })
    const w = await mountPanel()
    await open(w, 0)

    expect(card(w, 0).text()).not.toContain('Keine Visu-Geräte angelegt')
    expect(card(w, 0).get('[data-testid="check-visu-alarm-device-missing-kitchen"]').element.checked).toBe(true)
  })

  it('says that alarms go to every Visu without device profiles', async () => {
    visuDevicesApi.list.mockResolvedValueOnce({ data: [] })
    const w = await mountPanel()
    await open(w, 0)

    expect(card(w, 0).text()).toContain('Keine Visu-Geräte angelegt')
  })

  it('removes an alarm and reports save errors', async () => {
    const w = await mountPanel()
    await card(w, 0).get('[data-testid="btn-delete-visu-alarm"]').trigger('click')
    expect(w.findAll('[data-testid^="visu-alarm-"][data-testid$="0"]')).toHaveLength(1)

    visuAlarmsApi.update.mockRejectedValueOnce({ response: { data: { detail: 'Unknown page: post' } } })
    await w.get('[data-testid="btn-save-visu-alarms"]').trigger('click')
    await flushPromises()
    expect(w.get('[data-testid="visu-alarms-msg"]').text()).toBe('Unknown page: post')
    expect(visuAlarmsApi.update.mock.calls[0][0].map(a => a.id)).toEqual(['phone'])

    visuAlarmsApi.update.mockRejectedValueOnce({ response: { data: { detail: [{ msg: 'x' }] } } })
    await w.get('[data-testid="btn-save-visu-alarms"]').trigger('click')
    await flushPromises()
    expect(w.get('[data-testid="visu-alarms-msg"]').text()).toBe('Speichern fehlgeschlagen.')

    visuAlarmsApi.update.mockRejectedValueOnce(new Error('network'))
    await w.get('[data-testid="btn-save-visu-alarms"]').trigger('click')
    await flushPromises()
    expect(w.get('[data-testid="visu-alarms-msg"]').text()).toBe('Speichern fehlgeschlagen.')
  })

  it('flags an alarm whose datapoint was deleted and blocks saving until another one is chosen', async () => {
    dpApi.get.mockImplementation(async id => (id === DP ? Promise.reject({ response: { status: 404 } }) : { data: { name: 'Klingel' } }))
    const w = await mountPanel()

    expect(card(w, 0).text()).toContain('⚠')
    expect(card(w, 1).text()).not.toContain('⚠')
    await open(w, 0)
    await open(w, 1)
    expect(card(w, 0).get('[data-testid="input-visu-alarm-datapoint"]').attributes('data-name')).toBe('(gelöschter Datenpunkt)')
    expect(card(w, 1).get('[data-testid="input-visu-alarm-datapoint"]').attributes('data-name')).toBe('Klingel')
    expect(card(w, 0).get('[data-testid="visu-alarm-error"]').text()).toBe('Der gewählte Datenpunkt existiert nicht mehr. Bitte einen anderen wählen oder den Alarm entfernen.')
    expect(w.get('[data-testid="btn-save-visu-alarms"]').attributes('disabled')).toBeDefined()

    await card(w, 0).get('[data-testid="input-visu-alarm-datapoint"]').trigger('click')
    expect(card(w, 0).find('[data-testid="visu-alarm-error"]').exists()).toBe(false)
    expect(w.get('[data-testid="btn-save-visu-alarms"]').attributes('disabled')).toBeUndefined()
  })

  it('keeps the stored type of an untouched compare value and reads a changed text back as number, boolean, text or nothing', async () => {
    visuAlarmsApi.list.mockResolvedValueOnce({ data: [
      alarm({ id: 'a', compare_value: 5 }), alarm({ id: 'b', compare_value: 5 }), alarm({ id: 'c', compare_value: 5 }),
      alarm({ id: 'd', compare_value: 'on' }), alarm({ id: 'e', compare_value: 'on' }), alarm({ id: 'f', compare_value: '7' }),
      alarm({ id: 'g', compare_value: null }),
    ] })
    const w = await mountPanel()
    for (const i of [1, 2, 3, 4, 6]) await open(w, i)
    await card(w, 1).get('[data-testid="input-visu-alarm-compare"]').setValue('25 W')
    await card(w, 2).get('[data-testid="input-visu-alarm-compare"]').setValue(' -7.5 ')
    await card(w, 3).get('[data-testid="input-visu-alarm-compare"]').setValue('')
    await card(w, 4).get('[data-testid="input-visu-alarm-compare"]').setValue('false')
    await card(w, 6).get('[data-testid="input-visu-alarm-compare"]').setValue('true')

    await w.get('[data-testid="btn-save-visu-alarms"]').trigger('click')
    await flushPromises()

    const [payload] = visuAlarmsApi.update.mock.calls[0]
    expect(payload.map(a => a.compare_value)).toEqual([5, '25 W', -7.5, null, false, '7', true])
    expect(payload[0]).not.toHaveProperty('_compare')
    // After saving, the returned values are the new "untouched" ones.
    await w.get('[data-testid="btn-save-visu-alarms"]').trigger('click')
    await flushPromises()
    expect(visuAlarmsApi.update.mock.calls[1][0].map(a => a.compare_value)).toEqual([5, '25 W', -7.5, null, false, '7', true])
  })
})
