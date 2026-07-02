import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let messageArchivesApi

beforeEach(() => {
  vi.resetModules()
  messageArchivesApi = {
    list: vi.fn().mockResolvedValue({
      data: [
        { id: 'system', name: 'System' },
        { id: 'adapter', name: 'Adapter' },
      ],
    }),
  }
  vi.doMock('@/api/client', () => ({
    dpApi: { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    searchApi: { search: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    securityApi: { checkUrlTarget: vi.fn(), addUrlTarget: vi.fn() },
    messageArchivesApi,
  }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountPanel(data = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  const wrapper = mount(mod.default, {
    props: {
      node: {
        id: 'archive-1',
        type: 'message_archive',
        data: { archive_id: 'LEGACY', title: '', message: '', ...data },
      },
      nodeTypes: [{ type: 'message_archive', label: 'Meldungsarchiv', description: 'Schreibt eine Meldung.' }],
      nodeOutputs: {},
    },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

describe('NodeConfigPanel message_archive', () => {
  it('loads archives, normalizes archive ids, applies defaults and emits changes', async () => {
    const wrapper = await mountPanel()

    expect(messageArchivesApi.list).toHaveBeenCalled()
    expect(wrapper.text()).toContain('System')
    expect(wrapper.text()).toContain('Adapter')

    const selects = wrapper.findAll('select')
    expect(selects[0].element.value).toBe('legacy')
    expect([...selects[0].element.options].some(option => option.value === 'legacy')).toBe(true)
    expect(selects[1].element.value).toBe('automation')
    expect(selects[2].element.value).toBe('info')

    await selects[0].setValue('system')
    await selects[1].setValue('security')
    await selects[2].setValue('critical')
    await wrapper.find('input').setValue('Alarm')
    await wrapper.find('input').trigger('change')
    await wrapper.find('textarea').setValue('Meldung')
    await wrapper.find('textarea').trigger('change')

    expect(wrapper.emitted('update')).toBeTruthy()
    const lastUpdate = wrapper.emitted('update').at(-1)[0]
    expect(lastUpdate).toMatchObject({
      archive_id: 'system',
      type: 'security',
      severity: 'critical',
      title: 'Alarm',
      message: 'Meldung',
    })

    wrapper.unmount()
  })
})
