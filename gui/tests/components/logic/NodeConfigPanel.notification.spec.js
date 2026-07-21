import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let adapterApi

beforeEach(() => {
  vi.resetModules()
  adapterApi = {
    listInstances: vi.fn().mockResolvedValue({
      data: [{
        id: 'message-1',
        name: 'Benachrichtigungen',
        adapter_type: 'MESSAGE',
        enabled: true,
        config: { providers: {
          pushover: { enabled: true, targets: { default: {} } },
          telegram: { enabled: false, targets: { family: {} } },
        } },
      }],
    }),
  }
  vi.doMock('@/api/client', () => ({
    adapterApi,
    dpApi: { list: vi.fn() },
    messageArchivesApi: { list: vi.fn() },
    searchApi: { search: vi.fn() },
    securityApi: { checkUrlTarget: vi.fn(), addUrlTarget: vi.fn() },
  }))
})

afterEach(() => vi.doUnmock('@/api/client'))

async function mountPanel(type = 'notify_message', data = {}) {
  setActivePinia(createPinia())
  const { default: NodeConfigPanel } = await import('@/components/logic/NodeConfigPanel.vue')
  const wrapper = mount(NodeConfigPanel, {
    props: {
      node: { id: 'notify-1', type, data: { adapter_instance_id: '', providers: [], message: '', ...data } },
      nodeTypes: [{ type, label: type, legacy: type !== 'notify_message', config_schema: {} }],
    },
  })
  await flushPromises()
  return wrapper
}

describe('NodeConfigPanel notification', () => {
  it('selects an enabled adapter and one or more active targets', async () => {
    const wrapper = await mountPanel()
    expect(adapterApi.listInstances).toHaveBeenCalled()

    await wrapper.find('select').setValue('message-1')
    expect(wrapper.text()).toContain('pushover/default')
    expect(wrapper.text()).not.toContain('telegram/family')
    await wrapper.find('input[type="checkbox"]').setValue(true)

    expect(wrapper.emitted('update').at(-1)[0]).toMatchObject({
      adapter_instance_id: 'message-1',
      providers: [{ provider: 'pushover', target: 'default' }],
    })
  })

  it('shows the legacy warning for old provider-specific nodes', async () => {
    const wrapper = await mountPanel('notify_sms')
    expect(wrapper.text()).toContain('Legacy')
  })

  it('drops stale targets when a visible target is edited', async () => {
    const wrapper = await mountPanel('notify_message', {
      adapter_instance_id: 'message-1',
      providers: [{ provider: 'removed', target: 'stale' }, { provider: 'pushover', target: 'default' }],
    })

    await wrapper.find('input[type="checkbox"]').setValue(false)
    expect(wrapper.emitted('update').at(-1)[0].providers).toEqual([])
  })

  it('deduplicates the selected target and handles malformed saved providers', async () => {
    const wrapper = await mountPanel('notify_message', { adapter_instance_id: 'message-1', providers: 'invalid' })

    await wrapper.find('input[type="checkbox"]').setValue(true)
    await wrapper.find('input[type="checkbox"]').setValue(true)
    expect(wrapper.emitted('update').at(-1)[0].providers).toEqual([{ provider: 'pushover', target: 'default' }])
  })

  it('handles adapter loading failures', async () => {
    adapterApi.listInstances.mockRejectedValueOnce(new Error('offline'))
    const wrapper = await mountPanel()

    expect(wrapper.findAll('select option')).toHaveLength(1)
  })

  it('ignores non-array adapter responses', async () => {
    adapterApi.listInstances.mockResolvedValueOnce({ data: {} })
    const wrapper = await mountPanel()

    expect(wrapper.findAll('select option')).toHaveLength(1)
  })
})
