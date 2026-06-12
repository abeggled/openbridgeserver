import { describe, expect, it } from 'vitest'

import { adapterStatusDetailText, adapterStatusLabel } from '@/utils/adapterStatus'

// Fake vue-i18n t/te: te() reports whether a key exists in `keys`; t() interpolates {params}.
function makeI18n(keys) {
  const te = key => key in keys
  const t = (key, params = {}) => keys[key].replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ''))
  return { t, te }
}

describe('adapterStatusDetailText', () => {
  const keys = {
    'adapters.statusDetail.connectedTo': 'Connected to {host}:{port}',
    'adapters.statusDetail.disconnected': 'Disconnected',
  }

  it('translates a known code with params', () => {
    const { t, te } = makeI18n(keys)
    const a = { status_detail_code: 'connectedTo', status_detail_params: { host: 'h', port: 502 }, status_detail: 'h:502' }
    expect(adapterStatusDetailText(a, t, te)).toBe('Connected to h:502')
  })

  it('translates a code that takes no params', () => {
    const { t, te } = makeI18n(keys)
    const a = { status_detail_code: 'disconnected', status_detail_params: {}, status_detail: 'Disconnected (raw)' }
    expect(adapterStatusDetailText(a, t, te)).toBe('Disconnected')
  })

  it('falls back to status_detail when the code key is unknown', () => {
    const { t, te } = makeI18n(keys)
    const a = { status_detail_code: 'somethingNew', status_detail: 'raw fallback text' }
    expect(adapterStatusDetailText(a, t, te)).toBe('raw fallback text')
  })

  it('falls back to status_detail when no code is present (dynamic detail)', () => {
    const { t, te } = makeI18n(keys)
    const a = { status_detail_code: null, status_detail: 'ValueError: boom' }
    expect(adapterStatusDetailText(a, t, te)).toBe('ValueError: boom')
  })

  it('returns empty string for an adapter with neither code nor detail', () => {
    const { t, te } = makeI18n(keys)
    expect(adapterStatusDetailText({}, t, te)).toBe('')
    expect(adapterStatusDetailText(null, t, te)).toBe('')
  })
})

describe('adapterStatusLabel', () => {
  it('still maps severity/connected to locale keys (regression guard)', () => {
    expect(adapterStatusLabel({ running: false })).toBe('adapters.status.inactive')
    expect(adapterStatusLabel({ running: true, severity: 'error' })).toBe('common.error')
    expect(adapterStatusLabel({ running: true, severity: 'warning' })).toBe('adapters.status.degraded')
    expect(adapterStatusLabel({ running: true, connected: true })).toBe('adapters.status.connected')
  })
})
