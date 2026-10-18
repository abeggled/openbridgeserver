import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

let visuDevicesApi

const PAGES = [
  { id: 'home', parent_id: null, name: 'Haus', type: 'LOCATION' },
  { id: 'eg', parent_id: 'home', name: 'EG', type: 'LOCATION' },
  { id: 'kitchen', parent_id: 'eg', name: 'Küche', type: 'PAGE' },
  { id: 'garden', parent_id: null, name: 'Garten', type: 'PAGE' },
]

const DEVICES = [
  { slug: 'panel', name: 'Wandpanel', start_node_id: 'kitchen', return_after_s: 120 },
  { slug: 'handy', name: 'Handy', start_node_id: null, return_after_s: 0 },
]

beforeEach(() => {
  vi.resetModules()
  visuDevicesApi = {
    list: vi.fn().mockResolvedValue({ data: structuredClone(DEVICES) }),
    pages: vi.fn().mockResolvedValue({ data: PAGES }),
    update: vi.fn().mockImplementation(async devices => ({ data: devices })),
  }
  vi.doMock('@/api/client', () => ({ visuDevicesApi }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.unstubAllGlobals()
})

async function mountPanel() {
  const { default: VisuDevicesPanel } = await import('@/components/settings/VisuDevicesPanel.vue')
  const wrapper = mount(VisuDevicesPanel, {
    global: {
      stubs: {
        HelpButton: { props: ['helpId'], template: '<span data-testid="help" :data-help-id="helpId" />' },
        Spinner: { template: '<span data-testid="spinner" />' },
      },
    },
  })
  await flushPromises()
  return wrapper
}

const row = (wrapper, index) => wrapper.get(`[data-testid="visu-device-row-${index}"]`)

describe('VisuDevicesPanel', () => {
  it('loads the profiles and offers every page with its path, in tree order', async () => {
    const wrapper = await mountPanel()

    expect(wrapper.get('[data-testid="help"]').attributes('data-help-id')).toBe('settings-visu-devices')
    expect(row(wrapper, 0).get('[data-testid="input-visu-device-name"]').element.value).toBe('Wandpanel')
    expect(row(wrapper, 0).get('[data-testid="input-visu-device-slug"]').element.value).toBe('panel')
    expect(row(wrapper, 0).get('[data-testid="select-visu-device-start"]').element.value).toBe('kitchen')
    expect(row(wrapper, 0).get('[data-testid="input-visu-device-return"]').element.value).toBe('120')
    expect(row(wrapper, 1).get('[data-testid="select-visu-device-start"]').element.value).toBe('')

    const labels = row(wrapper, 0).findAll('option').map(o => o.text())
    expect(labels).toEqual(['— Übersicht —', 'Haus', 'Haus › EG', 'Haus › EG › Küche', 'Garten'])
    expect(row(wrapper, 0).get('[data-testid="visu-device-link"]').text()).toBe(`${window.location.origin}/visu/?device=panel`)
    expect(wrapper.find('[data-testid="visu-device-error"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="btn-save-visu-devices"]').attributes('disabled')).toBeUndefined()
  })

  it('shows the empty state without profiles', async () => {
    visuDevicesApi.list.mockResolvedValue({ data: [] })
    const wrapper = await mountPanel()

    expect(wrapper.get('[data-testid="visu-devices-empty"]').text()).toContain('Noch keine Geräte')
  })

  it('reports a load failure', async () => {
    visuDevicesApi.pages.mockRejectedValue(new Error('offline'))
    const wrapper = await mountPanel()

    expect(wrapper.get('[data-testid="visu-devices-msg"]').text()).toBe('Geräte konnten nicht geladen werden.')
    expect(wrapper.find('[data-testid="spinner"]').exists()).toBe(false)
  })

  it('suggests the slug from the name until it is edited by hand', async () => {
    const wrapper = await mountPanel()
    await wrapper.get('[data-testid="btn-add-visu-device"]').trigger('click')
    const added = row(wrapper, 2)

    await added.get('[data-testid="input-visu-device-name"]').setValue('  Visu-PC Küche & Bad!  ')
    expect(added.get('[data-testid="input-visu-device-slug"]').element.value).toBe('visu-pc-kueche-bad')

    await added.get('[data-testid="input-visu-device-name"]').setValue('Größe Öl Übung')
    expect(added.get('[data-testid="input-visu-device-slug"]').element.value).toBe('groesse-oel-uebung')

    await added.get('[data-testid="input-visu-device-slug"]').setValue('mein-pc')
    await added.get('[data-testid="input-visu-device-name"]').setValue('Anderer Name')
    expect(added.get('[data-testid="input-visu-device-slug"]').element.value).toBe('mein-pc')

    // Loaded profiles never get an automatic slug.
    await row(wrapper, 0).get('[data-testid="input-visu-device-name"]').setValue('Neu')
    expect(row(wrapper, 0).get('[data-testid="input-visu-device-slug"]').element.value).toBe('panel')
  })

  it.each([
    ['name', 'input-visu-device-name', '   ', 'Bitte einen Namen angeben.'],
    ['slug', 'input-visu-device-slug', 'Panel', 'Die Kennung darf nur'],
    ['duplicate slug', 'input-visu-device-slug', 'handy', 'Diese Kennung ist bereits vergeben.'],
    ['negative return', 'input-visu-device-return', '-5', 'zwischen 0 und 86400'],
    ['too large return', 'input-visu-device-return', '86401', 'zwischen 0 und 86400'],
    ['fractional return', 'input-visu-device-return', '1.5', 'zwischen 0 und 86400'],
    ['empty return', 'input-visu-device-return', '', 'zwischen 0 und 86400'],
  ])('blocks saving on an invalid %s', async (_case, testid, value, message) => {
    const wrapper = await mountPanel()
    await row(wrapper, 0).get(`[data-testid="${testid}"]`).setValue(value)

    expect(row(wrapper, 0).get('[data-testid="visu-device-error"]').text()).toContain(message)
    expect(wrapper.get('[data-testid="btn-save-visu-devices"]').attributes('disabled')).toBeDefined()
  })

  it('keeps a deleted start page visible and asks for another one', async () => {
    visuDevicesApi.list.mockResolvedValue({ data: [{ slug: 'panel', name: 'Panel', start_node_id: 'gone', return_after_s: 0 }] })
    const wrapper = await mountPanel()

    const select = row(wrapper, 0).get('[data-testid="select-visu-device-start"]')
    expect(select.element.value).toBe('gone')
    expect(select.findAll('option')[1].text()).toBe('(gelöschte Seite)')
    expect(row(wrapper, 0).get('[data-testid="visu-device-error"]').text()).toContain('existiert nicht mehr')

    await select.setValue('garden')
    expect(row(wrapper, 0).find('[data-testid="visu-device-error"]').exists()).toBe(false)
    expect(select.findAll('option').map(o => o.text())).not.toContain('(gelöschte Seite)')
  })

  it('saves all profiles, turning "overview" into no start page', async () => {
    const wrapper = await mountPanel()
    await row(wrapper, 0).get('[data-testid="select-visu-device-start"]').setValue('')
    await row(wrapper, 1).get('[data-testid="input-visu-device-return"]').setValue('300')

    await wrapper.get('[data-testid="btn-save-visu-devices"]').trigger('click')
    await flushPromises()

    expect(visuDevicesApi.update).toHaveBeenCalledWith([
      { slug: 'panel', name: 'Wandpanel', start_node_id: null, return_after_s: 120 },
      { slug: 'handy', name: 'Handy', start_node_id: null, return_after_s: 300 },
    ])
    expect(wrapper.get('[data-testid="visu-devices-msg"]').text()).toBe('Geräte gespeichert.')
  })

  it('removes a profile and saves the rest', async () => {
    const wrapper = await mountPanel()
    await row(wrapper, 0).get('[data-testid="btn-delete-visu-device"]').trigger('click')
    await wrapper.get('[data-testid="btn-save-visu-devices"]').trigger('click')
    await flushPromises()

    expect(visuDevicesApi.update).toHaveBeenCalledWith([{ slug: 'handy', name: 'Handy', start_node_id: null, return_after_s: 0 }])
  })

  it('shows the server message when saving is rejected', async () => {
    visuDevicesApi.update.mockRejectedValue({ response: { data: { detail: 'Unknown start page: kitchen' } } })
    const wrapper = await mountPanel()
    await wrapper.get('[data-testid="btn-save-visu-devices"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="visu-devices-msg"]').text()).toBe('Unknown start page: kitchen')
  })

  it('falls back to a generic message for structured or missing error details', async () => {
    visuDevicesApi.update.mockRejectedValueOnce({ response: { data: { detail: [{ msg: 'x' }] } } })
    const wrapper = await mountPanel()
    await wrapper.get('[data-testid="btn-save-visu-devices"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="visu-devices-msg"]').text()).toBe('Speichern fehlgeschlagen.')

    visuDevicesApi.update.mockRejectedValueOnce(new Error('network'))
    await wrapper.get('[data-testid="btn-save-visu-devices"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="visu-devices-msg"]').text()).toBe('Speichern fehlgeschlagen.')
  })

  it('copies the device link and reports a blocked clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const wrapper = await mountPanel()

    await row(wrapper, 0).get('[data-testid="btn-copy-visu-device-link"]').trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/visu/?device=panel`)
    expect(row(wrapper, 0).get('[data-testid="btn-copy-visu-device-link"]').text()).toBe('Kopiert ✓')
    expect(row(wrapper, 1).get('[data-testid="btn-copy-visu-device-link"]').text()).toBe('Kopieren')

    writeText.mockRejectedValueOnce(new Error('denied'))
    await row(wrapper, 1).get('[data-testid="btn-copy-visu-device-link"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="visu-devices-msg"]').text()).toContain('nicht in die Zwischenablage')
  })

  it('disables copying while the slug is invalid', async () => {
    const wrapper = await mountPanel()
    await row(wrapper, 0).get('[data-testid="input-visu-device-slug"]').setValue('-bad')

    expect(row(wrapper, 0).get('[data-testid="btn-copy-visu-device-link"]').attributes('disabled')).toBeDefined()
  })
})
