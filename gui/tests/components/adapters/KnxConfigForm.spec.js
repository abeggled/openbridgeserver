import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

let uploadMock = vi.fn()

beforeEach(() => {
  vi.resetModules()
  uploadMock = vi.fn().mockResolvedValue({ data: { project_name: 'TestProject', tunnels: [], backbone: null } })
  vi.doMock('@/api/client.js', () => ({
    knxKeyfileApi: { upload: uploadMock },
  }))
})

const BASE_CFG = {
  connection_type: 'tunneling',
  host: '192.168.1.100',
  port: 3671,
  individual_address: '1.1.255',
  local_ip: null,
  multicast_group: '224.0.23.12',
  multicast_port: 3671,
  knxkeys_file_path: null,
  knxkeys_password: null,
}

async function mountKnx(modelValue = {}) {
  const { default: KnxConfigForm } = await import('@/components/adapters/KnxConfigForm.vue')
  return mount(KnxConfigForm, {
    props: { modelValue: { ...BASE_CFG, ...modelValue } },
  })
}

describe('KnxConfigForm — connection type select', () => {
  it('renders connection type select with 5 options', async () => {
    const w = await mountKnx()
    await flushPromises()
    const select = w.find('select')
    expect(select.findAll('option').length).toBe(5)
  })

  it('selects "tunneling" by default', async () => {
    const w = await mountKnx()
    await flushPromises()
    expect(w.find('select').element.value).toBe('tunneling')
  })

  it('selects the value from modelValue', async () => {
    const w = await mountKnx({ connection_type: 'routing' })
    await flushPromises()
    expect(w.find('select').element.value).toBe('routing')
  })
})

describe('KnxConfigForm — tunneling mode', () => {
  it('shows host input for tunneling mode', async () => {
    const w = await mountKnx({ connection_type: 'tunneling' })
    await flushPromises()
    const hostInput = w.findAll('input').find(i => i.attributes('placeholder') === '192.168.1.100')
    expect(hostInput).toBeTruthy()
  })

  it('shows port input for tunneling mode', async () => {
    const w = await mountKnx({ connection_type: 'tunneling' })
    await flushPromises()
    const portInput = w.findAll('input[type="number"]').find(i => i.attributes('placeholder') === '3671')
    expect(portInput).toBeTruthy()
  })

  it('shows individual_address input for plain tunneling', async () => {
    const w = await mountKnx({ connection_type: 'tunneling' })
    await flushPromises()
    const iaInput = w.findAll('input').find(i => i.attributes('placeholder') === '1.1.255')
    expect(iaInput).toBeTruthy()
  })

  it('hides individual_address input for secure tunneling (comes from keyfile)', async () => {
    const w = await mountKnx({ connection_type: 'tunneling_secure' })
    await flushPromises()
    // In secure mode without existing keyfile, individual_address is hidden
    // (it only shows after keyfile is uploaded/parsed)
    const iaInput = w.findAll('input').find(i => i.attributes('placeholder') === '1.1.255')
    expect(iaInput).toBeFalsy()
  })
})

describe('KnxConfigForm — routing mode', () => {
  it('shows multicast_group input for routing mode', async () => {
    const w = await mountKnx({ connection_type: 'routing' })
    await flushPromises()
    const mcInput = w.findAll('input').find(i => i.attributes('placeholder') === '224.0.23.12')
    expect(mcInput).toBeTruthy()
  })

  it('hides host/port inputs for routing mode', async () => {
    const w = await mountKnx({ connection_type: 'routing' })
    await flushPromises()
    const hostInput = w.findAll('input').find(i => i.attributes('placeholder') === '192.168.1.100')
    expect(hostInput).toBeFalsy()
  })
})

describe('KnxConfigForm — secure warning', () => {
  it('shows secure warning for tunneling_secure', async () => {
    const w = await mountKnx({ connection_type: 'tunneling_secure' })
    await flushPromises()
    // Docker warning is shown for all secure types
    expect(w.html()).toContain('amber')
  })

  it('shows secure warning for routing_secure', async () => {
    const w = await mountKnx({ connection_type: 'routing_secure' })
    await flushPromises()
    expect(w.html()).toContain('amber')
  })

  it('does not show secure warning for plain tunneling', async () => {
    const w = await mountKnx({ connection_type: 'tunneling' })
    await flushPromises()
    // Count amber elements — should be 0 warning boxes
    const amberWarning = w.findAll('[class*="amber"]').filter(el => el.text().includes('Docker') || el.text().includes('Warnung') || el.text().includes('Hinweis'))
    expect(amberWarning.length).toBe(0)
  })
})

describe('KnxConfigForm — keyfile upload form (secure mode)', () => {
  it('shows file input and password input for secure connection without existing keyfile', async () => {
    const w = await mountKnx({ connection_type: 'tunneling_secure', knxkeys_file_path: null })
    await flushPromises()
    expect(w.find('input[type="file"]').exists()).toBe(true)
    expect(w.find('input[type="password"]').exists()).toBe(true)
  })

  it('upload button disabled when no file selected', async () => {
    const w = await mountKnx({ connection_type: 'tunneling_secure' })
    await flushPromises()
    const uploadBtn = w.findAll('button').find(b => b.text().includes('Hochladen') || b.text().includes('Upload') || b.text().includes('hochladen'))
    expect(uploadBtn?.attributes('disabled')).toBeDefined()
  })

  it('shows existing keyfile name when knxkeys_file_path provided', async () => {
    const w = await mountKnx({
      connection_type: 'tunneling_secure',
      knxkeys_file_path: '/data/myproject.knxkeys',
    })
    await flushPromises()
    expect(w.text()).toContain('myproject.knxkeys')
  })
})

describe('KnxConfigForm — gateway scan', () => {
  it('shows scan button', async () => {
    const w = await mountKnx()
    await flushPromises()
    const scanBtn = w.findAll('button').find(b => b.text().includes('Scannen') || b.text().includes('Suchen') || b.text().includes('scan'))
    expect(scanBtn).toBeTruthy()
  })
})
