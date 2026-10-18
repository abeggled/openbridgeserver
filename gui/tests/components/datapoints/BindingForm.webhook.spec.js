import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

let createBinding
let updateBinding
let webhookBindings
let webhookRotateToken

const INSTANCES = [
  { id: 'hook-1', name: 'Hook Test', adapter_type: 'WEBHOOK' },
  { id: 'mqtt-1', name: 'MQTT Test', adapter_type: 'MQTT' },
]

function overview(bindings, extra = {}) {
  return {
    instance_id: 'hook-1',
    running: true,
    path_prefix: '/hook',
    trust_forwarded_for: false,
    rate_limit_per_minute: 60,
    rejections: { total: 0, counts: {}, last_reason: null, last_client_ip: null, last_slug: null, last_at: null },
    bindings,
    ...extra,
  }
}

const ENTRY = {
  binding_id: 'binding-1',
  slug: 'haustuer-klingel',
  token: 'tok-123',
  call_path: '/hook/haustuer-klingel?token=tok-123',
  call_path_token_in_path: '/hook/haustuer-klingel/tok-123',
  call_count: 2,
  publish_count: 2,
  last_called: '2026-10-06T07:30:00+00:00',
  last_status: 204,
}

beforeEach(() => {
  vi.resetModules()
  createBinding = vi.fn().mockResolvedValue({})
  updateBinding = vi.fn().mockResolvedValue({})
  webhookBindings = vi.fn().mockResolvedValue({ data: overview([ENTRY]) })
  webhookRotateToken = vi.fn().mockResolvedValue({
    data: {
      binding_id: 'binding-1',
      slug: 'haustuer-klingel',
      token: 'tok-new',
      call_path: '/hook/haustuer-klingel?token=tok-new',
      call_path_token_in_path: '/hook/haustuer-klingel/tok-new',
    },
  })
  vi.doMock('@/api/client', () => ({
    dpApi: { createBinding, updateBinding },
    adapterApi: {
      listInstances: vi.fn().mockResolvedValue({ data: INSTANCES }),
      knxDpts: vi.fn().mockResolvedValue({ data: [] }),
      knxGroupAddresses: vi.fn().mockResolvedValue({ data: [] }),
      mqttBrowseTopics: vi.fn().mockResolvedValue({ data: [] }),
      mqttSamplePayload: vi.fn().mockResolvedValue({ data: { payload: '{}' } }),
      iobrokerBrowseStates: vi.fn().mockResolvedValue({ data: [] }),
      snmpWalk: vi.fn().mockResolvedValue({ data: [] }),
      getZsuHolidays: vi.fn().mockResolvedValue({ data: [] }),
      webhookBindings,
      webhookRotateToken,
    },
    knxprojApi: { listGA: vi.fn().mockResolvedValue({ data: { total: 0, items: [] } }) },
    messageArchivesApi: { list: vi.fn().mockResolvedValue({ data: { archives: [] } }) },
  }))
})

afterEach(() => { vi.doUnmock('@/api/client') })

async function mountForm(props = {}) {
  const mod = await import('@/components/datapoints/BindingForm.vue')
  const wrapper = mount(mod.default, {
    props: { dpId: 'dp-1', dpPersistValue: false, dpDataType: 'BOOLEAN', ...props },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

function existingBinding(config = {}) {
  return {
    id: 'binding-1',
    adapter_instance_id: 'hook-1',
    adapter_type: 'WEBHOOK',
    instance_name: 'Hook Test',
    direction: 'SOURCE',
    enabled: true,
    config: {
      slug: 'haustuer-klingel',
      token: '[redacted]',
      methods: ['GET'],
      value_source: 'fixed',
      fixed_value: 'true',
      value_param: 'value',
      debounce_ms: 0,
      autoreset: false,
      autoreset_value: 'false',
      autoreset_delay_ms: 1000,
      ...config,
    },
  }
}

describe('BindingForm — WEBHOOK create', () => {
  it('hides the direction select and forces SOURCE', async () => {
    const w = await mountForm()
    await w.find('[data-testid="select-adapter-instance"]').setValue('hook-1')
    await flushPromises()

    expect(w.find('[data-testid="select-direction"]').exists()).toBe(false)
    expect(w.find('[data-testid="webhook-slug"]').exists()).toBe(true)
    w.unmount()
  })

  it('submits the webhook config with a normalised slug and no token', async () => {
    const w = await mountForm()
    await w.find('[data-testid="select-adapter-instance"]').setValue('hook-1')
    await flushPromises()
    await w.find('[data-testid="webhook-slug"]').setValue('  Haustuer-Klingel  ')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      adapter_instance_id: 'hook-1',
      direction: 'SOURCE',
      config: {
        slug: 'haustuer-klingel',
        methods: ['GET'],
        allowed_networks: [],
        value_source: 'fixed',
        fixed_value: 'true',
        debounce_ms: 0,
        autoreset: false,
      },
    }))
    w.unmount()
  })

  it('submits value_param instead of fixed_value for the request value source', async () => {
    const w = await mountForm()
    await w.find('[data-testid="select-adapter-instance"]').setValue('hook-1')
    await flushPromises()
    await w.find('[data-testid="webhook-slug"]').setValue('bell')
    await w.find('[data-testid="webhook-value-source"]').setValue('request')
    await w.find('[data-testid="webhook-value-param"]').setValue('  kovalue  ')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(createBinding.mock.calls[0][1].config).toEqual({
      slug: 'bell',
      methods: ['GET'],
      allowed_networks: [],
      value_source: 'request',
      value_param: 'kovalue',
      debounce_ms: 0,
      autoreset: false,
    })
    w.unmount()
  })

  it('falls back to GET and the default parameter name when the fields are emptied', async () => {
    const w = await mountForm()
    await w.find('[data-testid="select-adapter-instance"]').setValue('hook-1')
    await flushPromises()
    await w.find('[data-testid="webhook-slug"]').setValue('bell')
    await w.find('[data-testid="webhook-value-source"]').setValue('request')
    await w.find('[data-testid="webhook-value-param"]').setValue('')
    await w.find('[data-testid="webhook-method-GET"]').setValue(false)
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(createBinding.mock.calls[0][1].config).toMatchObject({ methods: ['GET'], value_param: 'value' })
    w.unmount()
  })

  it('normalises an emptied debounce field back to zero', async () => {
    const w = await mountForm()
    await w.find('[data-testid="select-adapter-instance"]').setValue('hook-1')
    await flushPromises()
    await w.find('[data-testid="webhook-slug"]').setValue('bell')
    await w.find('[data-testid="webhook-debounce"]').setValue('')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(createBinding.mock.calls[0][1].config.debounce_ms).toBe(0)
    w.unmount()
  })

  it('does not load the call URL before the binding exists', async () => {
    const w = await mountForm()
    await w.find('[data-testid="select-adapter-instance"]').setValue('hook-1')
    await flushPromises()

    expect(webhookBindings).not.toHaveBeenCalled()
    expect(w.find('[data-testid="webhook-call-url"]').exists()).toBe(false)
    w.unmount()
  })
})

describe('BindingForm — WEBHOOK edit', () => {
  it('loads the call URL for the edited binding', async () => {
    const w = await mountForm({ initial: existingBinding() })

    expect(webhookBindings).toHaveBeenCalledWith('hook-1')
    expect(w.find('[data-testid="webhook-call-url"]').element.value).toBe(`${window.location.origin}${ENTRY.call_path}`)
    w.unmount()
  })

  it('reports a binding that the webhook listing does not contain', async () => {
    webhookBindings.mockResolvedValue({ data: overview([]) })
    const w = await mountForm({ initial: existingBinding() })

    expect(w.text()).toContain('Aufruf-URL für diese Verknüpfung nicht gefunden')
    w.unmount()
  })

  it('reports a failed call-URL request', async () => {
    webhookBindings.mockRejectedValue({ response: { data: { detail: 'keine Berechtigung' } } })
    const w = await mountForm({ initial: existingBinding() })

    expect(w.text()).toContain('keine Berechtigung')
    w.unmount()
  })

  it('falls back to a generic message when the failure carries no detail', async () => {
    webhookBindings.mockRejectedValue(new Error('boom'))
    const w = await mountForm({ initial: existingBinding() })

    expect(w.text()).toContain('Aufruf-URL konnte nicht geladen werden')
    w.unmount()
  })

  it('submits the reset value and delay only when auto-reset is on', async () => {
    const w = await mountForm({ initial: existingBinding() })
    await w.find('[data-testid="webhook-autoreset"]').setValue(true)
    await w.find('[data-testid="webhook-autoreset-value"]').setValue('0')
    await w.find('[data-testid="webhook-autoreset-delay"]').setValue('250')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(updateBinding.mock.calls[0][2].config).toMatchObject({
      autoreset: true,
      autoreset_value: '0',
      autoreset_delay_ms: 250,
    })
    w.unmount()
  })

  it('leaves the reset fields out of the payload while it is off', async () => {
    const w = await mountForm({ initial: existingBinding({ autoreset_value: '0', autoreset_delay_ms: 250 }) })
    await w.find('form').trigger('submit')
    await flushPromises()

    const config = updateBinding.mock.calls[0][2].config
    expect(config.autoreset).toBe(false)
    expect(config).not.toHaveProperty('autoreset_value')
    expect(config).not.toHaveProperty('autoreset_delay_ms')
    w.unmount()
  })

  it('normalises an emptied reset delay back to zero', async () => {
    const w = await mountForm({ initial: existingBinding({ autoreset: true }) })
    await w.find('[data-testid="webhook-autoreset-delay"]').setValue('')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(updateBinding.mock.calls[0][2].config.autoreset_delay_ms).toBe(0)
    w.unmount()
  })

  it('tolerates an overview without a bindings list', async () => {
    webhookBindings.mockResolvedValue({ data: { ...overview([]), bindings: undefined } })
    const w = await mountForm({ initial: existingBinding() })

    expect(w.text()).toContain('Aufruf-URL für diese Verknüpfung nicht gefunden')
    w.unmount()
  })

  it('submits the binding allowlist, dropping blank rows', async () => {
    const w = await mountForm({
      initial: existingBinding({ allowed_networks: ['10.38.0.0/16'] }),
    })
    await w.find('[data-testid="allowlist-add"]').trigger('click')
    await w.find('[data-testid="allowlist-entry-1"]').setValue('  192.168.1.5  ')
    await w.find('[data-testid="allowlist-add"]').trigger('click')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(updateBinding.mock.calls[0][2].config.allowed_networks).toEqual(['10.38.0.0/16', '192.168.1.5'])
    w.unmount()
  })

  it('opens and re-submits a binding whose allowlist is still the legacy comma string', async () => {
    const w = await mountForm({
      initial: existingBinding({ allowed_networks: '10.0.0.0/8,192.168.1.4' }),
    })
    expect(w.find('[data-testid="allowlist-entry-1"]').element.value).toBe('192.168.1.4')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(updateBinding.mock.calls[0][2].config.allowed_networks).toEqual(['10.0.0.0/8', '192.168.1.4'])
    w.unmount()
  })

  it('surfaces rejections the server could not attribute to a binding', async () => {
    webhookBindings.mockResolvedValue({
      data: overview([ENTRY], {
        rejections: {
          total: 1,
          counts: { unknown_slug: 1 },
          last_reason: 'unknown_slug',
          last_client_ip: '192.0.2.10',
          last_slug: 'typo',
          last_at: '2026-10-07T00:00:00Z',
        },
      }),
    })
    const w = await mountForm({ initial: existingBinding() })

    const box = w.find('[data-testid="webhook-instance-rejections"]')
    expect(box.text()).toContain('unbekannter Slug')
    expect(box.text()).toContain('192.0.2.10')
    w.unmount()
  })

  it('shows no instance-level rejections when the overview carries none', async () => {
    webhookBindings.mockResolvedValue({ data: { ...overview([ENTRY]), rejections: undefined } })
    const w = await mountForm({ initial: existingBinding() })

    expect(w.find('[data-testid="webhook-instance-rejections"]').exists()).toBe(false)
    w.unmount()
  })

  it('warns when the binding allowlist excludes the host the GUI runs on', async () => {
    const w = await mountForm({ initial: existingBinding({ allowed_networks: ['10.38.0.0/16'] }) })

    expect(w.find('[data-testid="webhook-origin-warning"]').exists()).toBe(true)
    w.unmount()
  })

  it('replaces the shown URL after rotating the token', async () => {
    const w = await mountForm({ initial: existingBinding() })
    await w.find('[data-testid="webhook-rotate-token"]').trigger('click')
    await flushPromises()

    expect(webhookRotateToken).toHaveBeenCalledWith('hook-1', 'binding-1')
    expect(w.find('[data-testid="webhook-call-url"]').element.value).toBe(`${window.location.origin}/hook/haustuer-klingel?token=tok-new`)
    expect(w.find('[data-testid="webhook-call-count"]').text()).toBe('2')
    w.unmount()
  })

  it('reports a failed rotation', async () => {
    webhookRotateToken.mockRejectedValue({ response: { data: { detail: 'verboten' } } })
    const w = await mountForm({ initial: existingBinding() })
    await w.find('[data-testid="webhook-rotate-token"]').trigger('click')
    await flushPromises()

    expect(w.text()).toContain('verboten')
    w.unmount()
  })

  it('keeps the URL and the rotate button available after a failed rotation, so it can be retried', async () => {
    webhookRotateToken.mockRejectedValueOnce(new Error('temporary'))
    const w = await mountForm({ initial: existingBinding() })
    await w.find('[data-testid="webhook-rotate-token"]').trigger('click')
    await flushPromises()

    expect(w.find('[data-testid="webhook-error"]').exists()).toBe(true)
    expect(w.find('[data-testid="webhook-call-url"]').exists()).toBe(true)

    await w.find('[data-testid="webhook-rotate-token"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="webhook-error"]').exists()).toBe(false)
    expect(w.find('[data-testid="webhook-call-url"]').element.value).toContain('tok-new')
    w.unmount()
  })

  it('falls back to a generic message when the rotation failure carries no detail', async () => {
    webhookRotateToken.mockRejectedValue(new Error('boom'))
    const w = await mountForm({ initial: existingBinding() })
    await w.find('[data-testid="webhook-rotate-token"]').trigger('click')
    await flushPromises()

    expect(w.text()).toContain('Token konnte nicht neu erzeugt werden')
    w.unmount()
  })

  it('restores stored webhook settings and submits them unchanged', async () => {
    const w = await mountForm({
      initial: existingBinding({ methods: ['GET', 'POST'], value_source: 'request', value_param: 'kovalue', debounce_ms: 750 }),
    })
    expect(w.find('[data-testid="webhook-value-param"]').element.value).toBe('kovalue')
    expect(w.find('[data-testid="webhook-debounce"]').element.value).toBe('750')

    await w.find('form').trigger('submit')
    await flushPromises()

    expect(updateBinding).toHaveBeenCalledWith('dp-1', 'binding-1', expect.objectContaining({
      direction: 'SOURCE',
      config: {
        slug: 'haustuer-klingel',
        methods: ['GET', 'POST'],
        allowed_networks: [],
        value_source: 'request',
        value_param: 'kovalue',
        debounce_ms: 750,
        autoreset: false,
      },
    }))
    w.unmount()
  })

  it('replaces explicit nulls in a stored config with the defaults', async () => {
    const w = await mountForm({
      initial: {
        ...existingBinding(),
        config: {
          slug: null,
          methods: null,
          allowed_networks: null,
          autoreset: null,
          autoreset_value: null,
          autoreset_delay_ms: null,
          value_source: null,
          fixed_value: null,
          value_param: null,
          debounce_ms: null,
        },
      },
    })

    expect(w.find('[data-testid="webhook-slug"]').element.value).toBe('')
    expect(w.find('[data-testid="webhook-method-GET"]').element.checked).toBe(true)
    expect(w.find('[data-testid="webhook-value-source"]').element.value).toBe('fixed')
    expect(w.find('[data-testid="webhook-fixed-value"]').element.value).toBe('true')
    expect(w.find('[data-testid="webhook-debounce"]').element.value).toBe('0')
    w.unmount()
  })

  it('applies defaults for a stored config that predates the current fields', async () => {
    const w = await mountForm({ initial: { ...existingBinding(), config: { slug: 'legacy' } } })

    expect(w.find('[data-testid="webhook-value-source"]').element.value).toBe('fixed')
    expect(w.find('[data-testid="webhook-fixed-value"]').element.value).toBe('true')
    expect(w.find('[data-testid="webhook-debounce"]').element.value).toBe('0')
    expect(w.find('[data-testid="webhook-method-GET"]').element.checked).toBe(true)
    w.unmount()
  })
})
