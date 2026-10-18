import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import NetworkAllowlistEditor from '@/components/ui/NetworkAllowlistEditor.vue'

function mk(modelValue = [], props = {}) {
  return mount(NetworkAllowlistEditor, { props: { modelValue, ...props } })
}

describe('NetworkAllowlistEditor', () => {
  it('renders one row per entry', () => {
    const w = mk(['10.0.0.0/8', '192.168.1.5'])
    expect(w.find('[data-testid="allowlist-entry-0"]').element.value).toBe('10.0.0.0/8')
    expect(w.find('[data-testid="allowlist-entry-1"]').element.value).toBe('192.168.1.5')
    w.unmount()
  })

  it('explains an empty list instead of showing nothing', () => {
    const w = mk([])
    expect(w.text()).toContain('alle Absender dürfen diese Verknüpfung auslösen')
    expect(w.find('[data-testid="allowlist-entry-0"]').exists()).toBe(false)
    w.unmount()
  })

  it('adds an empty row', async () => {
    const w = mk(['10.0.0.0/8'])
    await w.find('[data-testid="allowlist-add"]').trigger('click')
    expect(w.emitted('update:modelValue').at(-1)[0]).toEqual(['10.0.0.0/8', ''])
    w.unmount()
  })

  it('adds a row when Enter is pressed in a field', async () => {
    const w = mk(['10.0.0.0/8'])
    await w.find('[data-testid="allowlist-entry-0"]').trigger('keydown.enter')
    expect(w.emitted('update:modelValue').at(-1)[0]).toEqual(['10.0.0.0/8', ''])
    w.unmount()
  })

  it('removes the row that was clicked', async () => {
    const w = mk(['10.0.0.0/8', '192.168.1.5', '172.16.0.0/12'])
    await w.find('[data-testid="allowlist-remove-1"]').trigger('click')
    expect(w.emitted('update:modelValue').at(-1)[0]).toEqual(['10.0.0.0/8', '172.16.0.0/12'])
    w.unmount()
  })

  it('edits a row in place', async () => {
    const w = mk(['10.0.0.0/8', '192.168.1.5'])
    await w.find('[data-testid="allowlist-entry-1"]').setValue('192.168.2.0/24')
    expect(w.emitted('update:modelValue').at(-1)[0]).toEqual(['10.0.0.0/8', '192.168.2.0/24'])
    w.unmount()
  })

  it('accepts a legacy comma-separated string and emits a list', async () => {
    const w = mk('10.0.0.0/8, 192.168.1.5')
    expect(w.find('[data-testid="allowlist-entry-1"]').element.value).toBe('192.168.1.5')
    await w.find('[data-testid="allowlist-add"]').trigger('click')
    expect(w.emitted('update:modelValue').at(-1)[0]).toEqual(['10.0.0.0/8', '192.168.1.5', ''])
    w.unmount()
  })

  it('treats a null model value as an empty list', () => {
    const w = mk(null)
    expect(w.text()).toContain('alle Absender dürfen diese Verknüpfung auslösen')
    w.unmount()
  })

  it('flags entries that cannot be an IP address or network', () => {
    const w = mk(['10.0.0.0/8', 'nope', '999.1.1.1'])
    expect(w.find('[data-testid="allowlist-invalid"]').text()).toContain('2')
    expect(w.find('[data-testid="allowlist-entry-1"]').classes()).toContain('border-red-400')
    expect(w.find('[data-testid="allowlist-entry-0"]').classes()).not.toContain('border-red-400')
    w.unmount()
  })

  it('does not flag IPv6 entries it cannot judge, nor a blank new row', () => {
    const w = mk(['fd00::/8', ''])
    expect(w.find('[data-testid="allowlist-invalid"]').exists()).toBe(false)
    expect(w.find('[data-testid="allowlist-entry-0"]').classes()).not.toContain('border-red-400')
    expect(w.find('[data-testid="allowlist-entry-1"]').classes()).not.toContain('border-red-400')
    w.unmount()
  })

  it('flags an IPv4 spelling with leading zeros that the backend rejects', () => {
    const w = mk(['010.0.0.1'])
    expect(w.find('[data-testid="allowlist-invalid"]').exists()).toBe(true)
    w.unmount()
  })
})
