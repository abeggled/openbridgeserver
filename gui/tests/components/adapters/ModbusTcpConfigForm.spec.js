import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import ModbusTcpConfigForm from '@/components/adapters/ModbusTcpConfigForm.vue'

// Mirrors the JSON schema of obs.adapters.modbus_tcp.adapter.ModbusTcpAdapterConfig.
const SCHEMA = {
  type: 'object',
  properties: {
    host: { type: 'string', default: '192.168.1.1' },
    port: { type: 'integer', default: 502 },
    timeout: { type: 'number', default: 3 },
    tls: { type: 'boolean', default: false },
    tls_verify: { type: 'boolean', default: true },
    tls_ca_file: { anyOf: [{ type: 'string' }, { type: 'null' }], default: null },
    tls_client_cert_file: { anyOf: [{ type: 'string' }, { type: 'null' }], default: null },
    tls_client_key_file: { anyOf: [{ type: 'string' }, { type: 'null' }], default: null },
    tls_client_key_password: { anyOf: [{ type: 'string' }, { type: 'null' }], default: null, format: 'password' },
  },
}

const HINT = '[data-testid="modbus-tls-verify-disabled-hint"]'
const field = (name) => `[data-testid="config-field-${name}"]`

function mountForm(modelValue) {
  return mount(ModbusTcpConfigForm, { props: { schema: SCHEMA, modelValue } })
}

function lastEmit(wrapper) {
  const events = wrapper.emitted('update:modelValue')
  return events[events.length - 1][0]
}

describe('ModbusTcpConfigForm', () => {
  it('hides every TLS detail while TLS is off', () => {
    const wrapper = mountForm({ host: 'plc', port: 502, tls: false })

    expect(wrapper.find(field('tls')).exists()).toBe(true)
    expect(wrapper.find(field('tls_verify')).exists()).toBe(false)
    expect(wrapper.find(field('tls_ca_file')).exists()).toBe(false)
    expect(wrapper.find(field('tls_client_cert_file')).exists()).toBe(false)
    expect(wrapper.find(HINT).exists()).toBe(false)
  })

  it('shows verification, CA and mTLS fields with TLS on and verification enabled', () => {
    const wrapper = mountForm({ port: 802, tls: true, tls_verify: true })

    expect(wrapper.get(field('tls_verify')).element.checked).toBe(true)
    expect(wrapper.find(field('tls_ca_file')).exists()).toBe(true)
    expect(wrapper.find(field('tls_client_cert_file')).exists()).toBe(true)
    expect(wrapper.find(field('tls_client_key_file')).exists()).toBe(true)
    expect(wrapper.get(field('tls_client_key_password')).attributes('type')).toBe('password')
    expect(wrapper.find(HINT).exists()).toBe(false)
  })

  it('warns that the server identity is unchecked when verification is disabled', () => {
    const wrapper = mountForm({ port: 802, tls: true, tls_verify: false })

    expect(wrapper.get(HINT).text()).toBe('Die Verbindung ist verschlüsselt, die Identität des Servers wird jedoch nicht geprüft.')
    expect(wrapper.find(field('tls_ca_file')).exists()).toBe(false)
    expect(wrapper.find(field('tls_client_cert_file')).exists()).toBe(true)
  })

  it('suggests port 802 when TLS is switched on from the plain default port', async () => {
    const wrapper = mountForm({ port: 502, tls: false })

    await wrapper.get(field('tls')).setValue(true)

    expect(lastEmit(wrapper)).toMatchObject({ tls: true, port: 802 })
  })

  it('suggests port 802 when TLS is switched on without any port', async () => {
    const wrapper = mountForm({ port: null, tls: false })

    await wrapper.get(field('tls')).setValue(true)

    expect(lastEmit(wrapper)).toMatchObject({ tls: true, port: 802 })
  })

  it('keeps a custom port when TLS is switched on', async () => {
    const wrapper = mountForm({ port: 1502, tls: false })

    await wrapper.get(field('tls')).setValue(true)

    expect(lastEmit(wrapper)).toMatchObject({ tls: true, port: 1502 })
  })

  it('returns to port 502 when TLS is switched off from the TLS default port', async () => {
    const wrapper = mountForm({ port: 802, tls: true, tls_verify: true })

    await wrapper.get(field('tls')).setValue(false)

    expect(lastEmit(wrapper)).toMatchObject({ tls: false, port: 502 })
  })

  it('passes other changes through unchanged', async () => {
    const wrapper = mountForm({ host: 'plc', port: 802, tls: true, tls_verify: true })

    await wrapper.get(field('tls_verify')).setValue(false)

    expect(lastEmit(wrapper)).toMatchObject({ tls: true, tls_verify: false, port: 802 })
  })
})
