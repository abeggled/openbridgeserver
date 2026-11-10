import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  vi.doMock('@/api/client', () => ({
    dpApi: { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    searchApi: { search: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    securityApi: { checkUrlTarget: vi.fn(), addUrlTarget: vi.fn() },
    adapterApi: { list: vi.fn().mockResolvedValue({ data: [] }) },
    messageArchivesApi: { list: vi.fn().mockResolvedValue({ data: [] }) },
    authApi: { login: vi.fn(), me: vi.fn() },
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

const lastUpdate = w => w.emitted('update').at(-1)[0]

describe('NodeConfigPanel — variables in text fields (#1301)', () => {
  it('api_client: inserts into URL, headers and auth fields', async () => {
    const w = await mountPanel('api_client', { url: 'http://x/', headers: '', auth_type: 'basic', auth_username: 'u', auth_password: 'p' })
    await flushPromises()
    const selects = w.findAll('[data-testid="variable-insert-select"]')
    expect(selects.length).toBe(4) // url, headers, username, password
    await selects[0].setValue('yyyy')
    expect(lastUpdate(w).url).toBe('http://x/###yyyy###')
    await selects[1].setValue('HH')
    expect(lastUpdate(w).headers).toBe('###HH###')
    await selects[2].setValue('DATE')
    expect(lastUpdate(w).auth_username).toBe('u###DATE###')
    await selects[3].setValue('TS')
    expect(lastUpdate(w).auth_password).toBe('p###TS###')
    w.unmount()
  })

  it('api_client: bearer token field offers variables', async () => {
    const w = await mountPanel('api_client', { url: 'http://x/', auth_type: 'bearer', auth_token: 't' })
    await flushPromises()
    const selects = w.findAll('[data-testid="variable-insert-select"]')
    await selects.at(-1).setValue('TIME')
    expect(lastUpdate(w).auth_token).toBe('t###TIME###')
    w.unmount()
  })

  it('string_concat: per-slot insert, variables editor and issues', async () => {
    const w = await mountPanel('string_concat', { count: 2, text_1: 'a', text_2: '' }, { n1: { _issues: ['variable OBS3 is not configured'] } })
    await flushPromises()
    expect(w.find('[data-testid="extractor-issues"]').text()).toContain('OBS3')
    await w.findAll('[data-testid="variable-insert-select"]')[1].setValue('mm')
    expect(lastUpdate(w).text_2).toBe('###mm###')
    expect(w.find('[data-testid="concat-add-variable"]').exists()).toBe(true)
    w.unmount()
  })

  it('string_replace: insert into the replacement text', async () => {
    const w = await mountPanel('string_replace', { rules: [{ search: 'a', replace: 'x', mode: 'plain' }] })
    await flushPromises()
    await w.find('[data-testid="variable-insert-select"]').setValue('dd')
    const rules = lastUpdate(w).rules
    expect((typeof rules === 'string' ? JSON.parse(rules) : rules)[0].replace).toBe('x###dd###')
    expect(w.find('[data-testid="replace-add-variable"]').exists()).toBe(true)
    w.unmount()
  })

  it('ical: insert into URL and variables editor', async () => {
    const w = await mountPanel('ical', { url: 'https://e.com/', filters: '[]', refresh_interval_min: 60 })
    await flushPromises()
    await w.find('[data-testid="variable-insert-select"]').setValue('yyyy')
    expect(lastUpdate(w).url).toBe('https://e.com/###yyyy###')
    expect(w.find('[data-testid="ical-add-variable"]').exists()).toBe(true)
    w.unmount()
  })

  it('message_archive: title and message', async () => {
    const w = await mountPanel('message_archive', { archive_id: '', title: 't', message: 'm' })
    await flushPromises()
    const selects = w.findAll('[data-testid="variable-insert-select"]')
    await selects[0].setValue('HH')
    expect(lastUpdate(w).title).toBe('t###HH###')
    await selects[1].setValue('mm')
    expect(lastUpdate(w).message).toBe('m###mm###')
    expect(w.find('[data-testid="archive-add-variable"]').exists()).toBe(true)
    w.unmount()
  })

  it('notify_message: title and fallback message, with a configured OBS slot', async () => {
    const w = await mountPanel('notify_message', {
      adapter_instance_id: '', providers: [], title: 't', message: 'm',
      variables: [{ slot: 1, datapoint_id: 'dp', datapoint_name: 'Lamp' }],
    })
    await flushPromises()
    const selects = w.findAll('[data-testid="variable-insert-select"]')
    expect(selects[0].findAll('optgroup').length).toBe(3)
    await selects[0].setValue('OBS1')
    expect(lastUpdate(w).title).toBe('t###OBS1###')
    await selects[1].setValue('TS')
    expect(lastUpdate(w).message).toBe('m###TS###')
    w.unmount()
  })
  it('object picker: refreshes when another node is selected', async () => {
    const w = await mountPanel('api_client', { variables: [{ slot: 1, datapoint_id: 'a', datapoint_name: 'Lamp' }] })
    await flushPromises()
    await w.setProps({ node: { id: 'n2', type: 'api_client', data: { variables: [{ slot: 1, datapoint_id: 'b', datapoint_name: 'Temperature' }] } } })
    await flushPromises()
    expect(w.find('[data-testid="api-client-variable-search-0"]').element.value).toBe('Temperature')
    w.unmount()
  })

  it('object picker: ignores stale search responses', async () => {
    const { searchApi } = await import('@/api/client')
    let resolveA
    let resolveAB
    searchApi.search
      .mockImplementationOnce(() => new Promise((r) => { resolveA = r }))
      .mockImplementationOnce(() => new Promise((r) => { resolveAB = r }))
    const w = await mountPanel('api_client', { variables: [{ slot: 1, datapoint_id: '', datapoint_name: '' }] })
    await flushPromises()
    const input = w.find('[data-testid="api-client-variable-search-0"]')
    await input.setValue('a')
    await input.setValue('ab')
    resolveAB({ data: { items: [{ id: 'b', name: 'Newest' }] } })
    await flushPromises()
    resolveA({ data: { items: [{ id: 'a', name: 'Stale' }] } })
    await flushPromises()
    expect(w.text()).toContain('Newest')
    expect(w.text()).not.toContain('Stale')
    w.unmount()
  })

  it('object picker: a search failure empties the results and removing a row drops its state', async () => {
    const { searchApi } = await import('@/api/client')
    searchApi.search.mockRejectedValueOnce(new Error('boom'))
    const w = await mountPanel('api_client', { variables: [{ slot: 1, datapoint_id: '', datapoint_name: '' }] })
    await flushPromises()
    await w.find('[data-testid="api-client-variable-search-0"]').setValue('x')
    await flushPromises()
    expect(w.find('[data-testid="api-client-variable-result-0"]').exists()).toBe(false)
    await w.find('[data-testid="api-client-variable-remove-0"]').trigger('click')
    expect(lastUpdate(w).variables).toEqual([])
    w.unmount()
  })
  it('object picker: ignores a pending response after switching nodes', async () => {
    const { searchApi } = await import('@/api/client')
    let resolveOld
    searchApi.search.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
    const w = await mountPanel('json_extractor', { json_paths: '[]', variables: [{ slot: 1, datapoint_id: 'a', datapoint_name: 'Lamp' }] })
    await flushPromises()
    await w.find('[data-testid="extractor-variable-search-0"]').setValue('old')
    await w.setProps({ node: { id: 'n2', type: 'json_extractor', data: { json_paths: '[]', variables: [{ slot: 1, datapoint_id: 'b', datapoint_name: 'Temperature' }] } } })
    await flushPromises()
    resolveOld({ data: { items: [{ id: 'old', name: 'Stale result' }] } })
    await flushPromises()
    expect(w.text()).not.toContain('Stale result')
    w.unmount()
  })
  it('object picker: ignores a pending response after removing its row', async () => {
    const { searchApi } = await import('@/api/client')
    let resolveRemoved
    searchApi.search.mockImplementationOnce(() => new Promise((resolve) => { resolveRemoved = resolve }))
    const w = await mountPanel('json_extractor', { json_paths: '[]', variables: [{ slot: 1, datapoint_id: '', datapoint_name: '' }, { slot: 2, datapoint_id: '', datapoint_name: '' }] })
    await flushPromises()
    const inputs = w.findAll('[data-testid^="extractor-variable-search-"]')
    await inputs[0].setValue('removed')
    await inputs[1].setValue('kept')
    await flushPromises()
    await w.find('[data-testid="extractor-variable-remove-0"]').trigger('click')
    resolveRemoved({ data: { items: [{ id: 'old', name: 'Stale removed-row result' }] } })
    await flushPromises()
    expect(w.text()).not.toContain('Stale removed-row result')
    w.unmount()
  })
  it('object picker: invalidates a pending search when both nodes have empty ids', async () => {
    const { searchApi } = await import('@/api/client')
    let resolveOld
    searchApi.search.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
    const empty = [{ slot: 1, datapoint_id: '', datapoint_name: '' }]
    const w = await mountPanel('json_extractor', { json_paths: '[]', variables: empty })
    await flushPromises()
    await w.find('[data-testid="extractor-variable-search-0"]').setValue('old')
    await w.setProps({ node: { id: 'n2', type: 'json_extractor', data: { json_paths: '[]', variables: empty } } })
    resolveOld({ data: { items: [{ id: 'old', name: 'Stale result' }] } })
    await flushPromises()
    expect(w.text()).not.toContain('Stale result')
    w.unmount()
  })

  it('discards the resolved path when an object variable changes', async () => {
    const { searchApi } = await import('@/api/client')
    searchApi.search.mockResolvedValueOnce({ data: { items: [{ id: 'new', name: 'Index zero' }] } })
    const w = await mountPanel(
      'json_extractor',
      { json_paths: JSON.stringify([{ label: 'a', path: '[###OBS1###].v' }]), variables: [{ slot: 1, datapoint_id: 'old', datapoint_name: 'Index one' }] },
      { n1: { _preview: '[{"v":"zero"},{"v":"one"}]', _resolved_paths: ['[1].v'], _path_templates: ['[###OBS1###].v'] } },
    )
    await flushPromises()
    expect(w.find('[data-testid="variable-resolved-path"]').exists()).toBe(true)
    await w.find('[data-testid="extractor-variable-search-0"]').setValue('zero')
    await flushPromises()
    await w.find('[data-testid="extractor-variable-result-0"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="variable-resolved-path"]').exists()).toBe(false)
    w.unmount()
  })
  it('keeps the resolved path invalid after the parent re-syncs the node data', async () => {
    const { searchApi } = await import('@/api/client')
    searchApi.search.mockResolvedValueOnce({ data: { items: [{ id: 'new', name: 'Index zero' }] } })
    const initial = { json_paths: JSON.stringify([{ label: 'a', path: '[###OBS1###].v' }]), variables: [{ slot: 1, datapoint_id: 'old', datapoint_name: 'Index one' }] }
    const w = await mountPanel('json_extractor', initial, { n1: { _preview: '[{"v":"zero"},{"v":"one"}]', _resolved_paths: ['[1].v'], _path_templates: ['[###OBS1###].v'] } })
    await flushPromises()
    await w.find('[data-testid="extractor-variable-search-0"]').setValue('zero')
    await flushPromises()
    await w.find('[data-testid="extractor-variable-result-0"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="variable-resolved-path"]').exists()).toBe(false)
    await w.setProps({ node: { id: 'n1', type: 'json_extractor', data: { ...initial, ...lastUpdate(w) } } })
    await flushPromises()
    expect(w.find('[data-testid="variable-resolved-path"]').exists()).toBe(false)
    w.unmount()
  })
})
